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

HOST = "127.0.0.1"
PORT = 8000


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
