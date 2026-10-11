<!--
Brief template for a Command Code T3 subagent (delegate_task `task` field; see docs/orchestration/cheap-agents.md).
Save the filled brief to data/swarm/<job>/prompts/<slug>.md for the ledger, then pass its full text as `task`.
Fill every <...>, delete these comments. Keep it self-contained: the child sees ONLY this text — none of the chief's conversation.
-->
ROLE: Command Code worker (agent id "<slug>", bead <bead-id>)

WORKSPACE (mandatory): work only inside the git worktree `<absolute worktree path>` (branch `<branch>`).
Your session starts in a different directory (the main checkout) — do not edit anything there.
Prefix EVERY shell command with `cd <absolute worktree path> && `. Run Python as
`cd <absolute worktree path> && PYTHONPATH=$PWD /Users/verebelyin/projects/vibe-quant/.venv/bin/python ...`.

GOAL: <one sentence: the exact change or extraction>

FILES YOU MAY EDIT OR CREATE (inside the worktree): <exact paths>. Leave every other file unchanged.
FILES TO READ FIRST: <exact paths + line ranges, the fewest that suffice — read them all in ONE turn>

SPEED RULES (measured: one model turn costs 4–10 s; the full test suite costs ~3 min serial, ~1 min under `-n auto --dist worksteal`):
- Batch independent reads/greps/commands into ONE turn (parallel tool calls). Read whole functions or the line ranges given below, not 70-line slices. No todo lists.
- While iterating, run only the targeted test files. The full suite runs ONCE, inside swarm-check at the end — do not also run `pytest tests/unit` separately.
- The gate is main's `scripts/agents/swarm-check.sh <worktree>`. Only when your task changes the gate itself (`scripts/agents/swarm_check.py`), prove it with the worktree's copy: `<worktree>/scripts/agents/swarm-check.sh <worktree>`.
- Chain checks in one shell call: `ruff check && mypy && <targeted pytest>`.
- E2E: iterate with `playwright test <spec> -g "<scenario>"`; run the full spec once at the end.
- Tests of anything launched asynchronously (API returns 201/running, work happens in a subprocess) must poll the run's FINAL status and fail on `failed` — a 201 proves nothing.
- Mutation self-check: at most 3 mutants, only on the lines you changed, each run against the targeted test file only.

FACTS YOU NEED (exact code excerpts / line ranges, so you don't have to search):
- <pasted schema / signature / convention>
- <the 1–3 gotchas that apply>

EXAMPLE OF A CORRECT RESULT:
<a short worked example of the output or the edit>

STEPS:
1. <step>
2. <step>
3. Run this check: `cd <absolute worktree path> && <command>`. Expected: <result>. If it fails, fix and re-run, at most 3 times.
4. Lint: `cd <absolute worktree path> && /Users/verebelyin/projects/vibe-quant/.venv/bin/ruff check <files> && /Users/verebelyin/projects/vibe-quant/.venv/bin/mypy <files>` → 0 errors.

DONE WHEN: <checkable criterion: command + expected output>.

GIT: <either "Do not commit." or "Commit in the worktree with a verbose message in the format of docs/orchestration/prompts/commit-message.md (Why / What / How / Decisions / Verification): `git add <paths> && git commit -F <msgfile>`">. Never push, never touch main.

<bus-protocol block from docs/orchestration/prompts/bus-protocol.md, placeholders filled: {AGENT}=<slug>, {BUS}=<abs job bus dir>, {REPO}=/Users/verebelyin/projects/vibe-quant, {PEERS}=<parallel agents: id: scope; ...>>

FINAL REPLY: exactly one line `DONE <output path or commit sha>` or `FAILED <one-line reason>` — OR, for implementation tasks,
the handoff contract (status / artifacts / evidence with commands + verbatim output / claims / open / comms).
