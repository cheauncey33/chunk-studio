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
from starlette.responses import JSONResponse

from . import config, current_user, db, jobs as job_service, lexical
from .storage import repositories
from .routers import (
    assistants,
    analytics,
    audit,
    auto_chunks,
    chunks,
    embeddings,
    export,
    extract,
    fields,
    files,
    jobs,
    knowledge_bases,
    ocr,
    search,
    settings,
    workspaces,
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


@app.middleware("http")
async def bind_current_user(request, call_next):
    """Resolve identity once so every router/repository sees one request user."""
    if request.url.path in {"/api/health", "/docs", "/openapi.json"}:
        return await call_next(request)
    try:
        user = current_user.current_user_for_headers(request.headers)
    except current_user.CurrentUserError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=401)
    workspace_repository = repositories.get_workspace_repository()
    is_active_member = (
        workspace_repository.is_active_member(
            workspace_id=user.workspace_id,
            user_id=user.user_id,
        )
        if workspace_repository is not None
        else db.is_active_workspace_member(
            workspace_id=user.workspace_id,
            user_id=user.user_id,
        )
    )
    if not is_active_member:
        return JSONResponse(
            {"detail": "current user is not an active workspace member"},
            status_code=403,
        )
    token = current_user.set_current_user(user)
    try:
        request.state.current_user = user
        return await call_next(request)
    finally:
        current_user.reset_current_user(token)


@app.on_event("startup")
async def _startup() -> None:
    config.validate_deployment_config()
    config.ensure_dirs()
    db.init_db()
    lexical.ensure_schema()
    app.state.job_worker = None
    if config.RUN_IN_PROCESS_WORKER:
        app.state.job_worker = asyncio.create_task(job_service.worker_loop())


@app.get("/api/health")
def health():
    return {"ok": True, "deployment": config.deployment_config()}


# API routers (mounted under /api for clarity)
api_prefix = "/api"
for r in (files.router, chunks.router, auto_chunks.router, fields.router, settings.router,
          knowledge_bases.router, assistants.router, audit.router, analytics.router,
          jobs.router, extract.router, ocr.router, export.router, search.router,
          embeddings.router, workspaces.router):
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
