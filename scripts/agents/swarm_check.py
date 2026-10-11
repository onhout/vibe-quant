#!/usr/bin/env python3
"""Pre-review gate for a worker's worktree — run by the orchestrator before any review.

    swarm-check.sh <worktree> [--base REF] [--scope glob,glob,...]
                   [--exactness auto|always|never] [--frontend auto|always|never]
                   [--json out.json] [--no-cache]

Checks (each prints "PASS|FAIL|SKIP <name>: <detail>"), then "GATE PASS" /
"GATE FAIL (n failed)" as the last line; exit 0/1. Default base: main;
default exactness/frontend: auto (run only when relevant paths changed).

PASS-stamp cache: a GATE PASS on a clean worktree writes
`<git-dir>/swarm-check/pass-<key[:16]>.json` in that worktree's git dir (per-worktree,
never committed, invisible to `git status`). The key is a sha256 over HEAD, the base
commit, the sorted scope globs, exactness/frontend modes, this script's own bytes and the
sha256 of the MAIN checkout's scripts/agents/exactness_239.py, uv.lock and
frontend/pnpm-lock.yaml (a missing file hashes as a fixed token), so any change to the
diff or to the checks invalidates it. A later run whose tree is clean and whose key
matches prints the stored check lines as `(cached) ...`, then
`PASS cache: reused stamp from <ts> for <head[:12]>` and `GATE PASS` (and posts
"GATE PASS (cached <head12>)" to the bus), without running any tool.
The stamp is written only when the key computed BEFORE the run is non-empty (clean start),
AND equals the key recomputed after it (nothing moved while the tools ran: a commit made
mid-run would otherwise stamp a HEAD that was never gated), AND the HEAD reflog length is
unchanged (`git rev-list --walk-reflogs --count HEAD`). The reflog guard catches a
commit + `reset --hard` made mid-run: it leaves the key identical but adds reflog entries.
Writes are atomic (temp file + os.replace). FAILs, WARNs, dirty trees and stubs are never
cached; load_stamp accepts only PASS/SKIP lines, and any problem reading a stamp falls back
to the full gate (fail closed: never a false PASS). `--no-cache` disables both reuse and
writing.
Known limit (accepted): the reflog guard cannot see an edit + revert of the working tree
with no commit - the tree and HEAD return to their start state and no reflog entry is added,
so such a run is still stamped.
NOT in the key (known gaps; --no-cache to force): the main checkout's .venv (tool
versions), data/catalog (exactness inputs), the frontend lint baseline in main's
working tree and any uncommitted edit in main other than the keyed files; and any
.gitignore'd input, which is part of neither the clean check nor the key.

The gate FAILS CLOSED: a bad --base, any git error, a timed-out or missing tool is a
FAIL, never a PASS. Exactness always runs the MAIN checkout's exactness_239.py (the
worktree copy must be identical or the check FAILs).

Tool commands can be stubbed via env (full command, shell-quoted):
SWARM_CHECK_RUFF / SWARM_CHECK_MYPY / SWARM_CHECK_PYTEST (the pytest stub replaces
the "<python> -m pytest" prefix and keeps the arguments). Stubs are refused (FAIL,
real tools used) unless SWARM_CHECK_ALLOW_STUBS=1; active stubs are reported.
Stdlib only; runs with any python3.
"""

from __future__ import annotations

import argparse
import contextlib
import fnmatch
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from typing import Any

    RunResult = tuple[int, str]
    RunFn = Callable[..., RunResult]  # (cmd, cwd, env=None, timeout=TOOL_TIMEOUT)

