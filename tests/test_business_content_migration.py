from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))

from app import db
import migrate_business_content_to_postgres as migration


def test_business_rows_are_workspace_scoped(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(db.config, "DATA_DIR", tmp_path)
    db_path = tmp_path / "chunkstudio.db"
    monkeypatch.setattr(db.config, "DB_PATH", db_path)
    monkeypatch.setattr(db, "_conn", None)
    db.init_db()

    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO workspaces(id,name,slug,status,created_at,updated_at) VALUES ('other-ws','Other','other','active','now','now')"
        )
        conn.execute(
            "INSERT INTO files(id,workspace_id,name,path,created_at) VALUES ('f-local','local-workspace','local.pdf','files/local.pdf','now')"
        )
        conn.execute(
            "INSERT INTO files(id,workspace_id,name,path,created_at) VALUES ('f-other','other-ws','other.pdf','files/other.pdf','now')"
        )
        conn.execute(
            """INSERT INTO chunks(id,workspace_id,file_id,page,bbox,text,status,created_at,updated_at)
               VALUES ('c-local','local-workspace','f-local',1,'{}','local','approved','now','now')"""
        )
        conn.execute(
            """INSERT INTO chunks(id,workspace_id,file_id,page,bbox,text,status,created_at,updated_at)
               VALUES ('c-other','other-ws','f-other',1,'{}','other','approved','now','now')"""
        )
        conn.execute(
            """INSERT INTO document_parses(id,workspace_id,file_id,status,created_at,updated_at)
               VALUES ('p-local','local-workspace','f-local','done','now','now')"""
        )
        conn.execute(
            """INSERT INTO document_parses(id,workspace_id,file_id,status,created_at,updated_at)
               VALUES ('p-other','other-ws','f-other','done','now','now')"""
        )
        conn.execute(
            """INSERT INTO knowledge_bases
               (id,workspace_id,name,description,status,is_default,parser_config,retrieval_config,
                manual_rules,few_shot_rules,created_at,updated_at)
               VALUES ('kb-local','local-workspace','Local KB','','active',1,'{}','{}','{}','{}','now','now')"""
        )
        conn.execute(
            """INSERT INTO knowledge_bases
               (id,workspace_id,name,description,status,is_default,parser_config,retrieval_config,
                manual_rules,few_shot_rules,created_at,updated_at)
               VALUES ('kb-other','other-ws','Other KB','','active',0,'{}','{}','{}','{}','now','now')"""
        )
        conn.execute(
            """INSERT INTO knowledge_base_files
               (knowledge_base_id,file_id,workspace_id,created_at)
               VALUES ('kb-local','f-local','local-workspace','now')"""
        )
        conn.execute(
            """INSERT INTO audit_assistants
               (id,workspace_id,name,description,status,created_at,updated_at)
               VALUES ('as-local','local-workspace','Local Assistant','','active','now','now')"""
        )
        conn.execute(
            """INSERT INTO assistant_versions
               (id,assistant_id,version,name,status,created_at)
               VALUES ('av-local','as-local',1,'v1','active','now')"""
        )
        conn.execute(
            """INSERT INTO assistant_knowledge_bases
               (assistant_id,knowledge_base_id,workspace_id)
               VALUES ('as-local','kb-local','local-workspace')"""
        )

    rows = migration._rows("local-workspace")

    assert {row["id"] for row in rows["files"]} == {"f-local"}
    assert {row["id"] for row in rows["chunks"]} == {"c-local"}
    assert {row["id"] for row in rows["document_parses"]} == {"p-local"}
    assert "kb-local" in {row["id"] for row in rows["knowledge_bases"]}
    assert "kb-other" not in {row["id"] for row in rows["knowledge_bases"]}
    assert "as-local" in {row["id"] for row in rows["audit_assistants"]}
    assert all(row["workspace_id"] == "local-workspace" for row in rows["audit_assistants"])
    assert "as-local" in {row["assistant_id"] for row in rows["assistant_knowledge_bases"]}
    assert all(row["workspace_id"] == "local-workspace" for row in rows["assistant_knowledge_bases"])


def test_content_schema_keeps_object_key_columns_and_scope_indexes() -> None:
    ddl = "\n".join(migration.postgres_content_schema_sql())

    assert "markdown_object_key TEXT NOT NULL DEFAULT ''" in ddl
    assert "raw_zip_object_key TEXT NOT NULL DEFAULT ''" in ddl
    assert "ix_chunks_scope_file_page" in ddl
    assert "ix_kb_files_scope_kb" in ddl
