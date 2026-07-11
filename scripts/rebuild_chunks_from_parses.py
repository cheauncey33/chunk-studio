"""Rebuild all chunks from completed MinerU parses without reparsing PDFs."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import config, db
from app.models import AutoImageChunkRequest, AutoSectionChunkRequest, AutoTableChunkRequest
from app.routers.auto_chunks import auto_image_chunks, auto_section_chunks, auto_table_chunks


def completed_parses() -> list[dict[str, str]]:
    rows = db.get_conn().execute(
        """
        SELECT f.id AS file_id, f.name, p.id AS parse_id
        FROM files AS f
        JOIN document_parses AS p ON p.file_id = f.id
        WHERE p.status IN ('done', 'completed') AND p.raw_zip_path IS NOT NULL
        ORDER BY f.created_at, p.created_at
        """
    ).fetchall()
    return [dict(row) for row in rows]


def clear_existing_chunks() -> int:
    conn = db.get_conn()
    count = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    with db.transaction() as tx:
        tx.execute("DELETE FROM chunks")

    for crop in config.CROPS_DIR.glob("*"):
        if crop.is_file():
            crop.unlink()
    return count


async def rebuild() -> dict[str, object]:
    db.init_db()
    removed = clear_existing_chunks()
    files = completed_parses()
    summary: dict[str, object] = {"removed_chunks": removed, "files": []}

    for item in files:
        file_id = item["file_id"]
        parse_id = item["parse_id"]
        tables = await auto_table_chunks(
            file_id,
            AutoTableChunkRequest(parse_id=parse_id, dry_run=False, skip_existing=False),
        )
        sections = await auto_section_chunks(
            file_id,
            AutoSectionChunkRequest(
                parse_id=parse_id,
                dry_run=False,
                target_level=2,
                max_chars=8192,
                skip_existing=False,
            ),
        )
        images = await auto_image_chunks(
            file_id,
            AutoImageChunkRequest(parse_id=parse_id, dry_run=False, skip_existing=False),
        )
        file_summary = {
            "file": item["name"],
            "tables": len(tables["created"]),
            "sections": len(sections["created"]),
            "images": len(images["created"]),
        }
        print(json.dumps(file_summary, ensure_ascii=False))
        summary["files"].append(file_summary)

    total = db.get_conn().execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    summary["total_chunks"] = total
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="List eligible parses without changing data.")
    args = parser.parse_args()
    db.init_db()
    if args.dry_run:
        print(json.dumps(completed_parses(), ensure_ascii=False, indent=2))
        return
    print(json.dumps(asyncio.run(rebuild()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
