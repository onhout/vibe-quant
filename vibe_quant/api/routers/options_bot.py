"""Options bot router (/api/options-bot): the Reddit-options trading bot.

The bot is a NautilusTrader node in the trading project (``TRADING_ROOT``, default ``~/trading``)
running as the ``trading-paper`` systemd user service. It talks to this API only through files in
``TRADING_ROOT/state``: ``status.json`` (the bot writes it every 15 s), ``commands.db`` (a SQLite
command queue the bot polls every 3 s), ``settings.json`` (approval and paper/live mode, read live by
the bot) and ``proposals.db`` (the proposal ledger). Daily reports are ``TRADING_ROOT/reports/*.md``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import subprocess
from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query

from vibe_quant.api.schemas.options_bot import (
    BotCommandRequest,
    BotCommandResponse,
    BotLogsResponse,
    BotReportResponse,
    BotServiceState,
    BotSettings,
    BotSettingsUpdate,
    BotStatusResponse,
    OutcomeStats,
    ProposalDate,
    ProposalDetailResponse,
    ProposalEvent,
    ProposalHistoryResponse,
    ProposalListResponse,
    ProposalSummary,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/options-bot", tags=["options-bot"])

BOT_SERVICE = "trading-paper.service"
NEW_YORK = ZoneInfo("America/New_York")
#: Status older than this (or a bot that says it isn't running) counts as stale.
STATUS_STALE_SECS = 60
DEFAULT_SETTINGS: dict[str, str] = {"approval_mode": "auto", "trading_mode": "paper"}
#: Trade states that use up the week's one new position.
WEEK_USED_STATES = frozenset({"ENTRY_PENDING", "ENTRY_WORKING", "OPEN", "EXITING", "CLOSED"})
_SUMMARY_FIELDS = tuple(ProposalSummary.model_fields)


def trading_root() -> Path:
    return Path(os.environ.get("TRADING_ROOT") or Path.home() / "trading")


def _state() -> Path:
    return trading_root() / "state"


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _write_json_atomic(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    os.replace(tmp, path)


def _today_ny() -> date:
    return datetime.now(NEW_YORK).date()


# --- Ledger (read-only) ---


def _ledger_rows(query: str, args: tuple[object, ...] = ()) -> list[sqlite3.Row]:
    path = _state() / "proposals.db"
    if not path.exists():
        return []
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)) as db:
        db.row_factory = sqlite3.Row
        return db.execute(query, args).fetchall()


def _summary(p: dict[str, Any]) -> ProposalSummary:
    return ProposalSummary.model_validate({k: p.get(k) for k in _SUMMARY_FIELDS})


def _proposals(query: str, args: tuple[object, ...] = ()) -> list[dict[str, Any]]:
    return [json.loads(r["payload_json"]) for r in _ledger_rows(query, args)]


# --- Command queue (same table the bot creates and polls) ---


def _commands_db() -> sqlite3.Connection:
    path = _state() / "commands.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS commands (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            command TEXT NOT NULL,
            args_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            result TEXT,
            done_at TEXT
        )
        """,
    )
    return db


def _command(row: sqlite3.Row) -> BotCommandResponse:
    return BotCommandResponse(
        id=row["id"],
        created_at=row["created_at"],
        command=row["command"],
        args=json.loads(row["args_json"] or "{}"),
        status=row["status"],
        result=row["result"],
        done_at=row["done_at"],
    )


# --- systemd user service ---


def _systemctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, timeout=30)


def _service_state() -> BotServiceState:
    try:
        result = _systemctl("show", BOT_SERVICE, "--property=ActiveState,SubState,LoadState,ActiveEnterTimestamp")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return BotServiceState(installed=False, error=str(exc))
    props = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    return BotServiceState(
        installed=props.get("LoadState") == "loaded",
        active=props.get("ActiveState"),
        sub_state=props.get("SubState"),
        since=props.get("ActiveEnterTimestamp") or None,
    )


def _settings() -> BotSettings:
    stored = _read_json(_state() / "settings.json")
    return BotSettings.model_validate({**DEFAULT_SETTINGS, **(stored if isinstance(stored, dict) else {})})


# --- Status / control ---