# Exactness triggers on ANY change under vibe_quant/ except these packages. Allowlist =
# packages outside the transitive import closure of screening/nt_runner, discovery/pipeline
# and validation/runner (checked with an import walk 2026-10-10), so they cannot move
# backtest numbers. Re-verify before adding to it.
EXACTNESS_SAFE_DIRS = (
    "vibe_quant/api/",
    "vibe_quant/paper/",
    "vibe_quant/research/",
    "vibe_quant/alerts/",
    "vibe_quant/ethereal/",
    "vibe_quant/jobs/",
    "vibe_quant/reconciliation.py",
    "vibe_quant/risk/",
    "vibe_quant/strategies/",
)
EXACTNESS_FILES = (
    "pyproject.toml",
    "uv.lock",
    "scripts/agents/exactness_239.py",
)
EXACTNESS_SCRIPT = "scripts/agents/exactness_239.py"
CHECK_ORDER = (
    "scope", "clean", "ruff", "mypy", "tests", "exactness", "frontend", "stubs", "internal",
)
UNTRACKED_OK = ("cmd-report.md",)
LINT_ERROR_RE = re.compile(r"^Found (\d+) errors?\.", re.MULTILINE)
SUMMARY_RE = re.compile(r"passed|failed|error|errors|no tests ran")
SKIP_STEMS = frozenset({"__init__", "conftest", "__main__"})
STUB_VARS = ("SWARM_CHECK_RUFF", "SWARM_CHECK_MYPY", "SWARM_CHECK_PYTEST")
TEST_TIMEOUT = 1800  # seconds: pytest, exactness
TOOL_TIMEOUT = 600  # seconds: everything else
PYTEST_NO_TESTS_RC = 5
# Big pytest selections (full suite, -k group) run under xdist: the wall time drops
# ~3x (183s -> ~60s on 10 cores, job 20261011-speed). `worksteal` beat the default
# `load` scheduler (56s vs 66s) once relative-speed tests were pulled out. A handful
# of targeted files stays serial: worker startup costs more than it saves there.
# Tests marked `timing` assert relative wall-clock speed and flake under worker
# contention, so they are deselected from the parallel pass and run serially after.
PYTEST_PARALLEL_ARGS = ("-n", "auto", "--dist", "worksteal")
TIMING_MARK = "timing"
CACHE_DIR = "swarm-check"
CACHE_VERSION = 2
CHECK_STATUSES = ("PASS", "FAIL", "SKIP", "WARN")
STAMP_STATUSES = ("PASS", "SKIP")  # the only line statuses a reusable stamp may contain
MAIN_KEY_FILES = ("scripts/agents/exactness_239.py", "uv.lock", "frontend/pnpm-lock.yaml")
MISSING_FILE_TOKEN = "missing"


@dataclass(frozen=True)
class Check:
    name: str
    status: str  # PASS | FAIL | SKIP | WARN (only FAIL fails the gate)
    detail: str


@dataclass(frozen=True)
class Config:
    worktree: Path
    main_root: Path
    base: str = "main"
    scope: tuple[str, ...] = field(default_factory=tuple)
    exactness: str = "auto"
    frontend: str = "auto"
    json_out: Path | None = None


@dataclass(frozen=True)
class GitState:
    changed: list[str]  # committed diff + uncommitted incl. untracked (cmd-report.md excluded)
    untracked: list[str]  # untracked files, cmd-report.md excluded
    error: str = ""  # non-empty: the diff is UNKNOWN (bad base / git failure) -> gate FAIL
    uncommitted: list[str] = field(default_factory=list)  # tracked/index changes (not committed)


@dataclass(frozen=True)
class Stamp:
    head: str
    ts: str
    checks: list[Check]


def real_run(
    cmd: Sequence[str],
    cwd: Path,
    env: Mapping[str, str] | None = None,
    timeout: int = TOOL_TIMEOUT,
) -> RunResult:
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    try:
        proc = subprocess.run(
            [str(c) for c in cmd],
            cwd=str(cwd),
            env=full_env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout}s: {cmd[0]}"
    except OSError as exc:  # missing binary or missing cwd
        return 127, f"cannot run {cmd[0]}: {exc}"
    return proc.returncode, proc.stdout + proc.stderr


