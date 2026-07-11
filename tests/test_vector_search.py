from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import struct
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app import db, embeddings


def test_vector_search_ranks_approved_chunks_and_returns_evidence(monkeypatch, tmp_path) -> None:
    conn = sqlite3.connect(tmp_path / "search.db")
    conn.row_factory = sqlite3.Row
    conn.executescript(db._SCHEMA)
    conn.execute(
        "INSERT INTO files(id,name,path,created_at) VALUES ('f','standard.pdf','files/f.pdf','now')"
    )
    for chunk_id, status, page in (("best", "approved", 2), ("other", "approved", 3), ("hidden", "rejected", 4)):
        conn.execute(
            """INSERT INTO chunks
               (id,file_id,page,bbox,crop_path,text,business_metadata,source_trace,status,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                chunk_id,
                "f",
                page,
                "{}",
                f"crops/{chunk_id}.png",
                f"text {chunk_id}",
                json.dumps({"standard_no": "GB/T 1-2024", "section": str(page)}),
                json.dumps({"page_start": page, "page_end": page}),
                status,
                "now",
                "now",
            ),
        )
    for chunk_id, vector in (("best", [1.0, 0.0]), ("other", [0.0, 1.0]), ("hidden", [1.0, 0.0])):
        conn.execute(
            """INSERT INTO chunk_embeddings
               (chunk_id,model,dimension,text_sha256,embedding,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?)""",
            (chunk_id, "test-model", 2, "hash", struct.pack("<2f", *vector), "now", "now"),
        )
    conn.commit()
    monkeypatch.setattr(db, "_conn", conn)

    result = embeddings.vector_search(
        "query",
        top_k=2,
        model="test-model",
        dimension=2,
        query_embedder=lambda query, **kwargs: [1.0, 0.0],
    )

    assert result["total_candidates"] == 2
    assert [hit["chunk_id"] for hit in result["hits"]] == ["best", "other"]
    assert result["hits"][0]["score"] == 1.0
    assert result["hits"][0]["source_trace"] == {"page_start": 2, "page_end": 2}
    assert result["hits"][0]["crop_url"] == "/crops/best.png"
    conn.close()
