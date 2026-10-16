"""Regime tuner: settle on a strategy whose parameters make money in bull AND bear markets.

Peter's loop (long-only stocks: buy low, sell high; no entry = stay flat):

1. **Candidates.** Every active strategy (discovery champions, promoted research ideas,
   templates) on a supported timeframe, plus any strategy queued from the UI. Tuned
   versions are new strategies (``strategy_type='tuned'``) linked to their root.
2. **Test in stages.** Each attempt runs validation backtests (``POST /api/backtest/validation``
   on Alpaca stocks) over the windows of the current stage: ``bull`` (the strategy's own
   market in the 2025-26 rally), then ``bear`` (the 2025 crash and the stocks that fell
   for months), then ``wide`` (other markets: indices, mega caps, metals, wild names).
3. **Losing -> AI parameters.** A window that traded and lost (return <= 0 or profit factor
   below the bar), or a stage without enough trades, sends the DSL, every window's metrics
   and the losing trades to the research LLM (``VQ_OPENAI_*``, the same client as
   extraction). Its numeric changes are clamped to each indicator's GA ranges, checked
   against the DSL schema and saved as a new strategy version, which is re-tested from the
   ``bull`` stage. ``max_rounds`` attempts per candidate.
4. **Winning -> widen.** A stage that passes moves the candidate to the next stage.
5. **Settled.** Passing every stage (with ``min_total_trades`` across them) settles the
   candidate: an Alpaca PAPER session starts from its bull validation run with the protected
   sizing caps, when none is running. Never live.

Runs through the backend API (tracked and visible like manual runs); bookkeeping in
``data/state/regime_tuner.json``, config overrides in ``data/state/regime_tuner_config.json``.
``python -m vibe_quant.research tune [--once] [--strategy-id N] [--dry-run]``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

STATE_DIR = Path(os.getenv("VQ_STATE_DIR", "data/state"))
STATE_PATH = STATE_DIR / "regime_tuner.json"
CONFIG_PATH = STATE_DIR / "regime_tuner_config.json"
ENV_API_URL = "VQ_API_URL"

TUNED_TYPE = "tuned"
STAGES = ("bull", "bear", "wide")
_NUMBER = re.compile(r"(?<![A-Za-z_])-?\d+(?:\.\d+)?")


@dataclass
class TunerWindow:
    """One backtest window: a market regime on a set of symbols."""

    label: str
    stage: str  # bull | bear | wide
    start: str
    end: str
    symbols: list[str] = field(default_factory=list)  # empty = the candidate's own symbols
    note: str = ""


def _default_windows() -> list[TunerWindow]:
    rally = ("2025-04-08", "2026-10-01")
    full = ("2023-10-02", "2026-10-01")
    return [
        TunerWindow(
            "bull_rally", "bull", *rally, [], "the strategy's own market in the 2025-26 rally"
        ),
        TunerWindow(
            "crash_2025",
            "bear",
            "2025-02-19",
            "2025-04-08",
            ["SPY", "QQQ", "NVDA", "TSLA"],
            "market crash: SPY -19%, QQQ -23%, NVDA -37%, TSLA -54%",
        ),
        TunerWindow("amd_bear", "bear", "2024-03-07", "2025-04-08", ["AMD"], "AMD -63%"),
        TunerWindow("intc_bear", "bear", "2023-12-27", "2025-04-08", ["INTC"], "INTC -64%"),
        TunerWindow("msft_bear", "bear", "2025-10-29", "2026-06-25", ["MSFT"], "MSFT -35%"),
        TunerWindow("now_bear", "bear", "2025-01-28", "2026-04-10", ["NOW"], "NOW -64%"),
        TunerWindow("pltr_bear", "bear", "2025-11-03", "2026-06-25", ["PLTR"], "PLTR -48%"),
        TunerWindow("nflx_bear", "bear", "2025-06-30", "2026-10-02", ["NFLX"], "NFLX -50%"),
        TunerWindow(
            "nke_bear", "bear", "2023-12-19", "2026-10-02", ["NKE"], "NKE -72% peak to trough"
        ),
        TunerWindow(
            "adbe_bear", "bear", "2024-02-02", "2026-06-25", ["ADBE"], "ADBE -70% peak to trough"
        ),
        TunerWindow("indices", "wide", *full, ["SPY", "QQQ", "IWM"], "US indices, three years"),
        TunerWindow(
            "mega_caps", "wide", *full, ["AAPL", "MSFT", "META", "GOOGL", "AMZN"], "mega caps"
        ),
        TunerWindow("metals", "wide", *full, ["GLD", "SLV"], "gold and silver"),
        TunerWindow(
            "wild", "wide", *full, ["TSLA", "PLTR", "HOOD", "RKLB"], "volatile Reddit names"
        ),
    ]


@dataclass
class TunerConfig:
    api_url: str = field(default_factory=lambda: os.getenv(ENV_API_URL, "http://127.0.0.1:8000"))
    interval_minutes: int = 30
    windows: list[TunerWindow] = field(default_factory=_default_windows)
    default_symbols: list[str] = field(default_factory=lambda: ["SPY", "QQQ"])
    timeframes: list[str] = field(default_factory=lambda: ["15m", "1h", "4h", "1d"])
    # A window that traded passes with return > min_return and profit factor >= min_profit_factor;
    # a window with no trades is flat (allowed: no entry = don't get in).
    min_return: float = 0.0
    min_profit_factor: float = 1.2
    min_stage_trades: dict[str, int] = field(
        default_factory=lambda: {"bull": 8, "bear": 0, "wide": 8}
    )
    min_total_trades: int = 30
    max_rounds: int = 5
    initial_balance: int = 1000
    run_timeout_minutes: int = 30
    candidate_ids: list[int] = field(default_factory=list)  # queued first (UI "Tune" button)
    auto_paper: bool = True
    paper_max_position_pct: float = 0.5
    paper_max_position_count: int = 1

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> TunerConfig:
        cfg = cls()
        if path.exists():
            for key, value in json.loads(path.read_text()).items():
                if key == "windows":
                    cfg.windows = [TunerWindow(**w) for w in value]
                elif hasattr(cfg, key):
                    setattr(cfg, key, value)
                else:
                    logger.warning("regime tuner config: unknown key %r ignored", key)
        return cfg

    def save(self, path: Path = CONFIG_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2))
        tmp.replace(path)

    def stage_windows(self, stage: str) -> list[TunerWindow]:
        return [w for w in self.windows if w.stage == stage]


def load_state(path: Path = STATE_PATH) -> dict[str, Any]:
    if not path.exists():
        return {"candidates": {}, "updated_at": None, "current": None}
    return dict(json.loads(path.read_text()))


def save_state(state: dict[str, Any], path: Path = STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = _now()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, default=str))
    tmp.replace(path)


def _now() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="seconds")


# -- verdicts --------------------------------------------------------------------------------------


def window_verdict(result: dict[str, Any], cfg: TunerConfig) -> str:
    """``flat`` (no trades), ``win`` or ``lose`` for one finished window."""
    trades = int(result.get("total_trades") or 0)
    if trades == 0:
        return "flat"
    ret = float(result.get("total_return") or 0.0)
    pf = result.get("profit_factor")
    pf_value = float(pf) if pf is not None else 0.0
    return "win" if ret > cfg.min_return and pf_value >= cfg.min_profit_factor else "lose"


def stage_passed(stage: str, windows: list[dict[str, Any]], cfg: TunerConfig) -> tuple[bool, str]:
    if any(w.get("verdict") in (None, "error") for w in windows):
        return False, "a window failed to run"
    losers = [w["label"] for w in windows if w["verdict"] == "lose"]
    if losers:
        return False, f"lost in {', '.join(losers)}"
    trades = sum(int(w.get("trades") or 0) for w in windows)
    need = int(cfg.min_stage_trades.get(stage, 0))
    if trades < need:
        return False, f"only {trades} trades in the {stage} stage (need {need})"
    return True, f"profitable in every {stage} window that traded ({trades} trades)"


# -- AI parameter suggestions ----------------------------------------------------------------------


def _param_ranges(dsl: dict[str, Any]) -> dict[str, dict[str, Any]]:
    from vibe_quant.dsl.indicators import indicator_registry

    out: dict[str, dict[str, Any]] = {}
    for name, spec in (dsl.get("indicators") or {}).items():
        reg = indicator_registry.get(str(spec.get("type", "")).upper())
        if reg is None:
            continue
        out[name] = {
            "type": reg.name,
            "params": {k: list(v) for k, v in reg.param_ranges.items()},
            "outputs": list(reg.output_names) if reg.output_names else [],
            "threshold_range": list(reg.threshold_range) if reg.threshold_range else None,
        }
    return out


def _trade_summary(trades: list[dict[str, Any]], limit: int = 8) -> dict[str, Any]:
    if not trades:
        return {"trades": 0}
    pnls = [float(t.get("roi_percent") or 0.0) for t in trades]
    reasons: dict[str, int] = {}
    for t in trades:
        reasons[str(t.get("exit_reason") or "?")] = (
            reasons.get(str(t.get("exit_reason") or "?"), 0) + 1
        )
    worst = sorted(trades, key=lambda t: float(t.get("roi_percent") or 0.0))[:limit]
    return {
        "trades": len(trades),
        "wins": sum(1 for p in pnls if p > 0),
        "losses": sum(1 for p in pnls if p <= 0),
        "avg_return_pct": round(sum(pnls) / len(pnls), 2),
        "exit_reasons": reasons,
        "worst_trades": [
            {
                "symbol": str(t.get("symbol", "")).split(".")[0],
                "entry": str(t.get("entry_time", ""))[:10],
                "entry_price": t.get("entry_price"),
                "exit": str(t.get("exit_time", ""))[:10],
                "exit_price": t.get("exit_price"),
                "return_pct": round(float(t.get("roi_percent") or 0.0), 2),
                "exit_reason": t.get("exit_reason"),
            }
            for t in worst
        ],
    }


_PROMPT = """You tune a LONG-ONLY stock trading strategy written in vibe-quant's strategy DSL.
Peter's rule: in a bull or a bear market, buy low and sell high; when there is no good entry,
stay out (a window with no trades is fine). The strategy must be profitable (return > 0 and
profit factor >= {min_pf}) in every window where it trades, bull and bear alike.

