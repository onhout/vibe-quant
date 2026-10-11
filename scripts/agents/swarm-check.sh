#!/usr/bin/env bash
# Pre-review gate: mechanical checks on a worker's worktree before any reviewer sees the diff.
#
#   scripts/agents/swarm-check.sh <worktree> [--base REF] [--scope glob,...]
#       [--exactness auto|always|never] [--frontend auto|always|never]
#       [--json out.json] [--no-cache]
#
# One "PASS|FAIL|SKIP <name>: <detail>" line per check, then "GATE PASS" /
# "GATE FAIL (n failed)"; exit 0/1. Tool commands overridable via
# SWARM_CHECK_RUFF / SWARM_CHECK_MYPY / SWARM_CHECK_PYTEST, but only honoured (else FAIL)
# with SWARM_CHECK_ALLOW_STUBS=1. Fails closed (bad base, empty diff, git errors).
# A GATE PASS on a clean start (and an unmoved tree at the end) writes a stamp in the
# worktree's git dir; a later run with the same HEAD/base/scope/modes, unchanged
# swarm_check.py and unchanged main exactness_239.py/uv.lock/pnpm-lock.yaml reuses it and
# prints "(cached) ..." + "PASS cache: ..." (--no-cache disables reuse and writing).
# Stdlib-only helper.
set -euo pipefail
exec python3 "$(cd "$(dirname "$0")" && pwd)/swarm_check.py" "$@"
