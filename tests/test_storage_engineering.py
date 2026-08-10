from __future__ import annotations

from pathlib import Path
import sys

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app import artifacts, config, current_user, embeddings, retrieval
from app.storage import object_store, repositories, vector_store
from app import runtime


def test_local_current_user_is_explicit_and_workspace_scoped() -> None:
    identity = current_user.local_current_user()

    assert identity.user_id == config.DEFAULT_USER_ID
    assert identity.workspace_id == config.DEFAULT_WORKSPACE_ID
    assert not identity.authenticated
    assert current_user.workspace_matches(identity, config.DEFAULT_WORKSPACE_ID)
    assert not current_user.workspace_matches(identity, "another-workspace")


def test_trusted_proxy_current_user_requires_operator_opt_in(monkeypatch) -> None:
    monkeypatch.setattr(config, "AUTH_MODE", "trusted_proxy")
    monkeypatch.setattr(config, "TRUST_PROXY_AUTH", False)
    with pytest.raises(current_user.CurrentUserError):
        current_user.from_headers({
            "x-auth-user": "u1",
            "x-auth-workspace": "workspace-1",
        })

    monkeypatch.setattr(config, "TRUST_PROXY_AUTH", True)
    identity = current_user.from_headers({
        "X-Auth-User": "u1",
        "X-Auth-Workspace": "workspace-1",
        "X-Auth-Roles": "analyst, reviewer",
    })
    assert identity.authenticated
    assert identity.user_id == "u1"
    assert identity.workspace_id == "workspace-1"
    assert identity.can("analyst")


def test_sqlite_vector_store_preserves_legacy_behavior(monkeypatch) -> None:
    seen: dict = {}

    def fake_search(query, query_vector, **kwargs):
        seen.update(query=query, query_vector=query_vector, kwargs=kwargs)
        return {"total_candidates": 0, "hits": []}

    monkeypatch.setattr(embeddings, "vector_search_by_vector", fake_search)
    result = vector_store.SQLiteVectorStore().search_by_vector(
        "query",
        [1.0],
        workspace_id="workspace-1",
        file_ids=["file-1"],
    )

    assert result == {"total_candidates": 0, "hits": []}
    assert seen["kwargs"]["file_ids"] == ["file-1"]
    assert seen["kwargs"]["workspace_id"] == "workspace-1"


def test_pgvector_schema_is_workspace_scoped_and_indexed() -> None:
    statements = vector_store.pgvector_schema_sql(dimension=3)
    schema = "\n".join(statements)

    assert "CREATE EXTENSION IF NOT EXISTS vector" in schema
    assert "workspace_id TEXT NOT NULL" in schema
    assert "embedding vector(3)" in schema
    assert "USING hnsw" in schema
    assert "PRIMARY KEY (workspace_id, chunk_id, model, dimension)" in schema
    assert "crop_object_key TEXT NOT NULL DEFAULT ''" in schema


def test_content_schema_has_artifact_metadata_and_scope_indexes() -> None:
    schema = "\n".join(repositories.postgres_content_schema_sql())

    assert "markdown_object_key TEXT NOT NULL DEFAULT ''" in schema
    assert "markdown_sha256 TEXT NOT NULL DEFAULT ''" in schema
    assert "crop_object_key TEXT NOT NULL DEFAULT ''" in schema
    assert "ix_kb_files_scope_kb" in schema


def test_vector_store_factory_requires_dsn_for_pgvector(monkeypatch) -> None:
    monkeypatch.setattr(config, "VECTOR_BACKEND", "pgvector")
    monkeypatch.setattr(config, "DATABASE_URL", "")

    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        vector_store.get_vector_store()


def test_hybrid_search_forwards_workspace_scope_to_vector_adapter() -> None:
    seen_workspaces: list[str] = []

    def vector_searcher(_query: str, _vector: list[float], **kwargs):
        seen_workspaces.append(kwargs["workspace_id"])
        return {"total_candidates": 0, "hits": []}

    result = retrieval.hybrid_search(
        "query",
        query_routes={"production": "query"},
        batch_embedder=lambda queries, **kwargs: [
            [1.0] * embeddings.DEFAULT_DIMENSION for _ in queries
        ],
        vector_searcher=vector_searcher,
        reranker=lambda query, documents, top_n: [],
        lexical_enabled=False,
        workspace_id="workspace-1",
    )

    assert result["hits"] == []
    assert seen_workspaces == ["workspace-1", "workspace-1"]


def test_local_object_store_round_trips_and_rejects_traversal(tmp_path) -> None:
    store = object_store.LocalObjectStore(tmp_path / "objects")
    info = store.put_bytes("workspace-1/files/a.pdf", b"pdf", content_type="application/pdf")

    assert info.size == 3
    assert info.sha256
    assert store.exists("workspace-1/files/a.pdf")
    assert store.get_bytes("workspace-1/files/a.pdf") == b"pdf"
    with pytest.raises(ValueError):
        store.put_bytes("../outside", b"bad")
    store.delete("workspace-1/files/a.pdf")
    assert not store.exists("workspace-1/files/a.pdf")


def test_artifact_helpers_prefer_object_store_and_keep_local_fallback(monkeypatch, tmp_path) -> None:
    local = tmp_path / "legacy.md"
    local.write_text("legacy", encoding="utf-8")
    store = object_store.LocalObjectStore(tmp_path / "objects")
    store.put_bytes("workspace-1/markdown.md", b"shared")
    monkeypatch.setattr(config, "OBJECT_STORAGE_BACKEND", "local")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "PARSES_DIR", tmp_path / "parses")
    monkeypatch.setattr(object_store, "get_object_store", lambda: store)

    assert artifacts.read_artifact("legacy.md", "workspace-1/markdown.md") == b"shared"
    assert artifacts.read_artifact("legacy.md", "missing/key") == b"legacy"
    assert artifacts.parse_artifact_key("workspace-1", "file-1", "parse-1", "markdown").endswith("/markdown.md")