Current strategy (DSL as JSON):
{dsl}

Valid parameter ranges per indicator (name -> type, params [min, max], outputs, threshold range):
{ranges}

Results of this version on each market window (return and drawdown are fractions; the
worst trades show where it bought and sold badly):
{results}

Earlier versions already tried for this strategy (do not repeat them):
{history}

Change the parameters so it stops losing in the losing windows while staying profitable in
the winning ones. You may:
- change any numeric indicator parameter (stay inside its range);
- change the thresholds and comparison operators of the existing conditions (e.g. buy at
  the lower band instead of the upper), keeping the same indicators in each condition;
- change stop_loss / take_profit (fixed_pct percent 0.1-50 / 0.1-100, atr types use
  atr_multiplier 0.5-10 / 0.5-20 and need an ATR indicator);
- add at most two extra long entry conditions and one extra exit condition as filters (e.g. a trend filter so it does not
  buy into a falling market), using new indicators you define in "new_indicators" (types from
  the registry: RSI, EMA, SMA, WMA, ADX, ATR, MACD, BBANDS, DONCHIAN, ROC, CCI, STOCH, MFI)
  or existing ones. A condition looks like "ema_fast > ema_slow", "rsi_14 < 30",
  "close > sma_200", "macd_1.histogram > 0", "adx_14 > 20".

