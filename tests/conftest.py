"""Isolate unit tests on the local SQLite profile.

The application default is PostgreSQL/pgvector. Tests that exercise the
postgres profile spawn a subprocess with a clean environment instead of
inheriting this override.
"""
from __future__ import annotations

import os

os.environ["CHUNK_STUDIO_DEPLOYMENT_PROFILE"] = "local"
os.environ["CHUNK_STUDIO_DATABASE_BACKEND"] = "sqlite"
os.environ["CHUNK_STUDIO_CONTENT_READ_BACKEND"] = "sqlite"
os.environ["CHUNK_STUDIO_VECTOR_BACKEND"] = "sqlite"
os.environ["CHUNK_STUDIO_OBJECT_STORAGE_BACKEND"] = "local"
os.environ["CHUNK_STUDIO_RUN_IN_PROCESS_WORKER"] = "1"
os.environ["CHUNK_STUDIO_REQUIRE_DISTRIBUTED_RUNTIME"] = "0"