def parse_porcelain(text: str) -> tuple[list[str], list[str], list[str]]:
    """Split `git status --porcelain=v1 -z` into (all changed, untracked, uncommitted).

    NUL-separated, so no quoting. A rename/copy entry is "XY new\\0old\\0": both sides
    are reported so an out-of-scope old path cannot hide behind a rename. `uncommitted`
    is every status entry that is not untracked (a dirty working tree/index) and is used
    by the PASS-stamp cache, which never caches a tree with uncommitted edits.
    """
    changed: list[str] = []
    untracked: list[str] = []
    uncommitted: list[str] = []
    tokens = text.split("\0")
    i = 0
    while i < len(tokens):
        entry = tokens[i]
        i += 1
        if len(entry) < 4:
            continue
        xy, path = entry[:2], entry[3:]
        paths = [path]
        if ("R" in xy or "C" in xy) and i < len(tokens):
            paths.append(tokens[i])
            i += 1
        if xy == "??":
            if path in UNTRACKED_OK:
                continue
            untracked.append(path)
        else:
            uncommitted.extend(p for p in paths if p)
        changed.extend(p for p in paths if p)
    return changed, untracked, uncommitted


def git_state(wt: Path, base: str, run: RunFn) -> GitState:
    """Committed + uncommitted + untracked changes. Fails closed: any git error -> .error."""

    def git(*args: str) -> RunResult:
        return run(["git", "-C", str(wt), *args], wt, None, TOOL_TIMEOUT)

    rc, out = git("rev-parse", "--verify", "--quiet", base + "^{commit}")
    if rc != 0:
        return GitState([], [], f"bad base ref: {base}")
    rc, out = git("merge-base", base, "HEAD")
    if rc != 0:
        return GitState([], [], f"no merge-base between {base} and HEAD: {tail_line(out)}")
    rc, diff_out = git("diff", "--name-only", "--no-renames", "-z", base + "...HEAD")
    if rc != 0:
        return GitState([], [], f"git diff failed: {tail_line(diff_out)}")
    rc, status_out = git("status", "--porcelain=v1", "-z", "--untracked-files=all")
    if rc != 0:
        return GitState([], [], f"git status failed: {tail_line(status_out)}")
    committed = [p for p in diff_out.split("\0") if p]
    changed, untracked, uncommitted = parse_porcelain(status_out)
    seen: dict[str, None] = {}
    for path in committed + changed:
        seen.setdefault(path, None)
    return GitState(changed=list(seen), untracked=untracked, uncommitted=uncommitted)


def scope_offenders(paths: Sequence[str], globs: Sequence[str]) -> list[str]:
    """Paths matching no glob. NB fnmatch: '*' also crosses '/', so 'vibe_quant/*' covers
    vibe_quant/a/b.py."""
    return [p for p in paths if not any(fnmatch.fnmatch(p, g) for g in globs)]


def is_changed_test(path: str) -> bool:
    parts = path.split("/")
    return (
        len(parts) >= 3
        and parts[0] == "tests"
        and parts[-1].startswith("test_")
        and parts[-1].endswith(".py")
    )


def changed_test_files(paths: Sequence[str]) -> list[str]:
    return [p for p in paths if is_changed_test(p)]


def k_expr(paths: Sequence[str]) -> str:
    stems = sorted(
        {
            Path(p).stem
            for p in paths
            if p.endswith(".py") and not is_changed_test(p)
        }
        - SKIP_STEMS
    )
    stems = [st for st in stems if st.isidentifier()]
    return " or ".join(stems)


def needs_full_suite(paths: Sequence[str]) -> bool:
    """True if a changed .py has a stem k_expr drops (conftest/__init__/__main__): no -k
    selection can represent it, so the whole tests/unit must run."""
    return any(p.endswith(".py") and Path(p).stem in SKIP_STEMS for p in paths)


def should_run_exactness(paths: Sequence[str]) -> bool:
    return any(
        (p.startswith("vibe_quant/") and not p.startswith(EXACTNESS_SAFE_DIRS))
        or p in EXACTNESS_FILES
        for p in paths
    )


def should_run_frontend(paths: Sequence[str]) -> bool:
    return any(p.startswith("frontend/") for p in paths)


