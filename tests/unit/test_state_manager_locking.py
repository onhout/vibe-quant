"""Locking invariants for StateManager's shared sqlite connection.

``StateManager`` opens ONE ``sqlite3.Connection`` (``check_same_thread=False``)
that is shared by the FastAPI threadpool, the extraction worker/drain threads
and the scrape subprocesses. Every read therefore races every write on the same
cursor state; an interleaved read surfaces as
``sqlite3.InterfaceError: bad parameter or other API misuse`` and then wedges the
connection for all later callers (a 20-30 s stall per query).

The invariant enforced here: every *read* of ``self.conn`` / ``self._conn`` is
serialized by ``with self._write_lock:``.
"""

from __future__ import annotations

import ast
import threading
from pathlib import Path
from typing import TYPE_CHECKING

import vibe_quant.db.state_manager as _state_manager_module

if TYPE_CHECKING:
    from vibe_quant.db.state_manager import StateManager

_SRC_PATH = Path(_state_manager_module.__file__)

# ``conn`` creates the connection under its own lock and ``close`` tears the
# object down; both are excluded. Methods ending in ``_locked`` are the
# documented "caller already holds the write lock" helpers, so their own bodies
# are exempt -- but every *call site* of them must itself hold the lock
# (checked by ``test_locked_helper_call_sites_hold_the_write_lock``).
_EXEMPT_METHODS = {"conn", "close"}


def _is_write_lock_with(node: ast.With) -> bool:
    """True for ``with self._write_lock:`` (possibly multi-item)."""
    return any(
        isinstance(item.context_expr, ast.Attribute)
        and item.context_expr.attr == "_write_lock"
        and isinstance(item.context_expr.value, ast.Name)
        and item.context_expr.value.id == "self"
        for item in node.items
    )


def _is_self_conn_access(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
        and node.attr in {"conn", "_conn"}
    )


def _scan_method(method: ast.FunctionDef) -> tuple[list[int], list[int]]:
    """Return (unlocked conn reads, unlocked ``*_locked`` calls) line numbers.

    A method whose name ends in ``_locked`` is treated as already inside the
    write lock (its callers hold it), so both lists stay empty for it.
    """
    conn_reads: list[int] = []
    locked_calls: list[int] = []
    baseline = method.name.endswith("_locked")

    def walk(node: ast.AST, inside_lock: bool) -> None:
        for child in ast.iter_child_nodes(node):
            child_inside = inside_lock
            if isinstance(child, ast.With) and _is_write_lock_with(child):
                child_inside = True
            if (
                isinstance(child, ast.Attribute)
                and isinstance(child.ctx, ast.Load)
                and _is_self_conn_access(child)
                and not child_inside
            ):
                conn_reads.append(child.lineno)
            if (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and isinstance(child.func.value, ast.Name)
                and child.func.value.id == "self"
                and child.func.attr.endswith("_locked")
                and not child_inside
            ):
                locked_calls.append(child.lineno)
            walk(child, child_inside)

    walk(method, baseline)
    return conn_reads, locked_calls


def _state_manager_methods(tree: ast.Module) -> dict[str, ast.FunctionDef]:
    cls = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == "StateManager"
    )
    return {
        node.name: node for node in cls.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _analyze(source: str) -> tuple[dict[str, list[int]], dict[str, list[int]]]:
    methods = _state_manager_methods(ast.parse(source))
    conn_offenders: dict[str, list[int]] = {}
    call_offenders: dict[str, list[int]] = {}
    for name, method in methods.items():
        if name in _EXEMPT_METHODS:
            continue
        conn_reads, locked_calls = _scan_method(method)
        if conn_reads:
            conn_offenders[name] = conn_reads
        if locked_calls:
            call_offenders[name] = locked_calls
    return conn_offenders, call_offenders


def test_every_conn_read_is_serialized_by_write_lock() -> None:
    conn_offenders, _ = _analyze(_SRC_PATH.read_text())
    assert conn_offenders == {}, (
        "StateManager methods read the shared sqlite connection without holding "
        f"self._write_lock: {conn_offenders}"
    )


def test_locked_helper_call_sites_hold_the_write_lock() -> None:
    _, call_offenders = _analyze(_SRC_PATH.read_text())
    assert call_offenders == {}, (
        "helper methods ending in _locked are called without the write lock "
        f"being held: {call_offenders}"
    )


_GOOD = """
class StateManager:
    def get_x(self):
        with self._write_lock:
            return self.conn.execute("SELECT 1").fetchone()

    def _helper_locked(self):
        return self.conn.execute("SELECT 1").fetchone()

    def public(self):
        with self._write_lock:
            return self._helper_locked()
"""

_BAD_CONN = """
class StateManager:
    def get_x(self):
        return self.conn.execute("SELECT 1").fetchone()
"""

_BAD_CALL = """
class StateManager:
    def _helper_locked(self):
        return self.conn.execute("SELECT 1").fetchone()

    def public(self):
        return self._helper_locked()
"""


def test_analyzer_flags_unlocked_connection_read() -> None:
    conn_offenders, _ = _analyze(_BAD_CONN)
    assert set(conn_offenders) == {"get_x"}
    assert conn_offenders["get_x"]


def test_analyzer_flags_locked_helper_called_unlocked() -> None:
    _, call_offenders = _analyze(_BAD_CALL)
    assert set(call_offenders) == {"public"}
    assert call_offenders["public"]


def test_analyzer_accepts_correct_locking() -> None:
    assert _analyze(_GOOD) == ({}, {})


def _seed_jobs(sm: StateManager, n_jobs: int) -> int:
    item_id = sm.create_research_item(
        source="reddit",
        external_id="stress-1",
        url="https://example.com/1",
        title="stress",
        body="body",
        author="author",
        posted_at=None,
        score=1,
    )
    for _ in range(n_jobs):
        sm.enqueue_extraction_job(item_id)
    return item_id


def test_concurrent_readers_and_writer_do_not_corrupt_connection(
    tmp_state_manager: StateManager,
) -> None:
    sm = tmp_state_manager
    n_jobs = 40
    item_id = _seed_jobs(sm, n_jobs)

    errors: list[Exception] = []
    stop = threading.Event()

    def reader() -> None:
        try:
            for _ in range(200):
                sm.list_extraction_queue()
                sm.list_extraction_jobs()
                sm.get_research_item(item_id)
                sm.count_active_extraction_jobs()
        except Exception as exc:  # pragma: no cover - only on the bug
            errors.append(exc)

    def writer() -> None:
        try:
            while not stop.is_set():
                job = sm.claim_next_extraction_job()
                if job is None:
                    return
                sm.complete_extraction_job(int(job["id"]), status="done")
        except Exception as exc:  # pragma: no cover - only on the bug
            errors.append(exc)

    readers = [threading.Thread(target=reader) for _ in range(8)]
    writer_thread = threading.Thread(target=writer)
    writer_thread.start()
    for thread in readers:
        thread.start()
    for thread in readers:
        thread.join()
    stop.set()
    writer_thread.join()

    assert errors == [], errors
    assert sm.count_active_extraction_jobs() == 0
    assert len(sm.list_extraction_jobs(status="done")) == n_jobs
