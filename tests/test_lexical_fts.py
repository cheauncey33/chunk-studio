from __future__ import annotations

import json
from pathlib import Path
import sys

from fastapi import BackgroundTasks

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app import db, lexical
from app.models import VectorSearchRequest
from app.routers import search as search_router
from app.storage import repositories


def setup_isolated_db(monkeypatch, tmp_path) -> None:
    if db._conn is not None:
        db._conn.close()
    monkeypatch.setattr(db.config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db.config, "DB_PATH", tmp_path / "chunkstudio.db")
    monkeypatch.setattr(db, "_conn", None)
    monkeypatch.setattr(lexical, "_schema_ready", False)
    monkeypatch.setattr(lexical, "_schema_db_path", "")
    db.init_db()


def teardown_isolated_db(monkeypatch) -> None:
    if db._conn is not None:
        db._conn.close()
    monkeypatch.setattr(db, "_conn", None)
    monkeypatch.setattr(lexical, "_schema_ready", False)
    monkeypatch.setattr(lexical, "_schema_db_path", "")


def insert_chunk(*, status: str = "approved") -> None:
    metadata = {
        "standard_no": "GB 20052-2024",
        "content_type": "table",
        "table_no": "1",
        "table_title": "10 kV油浸式变压器能效等级",
        "table_columns": ["额定容量kVA", "空载损耗W"],
    }
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO files(id,name,path,created_at) VALUES ('f','standard.pdf','files/f.pdf','now')"
        )
        conn.execute(
            """INSERT INTO chunks(
                   id,file_id,page,bbox,text,business_metadata,status,created_at,updated_at
               ) VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                "c", "f", 1, "{}",
                "<table><tr><td>200 kVA</td><td>空载损耗0.215 kW</td></tr></table>",
                json.dumps(metadata, ensure_ascii=False), status, "now", "v1",
            ),
        )


def test_tokens_preserve_domain_identifiers_and_strip_html() -> None:
    tokens = lexical.tokens_for_search(
        "<table><tr><td>S20-M.RL-200/10-NX2 200 kVA 空载损耗</td></tr></table>"
    )

    assert "table" not in tokens
    assert "tr" not in tokens
    assert "s20mrl20010nx2" in tokens
    assert "200kva" in tokens
    assert "空载损耗" in tokens


def test_fts_sync_search_update_and_remove(monkeypatch, tmp_path) -> None:
    setup_isolated_db(monkeypatch, tmp_path)
    try:
        insert_chunk()

        first = lexical.sync_index()
        result = lexical.search("GB 20052-2024 200 kVA 空载损耗", content_type="table")

        assert first["indexed"] == 1
        assert [hit["chunk_id"] for hit in result["hits"]] == ["c"]
        assert "table_columns" in result["hits"][0]["matched_fields"]
        assert lexical.index_status()["pending_chunks"] == 0

        with db.transaction() as conn:
            conn.execute(
                "UPDATE chunks SET text='特殊校验术语', updated_at='v2' WHERE id='c'"
            )
        updated = lexical.sync_index()
        assert updated["indexed"] == 1
        assert lexical.search("特殊校验术语", content_type="table")["hits"][0]["chunk_id"] == "c"

        with db.transaction() as conn:
            conn.execute("UPDATE chunks SET status='rejected', updated_at='v3' WHERE id='c'")
        removed = lexical.sync_index()
        assert removed["removed"] == 1
        assert lexical.search("特殊校验术语", content_type="table")["hits"] == []
        assert lexical.index_status()["fts_rows"] == 0
    finally:
        teardown_isolated_db(monkeypatch)


def test_shadow_run_records_hits_without_changing_search_response(monkeypatch, tmp_path) -> None:
    setup_isolated_db(monkeypatch, tmp_path)
    try:
        insert_chunk()
        lexical.sync_index()

        run_id = lexical.run_shadow(
            "200 kVA 空载损耗",
            {"production": "200 kVA 空载损耗", "keyword": "空载损耗 0.215 kW"},
            ["production-hit"],
        )
        runs = lexical.list_shadow_runs()

        assert run_id is not None
        assert len(runs) == 1
        assert runs[0]["status"] == "complete"
        assert runs[0]["payload"]["lexical_hits"][0]["chunk_id"] == "c"
        assert runs[0]["payload"]["overlap_count"] == 0
    finally:
        teardown_isolated_db(monkeypatch)


def test_search_router_schedules_shadow_after_production_result(monkeypatch) -> None:
    response = {
        "query": "query",
        "model": "model",
        "dimension": 1,
        "total_candidates": 1,
        "candidate_count": 1,
        "retrieval_mode": "hybrid_rerank",
        "query_routes": {"production": "query", "keyword": "keyword"},
        "rerank_model": "reranker",
        "degraded": [],
        "hits": [{"chunk_id": "c"}],
    }
    monkeypatch.setattr(search_router.retrieval, "hybrid_search", lambda *args, **kwargs: response)
    monkeypatch.setattr(search_router.lexical, "production_enabled", lambda: False)
    monkeypatch.setattr(search_router.lexical, "shadow_enabled", lambda: True)
    tasks = BackgroundTasks()

    result = search_router.search_chunks(VectorSearchRequest(query="query"), tasks)

    assert result is response
    assert len(tasks.tasks) == 1
    assert tasks.tasks[0].func is lexical.run_shadow


def test_search_router_skips_shadow_when_lexical_is_in_production(monkeypatch) -> None:
    response = {
        "query": "query",
        "model": "model",
        "dimension": 1,
        "total_candidates": 1,
        "candidate_count": 1,
        "retrieval_mode": "dual_rerank",
        "query_routes": {"production": "query", "keyword": "keyword"},
        "rerank_model": "reranker",
        "degraded": [],
        "hits": [{"chunk_id": "c"}],
    }
    monkeypatch.setattr(search_router.retrieval, "hybrid_search", lambda *args, **kwargs: response)
    monkeypatch.setattr(search_router.lexical, "production_enabled", lambda: True)
    monkeypatch.setattr(search_router.lexical, "shadow_enabled", lambda: True)
    tasks = BackgroundTasks()

    result = search_router.search_chunks(VectorSearchRequest(query="query"), tasks)

    assert result is response
    assert tasks.tasks == []


def test_search_router_forwards_injected_query_routes(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_hybrid_search(query, **kwargs):
        captured["query"] = query
        captured["kwargs"] = kwargs
        return {
            "query": query,
            "model": "model",
            "dimension": 1,
            "total_candidates": 0,
            "candidate_count": 0,
            "retrieval_mode": "dense_rerank",
            "query_routes": kwargs.get("query_routes") or {},
            "routes_injected": True,
            "rerank_model": "reranker",
            "degraded": [],
            "hits": [],
        }

    monkeypatch.setattr(search_router.retrieval, "hybrid_search", fake_hybrid_search)
    monkeypatch.setattr(search_router.lexical, "production_enabled", lambda: True)
    tasks = BackgroundTasks()
    routes = {"production": "S20-M.RL-400/10-NX2 400 kVA 空载损耗P0"}

    result = search_router.search_chunks(
        VectorSearchRequest(query=routes["production"], query_routes=routes),
        tasks,
    )

    assert captured["query"] == routes["production"]
    assert captured["kwargs"]["query_routes"] == routes
    assert result["query_routes"] == routes


def test_postgres_lexical_search_uses_content_repository(monkeypatch) -> None:
    class FakeRepository:
        def list_lexical_rows(self, *, content_type, file_ids=None, limit=5000):
            assert content_type == "table"
            assert file_ids == ["f"]
            assert limit >= 1000
            return [
                {
                    "id": "c",
                    "file_id": "f",
                    "file_name": "standard.pdf",
                    "page": 1,
                    "crop_path": "",
                    "crop_object_key": "crops/f/c.png",
                    "text": "200 kVA",
                    "business_metadata": {
                        "content_type": "table",
                        "table_columns": ["rated capacity kVA"],
                    },
                    "source_trace": {},
                }
            ]

    repository = FakeRepository()
    monkeypatch.setattr(lexical.config, "DATABASE_BACKEND", "postgres")
    monkeypatch.setattr(repositories, "get_content_repository", lambda: repository)
    monkeypatch.setattr(repositories, "get_content_write_repository", lambda: None)

    result = lexical.search(
        "rated capacity kVA",
        content_type="table",
        top_k=3,
        sync=False,
        file_ids=["f"],
    )

    assert result["sync"] == {"backend": "postgres"}
    assert result["hits"][0]["chunk_id"] == "c"
    assert result["hits"][0]["crop_url"] == "/api/chunks/c/crop"
