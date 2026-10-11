"""API routers and discovery modules never touch StateManager's shared connection.

``StateManager`` shares ONE ``sqlite3.Connection`` across the FastAPI threadpool and
worker threads; only the class's own methods hold ``_write_lock`` around it. A
router doing ``state.conn.execute(...)`` bypasses the lock and can interleave with
a writer (``sqlite3.InterfaceError: bad parameter or other API misuse``). Callers
must go through a locked StateManager method instead.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

import vibe_quant
from vibe_quant.db.state_manager import StateManager

_PKG = Path(vibe_quant.__file__).parent
_SCANNED = (_PKG / "api" / "routers", _PKG / "discovery")
#: Objects with their OWN sqlite connection (not a StateManager): RawDataArchive.
_OTHER_CONN_OWNERS = {"archive"}


def _conn_accesses(path: Path) -> list[int]:
    offenders: list[int] = []
    for node in ast.walk(ast.parse(path.read_text())):
        if (
            isinstance(node, ast.Attribute)
            and node.attr in {"conn", "_conn"}
            and not (isinstance(node.value, ast.Name) and node.value.id in _OTHER_CONN_OWNERS)
        ):
            offenders.append(node.lineno)
    return sorted(offenders)


def test_routers_and_discovery_never_touch_the_shared_connection() -> None:
    offenders = {
        str(p.relative_to(_PKG)): lines
        for root in _SCANNED
        for p in sorted(root.rglob("*.py"))
        if (lines := _conn_accesses(p))
    }
    assert offenders == {}


# --- New locked StateManager methods == the old inline queries ---------------


@pytest.fixture
def state(tmp_path: Path) -> StateManager:
    mgr = StateManager(tmp_path / "state.db")
    # Force schema creation.
    _ = mgr.conn
    return mgr


def _seed_run(
    state: StateManager,
    strategy_id: int,
    *,
    mode: str = "discovery",
    status: str = "completed",
    notes: str | None = None,
    timeframe: str = "4h",
    params: dict[str, object] | None = None,
) -> int:
    run_id = state.create_backtest_run(
        strategy_id=strategy_id,
        run_mode=mode,
        symbols=["BTCUSDT"],
        timeframe=timeframe,
        start_date="2024-01-01",
        end_date="2024-06-01",
        parameters=params or {},
    )
    state.update_backtest_run_status(run_id, status)
    if notes is not None:
        state.conn.execute(
            "INSERT INTO backtest_results (run_id, notes) VALUES (?, ?)", (run_id, notes)
        )
        state.conn.commit()
    return run_id


def test_get_database_file_matches_pragma(state: StateManager, tmp_path: Path) -> None:
    row = state.conn.execute("PRAGMA database_list").fetchone()
    assert state.get_database_file() == row["file"] != ""
    assert Path(state.get_database_file()).samefile(tmp_path / "state.db")


def test_table_names_and_counts_match_inline(state: StateManager) -> None:
    inline = [r[0] for r in state.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    assert state.list_table_names() == inline
    state.create_strategy("s1", {"name": "s1"})
    state.create_strategy("s2", {"name": "s2"})
    counts = state.count_table_rows(frozenset({"strategies", "trades", "no_such_table"}))
    assert counts["strategies"] == 2
    assert counts["trades"] == 0
    assert "no_such_table" not in counts
    assert list(counts) == [t for t in inline if t in {"strategies", "trades"}]


def test_recent_discovery_result_notes_matches_inline(state: StateManager) -> None:
    sid = state.create_strategy("s", {"name": "s"})
    for i in range(12):
        _seed_run(state, sid, notes=json.dumps({"i": i}))
    _seed_run(state, sid, notes="")  # empty -> excluded
    _seed_run(state, sid, status="failed", notes=json.dumps({"i": 99}))  # not completed
    _seed_run(state, sid, mode="screening", notes=json.dumps({"i": 98}))  # not discovery
    inline = [
        r[0]
        for r in state.conn.execute(
            """SELECT br.notes FROM backtest_runs r
               JOIN backtest_results br ON br.run_id = r.id
               WHERE r.run_mode='discovery' AND r.status='completed'
                 AND br.notes IS NOT NULL AND br.notes != ''
               ORDER BY r.id DESC LIMIT 10"""
        ).fetchall()
    ]
    got = state.list_recent_discovery_result_notes(limit=10)
    assert got == inline
    assert len(got) == 10
    assert json.loads(got[0]) == {"i": 11}


def test_active_strategy_dsl_rows_and_name_lookups_match_inline(state: StateManager) -> None:
    a = state.create_strategy("a", {"name": "a"})
    b = state.create_strategy("b", {"name": "b"})
    state.create_strategy("c", {"name": "c"})
    state.conn.execute("UPDATE strategies SET is_active = 0 WHERE name = 'c'")
    state.conn.execute("UPDATE strategies SET is_active = NULL WHERE id = ?", (b,))
    state.conn.commit()
    inline = [
        (r[0], r[1], r[2])
        for r in state.conn.execute(
            "SELECT id, name, dsl_config FROM strategies "
            "WHERE is_active IS NULL OR is_active = 1 ORDER BY id"
        ).fetchall()
    ]
    assert state.list_active_strategy_dsl_rows() == inline
    assert [r[0] for r in inline] == [a, b]
    assert state.strategy_name_exists("a") is True
    assert state.strategy_name_exists("c") is True  # inactive still holds the UNIQUE name
    assert state.strategy_name_exists("zzz") is False
    assert state.get_strategy_id_by_name("b") == b
    assert state.get_strategy_id_by_name("zzz") is None


def test_latest_extraction_job_for_item_picks_highest_id(state: StateManager) -> None:
    assert state.get_latest_extraction_job_for_item(1) is None
    for item_id in (1, 1, 2):
        state.conn.execute(
            "INSERT INTO research_items (id, source, external_id, url) "
            "VALUES (?, 'reddit', ?, ?) ON CONFLICT DO NOTHING",
            (item_id, f"e{item_id}", f"u{item_id}"),
        )
        state.conn.execute(
            "INSERT INTO research_extraction_jobs (research_item_id, status) VALUES (?, 'queued')",
            (item_id,),
        )
    state.conn.commit()
    inline = state.conn.execute(
        "SELECT * FROM research_extraction_jobs WHERE research_item_id = ? "
        "ORDER BY id DESC LIMIT 1",
        (1,),
    ).fetchone()
    got = state.get_latest_extraction_job_for_item(1)
    assert got == dict(inline)
    assert got is not None and got["id"] == 2


def test_campaign_queries_match_inline(state: StateManager) -> None:
    sid = state.create_strategy("s", {"name": "s"})
    disc = _seed_run(state, sid, notes=json.dumps({"top_strategies": [1]}))
    run = _seed_run(state, sid, mode="screening", status="running")
    assert state.get_result_notes_raw(disc) == json.dumps({"top_strategies": [1]})
    assert state.get_result_notes_raw(run) is None  # no result row
    assert state.get_result_notes_raw(99999) is None
    assert state.get_backtest_run_status(run) == "running"
    assert state.get_backtest_run_status(disc) == "completed"
    assert state.get_backtest_run_status(99999) is None

    oos_ids = [
        _seed_run(
            state, sid, mode="oos", notes=None, params={"campaign_id": "c1", "campaign_name": "n"}
        )
        for _ in range(2)
    ]
    _seed_run(state, sid, mode="oos", params={"campaign_id": "other"})
    state.conn.execute(
        "INSERT INTO backtest_results (run_id, sharpe_ratio, total_return, max_drawdown, total_trades)"
        " VALUES (?, 1.5, 0.2, 0.1, 7)",
        (oos_ids[0],),
    )
    state.conn.commit()
    sql = """
        SELECT br.id, br.parameters, r.sharpe_ratio, r.total_return,
               r.max_drawdown, r.total_trades
        FROM backtest_runs br
        LEFT JOIN backtest_results r ON r.run_id = br.id
        WHERE br.run_mode = ?
          AND json_extract(br.parameters, '$.campaign_id') = ?
        ORDER BY br.id
        """
    inline = [tuple(r) for r in state.conn.execute(sql, ("oos", "c1")).fetchall()]
    got = state.list_campaign_oos_rows("oos", "c1")
    assert got == inline
    assert [r[0] for r in got] == oos_ids
    assert got[0][2:] == (1.5, 0.2, 0.1, 7)
    assert got[1][2:] == (None, None, None, None)


def test_prior_discovery_trial_rows_match_inline(state: StateManager) -> None:
    sid = state.create_strategy("s", {"name": "s"})
    ids = [_seed_run(state, sid, notes=json.dumps({"evaluated": i})) for i in range(3)]
    _seed_run(state, sid, notes=json.dumps({"evaluated": 5}), timeframe="1h")
    _seed_run(state, sid, mode="screening", notes=json.dumps({"evaluated": 6}))
    current = _seed_run(state, sid, notes=json.dumps({"evaluated": 7}))
    inline = [
        tuple(r)
        for r in state.conn.execute(
            "SELECT b.id, r.notes, b.start_date, b.end_date "
            "FROM backtest_results r JOIN backtest_runs b ON b.id = r.run_id "
            "WHERE b.run_mode = ? AND b.id < ? AND b.timeframe = ?",
            ("discovery", current, "4h"),
        ).fetchall()
    ]
    got = state.list_prior_discovery_notes(current, "4h")
    assert got == inline
    assert [r[0] for r in got] == ids
