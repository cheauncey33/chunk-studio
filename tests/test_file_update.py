from __future__ import annotations

from pathlib import Path
import sys

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app import db
from app.routers import files


def test_update_file_name_keeps_pdf_contract(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(db.config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db.config, "DB_PATH", tmp_path / "chunkstudio.db")
    monkeypatch.setattr(db, "_conn", None)
    db.init_db()
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO files(id,name,path,sha,page_count,metadata,created_at)
               VALUES(?,?,?,?,?,?,?)""",
            ("rename_me", "old.pdf", "files/old.pdf", "sha", 1, "{}", "2026-07-25T00:00:00"),
        )

    updated = files.update_file("rename_me", files.FileUpdate(name="renamed.pdf"))
    assert updated["name"] == "renamed.pdf"

    with pytest.raises(HTTPException) as exc:
        files.update_file("rename_me", files.FileUpdate(name="not-a-pdf.txt"))
    assert exc.value.status_code == 422

    db.get_conn().close()
    monkeypatch.setattr(db, "_conn", None)
