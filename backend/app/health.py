"""Deployment-aware liveness dependency checks."""
from __future__ import annotations

import time
from typing import Callable

from . import config, db


Check = Callable[[], None]


def _check_database() -> None:
    if config.DATABASE_BACKEND not in {"postgres", "postgresql"}:
        db.get_conn().execute("SELECT 1").fetchone()
        return
    try:
        import psycopg
    except ModuleNotFoundError as exc:
        raise RuntimeError("psycopg is not installed") from exc
    with psycopg.connect(config.DATABASE_URL, connect_timeout=2) as conn:
        conn.execute("SELECT 1").fetchone()


def _check_redis() -> None:
    try:
        import redis
    except ModuleNotFoundError as exc:
        raise RuntimeError("redis-py is not installed") from exc
    client = redis.Redis.from_url(
        config.REDIS_URL,
        socket_connect_timeout=2,
        socket_timeout=2,
    )
    try:
        if not client.ping():
            raise RuntimeError("Redis ping returned false")
    finally:
        client.close()


def _check_object_storage() -> None:
    if config.OBJECT_STORAGE_BACKEND == "local":
        root = config.DATA_DIR / "objects"
        root.mkdir(parents=True, exist_ok=True)
        if not root.is_dir():
            raise RuntimeError("local object storage is unavailable")
        return
    try:
        import boto3
        from botocore.config import Config
    except ModuleNotFoundError as exc:
        raise RuntimeError("boto3 is not installed") from exc
    client = boto3.client(
        "s3",
        endpoint_url=config.OBJECT_STORAGE_ENDPOINT_URL or None,
        region_name=config.OBJECT_STORAGE_REGION,
        aws_access_key_id=config.OBJECT_STORAGE_ACCESS_KEY or None,
        aws_secret_access_key=config.OBJECT_STORAGE_SECRET_KEY or None,
        config=Config(connect_timeout=2, read_timeout=2, retries={"max_attempts": 0}),
    )
    client.head_bucket(Bucket=config.OBJECT_STORAGE_BUCKET)


def run_checks(checks: list[tuple[str, Check]]) -> tuple[bool, dict[str, dict[str, object]]]:
    details: dict[str, dict[str, object]] = {}
    ready = True
    for name, check in checks:
        started = time.perf_counter()
        try:
            check()
            status = "ok"
            error_type = ""
        except Exception as exc:
            ready = False
            status = "error"
            error_type = type(exc).__name__
        details[name] = {
            "status": status,
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
        }
        if error_type:
            details[name]["error_type"] = error_type
    return ready, details


def readiness_checks() -> tuple[bool, dict[str, dict[str, object]]]:
    checks: list[tuple[str, Check]] = [("database", _check_database)]
    if config.REDIS_URL:
        checks.append(("redis", _check_redis))
    if config.OBJECT_STORAGE_BACKEND in {"local", "s3", "minio"}:
        checks.append(("object_storage", _check_object_storage))
    return run_checks(checks)
