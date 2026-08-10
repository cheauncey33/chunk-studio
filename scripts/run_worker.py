"""Run a dedicated local worker process for selected job types.

Examples:
  ``uv run python scripts/run_worker.py --types ocr``
  ``uv run python scripts/run_worker.py --types parse,chunk,embed``

The PostgreSQL deployment uses the Repository claim protocol with
``FOR UPDATE SKIP LOCKED``; this SQLite runner is the development-compatible
counterpart while the application data remains on SQLite.
"""
from __future__ import annotations

import argparse
import asyncio

from app import jobs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--types",
        default="ocr,parse,chunk,audit,assistant_init,embed",
        help="comma-separated job types handled by this process",
    )
    args = parser.parse_args()
    types = {item.strip() for item in args.types.split(",") if item.strip()}
    if not types:
        raise SystemExit("--types must contain at least one job type")
    asyncio.run(jobs.worker_loop(types))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
