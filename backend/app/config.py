"""Central config and path setup for Chunk Studio backend.

All runtime data lives under one project root so a single tar/zip is a complete
backup. Paths are resolved to absolute at import time. DB stores forward-slash
relative paths for portability; this module is the single source of truth for
resolving them back to absolute.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# --- UTF-8 enforcement (Windows cp936 console would crash on Chinese print) ---
# Set early so any later import that prints Chinese is safe.
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass  # already utf-8 or not a tty

# --- Project root & data dir ---
# backend/app/config.py -> backend/ -> chunk-studio/
BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_DIR.parent

# Keep the repository-local directory as the default, while allowing maintenance
# scripts and tests to operate on an isolated copy of runtime data.
_DATA_DIR_RAW = os.environ.get("CHUNK_STUDIO_DATA_DIR", "./data")
_data_dir = Path(_DATA_DIR_RAW)
DATA_DIR = (_data_dir if _data_dir.is_absolute() else BACKEND_DIR / _data_dir).resolve()

FILES_DIR = DATA_DIR / "files"
CROPS_DIR = DATA_DIR / "crops"
PAGECACHE_DIR = DATA_DIR / "pagecache"
PARSES_DIR = DATA_DIR / "parses"
DB_PATH = DATA_DIR / "chunkstudio.db"

# --- Deployable service backends ---
# SQLite/local files remain the default so existing checkouts keep working.
# Production deployments opt in explicitly to PostgreSQL/pgvector, Redis and
# object storage; these values are configuration boundaries, not connection
# side effects during module import.
DATABASE_BACKEND = os.environ.get(
    "CHUNK_STUDIO_DATABASE_BACKEND",
    os.environ.get("DATABASE_BACKEND", "sqlite"),
).strip().casefold() or "sqlite"
DATABASE_URL = (
    os.environ.get("CHUNK_STUDIO_DATABASE_URL")
    or os.environ.get("DATABASE_URL")
    or ""
).strip()
VECTOR_BACKEND = os.environ.get(
    "CHUNK_STUDIO_VECTOR_BACKEND",
    os.environ.get(
        "VECTOR_BACKEND",
        "pgvector" if DATABASE_BACKEND in {"postgres", "postgresql"} else "sqlite",
    ),
).strip().casefold() or "sqlite"
REDIS_URL = (
    os.environ.get("CHUNK_STUDIO_REDIS_URL")
    or os.environ.get("REDIS_URL")
    or ""
).strip()
OBJECT_STORAGE_BACKEND = os.environ.get(
    "CHUNK_STUDIO_OBJECT_STORAGE_BACKEND",
    os.environ.get("OBJECT_STORAGE_BACKEND", "local"),
).strip().casefold() or "local"
OBJECT_STORAGE_BUCKET = (
    os.environ.get("CHUNK_STUDIO_OBJECT_STORAGE_BUCKET")
    or os.environ.get("OBJECT_STORAGE_BUCKET")
    or ""
).strip()
OBJECT_STORAGE_ENDPOINT_URL = (
    os.environ.get("CHUNK_STUDIO_OBJECT_STORAGE_ENDPOINT_URL")
    or os.environ.get("OBJECT_STORAGE_ENDPOINT_URL")
    or ""
).strip()
OBJECT_STORAGE_REGION = (
    os.environ.get("CHUNK_STUDIO_OBJECT_STORAGE_REGION")
    or os.environ.get("OBJECT_STORAGE_REGION")
    or "us-east-1"
).strip()
OBJECT_STORAGE_ACCESS_KEY = (
    os.environ.get("CHUNK_STUDIO_OBJECT_STORAGE_ACCESS_KEY")
    or os.environ.get("OBJECT_STORAGE_ACCESS_KEY")
    or ""
).strip()
OBJECT_STORAGE_SECRET_KEY = (
    os.environ.get("CHUNK_STUDIO_OBJECT_STORAGE_SECRET_KEY")
    or os.environ.get("OBJECT_STORAGE_SECRET_KEY")
    or ""
).strip()
# API content writes remain on SQLite during the staged migration. Worker-owned
# parse/chunk/OCR mutations, audit/init paths, and embedding writes switch with
# DATABASE_BACKEND=postgres; API CRUD and legacy lexical/settings paths remain
# on the next migration boundary.
CONTENT_READ_BACKEND = os.environ.get(
    "CHUNK_STUDIO_CONTENT_READ_BACKEND",
    os.environ.get("CONTENT_READ_BACKEND", "sqlite"),
).strip().casefold() or "sqlite"

# The local mode supplies one explicit current-user identity for development. A deployed
# service must switch to an upstream-authenticated mode before it can use
# workspace-aware repositories (the actual OIDC/JWT adapter is a later slice).
AUTH_MODE = os.environ.get(
    "CHUNK_STUDIO_AUTH_MODE",
    os.environ.get("AUTH_MODE", "dev"),
).strip().casefold() or "dev"
TRUST_PROXY_AUTH = os.environ.get(
    "CHUNK_STUDIO_TRUST_PROXY_AUTH",
    os.environ.get("TRUST_PROXY_AUTH", "0"),
).strip().casefold() in {"1", "true", "yes", "on"}
DEFAULT_WORKSPACE_ID = (
    os.environ.get("CHUNK_STUDIO_DEFAULT_WORKSPACE_ID")
    or os.environ.get("DEFAULT_WORKSPACE_ID")
    # Backward-compatible reads for local .env files created during the first
    # multi-user slice. New configuration should use workspace_id terminology.
    or os.environ.get("CHUNK_STUDIO_DEFAULT_TENANT_ID")
    or os.environ.get("DEFAULT_TENANT_ID")
    or "local-workspace"
).strip() or "local-workspace"
DEFAULT_USER_ID = (
    os.environ.get("CHUNK_STUDIO_DEFAULT_USER_ID")
    or os.environ.get("DEFAULT_USER_ID")
    or "local-user"
).strip() or "local-user"


def _positive_int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, str(default))))
    except ValueError:
        return default


USER_RATE_LIMIT_PER_MINUTE = _positive_int_env(
    "CHUNK_STUDIO_USER_RATE_LIMIT_PER_MINUTE",
    60,
)
AGENT_CONCURRENCY_LIMIT = _positive_int_env(
    "CHUNK_STUDIO_AGENT_CONCURRENCY_LIMIT",
    2,
)
AGENT_LEASE_SECONDS = _positive_int_env(
    "CHUNK_STUDIO_AGENT_LEASE_SECONDS",
    300,
)
RUN_IN_PROCESS_WORKER = os.environ.get(
    "CHUNK_STUDIO_RUN_IN_PROCESS_WORKER",
    "1",
).strip().casefold() in {"1", "true", "yes", "on"}

HOST = "127.0.0.1"
PORT = 8000


def validate_deployment_config() -> None:
    """Reject unsafe/incomplete production backend combinations early."""
    if DATABASE_BACKEND in {"postgres", "postgresql"} and not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL is required when CHUNK_STUDIO_DATABASE_BACKEND=postgres"
        )
    if VECTOR_BACKEND in {"pgvector", "postgres", "postgresql"} and not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL is required when CHUNK_STUDIO_VECTOR_BACKEND=pgvector"
        )
    if CONTENT_READ_BACKEND in {"postgres", "postgresql"} and not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL is required when CHUNK_STUDIO_CONTENT_READ_BACKEND=postgres"
        )
    if AUTH_MODE in {"trusted_proxy", "proxy"} and not TRUST_PROXY_AUTH:
        raise RuntimeError(
            "TRUST_PROXY_AUTH must be enabled for trusted_proxy authentication"
        )


def deployment_config() -> dict[str, str | bool]:
    """Return non-secret backend choices for health checks and diagnostics."""
    return {
        "database_backend": DATABASE_BACKEND,
        "vector_backend": VECTOR_BACKEND,
        "redis_configured": bool(REDIS_URL),
        "object_storage_backend": OBJECT_STORAGE_BACKEND,
        "object_storage_configured": bool(OBJECT_STORAGE_BUCKET),
        "content_read_backend": CONTENT_READ_BACKEND,
        "auth_mode": AUTH_MODE,
        "in_process_worker": RUN_IN_PROCESS_WORKER,
    }


def ensure_dirs() -> None:
    """Create data subdirs. Called at app startup."""
    for d in (DATA_DIR, FILES_DIR, CROPS_DIR, PAGECACHE_DIR, PARSES_DIR):
        d.mkdir(parents=True, exist_ok=True)


def to_rel(path: Path) -> str:
    """Store a path in DB as a portable forward-slash string relative to DATA_DIR."""
    try:
        return path.relative_to(DATA_DIR).as_posix()
    except ValueError:
        return path.as_posix()


def from_rel(rel: str) -> Path:
    """Resolve a DB-stored relative path back to absolute."""
    p = Path(rel)
    if p.is_absolute():
        return p
    return DATA_DIR / rel
