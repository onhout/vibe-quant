"""H-13 leverage-flush reversal evaluator (PRE-REGISTRATION H-13, docs/discovery-journal.md).

Conventions frozen in scripts/research/h13/SPEC_IMPL.md. Usage: h13_flush_reversal.py OUT_JSON
"""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from vibe_quant.data.catalog import CatalogManager
from vibe_quant.dsl.aux_data import settlement_series

SYMS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT")
METRICS_LOCAL = Path(__file__).resolve().parents[3] / "data/swarm/20261012-nextsteps/metrics"
METRICS_MAIN = Path("/Users/verebelyin/projects/vibe-quant/data/swarm/20261012-nextsteps/metrics")
METRICS_DIR = METRICS_LOCAL if METRICS_LOCAL.is_dir() else METRICS_MAIN

DAY_NS = 86_400_000_000_000
NS_2300 = 23 * 3600 * 1_000_000_000
NS_2355 = (23 * 3600 + 55 * 60) * 1_000_000_000
CLOSE_TAIL_NS = DAY_NS - 1_000_000
HOLD = 3
WEIGHT = 0.25
COST = 0.0005
N_DRAWS = 20_000
SEED = 13
B_START_STR = "2022-06-01"
B_END_STR = "2026-09-30"


def day_key(value: str) -> int:
    return int(np.datetime64(value, "ns").astype("int64")) // DAY_NS


def day_str(day: int) -> str:
    return str(np.datetime64(day * DAY_NS, "ns").astype("datetime64[D]"))


def load_oi(sym: str) -> pd.Series:
    frame = pd.read_parquet(METRICS_DIR / f"{sym}.parquet", columns=["ts", "sum_open_interest"])
    ts = frame["ts"].astype("int64").to_numpy()
    tod = ts % DAY_NS
    keep = (tod >= NS_2300) & (tod <= NS_2355)
    series = pd.Series(
        frame["sum_open_interest"].to_numpy(dtype="float64")[keep], index=ts[keep] // DAY_NS
    )
    series = series[~series.index.duplicated(keep="last")]
    return series.where(series > 0.0)


