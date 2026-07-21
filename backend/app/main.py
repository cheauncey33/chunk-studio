"""Chunk Studio FastAPI app.

- Enforces UTF-8 / data dirs on import.
- Mounts routers, serves crop images, optionally serves built frontend SPA.
- Binds 127.0.0.1 (loopback only) — single-user local tool.
"""
from __future__ import annotations

from pathlib import Path
import asyncio

# Load .env (PYTHONUTF8 / PYTHONIOENCODING) so the user doesn't have to set
# env vars per-shell. Must happen before config import, since config reconfigures
# stdout/stderr to utf-8 at import time.
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import config, db, jobs as job_service, lexical
from .routers import (
    assistants,
    audit,
    auto_chunks,
    chunks,
    export,
    extract,
    fields,
    files,
    jobs,
    knowledge_bases,
    ocr,
    search,
    settings,
)

# Ensure data dirs exist before StaticFiles mounts reference them (mounts happen
# at import time, before the startup event fires).
config.ensure_dirs()

app = FastAPI(title="Chunk Studio", version="0.1.0")

# CORS for Vite dev server (:5173). In prod the SPA is served from this app so
# CORS is moot, but harmless to leave on.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def _startup() -> None:
    config.ensure_dirs()
    db.init_db()
    lexical.ensure_schema()
    app.state.job_worker = asyncio.create_task(job_service.worker_loop())


@app.get("/api/health")
def health():
    return {"ok": True}


# API routers (mounted under /api for clarity)
api_prefix = "/api"
for r in (files.router, chunks.router, auto_chunks.router, fields.router, settings.router,
          knowledge_bases.router, assistants.router, audit.router,
          jobs.router, extract.router, ocr.router, export.router, search.router):
    app.include_router(r, prefix=api_prefix)


# Crop images served by chunk crop_url (e.g. /crops/<file>.png)
app.mount("/crops", StaticFiles(directory=str(config.CROPS_DIR)), name="crops")


# --- SPA fallback: serve built frontend if present ---
_FRONTEND_DIST = config.PROJECT_ROOT / "frontend" / "dist"
if _FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=str(_FRONTEND_DIST / "assets")), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        idx = _FRONTEND_DIST / "index.html"
        if full_path and (_FRONTEND_DIST / full_path).is_file():
            return FileResponse(_FRONTEND_DIST / full_path)
        return FileResponse(idx)