Answer with ONLY a JSON object, no prose around it:
{{"reasoning": "<2-4 sentences: what went wrong and what you changed>",
  "indicators": {{"<existing indicator name>": {{"<param>": <number>}}}},
  "new_indicators": {{"<new name>": {{"type": "<TYPE>", "<param>": <number>}}}},
  "entry_long": ["<every long entry condition, revised>"],
  "exit_long": ["<every long exit condition, revised>"],
  "stop_loss": {{"type": "fixed_pct", "percent": <number>}},
  "take_profit": {{"type": "fixed_pct", "percent": <number>}}}}
Omit keys you do not change."""


_OPERATOR_WORDS = frozenset({"crosses_above", "crosses_below", "between", "and", "or"})


def _shape(condition: str) -> tuple[str, ...]:
    """What a condition compares (its indicator/price references); operators and numbers may change."""
    names = re.findall(r"[A-Za-z_][A-Za-z0-9_.]*", _NUMBER.sub(" ", condition))
    return tuple(sorted(n for n in names if n not in _OPERATOR_WORDS))


def apply_suggestion(
    dsl: dict[str, Any], suggestion: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    """The DSL with the AI's changes applied within the rules; also the notes of what was dropped."""
    from vibe_quant.dsl.indicators import indicator_registry

    new = json.loads(json.dumps(dsl))
    notes: list[str] = []
    indicators: dict[str, dict[str, Any]] = new.setdefault("indicators", {})

    def clamp(spec_type: str, param: str, value: Any) -> Any:
        reg = indicator_registry.get(spec_type.upper())
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError(f"{param} must be a number")
        lo_hi = reg.param_ranges.get(param) if reg is not None else None
        if lo_hi is not None:
            lo, hi = lo_hi
            if not lo <= value <= hi:
                notes.append(f"{spec_type}.{param}={value} clamped to [{lo}, {hi}]")
                value = min(max(value, lo), hi)
        schema_type = reg.param_schema.get(param) if reg is not None else None
        return round(value) if schema_type is int else float(value)

    for name, params in (suggestion.get("indicators") or {}).items():
        spec = indicators.get(name)
        if spec is None or not isinstance(params, dict):
            notes.append(f"unknown indicator {name} ignored")
            continue
        for param, value in params.items():
            if param in ("type", "timeframe", "source"):
                continue
            if param not in spec:
                notes.append(f"{name}.{param} is not a parameter of {name}; ignored")
                continue
            try:
                spec[param] = clamp(str(spec["type"]), param, value)
            except ValueError as e:
                notes.append(f"{name}.{param}: {e}")

    added: set[str] = set()
    for name, spec in (suggestion.get("new_indicators") or {}).items():
        if (
            not isinstance(spec, dict)
            or name in indicators
            or not re.fullmatch(r"[a-z][a-z0-9_]{0,40}", str(name))
        ):
            notes.append(f"new indicator {name} rejected")
            continue
        type_name = str(spec.get("type", "")).upper()
        reg = indicator_registry.get(type_name)
        if reg is None:
            notes.append(f"new indicator type {type_name} unknown")
            continue
        entry: dict[str, Any] = {"type": type_name}
        for param, value in spec.items():
            if param == "type":
                continue
            if param not in reg.param_schema:
                notes.append(f"{name}.{param} not a {type_name} parameter; ignored")
                continue
            try:
                entry[param] = clamp(type_name, param, value)
            except ValueError as e:
                notes.append(f"{name}.{param}: {e}")
        indicators[name] = entry
        added.add(name)

    def conditions(key: str, side_key: str, old: list[str], extra_allowed: int) -> list[str] | None:
        proposed = suggestion.get(key)
        if not isinstance(proposed, list) or not all(isinstance(c, str) for c in proposed):
            return None
        old_shapes = [_shape(c) for c in old]
        kept: list[str] = []
        extras = 0
        remaining = list(old_shapes)
        for cond in proposed:
            shape = _shape(cond)
            if shape in remaining:
                remaining.remove(shape)
                kept.append(cond)
            elif extras < extra_allowed:
                extras += 1
                kept.append(cond)
            else:
                notes.append(f"{side_key} condition {cond!r} changes the strategy's shape; ignored")
        if remaining:
            # Every original condition must survive (numbers may change): keep the originals.
            notes.append(
                f"{side_key}: an original condition was dropped by the suggestion; kept the old ones"
            )
            return None
        return kept

    entry = new.setdefault("entry_conditions", {})
    revised = conditions("entry_long", "entry", list(entry.get("long") or []), 2)
    if revised is not None:
        entry["long"] = revised
    exits = new.setdefault("exit_conditions", {})
    revised = conditions("exit_long", "exit", list(exits.get("long") or []), 1)
    if revised is not None:
        exits["long"] = revised

    for key in ("stop_loss", "take_profit"):
        value = suggestion.get(key)
        if isinstance(value, dict) and value.get("type"):
            new[key] = {k: v for k, v in value.items() if v is not None}

    # Indicators added but never referenced are dropped again (the DSL rejects unused ones? keep it tidy).
    used = " ".join([*entry.get("long", []), *exits.get("long", [])])
    for name in added:
        if not re.search(rf"\b{re.escape(name)}\b", used):
            indicators.pop(name, None)
            notes.append(f"new indicator {name} unused; dropped")
    return new, notes


