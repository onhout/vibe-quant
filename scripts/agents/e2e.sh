#!/usr/bin/env bash
# One command for the whole Playwright E2E suite.
#
#   scripts/agents/e2e.sh <worktree> [playwright args...]
#
# Boots the throwaway ui-check stack (backend :8001 + Vite :5188), runs the
# chromium project against it and — only when it started the stack itself —
# ALWAYS tears it down (pass, fail, Ctrl-C or error). Exit code is Playwright's,
# except exit 3 when `up` found the ports busy (a live stack owned by another
# agent), which is left running.
#
# Extra args are forwarded to `playwright test`, e.g.:
#   scripts/agents/e2e.sh <wt> e2e/discovery-flow.spec.ts
#   scripts/agents/e2e.sh <wt> -g "Settings page"
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
wt="$(cd "${1:?usage: e2e.sh <worktree> [playwright args...]}" && pwd)"
shift

# `up` exits 3 when a port is busy (a live stack is already up). Exit 3 BEFORE
# installing the down-trap: otherwise cleanup would run `ui-check.sh down` and
# kill the stack that owns the port. Any other `up` failure left half-started
# servers behind, so tear those down first.
up_rc=0
"$here/ui-check.sh" up "$wt" || up_rc=$?
if [[ $up_rc -ne 0 ]]; then
  [[ $up_rc -eq 3 ]] || "$here/ui-check.sh" down "$wt" >/dev/null 2>&1 || true
  exit $up_rc
fi

cleanup() {
  "$here/ui-check.sh" down "$wt" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

cd "$wt/frontend"
# Second reporter (json) so the summary can count skips: data-picked specs skip
# silently when the DB copy has no matching run (vibe-quant-rh1w4).
report_json="$(mktemp)"
E2E_BASE_URL="${E2E_BASE_URL:-http://localhost:5188}" \
E2E_API_URL="${E2E_API_URL:-http://127.0.0.1:8001}" \
PLAYWRIGHT_HTML_OPEN=never PLAYWRIGHT_JSON_OUTPUT_FILE="$report_json" \
  pnpm exec playwright test --project=chromium --reporter=html,json "$@"
status=$?

# Summary: Playwright's own counts plus each skipped spec, so a skip is never silent.
python3 -I - "$report_json" <<'PY' || echo "e2e summary: no JSON report (skipped count unknown)"
import json
import sys

with open(sys.argv[1], encoding="utf-8") as f:
    report = json.load(f)
stats = report.get("stats", {})
print(
    f"e2e summary: expected={stats.get('expected')} unexpected={stats.get('unexpected')}"
    f" flaky={stats.get('flaky')} skipped={stats.get('skipped')}"
)


def walk(suite, prefix):
    for spec in suite.get("specs", []):
        tests = spec.get("tests", [])
        if tests and all(t.get("status") == "skipped" for t in tests):
            print(f"  skipped: {prefix}{spec.get('title')}")
    for child in suite.get("suites", []):
        walk(child, f"{prefix}{child.get('title')} > ")


for suite in report.get("suites", []):
    walk(suite, f"{suite.get('file')} > ")
PY
rm -f "$report_json"
exit "$status"
