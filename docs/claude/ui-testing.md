# Dev servers & UI testing

Read before starting the backend/frontend, checking UI, or running the Playwright spec.

1. **Port 5173 may belong to another project.** vibe-quant's Vite can land on 5174/5175; screenshot or check the page title before testing (one session drove a Minecraft clone).
2. **Stop the backend with `pgrep -f "uvicorn vibe_quant" | xargs kill`.** `lsof -ti :8000 | xargs kill` also kills Vite, which holds proxy connections to :8000.
3. **The backend caches code at startup**: plugin registrations and endpoint changes show only after a restart. Discovery, validation and screening run as subprocesses and pick up new code from disk.
4. **Check ports before starting servers** (`lsof -i :8000`).
5. **Test a worktree's UI next to the user's servers** with `scripts/agents/ui-check.sh up|down <worktree>`; always run `down` afterwards.
