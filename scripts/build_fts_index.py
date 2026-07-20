"""Build or incrementally refresh the local Jieba-pretokenized FTS5 index."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import db, lexical  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()

    db.init_db()
    result = lexical.sync_index(force=args.force, limit=args.limit)
    print(json.dumps({"sync": result, "status": lexical.index_status()}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