def parse_lint_errors(output: str) -> int:
    return sum(int(n) for n in LINT_ERROR_RE.findall(output))


def has_lint_count(output: str) -> bool:
    return LINT_ERROR_RE.search(output) is not None


def lint_regressed(wt_output: str, main_output: str) -> bool:
    return parse_lint_errors(wt_output) > parse_lint_errors(main_output)


def pytest_summary(output: str) -> str:
    lines = [ln.strip() for ln in output.splitlines() if ln.strip()]
    for line in reversed(lines):
        if SUMMARY_RE.search(line):
            return line
    return lines[-1] if lines else "no output"


def tail_line(output: str) -> str:
    lines = [ln.strip() for ln in output.splitlines() if ln.strip()]
    return lines[-1] if lines else "ok"


def format_line(check: Check) -> str:
    return f"{check.status} {check.name}: {check.detail}"


def gate_summary(checks: Sequence[Check]) -> tuple[str, int]:
    failed = [c for c in checks if c.status == "FAIL"]
    if failed:
        return f"GATE FAIL ({len(failed)} failed)", len(failed)
    return "GATE PASS", 0


def to_json_dict(checks: Sequence[Check]) -> dict[str, Any]:
    data: dict[str, Any] = {
        c.name: {"status": c.status, "detail": c.detail} for c in checks
    }
    _, failed = gate_summary(checks)
    data["gate"] = "FAIL" if failed else "PASS"
    return data


def is_clean(state: GitState) -> bool:
    """A reuse/write-safe tree: known diff, no untracked files, no uncommitted edits."""
    return not state.error and not state.untracked and not state.uncommitted


def tool_hash() -> str:
    """sha256 of this script's own source bytes: any gate change invalidates the cache."""
    return hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest()


def main_files_hash(main_root: Path) -> dict[str, str]:
    """sha256 per main-checkout input of the gate (exactness script, lockfiles)."""
    out: dict[str, str] = {}
    for rel in MAIN_KEY_FILES:
        try:
            out[rel] = hashlib.sha256((main_root / rel).read_bytes()).hexdigest()
        except OSError:
            out[rel] = MISSING_FILE_TOKEN
    return out


