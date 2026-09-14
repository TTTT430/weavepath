"""Desktop entry point for the packaged WeavePath application.

The desktop shell starts this executable as a localhost-only sidecar.  The
FastAPI application serves both the API and the built React application so the
frontend can keep using same-origin ``/api/v1`` requests in production.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import uvicorn
from fastapi import HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from api.app import create_app
from graph_core import GraphStore


def bundled_web_root() -> Path:
    """Return the Vite distribution bundled by PyInstaller or the local build."""

    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS")) / "web"
    return Path(__file__).resolve().parents[1] / "apps" / "web" / "dist"


def create_desktop_app(
    store: GraphStore | None = None,
    web_root: str | Path | None = None,
):
    root = Path(web_root) if web_root is not None else bundled_web_root()
    index = root / "index.html"
    if not index.is_file():
        raise RuntimeError(
            f"WeavePath web assets are missing at {root}. Build apps/web before packaging."
        )

    app = create_app(store)
    assets = root / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="desktop-assets")

    @app.get("/", include_in_schema=False)
    def desktop_index():
        return FileResponse(index)

    @app.get("/{full_path:path}", include_in_schema=False)
    def desktop_spa(full_path: str):
        # Unknown API routes must stay API 404s instead of returning HTML.
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        candidate = (root / full_path).resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError:
            raise HTTPException(status_code=404, detail="Not Found") from None
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index)

    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the WeavePath desktop sidecar")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--log-level", default="warning")
    args = parser.parse_args(argv)
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("the packaged desktop service may only bind to localhost")
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")

    # PyInstaller one-folder applications can otherwise inherit a surprising
    # working directory from Explorer. Persistent data is independently rooted
    # by api.app.default_database_path under LOCALAPPDATA.
    os.chdir(Path(sys.executable).resolve().parent if getattr(sys, "frozen", False)
             else Path(__file__).resolve().parents[1])
    uvicorn.run(
        create_desktop_app(),
        host=args.host,
        port=args.port,
        log_level=args.log_level,
        access_log=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
