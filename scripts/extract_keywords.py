"""Generate incremental LLM keyword suggestions for approved chunks."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import db, keyword_extraction  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=keyword_extraction.DEFAULT_MODEL)
    parser.add_argument("--batch-size", type=int, default=keyword_extraction.DEFAULT_BATCH_SIZE)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    db.init_db()

    def progress(done: int, total: int) -> None:
        print(json.dumps({"extracted": done, "total": total}), flush=True)

    result = keyword_extraction.extract_keywords(
        model=args.model,
        batch_size=args.batch_size,
        limit=args.limit,
        force=args.force,
        on_batch=progress,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
