"""Third exactness proof: the funding-carry engine == the no-NT reference (T6b).

Runs the pinned 4-coin carry book (BTCUSDT/ETHUSDT/SOLUSDT/BNBUSDT,
2022-06-01..2026-09-28, E0 10,000, divisor 3, gross 1.0, band 0.10) on the real
catalog and funding archive, then

1. compares the NT engine run, the reference run and both metric reports field by
   field with ``parity.compare_runs`` -- exact equality, no tolerance (R30); and
2. asserts the engine's recorded headline numbers (the T6b pins).

Exit 0 = IDENTICAL, 1 = DRIFT. Data paths resolve from this script's repo root,
so the proof runs from any cwd.

Run from the tree under test (in a worktree: PYTHONPATH=$PWD .venv/bin/python ...):
    .venv/bin/python scripts/agents/exactness_carry.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from vibe_quant.portfolio.metrics import carry_metrics
from vibe_quant.portfolio.parity import compare_runs
from vibe_quant.portfolio.reference import simulate_reference
from vibe_quant.portfolio.runner import run_carry_engine
from vibe_quant.portfolio.spec import CarrySpec
from vibe_quant.portfolio.ticks import load_carry_inputs

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT")
START = "2022-06-01"
END = "2026-09-28"

# Date pins (R20, MEASURED): 2022-06-01 00:00 UTC .. 2026-09-28 00:00 UTC.
EXPECTED_START_NS = 1654041600000000000
EXPECTED_END_NS = 1790553600000000000
EXPECTED_DECISIONS = 225

# Headline numbers of the engine run, recorded verbatim from the first passing
# run (2026-10-15; MEASURED runtime 4.76 s real, and engine == reference with 0
# mismatches over 413 fills, 9,487 payments, 1,580 daily and 225 weekly returns).
RECORDED: dict[str, object] = {
    "weekly_sharpe": 0.74651476705443,
    "total_return": 1.2884394452088221,
    "max_drawdown_daily": 0.3171670714370357,
    "n_fills": 413,
    "n_decisions": EXPECTED_DECISIONS,
    "funding_paid_total": -5670.5484016181945,
    "fees_total": 1743.22191953,
    "funding_fallback_settlements": 0,
}

REPO_ROOT = Path(__file__).resolve().parents[2]
CATALOG_PATH = REPO_ROOT / "data/catalog"
ARCHIVE_DB_PATH = REPO_ROOT / "data/archive/raw_data.db"


def main() -> int:
    spec = CarrySpec.from_dates(SYMBOLS, START, END)
    inputs = load_carry_inputs(spec, CATALOG_PATH, ARCHIVE_DB_PATH)
    engine = run_carry_engine(inputs)
    reference = simulate_reference(inputs)
    report = carry_metrics(engine, inputs)
    mismatches = compare_runs(engine, reference, report, carry_metrics(reference, inputs))

    print(
        f"parity: engine==reference, {len(mismatches)} mismatches over "
        f"{len(engine.fills)} fills, {len(engine.payments)} payments, "
        f"{len(report.daily)} daily and {len(report.weekly)} weekly returns"
    )
    for message in mismatches[:10]:
        print(f"  {message}")

    checks: dict[str, tuple[object, object]] = {
        "spec.start_ns": (spec.start_ns, EXPECTED_START_NS),
        "spec.end_ns": (spec.end_ns, EXPECTED_END_NS),
        "decisions": (len(inputs.decisions), EXPECTED_DECISIONS),
        "compare_runs": (mismatches, []),
        **{
            f"summary.{name}": (report.summary[name], expected)
            for name, expected in RECORDED.items()
        },
    }
    ok = all(actual == expected for actual, expected in checks.values())
    print(f"carry exactness: {'IDENTICAL' if ok else 'DRIFT'}")
    for name, (actual, expected) in checks.items():
        note = "" if actual == expected else f"  (expected {expected!r})"
        print(f"  {name} = {actual!r}{note}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