def load_closes(sym: str) -> pd.Series:
    days: list[int] = []
    closes: list[float] = []
    for bar in CatalogManager().get_bars(sym, "1d"):
        ts_init = int(bar.ts_init)
        if ts_init % DAY_NS not in (CLOSE_TAIL_NS, 0):
            continue
        days.append(ts_init // DAY_NS)
        closes.append(float(bar.close))
    series = pd.Series(closes, index=days, dtype="float64")
    return series[~series.index.duplicated(keep="last")]


def build():
    closes = {sym: load_closes(sym) for sym in SYMS}
    open_interest = {sym: load_oi(sym) for sym in SYMS}
    d0 = min(int(s.index.min()) for s in open_interest.values())
    d1 = max(int(s.index.max()) for s in closes.values())
    days = np.arange(d0, d1 + 1, dtype=np.int64)
    close = np.full((len(SYMS), days.size), np.nan)
    doilog = np.full((len(SYMS), days.size), np.nan)
    ret1d = np.full((len(SYMS), days.size), np.nan)
    withdata = np.zeros((len(SYMS), days.size), dtype=bool)
    for i, sym in enumerate(SYMS):
        c = closes[sym].reindex(days).to_numpy(dtype="float64")
        o = open_interest[sym].reindex(days).to_numpy(dtype="float64")
        c_prev = np.concatenate(([np.nan], c[:-1]))
        o_prev = np.concatenate(([np.nan], o[:-1]))
        close[i] = c
        doilog[i] = np.log(o / o_prev)
        ret1d[i] = c / c_prev - 1.0
        withdata[i] = ~np.isnan(doilog[i]) & ~np.isnan(c) & ~np.isnan(c_prev)
    count = withdata.sum(axis=0)
    valid = count >= 3
    D = np.nansum(np.where(withdata, doilog, np.nan), axis=0) / np.maximum(count, 1)
    R = np.nansum(np.where(withdata, ret1d, np.nan), axis=0) / np.maximum(count, 1)
    D = np.where(valid, D, np.nan)
    R = np.where(valid, R, np.nan)
    pct10 = np.full(days.size, np.nan)
    for p in range(days.size):
        window = D[max(0, p - 365) : p]
        window = window[~np.isnan(window)]
        if window.size >= 180:
            pct10[p] = np.percentile(window, 10, method="linear")
    flush = (pct10 >= D) & (R < 0.0) & ~np.isnan(pct10)
    close_ff = pd.DataFrame(close).ffill(axis=1).to_numpy()
    fund = {sym: settlement_series(sym) for sym in SYMS}
    return {
        "days": days,
        "close": close,
        "close_ff": close_ff,
        "withdata": withdata,
        "D": D,
        "R": R,
        "pct10": pct10,
        "flush": flush,
        "fund": fund,
        "d0": d0,
    }


def find_entries(data):
    days, flush, withdata, close = data["days"], data["flush"], data["withdata"], data["close"]
    entries: list[tuple[int, np.ndarray]] = []
    skipped = 0
    last = None
    for p in range(days.size):
        if not flush[p]:
            continue
        t = int(days[p])
        if last is not None and t - last < HOLD:
            continue
        coins = np.flatnonzero(withdata[:, p])
        if p + HOLD >= days.size or np.isnan(close[coins, p + HOLD]).any():
            skipped += 1
            continue
        last = t
        entries.append((t, coins))
    return entries, skipped


def funding_window(data, coins, q, a, b) -> float:
    """Funding over settlements s with T_a < s <= T_b, T_x = (x + 1) * DAY_NS.

    mark_i(s) = close_i of day s // DAY_NS - 1 (the last daily close at or before s).
    """
    close, d0, fund = data["close_ff"], data["d0"], data["fund"]
    lo, hi = (a + 1) * DAY_NS, (b + 1) * DAY_NS
    total = 0.0
    for j, i in enumerate(coins):
        times, rates = fund[SYMS[i]]
        mask = (times > lo) & (times <= hi)
        marks = close[i][times[mask] // DAY_NS - 1 - d0]
        total += float(q[j] * np.sum(marks * rates[mask]))
    return total


def event_stats(data, t: int, coins: np.ndarray):
    close, d0 = data["close_ff"], data["d0"]
    p0 = t - d0
    c0 = close[coins, p0]
    c3 = close[coins, p0 + HOLD]
    q = WEIGHT / c0
    gross = float(np.sum(WEIGHT * (c3 / c0 - 1.0)))
    cost = COST * (WEIGHT * coins.size + float(np.sum(WEIGHT * c3 / c0)))
    funding = funding_window(data, coins, q, t, t + HOLD)
    return {"gross": gross, "cost": cost, "funding": funding, "net": gross - cost - funding}


def daily_series(data, entries) -> np.ndarray:
    close, d0 = data["close_ff"], data["d0"]
    b_start, b_end = day_key(B_START_STR), day_key(B_END_STR)
    by_entry = {t: (coins, WEIGHT / close[coins, t - d0]) for t, coins in entries}
    out = np.zeros(b_end - b_start + 1)
    for j, d in enumerate(range(b_start, b_end + 1)):
        entry = None
        for k in (1, 2, 3):
            if d - k in by_entry:
                entry = by_entry[d - k]
                t = d - k
                break
        if entry is None:
            continue
        coins, q = entry
        v_prev = float(np.sum(q * close[coins, d - 1 - d0]))
        v_cur = float(np.sum(q * close[coins, d - d0]))
        cost = 0.0
        if d - 1 == t:
            cost += COST * float(np.sum(q * close[coins, t - d0]))
        if d == t + HOLD:
            cost += COST * float(np.sum(q * close[coins, t + HOLD - d0]))
        funding = funding_window(data, coins, q, d - 1, d)
        out[j] = (v_cur - v_prev - cost - funding) / v_prev
    return out


def null_p(observed: float, nets, n_events: int, rng, draws: int):
    if not nets or n_events == 0:
        return float("nan"), 0
    days = np.array(sorted(nets), dtype=np.int64)
    vals = np.array([nets[int(t)] for t in days])
    lo = int(days[0]) - 3
    blocked_size = int(days[-1]) - lo + 4
    offsets = days - lo
    hits = 0
    short = 0
    for _ in range(draws):
        order = rng.permutation(offsets.size)
        blocked = np.zeros(blocked_size, dtype=bool)
        total, taken = 0.0, 0
        for pi in order:
            idx = offsets[pi]
            if blocked[idx]:
                continue
            blocked[idx - 2 : idx + 3] = True
            total += vals[pi]
            taken += 1
            if taken == n_events:
                break
        if taken < n_events:
            short += 1
        if taken and total / taken >= observed:
            hits += 1
    return (1.0 + hits) / (draws + 1.0), short


def main() -> None:
    out_path = Path(sys.argv[1])
    data = build()
    days = data["days"]
    b_start, b_end = day_key(B_START_STR), day_key(B_END_STR)
    b_mask = (days >= b_start) & (days <= b_end)

    entries, skipped = find_entries(data)
    b_entries = [(t, coins) for t, coins in entries if b_start <= t <= b_end]
    events = [
        {
            "t": day_str(t),
            "t_day": t,
            "coins": [SYMS[i] for i in coins],
            **event_stats(data, t, coins),
        }
        for t, coins in b_entries
    ]
    nets = np.array([event["net"] for event in events])
    n_b = int(nets.size)
    mean_net = float(nets.mean())

    daily = daily_series(data, entries)
    sharpe = float(daily.mean() / daily.std(ddof=1) * np.sqrt(365.0))

    signal = ~np.isnan(data["D"]) & ~np.isnan(data["R"]) & ~np.isnan(data["pct10"])
    first_signal = int(days[np.argmax(signal)]) if signal.any() else None
    first_pct10 = int(days[np.argmax(~np.isnan(data["pct10"]))])
    print(f"signal days (all): {int(signal.sum())}, first signal day: {day_str(first_signal)}")
    print(f"first day with pct10 defined: {day_str(first_pct10)}")
    print(f"flush days in B: {int(np.sum(data['flush'] & b_mask))}")
    print(f"signal days in B: {int(np.sum(signal & b_mask))}")
    print(f"entries in B: {n_b}, events skipped: {skipped}")

    withdata, R, close = data["withdata"], data["R"], data["close"]
    candidates = {"primary": {}, "secondary": {}}
    for p in range(days.size):
        t = int(days[p])
        if not (b_start <= t <= b_end) or np.isnan(R[p]):
            continue
        coins = np.flatnonzero(withdata[:, p])
        if p + HOLD >= days.size or np.isnan(close[coins, p + HOLD]).any():
            continue
        net = event_stats(data, t, coins)["net"]
        candidates["secondary"][t] = net
        if R[p] < 0.0:
            candidates["primary"][t] = net
    print(
        f"null candidates: primary {len(candidates['primary'])}, "
        f"secondary {len(candidates['secondary'])}"
    )

    rng = np.random.default_rng(SEED)
    p_primary, short_primary = null_p(mean_net, candidates["primary"], n_b, rng, N_DRAWS)
    p_secondary, short_secondary = null_p(mean_net, candidates["secondary"], n_b, rng, N_DRAWS)

    conditions = {
        "1_n_B_ge_40": n_b >= 40,
        "2_mean_net_B_ge_0.005": mean_net >= 0.005,
        "3_p_primary_le_0.05": bool(p_primary <= 0.05),
        "4_sharpe_B_ge_0.5": sharpe >= 0.5,
    }
    report = {
        "n_B": n_b,
        "mean_net_B": mean_net,
        "p_primary": p_primary,
        "p_secondary": p_secondary,
        "sharpe_B": sharpe,
        "events_skipped": skipped,
        "coverage": {
            "n_days": int(days.size),
            "first_day": day_str(int(days[0])),
            "last_day": day_str(int(days[-1])),
            "n_signal_days": int(signal.sum()),
            "first_signal_day": day_str(first_signal),
            "first_pct10_day": day_str(first_pct10),
            "n_signal_days_B": int(np.sum(signal & b_mask)),
            "n_flush_days_B": int(np.sum(data["flush"] & b_mask)),
            "n_candidates_primary": len(candidates["primary"]),
            "n_candidates_secondary": len(candidates["secondary"]),
            "n_draws_short_primary": short_primary,
            "n_draws_short_secondary": short_secondary,
        },
        "events": events,
        "conditions": conditions,
        "verdict": "PASS" if all(conditions.values()) else "REJECT",
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2) + "\n")
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