def cache_key(head: str, base_sha: str, cfg: Config, thash: str) -> str:
    payload = json.dumps(
        {
            "head": head,
            "base": base_sha,
            "scope": sorted(cfg.scope),
            "exactness": cfg.exactness,
            "frontend": cfg.frontend,
            "tool": thash,
            "main": main_files_hash(cfg.main_root),
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def parse_check_line(line: str) -> Check | None:
    """Inverse of format_line (None when the line is not a well-formed check)."""
    status, sep, rest = line.partition(" ")
    name, sep2, detail = rest.partition(": ")
    if not sep or not sep2 or status not in CHECK_STATUSES or not name:
        return None
    return Check(name, status, detail)


def _git_out(wt: Path, run: RunFn, *args: str) -> str:
    rc, out = run(["git", "-C", str(wt), *args], wt, None, TOOL_TIMEOUT)
    return out.strip() if rc == 0 else ""


def git_dir(wt: Path, run: RunFn) -> Path | None:
    """This worktree's own git dir (per-worktree for linked worktrees; never committed)."""
    out = _git_out(wt, run, "rev-parse", "--absolute-git-dir")
    return Path(out) if out else None


def stamp_path(wt: Path, run: RunFn, key: str) -> Path | None:
    gd = git_dir(wt, run)
    return gd / CACHE_DIR / f"pass-{key[:16]}.json" if gd is not None else None


def head_reflog_count(wt: Path, run: RunFn) -> int | None:
    """HEAD reflog length, or None when git cannot report it (the caller fails closed)."""
    out = _git_out(wt, run, "rev-list", "--walk-reflogs", "--count", "HEAD")
    return int(out) if out.isdigit() else None


def _cache_probe(cfg: Config, run: RunFn) -> tuple[str, GitState | None]:
    """(cache key, git state): the key is "" when a stamp must not be reused or written."""
    try:
        state = git_state(cfg.worktree, cfg.base, run)
        if not is_clean(state):
            return "", state
        head = _git_out(cfg.worktree, run, "rev-parse", "HEAD")
        base_sha = _git_out(cfg.worktree, run, "rev-parse", cfg.base + "^{commit}")
        if not head or not base_sha:
            return "", state
        return cache_key(head, base_sha, cfg, tool_hash()), state
    except Exception:  # fail closed: any doubt -> run the full gate
        return "", None


def load_stamp(wt: Path, run: RunFn, key: str) -> Stamp | None:
    """A valid stored PASS for `key`, or None (any corruption/mismatch -> full gate)."""
    if not key:
        return None
    try:
        path = stamp_path(wt, run, key)
        if path is None or not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        if data.get("version") != CACHE_VERSION or data.get("key") != key:
            return None
        if data.get("gate") != "PASS":
            return None
        head, ts, lines = data.get("head"), data.get("ts"), data.get("checks")
        if not isinstance(head, str) or not isinstance(ts, str):
            return None
        if not isinstance(lines, list) or not lines:
            return None
        checks: list[Check] = []
        for line in lines:
            parsed = parse_check_line(line) if isinstance(line, str) else None
            if parsed is None:
                return None
            checks.append(parsed)
        if any(c.status not in STAMP_STATUSES for c in checks):
            return None  # only PASS/SKIP lines: a stored FAIL or WARN is never a PASS
        return Stamp(head=head, ts=ts, checks=checks)
    except Exception:
        return None


def write_stamp(wt: Path, run: RunFn, cfg: Config, key: str, checks: Sequence[Check]) -> None:
    if not key:
        return
    path = stamp_path(wt, run, key)
    if path is None:
        return
    data = {
        "version": CACHE_VERSION,
        "key": key,
        "head": _git_out(wt, run, "rev-parse", "HEAD"),
        "base": cfg.base,
        "base_sha": _git_out(wt, run, "rev-parse", cfg.base + "^{commit}"),
        "scope": sorted(cfg.scope),
        "exactness": cfg.exactness,
        "frontend": cfg.frontend,
        "tool": tool_hash(),
        "ts": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "gate": "PASS",
        "checks": [format_line(c) for c in checks],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-pass-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(data, indent=2) + "\n")
        os.replace(tmp_name, path)  # atomic: a reader sees the whole stamp or none
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def stubs_allowed() -> bool:
    return os.environ.get("SWARM_CHECK_ALLOW_STUBS") == "1"


def active_stubs() -> dict[str, str]:
    return {v: os.environ[v] for v in STUB_VARS if os.environ.get(v)}


def _cmd(env_var: str, default: list[str]) -> list[str]:
    override = os.environ.get(env_var) if stubs_allowed() else None
    return shlex.split(override) if override else default


def _tool_commands(cfg: Config) -> dict[str, list[str]]:
    venv = cfg.main_root / ".venv" / "bin"
    return {
        "ruff": _cmd("SWARM_CHECK_RUFF", [str(venv / "ruff"), "check"]),
        "mypy": _cmd("SWARM_CHECK_MYPY", [str(venv / "mypy")]),
        "pytest": _cmd("SWARM_CHECK_PYTEST", [str(venv / "python"), "-m", "pytest"]),
        "python": [str(venv / "python")],
    }


def _run_tests(
    cfg: Config, state: GitState, tools: dict[str, list[str]], run: RunFn, wt_env: dict[str, str]
) -> Check:
    python_changed = [p for p in state.changed if p.endswith(".py")]
    if not python_changed:
        return Check("tests", "SKIP", "no python changed")
    targets = [p for p in changed_test_files(state.changed) if (cfg.worktree / p).exists()]
    expr = k_expr(state.changed)
    # (args, rc5_is_skip, rc5_note)
    commands: list[tuple[list[str], bool, str]] = []
    # A handful of targeted files runs serially (worker startup costs more than it
    # saves); timing-marked tests in them therefore run uncontended.
    if targets:
        commands.append((targets, False, ""))
    # A big selection (full suite, -k group) runs under xdist with the timing-marked
    # relative-speed tests deselected, then those run serially (they flake under load).
    full = needs_full_suite(state.changed)
    group = ["tests/unit"] if full else (["tests/unit", "-k", expr] if expr else [])
    if group:
        # rc5 on a full-suite run is a real failure; on a -k group it means "no match".
        commands.append(
            (group + list(PYTEST_PARALLEL_ARGS) + ["-m", f"not {TIMING_MARK}"], not full,
             f"no tests matched -k {expr}" if expr else "")
        )
        commands.append(
            (group + ["-m", TIMING_MARK], True,
             f"no timing tests matched -k {expr}" if expr else "no timing tests matched")
        )
    if not commands:
        return Check("tests", "SKIP", "nothing to run for changed python")
    summaries: list[str] = []
    failed = False
    passed = 0
    for args, rc5_skip, rc5_note in commands:
        rc, out = run(tools["pytest"] + args, cfg.worktree, wt_env, TEST_TIMEOUT)
        if rc == PYTEST_NO_TESTS_RC and rc5_skip:
            summaries.append(rc5_note)
            continue
        summaries.append(pytest_summary(out))
        if rc != 0:
            failed = True
        else:
            passed += 1
    if failed:
        return Check("tests", "FAIL", " | ".join(summaries))
    if passed == 0:
        return Check("tests", "SKIP", " | ".join(summaries))
    return Check("tests", "PASS", " | ".join(summaries))


def _run_exactness(cfg: Config, tools: dict[str, list[str]], run: RunFn) -> Check:
    main_script = cfg.main_root / EXACTNESS_SCRIPT
    wt_script = cfg.worktree / EXACTNESS_SCRIPT
    if not main_script.is_file():
        return Check("exactness", "FAIL", f"main exactness script missing: {main_script}")
    if wt_script.is_file() and wt_script.read_bytes() != main_script.read_bytes():
        return Check("exactness", "FAIL", "exactness script modified in worktree")
    rc, out = run(
        tools["python"] + [str(main_script)],
        cfg.worktree,
        {"PYTHONPATH": str(cfg.worktree)},
        TEST_TIMEOUT,
    )
    if rc == 0 and "IDENTICAL" in out:
        return Check("exactness", "PASS", "IDENTICAL")
    return Check("exactness", "FAIL", f"drift (rc={rc}): " + tail_line(out))


def _lint_problem(label: str, rc: int, out: str) -> str:
    if rc != 0 and not has_lint_count(out):
        return f"{label} lint failed without an error count: " + tail_line(out)
    return ""


def _run_frontend(cfg: Config, run: RunFn) -> Check:
    wt_fe = cfg.worktree / "frontend"
    tsc_rc, tsc_out = run(["npx", "tsc", "-b"], wt_fe, None, TOOL_TIMEOUT)
    build_rc, build_out = run(["pnpm", "build"], wt_fe, None, TOOL_TIMEOUT)
    wt_rc, lint_wt = run(["pnpm", "lint"], wt_fe, None, TOOL_TIMEOUT)
    main_rc, lint_main = run(["pnpm", "lint"], cfg.main_root / "frontend", None, TOOL_TIMEOUT)
    wt_errs, main_errs = parse_lint_errors(lint_wt), parse_lint_errors(lint_main)
    problems = []
    if tsc_rc != 0:
        problems.append("tsc: " + tail_line(tsc_out))
    if build_rc != 0:
        problems.append("build: " + tail_line(build_out))
    for label, rc, out in (("worktree", wt_rc, lint_wt), ("main", main_rc, lint_main)):
        if msg := _lint_problem(label, rc, out):
            problems.append(msg)
    if lint_regressed(lint_wt, lint_main):
        problems.append(f"lint errors {wt_errs} > main {main_errs}")
    counts = f"lint errors {wt_errs} (main {main_errs})"
    if problems:
        return Check("frontend", "FAIL", "; ".join(problems) + "; " + counts)
    return Check("frontend", "PASS", "tsc ok, build ok, " + counts)


def run_checks(cfg: Config, run: RunFn = real_run) -> list[Check]:
    state = git_state(cfg.worktree, cfg.base, run)
    tools = _tool_commands(cfg)
    wt_env = {"PYTHONPATH": str(cfg.worktree)}
    checks: list[Check] = []

    # 0. stubs: refused unless explicitly allowed; always reported
    stubs = active_stubs()
    if stubs:
        listed = ", ".join(f"{k}={v}" for k, v in stubs.items())
        if stubs_allowed():
            checks.append(Check("stubs", "WARN", f"NOTE stub active: {listed}"))
        else:
            checks.append(
                Check("stubs", "FAIL", f"stubs refused (set SWARM_CHECK_ALLOW_STUBS=1): {listed}")
            )

    # 1. scope
    if state.error:
        checks.append(Check("scope", "FAIL", state.error))
    elif not state.changed:
        checks.append(Check("scope", "FAIL", f"empty diff vs {cfg.base} (nothing to review)"))
    elif cfg.scope:
        offenders = scope_offenders(state.changed, cfg.scope)
        if offenders:
            shown = ", ".join(offenders[:5])
            more = f" (+{len(offenders) - 5} more)" if len(offenders) > 5 else ""
            detail = f"{len(offenders)}/{len(state.changed)} paths out of scope: {shown}{more}"
            checks.append(Check("scope", "FAIL", detail))
        else:
            checks.append(Check("scope", "PASS", f"{len(state.changed)} paths in scope"))
    else:
        checks.append(Check("scope", "SKIP", "no --scope given"))

    # 2. clean
    if state.untracked:
        shown = ", ".join(state.untracked[:5])
        more = f" (+{len(state.untracked) - 5} more)" if len(state.untracked) > 5 else ""
        checks.append(Check("clean", "WARN", f"{len(state.untracked)} untracked: {shown}{more}"))
    else:
        checks.append(Check("clean", "PASS", "no untracked files"))

    # 3. ruff / 4. mypy
    for name in ("ruff", "mypy"):
        rc, out = run(tools[name], cfg.worktree, None, TOOL_TIMEOUT)
        checks.append(Check(name, "PASS" if rc == 0 else "FAIL", tail_line(out)))

    unknown = "git diff unknown (see scope)"
    # 5. tests
    if state.error:
        checks.append(Check("tests", "SKIP", unknown))
    else:
        checks.append(_run_tests(cfg, state, tools, run, wt_env))

    # 6. exactness
    if cfg.exactness == "never":
        checks.append(Check("exactness", "SKIP", "disabled"))
    elif cfg.exactness == "always" or (not state.error and should_run_exactness(state.changed)):
        checks.append(_run_exactness(cfg, tools, run))
    elif state.error:
        checks.append(Check("exactness", "SKIP", unknown))
    else:
        checks.append(
            Check(
                "exactness",
                "SKIP",
                "auto: nothing changed under vibe_quant/ (except "
                + ", ".join(d.rstrip("/").removeprefix("vibe_quant/") for d in EXACTNESS_SAFE_DIRS)
                + ") nor in " + ", ".join(EXACTNESS_FILES),
            )
        )

    # 7. frontend
    if cfg.frontend == "never":
        checks.append(Check("frontend", "SKIP", "disabled"))
    elif cfg.frontend == "always" or (not state.error and should_run_frontend(state.changed)):
        checks.append(_run_frontend(cfg, run))
    elif state.error:
        checks.append(Check("frontend", "SKIP", unknown))
    else:
        checks.append(Check("frontend", "SKIP", "auto: nothing under frontend/ changed"))

    order = {name: i for i, name in enumerate(CHECK_ORDER)}
    checks.sort(key=lambda c: order.get(c.name, len(order)))
    return checks


def _derive_main_root(wt: Path) -> Path:
    proc = subprocess.run(
        ["git", "-C", str(wt), "rev-parse", "--path-format=absolute", "--git-common-dir"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return Path("/Users/verebelyin/projects/vibe-quant")
    return Path(proc.stdout.strip()).parent


def bus_post(cfg: Config, checks: Sequence[Check], cached_head: str = "") -> None:
    bus = os.environ.get("SWARM_BUS")
    if not bus:
        return
    gate, _ = gate_summary(checks)
    failed = [c.name for c in checks if c.status == "FAIL"]
    body = f"{cfg.worktree.name}: {gate}"
    if cached_head:
        body += f" (cached {cached_head[:12]})"
    if failed:
        body += " (" + ", ".join(failed) + ")"
    with contextlib.suppress(Exception):
        subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve().parent / "bus.py"),
                "--bus", bus, "--as", "swarm-check",
                "post", "--to", "chief", "--kind", "info", "--body", body,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )


def main(argv: Sequence[str] | None = None, run: RunFn | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pre-review gate for a worker's worktree")
    parser.add_argument("worktree")
    parser.add_argument("--base", default="main")
    parser.add_argument("--scope", default="")
    parser.add_argument("--exactness", choices=("auto", "always", "never"), default="auto")
    parser.add_argument("--frontend", choices=("auto", "always", "never"), default="auto")
    parser.add_argument("--json", dest="json_out", default=None)
    parser.add_argument("--no-cache", dest="no_cache", action="store_true")
    args = parser.parse_args(argv)

    wt = Path(args.worktree).resolve()
    if not (wt / ".git").exists() and subprocess.run(
        ["git", "-C", str(wt), "rev-parse", "--git-dir"], capture_output=True
    ).returncode != 0:
        print(f"not a git worktree: {wt}")
        return 2

    cfg = Config(
        worktree=wt,
        main_root=_derive_main_root(wt),
        base=args.base,
        scope=tuple(g for g in (s.strip() for s in args.scope.split(",")) if g),
        exactness=args.exactness,
        frontend=args.frontend,
        json_out=Path(args.json_out) if args.json_out else None,
    )

    runner: RunFn = run or real_run
    key, _ = _cache_probe(cfg, runner) if not args.no_cache else ("", None)  # PRE-run key
    stamp = load_stamp(wt, runner, key)

    if stamp is not None:
        for check in stamp.checks:
            print(f"(cached) {format_line(check)}")
        print(f"PASS cache: reused stamp from {stamp.ts} for {stamp.head[:12]}")
        print("GATE PASS")
        if cfg.json_out:
            cached = to_json_dict(stamp.checks)
            cached["cached"] = True
            with open(cfg.json_out, "w", encoding="utf-8") as fh:
                json.dump(cached, fh, indent=2)
                fh.write("\n")
        bus_post(cfg, stamp.checks, cached_head=stamp.head)
        return 0

    # A commit+reset --hard made while the tools run puts HEAD and the tree back where they
    # started, so the pre/post key compare cannot see it; the reflog length can (see N1 doc).
    reflog_before = head_reflog_count(wt, runner) if key else None

    try:
        checks = run_checks(cfg) if run is None else run_checks(cfg, runner)
    except Exception as exc:  # fail closed: a crashed gate is a failed gate
        checks = [Check("internal", "FAIL", f"gate crashed: {type(exc).__name__}: {exc}")]
    for check in checks:
        print(format_line(check))
    gate, failed = gate_summary(checks)
    print(gate)

    if cfg.json_out:
        with open(cfg.json_out, "w", encoding="utf-8") as fh:
            json.dump(to_json_dict(checks), fh, indent=2)
            fh.write("\n")

    if not args.no_cache and not failed and not active_stubs() and key:
        # key is the PRE-run key (non-empty = clean start). Stamp only if nothing moved while
        # the tools ran; otherwise the verdict is for a HEAD/tree that was never gated.
        post_key, _ = _cache_probe(cfg, runner)
        reflog_after = head_reflog_count(wt, runner)
        if post_key == key and reflog_before is not None and reflog_after == reflog_before:
            with contextlib.suppress(Exception):
                write_stamp(wt, runner, cfg, key, checks)

    bus_post(cfg, checks)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
