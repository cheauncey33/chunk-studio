"""Build or refresh embeddings for approved chunks."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import db, embeddings  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=embeddings.DEFAULT_MODEL)
    parser.add_argument("--dimension", type=int, default=embeddings.DEFAULT_DIMENSION)
    parser.add_argument("--batch-size", type=int, default=embeddings.MAX_BATCH_SIZE)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--include-table-columns",
        action="store_true",
        help="Ablation (experiment A): add table header columns to the dense document "
             "prefix. Use with --force to rebuild all vectors before evaluating.",
    )
    args = parser.parse_args()

    db.init_db()

    def progress(done: int, total: int, tokens: int) -> None:
        print(json.dumps({"embedded": done, "total": total, "tokens": tokens}))

    result = embeddings.build_embeddings(
        model=args.model,
        dimension=args.dimension,
        batch_size=args.batch_size,
        force=args.force,
        limit=args.limit,
        include_table_columns=args.include_table_columns,
        on_batch=progress,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