def test_runtime_local_lock_idempotency_stream_and_limits() -> None:
    lock = runtime.ConversationLock("workspace-1:chat-1")
    with lock:
        blocked = runtime.ConversationLock("workspace-1:chat-1")
        assert not blocked.acquire()

    idem = runtime.IdempotencyStore()
    assert idem.reserve("workspace-1:user-1", "request-1")
    assert not idem.reserve("workspace-1:user-1", "request-1")
    idem.save_response("workspace-1:user-1", "request-1", {"answer": "ok"})
    assert idem.get_response("workspace-1:user-1", "request-1") == {"answer": "ok"}

    events = runtime.StreamEventStore(max_events=10)
    first = events.append("workspace-1:chat-1", {"type": "token", "text": "a"})
    second = events.append("workspace-1:chat-1", {"type": "done"})
    assert [event["id"] for event in events.read_since("workspace-1:chat-1", first)] == [second]

    limiter = runtime.RateLimiter()
    assert limiter.allow("user-1", limit=2, window_seconds=60)[0]
    assert limiter.allow("user-1", limit=2, window_seconds=60)[0]
    assert not limiter.allow("user-1", limit=2, window_seconds=60)[0]


def test_object_store_factory_uses_local_backend(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(config, "OBJECT_STORAGE_BACKEND", "local")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    store = object_store.get_object_store()
    assert isinstance(store, object_store.LocalObjectStore)


def test_postgres_repository_schema_has_scope_and_skip_locked_queue() -> None:
    schema = "\n".join(repositories.postgres_schema_sql())

    assert "CREATE TABLE IF NOT EXISTS users" in schema
    assert "CREATE TABLE IF NOT EXISTS workspaces" in schema
    assert "CREATE TABLE IF NOT EXISTS workspace_members" in schema
    assert "workspace_id TEXT NOT NULL" in schema
    assert "config_snapshot JSONB" in schema
    assert "locked_until TIMESTAMPTZ" in schema
    assert "available_at TIMESTAMPTZ" in schema


def test_postgres_job_claim_uses_skip_locked_and_leases(monkeypatch) -> None:
    class Result:
        def __init__(self, rows):
            self.rows = rows

        def fetchall(self):
            return self.rows

        def fetchone(self):
            return self.rows[0] if self.rows else None

    class Connection:
        def __init__(self):
            self.sql: list[str] = []

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, statement, _params):
            self.sql.append(statement)
            if "SELECT * FROM jobs" in statement:
                return Result([{
                    "id": "job-1",
                    "status": "queued",
                    "result": {},
                    "attempts": 0,
                    "max_attempts": 2,
                }])
            return Result([{
                "id": "job-1",
                "status": "running",
                "result": {},
                "attempts": 1,
                "max_attempts": 2,
            }])

    connection = Connection()
    monkeypatch.setattr(
        repositories.PostgresJobRepository,
        "_connect",
        lambda _self: connection,
    )
    claimed = repositories.PostgresJobRepository("postgresql://test").claim_pending(
        "worker-1", job_types={"ocr"}
    )

    assert claimed[0]["status"] == "running"
    assert "FOR UPDATE SKIP LOCKED" in connection.sql[0]
    assert "locked_until" in connection.sql[1]


def test_postgres_workspace_repository_scopes_active_membership(monkeypatch) -> None:
    class Result:
        def fetchone(self):
            return {
                "id": "workspace-1",
                "name": "Workspace 1",
                "slug": "workspace-1",
                "status": "active",
                "role": "member",
                "member_status": "active",
            }

        def fetchall(self):
            return [{"id": "user-1", "display_name": "User 1", "role": "member", "status": "active"}]

    class Connection:
        def __init__(self):
            self.sql: list[str] = []
            self.params: list[tuple] = []

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, statement, params):
            self.sql.append(statement)
            self.params.append(params)
            return Result()

    connection = Connection()
    monkeypatch.setattr(
        repositories.PostgresWorkspaceRepository,
        "_connect",
        lambda _self: connection,
    )
    repository = repositories.PostgresWorkspaceRepository("postgresql://test")

    assert repository.is_active_member(workspace_id="workspace-1", user_id="user-1")
    assert repository.list_members(workspace_id="workspace-1")[0]["id"] == "user-1"
    assert connection.params[0] == ("workspace-1", "user-1")
    assert "w.status='active'" in connection.sql[0]
    assert "wm.status='active'" in connection.sql[0]
    assert "?" not in connection.sql[1]


def test_postgres_repository_factory_is_opt_in(monkeypatch) -> None:
    monkeypatch.setattr(config, "DATABASE_BACKEND", "sqlite")
    assert repositories.get_chat_repository() is None
    assert repositories.get_job_repository() is None

    monkeypatch.setattr(config, "DATABASE_BACKEND", "postgres")
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://example.invalid/db")
    assert isinstance(repositories.get_chat_repository(), repositories.PostgresChatRepository)
    assert isinstance(repositories.get_job_repository(), repositories.PostgresJobRepository)
    assert isinstance(repositories.get_workspace_repository(), repositories.PostgresWorkspaceRepository)

    monkeypatch.setattr(config, "CONTENT_READ_BACKEND", "postgres")
    assert isinstance(repositories.get_content_repository(), repositories.PostgresContentRepository)

    writer = repositories.get_content_write_repository()
    assert isinstance(writer, repositories.PostgresContentRepository)
