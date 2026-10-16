"""
Serve the whole dashboard from one process: vibe-quant's API plus the Robinhood/Lab routes, and the
built frontend (``frontend/dist``) with client-side routes falling back to ``index.html``.

    .venv/bin/python -m vibe_quant.robinhood.serve --port 8090      # http://<this VM>:8090

Rebuild the frontend after changing it: ``cd frontend && pnpm build``.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.responses import FileResponse

from vibe_quant.api.app import create_app as create_api_app
from vibe_quant.robinhood.api import lab_router
from vibe_quant.robinhood.api import router as robinhood_router


DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
_NOT_SPA = ("api/", "ws/", "health", "docs", "redoc", "openapi.json")


def create_app() -> FastAPI:
    app = create_api_app()
    app.include_router(robinhood_router)
    app.include_router(lab_router)

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str) -> FileResponse:
        if path.startswith(_NOT_SPA):
            raise HTTPException(status_code=404)
        file = (DIST / path).resolve()
        if path and file.is_file() and file.is_relative_to(DIST):
            return FileResponse(file)
        index = DIST / "index.html"
        if not index.exists():
            raise HTTPException(status_code=503, detail="frontend not built: cd frontend && pnpm build")
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    return app


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")  # noqa: S104 (home network)
    parser.add_argument("--port", type=int, default=8090)
    args = parser.parse_args()
    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