@router.get("/status", response_model=BotStatusResponse)
def get_bot_status() -> BotStatusResponse:
    bot = _read_json(_state() / "status.json")
    bot = bot if isinstance(bot, dict) else None
    age: int | None = None
    stale = True
    if bot and bot.get("updated_at"):
        age = round((datetime.now(UTC) - datetime.fromisoformat(str(bot["updated_at"]))).total_seconds())
        stale = age > STATUS_STALE_SECS or not bot.get("running")
    with closing(_commands_db()) as db:
        commands = [_command(r) for r in db.execute("SELECT * FROM commands ORDER BY id DESC LIMIT 15")]
    today = _today_ny()
    week_start = (today - timedelta(days=today.weekday())).isoformat()
    week = _proposals("SELECT payload_json FROM proposals WHERE traded = 1 AND trading_date >= ?", (week_start,))
    used = [p for p in week if (p.get("trade") or {}).get("status") in WEEK_USED_STATES]
    return BotStatusResponse(
        service=_service_state(),
        bot=bot,
        bot_stale=stale,
        bot_age_secs=age,
        settings=_settings(),
        commands=commands,
        week_new_positions=len(used),
        today=today.isoformat(),
    )


@router.post("/service/{action}", response_model=BotServiceState)
def control_bot_service(action: Literal["start", "stop", "restart"]) -> BotServiceState:
    """Start/stop also enable/disable the service, so the bot comes back (or stays off) after a reboot."""
    if not _service_state().installed:
        raise HTTPException(
            status_code=409,
            detail=f"{BOT_SERVICE} isn't installed: run tools/install_services.sh in {trading_root()}",
        )
    verb = {"start": ("enable", "--now"), "stop": ("disable", "--now"), "restart": ("restart",)}[action]
    result = _systemctl(*verb, BOT_SERVICE)
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=result.stderr.strip() or f"systemctl {action} failed")
    return _service_state()


@router.get("/settings", response_model=BotSettings)
def get_bot_settings() -> BotSettings:
    return _settings()


@router.put("/settings", response_model=BotSettings)
def update_bot_settings(body: BotSettingsUpdate) -> BotSettings:
    before = _settings()
    changes = body.model_dump(exclude_none=True)
    after = BotSettings.model_validate({**before.model_dump(), **changes})
    stored = _read_json(_state() / "settings.json")
    _write_json_atomic(_state() / "settings.json", {**(stored if isinstance(stored, dict) else {}), **after.model_dump()})
    # The bot picks paper/live at start: switching modes restarts a running bot.
    if after.trading_mode != before.trading_mode and _service_state().active == "active":
        _systemctl("restart", BOT_SERVICE)
    return after


