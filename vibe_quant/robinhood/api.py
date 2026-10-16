"""
Dashboard API for the Robinhood trading system: the bot, its proposals, the real account, options
backtests and the Research Lab.

It never places orders itself. It controls the bot (the ``trading-paper`` user service, paper or live
per ``state/settings.json``) and the lab (``trading-lab``) and talks to the bot through files in the
trading project's ``state/`` (status, command queue, settings).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import re
import sqlite3
import subprocess
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi import Body
from fastapi import HTTPException
from fastapi import Query

from vibe_quant.robinhood import STATE
from vibe_quant.robinhood import TRADING_ROOT

from research.control import CommandQueue  # noqa: E402  (trading project, on sys.path)
from research.control import Settings  # noqa: E402
from research.control import read_json  # noqa: E402


router = APIRouter(prefix="/api/robinhood", tags=["robinhood"])
lab_router = APIRouter(prefix="/api/lab", tags=["lab"])

BOT_SERVICE = "trading-paper.service"
LAB_SERVICE = "trading-lab.service"
BACKTESTS = TRADING_ROOT / "reports" / "backtest"
REPORTS = TRADING_ROOT / "reports"
PYTHON = str(TRADING_ROOT / ".venv" / "bin" / "python")
STATUS_STALE_SECS = 60
ACCOUNT_CACHE_SECS = 30

_backtest_proc: subprocess.Popen | None = None
_account_cache: tuple[float, dict[str, Any]] | None = None
_account_lock = asyncio.Lock()


# -- HELPERS ----------------------------------------------------------------------------------------------


def _bad(message: str, status: int = 400) -> HTTPException:
    return HTTPException(status_code=status, detail=message)


def _ledger_rows(query: str, args: tuple = ()) -> list[sqlite3.Row]:
    path = STATE / "proposals.db"
    if not path.exists():
        return []
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    db.row_factory = sqlite3.Row
    try:
        return db.execute(query, args).fetchall()
    finally:
        db.close()


def _systemctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, timeout=30)  # noqa: S603, S607


def _service_state(unit: str) -> dict[str, Any]:
    try:
        result = _systemctl("show", unit, "--property=ActiveState,SubState,LoadState,ActiveEnterTimestamp")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"installed": False, "error": str(exc)}
    props = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    return {
        "installed": props.get("LoadState") == "loaded",
        "active": props.get("ActiveState"),
        "sub": props.get("SubState"),
        "since": props.get("ActiveEnterTimestamp") or None,
    }


def _new_york() -> Any:
    from nautilus_robinhood.parsing import NEW_YORK

    return NEW_YORK


def _today_ny() -> str:
    return dt.datetime.now(_new_york()).date().isoformat()


def _proposal_summary(p: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "proposal_id", "trading_date", "proposed_at", "status", "ticker", "thesis", "thesis_source", "contract",
        "expiration", "strike", "option_type", "dte", "proposed_quantity", "proposed_limit", "maximum_debit",
        "underlying_at_proposal", "option_bid_at_proposal", "option_ask_at_proposal", "option_delta_at_proposal",
        "selected_setup", "combined_setup_score", "social_score", "entry_score", "decision", "fatal_flags",
        "missing_fields", "entry_eligible", "trade_eligible", "trade", "shadow", "ai_summary", "component_scores",
        "lab", "exit_rules",
    )
    return {k: p.get(k) for k in keys}


def _num(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


# -- BOT: STATUS / CONTROLS -----------------------------------------------------------------------------


@router.get("/status")
async def status() -> dict[str, Any]:
    bot = read_json(STATE / "status.json")
    stale = True
    if bot and bot.get("updated_at"):
        age = (dt.datetime.now(dt.UTC) - dt.datetime.fromisoformat(bot["updated_at"])).total_seconds()
        stale = age > STATUS_STALE_SECS or not bot.get("running")
        bot["age_secs"] = round(age)
    queue = CommandQueue(STATE / "commands.db")
    try:
        commands = queue.recent(15)
    finally:
        queue.close()
    today = dt.datetime.now(_new_york()).date()
    week_start = (today - dt.timedelta(days=today.weekday())).isoformat()
    week = [json.loads(r[0]) for r in _ledger_rows("SELECT payload_json FROM proposals WHERE traded = 1 AND trading_date >= ?", (week_start,))]
    used = [p for p in week if (p.get("trade") or {}).get("status") in ("ENTRY_PENDING", "ENTRY_WORKING", "OPEN", "EXITING", "CLOSED")]
    return {
        "service": await asyncio.to_thread(_service_state, BOT_SERVICE),
        "bot": bot,
        "bot_stale": stale,
        "settings": Settings(STATE / "settings.json").load(),
        "commands": commands,
        "week_new_positions": len(used),
        "today": today.isoformat(),
        "server_time": dt.datetime.now(dt.UTC).isoformat(),
    }


@router.post("/bot/{action}")
async def bot_action(action: str) -> dict[str, Any]:
    if action not in ("start", "stop", "restart"):
        raise _bad("unknown action")
    state = await asyncio.to_thread(_service_state, BOT_SERVICE)
    if not state.get("installed"):
        raise _bad("the trading-paper service isn't installed (run tools/install_services.sh in ~/trading)")
    # Start/stop also enable/disable, so the bot comes back (or stays off) after a reboot.
    verb = {"start": ("enable", "--now"), "stop": ("disable", "--now"), "restart": ("restart",)}[action]
    result = await asyncio.to_thread(_systemctl, *verb, BOT_SERVICE)
    if result.returncode != 0:
        raise _bad(result.stderr.strip() or f"systemctl {action} failed", 500)
    return {"ok": True, "service": await asyncio.to_thread(_service_state, BOT_SERVICE)}


@router.post("/command")
async def command(body: dict[str, Any] = Body(...)) -> dict[str, Any]:  # noqa: B008
    queue = CommandQueue(STATE / "commands.db")
    try:
        command_id = queue.submit(str(body.get("command")), body.get("args") or {})
    except ValueError as exc:
        raise _bad(str(exc)) from exc
    finally:
        queue.close()
    return {"ok": True, "id": command_id}


@router.get("/command/{command_id}")
async def command_result(command_id: int) -> dict[str, Any]:
    queue = CommandQueue(STATE / "commands.db")
    try:
        found = [c for c in queue.recent(200) if c["id"] == command_id]
    finally:
        queue.close()
    return found[0] if found else {"id": command_id, "status": "unknown"}


@router.get("/settings")
async def get_settings() -> dict[str, Any]:
    return Settings(STATE / "settings.json").load()


@router.put("/settings")
async def put_settings(body: dict[str, Any] = Body(...)) -> dict[str, Any]:  # noqa: B008
    settings = Settings(STATE / "settings.json")
    before = settings.load()
    try:
        after = settings.update(body)
    except ValueError as exc:
        raise _bad(str(exc)) from exc
    # The bot picks its mode at start: switching paper/live restarts a running bot.
    if after["trading_mode"] != before["trading_mode"]:
        state = await asyncio.to_thread(_service_state, BOT_SERVICE)
        if state.get("active") == "active":
            await asyncio.to_thread(_systemctl, "restart", BOT_SERVICE)
    return after


@router.get("/paper")
async def paper() -> dict[str, Any]:
    from nautilus_robinhood.paper import PaperState

    path = STATE / "paper.db"
    if not path.exists():
        return {"exists": False}
    state = PaperState(path)
    try:
        snapshot = state.load()
        fills = state.fills()
        started = state.meta("starting_balances")
        created = state.meta("created_at")
    finally:
        state.close()
    return {
        "exists": True,
        "cash": float(snapshot[0]) if snapshot else None,
        "positions": snapshot[1] if snapshot else [],
        "starting_balances": json.loads(started) if started else None,
        "created_at": created,
        "fills": [
            {"ts": f[1], "strategy": f[2], "instrument_id": f[3], "side": f[5], "qty": f[6], "px": f[7]}
            for f in fills[-100:]
        ],
    }


@router.post("/paper/reset")
async def paper_reset(body: dict[str, Any] = Body(...)) -> dict[str, Any]:  # noqa: B008
    try:
        balance = float(body.get("balance", 1000))
    except (TypeError, ValueError) as exc:
        raise _bad("balance must be a number") from exc
    if not 100 <= balance <= 1_000_000:
        raise _bad("balance must be between 100 and 1,000,000")
    state = await asyncio.to_thread(_service_state, BOT_SERVICE)
    if state.get("active") in ("active", "activating", "deactivating"):
        raise _bad("stop the bot first")
    from nautilus_robinhood.paper import PaperState

    archive = PaperState.reset(STATE / "paper.db")
    fresh = PaperState(STATE / "paper.db")
    try:
        fresh.set_meta("starting_balances", json.dumps([f"{balance:g} USD"]))
        fresh.set_meta("created_at", dt.datetime.now(dt.UTC).isoformat())
        fresh.save_snapshot(Decimal(str(balance)), [])
    finally:
        fresh.close()
    return {"ok": True, "archived": archive.name if archive else None, "balance": balance}


@router.get("/logs")
async def logs(lines: int = Query(300, ge=10, le=3000), unit: str = Query("bot")) -> dict[str, Any]:
    service = {"bot": BOT_SERVICE, "lab": LAB_SERVICE}.get(unit)
    if service is None:
        raise _bad("unit must be bot or lab")
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            ["journalctl", "--user", "-u", service, "-n", str(lines), "--no-pager", "-o", "cat"],  # noqa: S607
            capture_output=True, text=True, timeout=20,
        )
        text = result.stdout
    except (OSError, subprocess.TimeoutExpired) as exc:
        text = f"(journal unavailable: {exc})"
    return {"text": re.sub(r"\x1b\[[0-9;]*m", "", text) or "(no log output yet)"}


# -- REAL ACCOUNT (read-only) -----------------------------------------------------------------------------


async def _fetch_account() -> dict[str, Any]:
    from nautilus_robinhood.mcp_client import RobinhoodMCPClient

    client = RobinhoodMCPClient(live_trading=False)
    await client.connect()
    try:
        since = (dt.datetime.now(dt.UTC) - dt.timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%SZ")
        portfolio, positions, option_positions, option_orders, orders = await asyncio.gather(
            client.get_portfolio(),
            client.get_positions(),
            client.get_option_positions(),
            client.get_option_orders(created_at_gte=since, max_pages=2),
            client.get_orders(created_at_gte=since, max_pages=2),
        )
        held = [p for p in positions if p.quantity != 0]
        quotes = {q.symbol: q for q in await client.get_quotes([p.symbol for p in held])} if held else {}
    finally:
        await client.disconnect()

    def order_row(o: Any, kind: str) -> dict[str, Any]:
        return {
            "id": o.id, "kind": kind, "symbol": o.symbol, "side": o.side, "type": o.type, "state": o.state,
            "created_at": o.created_at, "quantity": _num(o.quantity), "filled": _num(o.cumulative_quantity),
            "price": _num(o.price), "average_price": _num(o.average_price), "ref_id": o.ref_id,
        }

    def last_price(symbol: str) -> float | None:
        q = quotes.get(symbol)
        if q is None:
            return None
        price = getattr(q, "last_trade_price", None) or getattr(q, "ask_price", None)
        return _num(price)

    return {
        "account": f"••••{client.account_number[-4:]}",
        "portfolio": {
            "total_value": _num(portfolio.total_value),
            "equity_value": _num(portfolio.equity_value),
            "cash": _num(portfolio.cash),
            "buying_power": _num(portfolio.buying_power.buying_power),
        },
        "positions": [
            {
                "symbol": p.symbol, "quantity": _num(p.quantity), "average_price": _num(p.average_buy_price),
                "last_price": last_price(p.symbol),
            }
            for p in held
        ],
        "option_positions": [
            {"symbol": p.symbol, "quantity": _num(p.quantity), "average_price": _num(p.average_buy_price)}
            for p in option_positions
        ],
        "orders": sorted(
            [order_row(o, "option") for o in option_orders] + [order_row(o, "equity") for o in orders],
            key=lambda r: r["created_at"], reverse=True,
        )[:40],
        "fetched_at": dt.datetime.now(dt.UTC).isoformat(),
    }


@router.get("/account")
async def account(refresh: bool = False) -> dict[str, Any]:
    global _account_cache
    async with _account_lock:
        if not refresh and _account_cache and time.monotonic() - _account_cache[0] < ACCOUNT_CACHE_SECS:
            return _account_cache[1]
        try:
            data = await asyncio.wait_for(_fetch_account(), timeout=90)
        except Exception as exc:  # router down, timeout, ...
            raise _bad(f"Robinhood unavailable: {exc}", 502) from exc
        _account_cache = (time.monotonic(), data)
        return data


# -- PROPOSALS / REPORTS ----------------------------------------------------------------------------------


@router.get("/proposals")
async def proposals(date: str | None = None) -> dict[str, Any]:
    day = date or _today_ny()
    rows = _ledger_rows("SELECT payload_json FROM proposals WHERE trading_date = ? ORDER BY created_at", (day,))
    return {"date": day, "proposals": [_proposal_summary(json.loads(r[0])) for r in rows]}


@router.get("/proposals/dates")
async def proposal_dates() -> list[dict[str, Any]]:
    rows = _ledger_rows("SELECT trading_date, COUNT(*) FROM proposals GROUP BY trading_date ORDER BY trading_date DESC")
    return [{"date": r[0], "count": r[1]} for r in rows]


def _evidence_for(p: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    freeze = read_json(p.get("evidence_path") or "", {}) or {}
    record = next((e for e in freeze.get("evaluated", []) if e.get("ticker") == p["ticker"]), None)
    if record:
        for key in ("price_measurements", "preliminary", "final", "selection", "ensemble", "entry_payload"):
            out[key] = record.get(key)
    research = read_json(freeze.get("research_evidence_path") or "", {}) or {}
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
        out["comment_count"] = ticker.get("comment_count")
    return out


@router.get("/proposal")
async def proposal(proposal_id: str = Query(..., alias="id")) -> dict[str, Any]:
    rows = _ledger_rows("SELECT payload_json FROM proposals WHERE proposal_id = ?", (proposal_id,))
    if not rows:
        raise _bad("not found", 404)
    p = json.loads(rows[0][0])
    events = _ledger_rows(
        "SELECT event_at, event_type, payload_json FROM events WHERE proposal_id = ? ORDER BY event_id", (proposal_id,),
    )
    return {
        "proposal": p,
        "events": [{"at": e[0], "type": e[1], "payload": json.loads(e[2])} for e in events if e[1] != "marks"],
        "evidence": await asyncio.to_thread(_evidence_for, p),
    }


@router.get("/history")
async def history() -> dict[str, Any]:
    rows = [json.loads(r[0]) for r in _ledger_rows("SELECT payload_json FROM proposals ORDER BY trading_date DESC, created_at DESC")]
    trades = [p for p in rows if (p.get("trade") or {}).get("status") == "CLOSED"]
    shadows = [p for p in rows if (p.get("shadow") or {}).get("status") == "CLOSED"]

    def stats(items: list[dict[str, Any]], key: str) -> dict[str, Any]:
        returns = [i[key]["return_pct"] for i in items if i[key].get("return_pct") is not None]
        pnl = [i[key].get("pnl") or 0 for i in items]
        wins = [r for r in returns if r > 0]
        return {
            "count": len(items),
            "wins": len(wins),
            "win_rate_pct": round(100 * len(wins) / len(returns), 1) if returns else None,
            "avg_return_pct": round(sum(returns) / len(returns), 2) if returns else None,
            "total_pnl": round(sum(pnl), 2),
        }

    return {
        "trades": stats(trades, "trade"),
        "shadows": stats(shadows, "shadow"),
        "proposals": len(rows),
        "rows": [_proposal_summary(p) for p in rows[:300]],
    }


@router.get("/reports")
async def reports() -> list[str]:
    files = sorted(REPORTS.glob("????-??-??.md"), reverse=True) if REPORTS.exists() else []
    return [f.stem for f in files]


@router.get("/reports/{date}")
async def report(date: str) -> dict[str, Any]:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise _bad("bad date")
    path = REPORTS / f"{date}.md"
    return {"date": date, "markdown": path.read_text() if path.exists() else "No report for that day."}


# -- OPTIONS BACKTESTS (tools/backtest_options.py) --------------------------------------------------------


def _backtest_summary(path: Path) -> dict[str, Any]:
    data = read_json(path, {}) or {}
    args = data.get("args", {})
    sim = data.get("account_1000", {})
    return {
        "run_id": data.get("run_id") or path.stem.removeprefix("backtest-"),
        "finished_at": data.get("finished_at") or dt.datetime.fromtimestamp(path.stat().st_mtime, dt.UTC).isoformat(),
        "start": args.get("start"),
        "end": args.get("end"),
        "setups": [s for s in data.get("setups", {}) if s != "all"],
        "tickers": data.get("tickers", []),
        "all": (data.get("setups", {}).get("all") or {}).get("all"),
        "out_of_sample": (data.get("setups", {}).get("all") or {}).get("out_of_sample"),
        "account": {k: sim.get(k) for k in ("start_cash", "end_cash", "return_pct", "max_drawdown_pct", "trades")},
    }


@router.get("/backtests")
async def backtests() -> dict[str, Any]:
    BACKTESTS.mkdir(parents=True, exist_ok=True)
    finished = [
        _backtest_summary(p)
        for p in sorted(BACKTESTS.glob("backtest-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not p.name.endswith(".progress.json")
    ]
    running = None
    if _backtest_proc is not None and _backtest_proc.poll() is None:
        progress = sorted(BACKTESTS.glob("backtest-*.progress.json"), key=lambda p: p.stat().st_mtime)
        running = read_json(progress[-1]) if progress else {"done": [], "total": None}
    return {"running": running, "runs": finished}


@router.get("/backtests/{run_id}")
async def backtest(run_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9A-Za-z_-]{1,60}", run_id):
        raise _bad("bad run id")
    path = BACKTESTS / f"backtest-{run_id}.json"
    if not path.exists():
        raise _bad("not found", 404)
    return read_json(path)


@router.post("/backtests")
async def start_backtest(body: dict[str, Any] = Body(...)) -> dict[str, Any]:  # noqa: B008
    global _backtest_proc
    if _backtest_proc is not None and _backtest_proc.poll() is None:
        raise _bad("a backtest is already running")
    run_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    cmd = [PYTHON, str(TRADING_ROOT / "tools" / "backtest_options.py"), "--run-id", run_id, "--out", str(BACKTESTS)]
    tickers = [t.strip().upper() for t in str(body.get("tickers") or "").split(",") if t.strip()]
    if tickers:
        if not all(re.fullmatch(r"[A-Z.]{1,6}", t) for t in tickers):
            raise _bad("tickers must be symbols like NVDA,AMD")
        cmd += ["--tickers", ",".join(tickers)]
    else:
        cmd += ["--universe", str(max(1, min(40, int(body.get("universe") or 20))))]
    start = str(body.get("start") or "2023-06-01")
    try:
        dt.date.fromisoformat(start)
    except ValueError as exc:
        raise _bad("start must be YYYY-MM-DD") from exc
    cmd += ["--start", start]
    setups = [s for s in body.get("setups") or [] if re.fullmatch(r"[a-z0-9_]{1,60}", s)]
    if setups:
        cmd += ["--setups", ",".join(setups)]
    BACKTESTS.mkdir(parents=True, exist_ok=True)
    (TRADING_ROOT / "logs").mkdir(exist_ok=True)
    log = open(TRADING_ROOT / "logs" / f"backtest-{run_id}.log", "w")  # noqa: SIM115 (kept open by the child)
    _backtest_proc = subprocess.Popen(cmd, cwd=TRADING_ROOT, stdout=log, stderr=subprocess.STDOUT)  # noqa: S603
    return {"ok": True, "run_id": run_id}


# -- RESEARCH LAB -----------------------------------------------------------------------------------------


def _lab() -> Any:
    from lab import LAB_DB
    from lab.db import LabDB

    return LabDB(LAB_DB)


def _live_results() -> dict[str, dict[str, Any]]:
    """Per Lab strategy: closed shadow and real trades of its proposals."""
    from lab.metrics import profit_factor

    out: dict[str, dict[str, Any]] = {}
    for (payload,) in _ledger_rows("SELECT payload_json FROM proposals"):
        p = json.loads(payload)
        sid = (p.get("lab") or {}).get("strategy_id")
        if not sid:
            continue
        entry = out.setdefault(sid, {"proposals": 0, "shadow": [], "real": []})
        entry["proposals"] += 1
        for key in ("shadow", "trade"):
            record = p.get(key) or {}
            if record.get("status") == "CLOSED" and record.get("return_pct") is not None:
                entry["shadow" if key == "shadow" else "real"].append(record["return_pct"])
    for entry in out.values():
        both = entry["shadow"] + entry["real"]
        pf = profit_factor(both) if both else None
        entry["closed"] = len(both)
        entry["profit_factor"] = None if pf is None else (99.0 if pf == float("inf") else round(pf, 2))
        entry["avg_return_pct"] = round(sum(both) / len(both), 1) if both else None
    return out


def _experiment_row(e: dict[str, Any]) -> dict[str, Any]:
    r = e.get("result") or {}
    gates = r.get("gates") or {}
    train = r.get("train") or {}
    return {
        "id": e["id"], "created_at": e["created_at"], "finished_at": e["finished_at"], "status": e["status"],
        "stage": e["stage"], "source_kind": e["source_kind"], "title": e["title"], "groups": e["groups"],
        "verdict": e["verdict"], "lesson": e["lesson"], "error": (e.get("error") or "").split("\n")[0][:300] or None,
        "strategy": (r.get("strategy") or {}).get("name"),
        "train_return_pct": train.get("return_pct"), "train_trades": train.get("trades"),
        "train_pf": train.get("profit_factor"),
        "holdout_pf": gates.get("profit_factor"), "holdout_trades": (gates.get("stats") or {}).get("trades"),
        "holdout_account_pct": (gates.get("account") or {}).get("median_return_pct"),
        "holdout_win_rate": (gates.get("stats") or {}).get("win_rate_pct"),
    }


@lab_router.get("")
async def lab_status() -> dict[str, Any]:
    def gather() -> dict[str, Any]:
        from lab import wsb

        lab = _lab()
        try:
            counts = {r[0]: r[1] for r in lab.db.execute("SELECT status, COUNT(*) FROM experiments GROUP BY status")}
            plays = lab.db.execute("SELECT COUNT(*) FROM plays").fetchone()[0]
            knowledge = lab.db.execute("SELECT source, COUNT(*), SUM(tested > 0) FROM knowledge GROUP BY source").fetchall()
            out = {
                "status": lab.get("status", {}),
                "stop_requested": bool(lab.get("stop_requested")),
                "run_now": lab.get("run_now"),
                "experiments": counts,
                "plays": plays,
                "knowledge": {s: {"count": n, "tested": t or 0} for s, n, t in knowledge},
                "pool": lab.get("pool"),
                "groups": {n: {"description": g["description"], "count": len(g["members"])} for n, g in lab.groups().items()},
            }
        finally:
            lab.close()
        try:
            out["wsb"] = wsb.status()
        except sqlite3.Error:
            out["wsb"] = None
        out["service"] = _service_state(LAB_SERVICE)
        out["live"] = _live_results()
        return out

    return await asyncio.to_thread(gather)


@lab_router.post("/control/{action}")
async def lab_action(action: str) -> dict[str, Any]:
    if action not in ("start", "stop", "run-now"):
        raise _bad("unknown action")
    state = await asyncio.to_thread(_service_state, LAB_SERVICE)
    if not state.get("installed"):
        raise _bad("the trading-lab service isn't installed (run tools/install_services.sh in ~/trading)")
    lab = _lab()
    try:
        if action == "stop":
            lab.put("stop_requested", True)
            result = await asyncio.to_thread(_systemctl, "disable", "--now", LAB_SERVICE)
        else:
            lab.put("stop_requested", False)
            if action == "run-now":
                lab.put("run_now", dt.datetime.now(dt.UTC).isoformat())
            result = await asyncio.to_thread(_systemctl, "enable", "--now", LAB_SERVICE)
    finally:
        lab.close()
    if result.returncode != 0:
        raise _bad(result.stderr.strip() or f"systemctl failed for {action}", 500)
    return {"ok": True, "service": await asyncio.to_thread(_service_state, LAB_SERVICE)}


@lab_router.get("/experiments")
async def lab_experiments(limit: int = Query(100, ge=1, le=500)) -> list[dict[str, Any]]:
    lab = _lab()
    try:
        return [_experiment_row(e) for e in lab.experiments(limit=limit)]
    finally:
        lab.close()


@lab_router.get("/experiments/{experiment_id}")
async def lab_experiment(experiment_id: int) -> dict[str, Any]:
    from lab import strategy as lab_strategy

    lab = _lab()
    try:
        e = lab.experiment(experiment_id)
    finally:
        lab.close()
    if e is None:
        raise _bad("not found", 404)
    files = None
    if e.get("strategy_id"):
        try:
            files = lab_strategy.load_files(e["strategy_id"])
        except (OSError, ValueError):
            files = None
    return json.loads(json.dumps({**e, "files": files}, default=str))


@lab_router.get("/strategies")
async def lab_strategies() -> list[dict[str, Any]]:
    lab = _lab()
    try:
        rows = lab.strategies()
    finally:
        lab.close()
    live = await asyncio.to_thread(_live_results)
    out = []
    for s in rows:
        holdout = s.get("holdout") or {}
        out.append({
            **{k: s[k] for k in ("id", "name", "summary", "created_at", "experiment_id", "status", "groups", "score", "promoted_at", "retired_at", "retire_reason")},
            "holdout": {
                "profit_factor": holdout.get("profit_factor"), "account": holdout.get("account"),
                "stats": holdout.get("stats"), "checks": holdout.get("checks"),
            },
            "live": live.get(s["id"]),
        })
    return out


@lab_router.post("/strategies/{sid}/{action}")
async def lab_strategy_action(sid: str, action: str) -> dict[str, Any]:
    if action not in ("retire", "activate"):
        raise _bad("unknown action")
    lab = _lab()
    try:
        s = lab.strategy(sid)
        if s is None:
            raise _bad("not found", 404)
        now = dt.datetime.now(dt.UTC).isoformat()
        if action == "retire":
            lab.upsert_strategy(id=sid, name=s["name"], created_at=s["created_at"], status="retired", retired_at=now, retire_reason="retired by Peter")
        else:
            lab.upsert_strategy(id=sid, name=s["name"], created_at=s["created_at"], status="active", promoted_at=now, retire_reason=None)
    finally:
        lab.close()
    return {"ok": True}


@lab_router.get("/knowledge")
async def lab_knowledge() -> list[dict[str, Any]]:
    lab = _lab()
    try:
        return lab.knowledge()
    finally:
        lab.close()


@lab_router.post("/knowledge")
async def lab_add_knowledge(body: dict[str, Any] = Body(...)) -> dict[str, Any]:  # noqa: B008
    title, text = str(body.get("title") or "").strip(), str(body.get("body") or "").strip()
    if not title or len(text) < 20:
        raise _bad("give the idea a title and a description (a sentence or more)")
    lab = _lab()
    try:
        if lab.add_knowledge("peter", title, text, ["peter"]) is None:
            raise _bad("an idea with that title already exists")
    finally:
        lab.close()
    return {"ok": True}


@lab_router.delete("/knowledge/{knowledge_id}")
async def lab_delete_knowledge(knowledge_id: int) -> dict[str, Any]:
    lab = _lab()
    try:
        lab.delete_knowledge(knowledge_id)
    finally:
        lab.close()
    return {"ok": True}


@lab_router.get("/plays")
async def lab_plays() -> dict[str, Any]:
    lab = _lab()
    try:
        return {"stats": lab.play_stats(), "recent": lab.plays(100)}
    finally:
        lab.close()


@lab_router.get("/groups")
async def lab_groups() -> dict[str, Any]:
    lab = _lab()
    try:
        return lab.groups()
    finally:
        lab.close()