def suggest_parameters(
    dsl: dict[str, Any],
    windows: list[dict[str, Any]],
    trades_by_window: dict[str, list[dict[str, Any]]],
    history: list[dict[str, Any]],
    cfg: TunerConfig,
    *,
    chat: Any = None,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """Ask the research LLM for new parameters; returns (new dsl, raw suggestion, notes)."""
    from vibe_quant.dsl.parser import DSLParseError, validate_strategy_dict
    from vibe_quant.research.openai_chat import chat_completion, config_from_env

    results = [
        {
            "window": w["label"],
            "stage": w["stage"],
            "market": w.get("note"),
            "symbols": w["symbols"],
            "dates": f"{w['start']} to {w['end']}",
            "verdict": w.get("verdict"),
            "return": w.get("total_return"),
            "profit_factor": w.get("profit_factor"),
            "max_drawdown": w.get("max_drawdown"),
            "trades": _trade_summary(trades_by_window.get(w["label"], [])),
        }
        for w in windows
    ]
    prompt = _PROMPT.format(
        min_pf=cfg.min_profit_factor,
        dsl=json.dumps({k: v for k, v in dsl.items() if k not in ("description",)}, indent=1),
        ranges=json.dumps(_param_ranges(dsl), indent=1),
        results=json.dumps(results, indent=1, default=str),
        history=json.dumps(history[-6:], indent=1, default=str) if history else "none",
    )
    ask = chat or (lambda p: chat_completion(p, config=config_from_env(), timeout_seconds=300))
    last_error = ""
    for _ in range(2):
        reply = ask(
            prompt + (f"\n\nYour previous answer was rejected: {last_error}" if last_error else "")
        )
        try:
            suggestion = _parse_json_object(reply)
            new_dsl, notes = apply_suggestion(dsl, suggestion)
            validate_strategy_dict(new_dsl)
        except (ValueError, TypeError, DSLParseError) as e:
            last_error = str(e)[:500]
            logger.info("regime tuner: suggestion rejected: %s", last_error)
            continue
        if new_dsl == dsl:
            last_error = "it changed nothing"
            continue
        return new_dsl, suggestion, notes
    raise ValueError(f"no usable parameter suggestion: {last_error}")


def _parse_json_object(text: str) -> dict[str, Any]:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in the reply")
    value = json.loads(text[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("reply is not a JSON object")
    return value


# -- the tuner -------------------------------------------------------------------------------------


class RegimeTuner:
    def __init__(
        self,
        cfg: TunerConfig | None = None,
        client: httpx.Client | None = None,
        *,
        dry_run: bool = False,
        chat: Any = None,
    ) -> None:
        self.cfg = cfg or TunerConfig.load()
        self.state = load_state()
        self.http = client or httpx.Client(base_url=self.cfg.api_url, timeout=120.0)
        self.dry_run = dry_run
        self.chat = chat

    def _api(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self.http.request(method, path, **kwargs)
        if response.status_code >= 400:
            raise RuntimeError(
                f"{method} {path}: HTTP {response.status_code} {response.text[:300]}"
            )
        return response.json() if response.content else None

    def _save(self) -> None:
        if not self.dry_run:
            save_state(self.state)

    # -- candidates --------------------------------------------------------------------------------

    def strategies(self) -> list[dict[str, Any]]:
        data = self._api("GET", "/api/strategies")
        rows = data.get("strategies", data) if isinstance(data, dict) else data
        return [r for r in rows if r.get("is_active", True)]

    def next_candidate(self, only: int | None = None) -> dict[str, Any] | None:
        candidates = self.state.setdefault("candidates", {})
        strategies = {int(s["id"]): s for s in self.strategies()}
        order: list[int] = []
        if only is not None:
            order = [only]
        else:
            order += [int(i) for i in self.cfg.candidate_ids]
            order += sorted(strategies, reverse=True)
        for sid in order:
            s = strategies.get(sid)
            if s is None or s.get("strategy_type") == TUNED_TYPE:
                continue
            tf = (s.get("dsl_config") or {}).get("timeframe")
            if tf not in self.cfg.timeframes:
                continue
            entry: dict[str, Any] | None = candidates.get(str(sid))
            if entry is None:
                entry = candidates[str(sid)] = {
                    "root_strategy_id": sid,
                    "root_name": s["name"],
                    "timeframe": tf,
                    "symbols": self.home_symbols(sid),
                    "status": "testing",
                    "stage": "bull",
                    "current_strategy_id": sid,
                    "round": 0,
                    "attempts": [],
                    "created_at": _now(),
                }
            if entry["status"] in ("testing", "tuning"):
                return entry
        return None

    def home_symbols(self, strategy_id: int) -> list[str]:
        """The stock symbols of the strategy's first stock run (its own market), else the defaults."""
        from vibe_quant.data.catalog import is_equity_symbol

        try:
            data = self._api(
                "GET", "/api/results/runs/summary", params={"strategy_id": strategy_id}
            )
        except RuntimeError:
            return list(self.cfg.default_symbols)
        for run in sorted(data.get("runs", []), key=lambda r: int(r.get("run_id", 0))):
            symbols = [str(s) for s in (run.get("symbols") or [])]
            if symbols and all(is_equity_symbol(s) for s in symbols):
                return symbols
        return list(self.cfg.default_symbols)

    # -- one attempt -------------------------------------------------------------------------------

    def run_window(
        self, strategy_id: int, timeframe: str, window: TunerWindow, symbols: list[str]
    ) -> dict[str, Any]:
        row: dict[str, Any] = {
            "label": window.label,
            "stage": window.stage,
            "start": window.start,
            "end": window.end,
            "symbols": symbols,
            "note": window.note,
            "run_id": None,
            "verdict": None,
        }
        if self.dry_run:
            logger.info(
                "regime tuner (dry run): would validate %s on %s %s",
                strategy_id,
                window.label,
                symbols,
            )
            return row
        body = {
            "strategy_id": strategy_id,
            "symbols": symbols,
            "timeframe": timeframe,
            "start_date": window.start,
            "end_date": window.end,
            "parameters": {"initial_balance": self.cfg.initial_balance},
        }
        for _ in range(120):
            try:
                launched = self._api("POST", "/api/backtest/validation", json=body)
                break
            except RuntimeError as e:
                if "409" in str(e) or "already" in str(e):
                    time.sleep(10)
                    continue
                row.update(verdict="error", error=str(e)[:300])
                return row
        else:
            row.update(verdict="error", error="backend busy")
            return row
        run_id = int(launched.get("run_id") or launched.get("id"))
        row["run_id"] = run_id
        deadline = time.monotonic() + self.cfg.run_timeout_minutes * 60
        status = "running"
        while time.monotonic() < deadline:
            status = str(self._api("GET", f"/api/backtest/jobs/{run_id}").get("status"))
            if status not in ("running", "pending", "queued"):
                break
            time.sleep(5)
        if status != "completed":
            meta = self._api("GET", f"/api/results/runs/{run_id}/meta")
            error = str(meta.get("error_message") or f"run {run_id} ended {status}")
            # Validation fails a run that never traded; with bars present that is a flat window
            # (no entry = stay out), not an error.
            if "0 trades" in error and self._has_data(symbols, timeframe, window):
                row.update(
                    verdict="flat",
                    trades=0,
                    total_return=0.0,
                    profit_factor=None,
                    max_drawdown=0.0,
                    run_note="no entries: stayed flat",
                )
            else:
                row.update(verdict="error", error=error[:300])
            return row
        result = self._api("GET", f"/api/results/runs/{run_id}")
        row.update(
            total_return=result.get("total_return"),
            profit_factor=result.get("profit_factor"),
            trades=int(result.get("total_trades") or 0),
            max_drawdown=result.get("max_drawdown"),
            win_rate=result.get("win_rate"),
        )
        row["verdict"] = window_verdict(result, self.cfg)
        return row

    def _has_data(self, symbols: list[str], timeframe: str, window: TunerWindow) -> bool:
        try:
            check = self._api(
                "POST",
                "/api/backtest/validate-coverage",
                json={
                    "symbols": symbols,
                    "timeframe": timeframe,
                    "start_date": window.start,
                    "end_date": window.end,
                },
            )
        except RuntimeError:
            return False
        coverage = check.get("coverage") or {}
        return bool(coverage) and all(
            isinstance(c, dict) and c.get("has_data") and int(c.get("bars") or 0) > 0
            for c in coverage.values()
        )

    def step(self, only: int | None = None) -> dict[str, Any] | None:
        """Run one attempt of one candidate (all windows of its current stage) and decide."""
        cand = self.next_candidate(only)
        if cand is None:
            logger.info("regime tuner: nothing to tune")
            return None
        if (
            cand["attempts"]
            and not cand["attempts"][-1].get("finished_at")
            and "passed" not in cand["attempts"][-1]
        ):
            # An attempt cut short (the tuner stopped mid-stage) is run again from the start.
            dropped = cand["attempts"].pop()
            logger.info(
                "regime tuner: %s: rerunning unfinished attempt %d", cand["root_name"], dropped["n"]
            )
        sid = int(cand["current_strategy_id"])
        stage = cand["stage"]
        strategy = self._api("GET", f"/api/strategies/{sid}")
        dsl = dict(strategy["dsl_config"])
        self.state["current"] = {
            "root_strategy_id": cand["root_strategy_id"],
            "strategy_id": sid,
            "stage": stage,
            "since": _now(),
        }
        self._save()
        attempt: dict[str, Any] = {
            "n": len(cand["attempts"]) + 1,
            "strategy_id": sid,
            "strategy_name": strategy["name"],
            "stage": stage,
            "dsl": dsl,
            "windows": [],
            "started_at": _now(),
        }
        cand["attempts"].append(attempt)
        logger.info(
            "regime tuner: %s attempt %d, strategy %s, stage %s",
            cand["root_name"],
            attempt["n"],
            sid,
            stage,
        )
        trades_by_window: dict[str, list[dict[str, Any]]] = {}
        for window in self.cfg.stage_windows(stage):
            symbols = window.symbols or list(cand["symbols"])
            row = self.run_window(sid, cand["timeframe"], window, symbols)
            attempt["windows"].append(row)
            if row.get("run_id") and row.get("trades"):
                try:
                    trades_by_window[window.label] = self._api(
                        "GET", f"/api/results/runs/{row['run_id']}/trades"
                    )
                except RuntimeError:
                    trades_by_window[window.label] = []
            logger.info(
                "regime tuner: %s %s %s -> %s (return=%s pf=%s trades=%s)",
                cand["root_name"],
                window.label,
                symbols,
                row.get("verdict"),
                row.get("total_return"),
                row.get("profit_factor"),
                row.get("trades"),
            )
            self._save()
        if self.dry_run:
            return cand
        passed, reason = stage_passed(stage, attempt["windows"], self.cfg)
        attempt.update(passed=passed, reason=reason, finished_at=_now())
        if passed:
            index = STAGES.index(stage)
            if index + 1 < len(STAGES):
                cand.update(stage=STAGES[index + 1], status="testing")
                logger.info(
                    "regime tuner: %s passed %s -> widening to %s",
                    cand["root_name"],
                    stage,
                    cand["stage"],
                )
            else:
                self._settle(cand)
        else:
            self._retune(cand, dsl, attempt, trades_by_window)
        self.state["current"] = None
        self._save()
        return cand

    def _retune(
        self,
        cand: dict[str, Any],
        dsl: dict[str, Any],
        attempt: dict[str, Any],
        trades_by_window: dict[str, list[dict[str, Any]]],
    ) -> None:
        if cand["round"] >= self.cfg.max_rounds:
            cand.update(
                status="exhausted",
                finished_at=_now(),
                verdict=f"no parameters passed after {self.cfg.max_rounds} AI rounds ({attempt['reason']})",
            )
            logger.info("regime tuner: %s exhausted", cand["root_name"])
            return
        history = [
            {
                "attempt": a["n"],
                "stage": a["stage"],
                "reason": a.get("reason"),
                "changes": (a.get("ai") or {}).get("suggestion"),
            }
            for a in cand["attempts"][:-1]
        ]
        try:
            new_dsl, suggestion, notes = suggest_parameters(
                dsl,
                attempt["windows"],
                trades_by_window,
                history,
                self.cfg,
                chat=self.chat,
            )
        except Exception as e:  # noqa: BLE001 — the AI being down never kills the tuner
            attempt["ai"] = {"error": str(e)[:300]}
            cand.update(status="tuning")
            logger.warning("regime tuner: %s: AI suggestion failed: %s", cand["root_name"], e)
            return
        cand["round"] += 1
        taken = {str(s["name"]) for s in self.strategies()}
        name = f"{cand['root_name']}_t{cand['round']}"
        suffix = 2
        while name in taken:
            name = f"{cand['root_name']}_t{cand['round']}_{suffix}"
            suffix += 1
        new_dsl["name"] = name
        created = self._api(
            "POST",
            "/api/strategies",
            json={
                "name": name,
                "dsl_config": new_dsl,
                "description": f"Regime tuner round {cand['round']} of {cand['root_name']} "
                f"(strategy {cand['root_strategy_id']}): {suggestion.get('reasoning', '')}"[:1000],
                "strategy_type": TUNED_TYPE,
            },
        )
        attempt["ai"] = {
            "reasoning": suggestion.get("reasoning"),
            "suggestion": suggestion,
            "notes": notes,
            "new_strategy_id": int(created["id"]),
        }
        # A new version starts over from the bull stage: it must still make money there.
        cand.update(current_strategy_id=int(created["id"]), stage="bull", status="tuning")
        logger.info(
            "regime tuner: %s round %d -> strategy %s (%s)",
            cand["root_name"],
            cand["round"],
            created["id"],
            suggestion.get("reasoning"),
        )

    def _settle(self, cand: dict[str, Any]) -> None:
        trades = sum(
            int(w.get("trades") or 0)
            for a in cand["attempts"]
            if a["strategy_id"] == cand["current_strategy_id"]
            for w in a["windows"]
        )
        if trades < self.cfg.min_total_trades:
            cand.update(
                status="exhausted",
                finished_at=_now(),
                verdict=f"passed every stage but only {trades} trades (need {self.cfg.min_total_trades})",
            )
            return
        bull = next(
            (
                w
                for a in reversed(cand["attempts"])
                if a["strategy_id"] == cand["current_strategy_id"] and a["stage"] == "bull"
                for w in a["windows"]
                if w.get("run_id")
            ),
            None,
        )
        cand.update(
            status="settled",
            settled_at=_now(),
            verdict=f"profitable in bull and bear markets ({trades} trades)",
        )
        logger.info(
            "regime tuner: %s SETTLED as strategy %s",
            cand["root_name"],
            cand["current_strategy_id"],
        )
        if not self.cfg.auto_paper or bull is None:
            return
        try:
            status = self._api("GET", "/api/paper/status")
            if status.get("state") not in ("idle", None):
                cand["paper"] = {"state": "waiting", "note": "another paper session is running"}
                return
            started = self._api(
                "POST",
                "/api/paper/start",
                json={
                    "validation_run_id": int(bull["run_id"]),
                    "testnet": True,
                    "max_position_pct": self.cfg.paper_max_position_pct,
                    "max_position_count": self.cfg.paper_max_position_count,
                },
            )
            cand["paper"] = {
                "state": "started",
                "validation_run_id": int(bull["run_id"]),
                "started_at": _now(),
                "response": started,
            }
        except RuntimeError as e:
            cand["paper"] = {"state": "failed", "error": str(e)[:300]}

    def start_waiting_papers(self) -> None:
        for cand in self.state.get("candidates", {}).values():
            if (
                cand.get("status") == "settled"
                and (cand.get("paper") or {}).get("state") == "waiting"
            ):
                cand.pop("paper", None)
                self._settle(cand)
                self._save()
                return

    def run_forever(self) -> None:
        while True:
            started = time.monotonic()
            try:
                self.start_waiting_papers()
                did = self.step()
            except Exception:  # noqa: BLE001 — keep the tuner alive
                logger.exception("regime tuner: step failed")
                did = None
            if did is None:
                elapsed = time.monotonic() - started
                time.sleep(max(60.0, self.cfg.interval_minutes * 60 - elapsed))