@router.post("/commands", response_model=BotCommandResponse, status_code=201)
def submit_bot_command(body: BotCommandRequest) -> BotCommandResponse:
    args: dict[str, object] = {}
    if body.command in ("trade", "skip", "close"):
        if not body.proposal_id:
            raise HTTPException(status_code=422, detail=f"{body.command} needs proposal_id")
        if not _ledger_rows("SELECT 1 FROM proposals WHERE proposal_id = ?", (body.proposal_id,)):
            raise HTTPException(status_code=404, detail=f"Proposal {body.proposal_id} not found")
        args["proposal_id"] = body.proposal_id
    with closing(_commands_db()) as db:
        cur = db.execute(
            "INSERT INTO commands (created_at, command, args_json) VALUES (?, ?, ?)",
            (datetime.now(UTC).isoformat(), body.command, json.dumps(args)),
        )
        db.commit()
        row = db.execute("SELECT * FROM commands WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _command(row)


@router.get("/commands/{command_id}", response_model=BotCommandResponse)
def get_bot_command(command_id: int) -> BotCommandResponse:
    with closing(_commands_db()) as db:
        row = db.execute("SELECT * FROM commands WHERE id = ?", (command_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Command {command_id} not found")
    return _command(row)


@router.get("/logs", response_model=BotLogsResponse)
def get_bot_logs(lines: int = Query(300, ge=10, le=3000)) -> BotLogsResponse:
    try:
        result = subprocess.run(
            ["journalctl", "--user", "-u", BOT_SERVICE, "-n", str(lines), "--no-pager", "-o", "cat"],
            capture_output=True,
            text=True,
            timeout=20,
        )
        text = result.stdout
    except (OSError, subprocess.TimeoutExpired) as exc:
        text = f"(journal unavailable: {exc})"
    return BotLogsResponse(unit=BOT_SERVICE, text=re.sub(r"\x1b\[[0-9;]*m", "", text) or "(no log output yet)")


# --- Proposals ---


@router.get("/proposals", response_model=ProposalListResponse)
def list_proposals(day: str | None = Query(None, alias="date", pattern=r"^\d{4}-\d{2}-\d{2}$")) -> ProposalListResponse:
    trading_date = day or _today_ny().isoformat()
    rows = _proposals("SELECT payload_json FROM proposals WHERE trading_date = ? ORDER BY created_at", (trading_date,))
    return ProposalListResponse(date=trading_date, proposals=[_summary(p) for p in rows])


@router.get("/proposals/dates", response_model=list[ProposalDate])
def list_proposal_dates() -> list[ProposalDate]:
    rows = _ledger_rows("SELECT trading_date, COUNT(*) AS n FROM proposals GROUP BY trading_date ORDER BY trading_date DESC")
    return [ProposalDate(date=r["trading_date"], count=r["n"]) for r in rows]


def _evidence(p: dict[str, Any]) -> dict[str, object]:
    """What the freeze and research stages recorded for this proposal's ticker."""
    out: dict[str, object] = {}
    freeze = _read_json(Path(str(p.get("evidence_path") or ""))) or {}
    record = next((e for e in freeze.get("evaluated", []) if e.get("ticker") == p["ticker"]), None)
    if record:
        for key in ("price_measurements", "preliminary", "final", "selection", "ensemble"):
            out[key] = record.get(key)
    research = _read_json(Path(str(freeze.get("research_evidence_path") or ""))) or {}
    ticker = next((t for t in research.get("tickers", []) if t.get("ticker") == p["ticker"]), None)
    if ticker:
        out["reddit_measurements"] = ticker.get("reddit_measurements")
        out["posts"] = [
            {k: x.get(k) for k in ("subreddit", "author", "created", "title", "link")} for x in ticker.get("posts", [])[:25]
        ]
        out["headlines"] = ticker.get("headlines", [])[:20]
        out["filings"] = ticker.get("filings")
        ai = dict(ticker.get("ai") or {})
        ai.pop("raw", None)
        index = ai.pop("evidence_index", {}) or {}
        cited = (ai.get("dd_post_ids") or []) + ((ai.get("catalyst") or {}).get("evidence_ids") or [])
        ai["cited"] = {k: index[k] for k in cited if k in index}
        out["ai"] = ai
    return out


@router.get("/proposal", response_model=ProposalDetailResponse)
def get_proposal(proposal_id: str) -> ProposalDetailResponse:
    rows = _proposals("SELECT payload_json FROM proposals WHERE proposal_id = ?", (proposal_id,))
    if not rows:
        raise HTTPException(status_code=404, detail=f"Proposal {proposal_id} not found")
    p = rows[0]
    events = _ledger_rows(
        "SELECT event_id, event_at, event_type, payload_json FROM events WHERE proposal_id = ? ORDER BY event_id",
        (proposal_id,),
    )
    return ProposalDetailResponse(
        proposal=p,
        events=[
            ProposalEvent(id=e["event_id"], at=e["event_at"], type=e["event_type"], payload=json.loads(e["payload_json"]))
            for e in events
            if e["event_type"] != "marks"
        ],
        evidence=_evidence(p),
    )


def _outcomes(items: list[dict[str, Any]], key: str) -> OutcomeStats:
    returns = [i[key]["return_pct"] for i in items if i[key].get("return_pct") is not None]
    wins = [r for r in returns if r > 0]
    return OutcomeStats(
        count=len(items),
        wins=len(wins),
        win_rate_pct=round(100 * len(wins) / len(returns), 1) if returns else None,
        avg_return_pct=round(sum(returns) / len(returns), 2) if returns else None,
        total_pnl=round(sum(i[key].get("pnl") or 0 for i in items), 2),
    )


@router.get("/history", response_model=ProposalHistoryResponse)
def get_proposal_history(limit: int = Query(300, ge=1, le=2000)) -> ProposalHistoryResponse:
    rows = _proposals("SELECT payload_json FROM proposals ORDER BY trading_date DESC, created_at DESC")
    trades = [p for p in rows if (p.get("trade") or {}).get("status") == "CLOSED"]
    shadows = [p for p in rows if (p.get("shadow") or {}).get("status") == "CLOSED"]
    return ProposalHistoryResponse(
        trades=_outcomes(trades, "trade"),
        shadows=_outcomes(shadows, "shadow"),
        proposals=len(rows),
        rows=[_summary(p) for p in rows[:limit]],
    )


# --- Daily reports ---


@router.get("/reports", response_model=list[str])
def list_bot_reports() -> list[str]:
    folder = trading_root() / "reports"
    return sorted((f.stem for f in folder.glob("????-??-??.md")), reverse=True) if folder.exists() else []


@router.get("/reports/{report_date}", response_model=BotReportResponse)
def get_bot_report(report_date: str) -> BotReportResponse:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", report_date):
        raise HTTPException(status_code=422, detail="report_date must be YYYY-MM-DD")
    path = trading_root() / "reports" / f"{report_date}.md"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"No report for {report_date}")
    return BotReportResponse(date=report_date, markdown=path.read_text())
