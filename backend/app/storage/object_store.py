"""Object storage boundary for uploaded files and derived artifacts.

The application may keep using local disk in development, but callers should
address an object by a stable key rather than by a machine-local path.  S3 and
MinIO both use the S3 adapter below.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Any, Protocol

from .. import config


@dataclass(frozen=True)
class ObjectInfo:
    key: str
    size: int
    sha256: str
    content_type: str | None = None


class ObjectStore(Protocol):
    def put_bytes(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str | None = None,
    ) -> ObjectInfo: ...

    def get_bytes(self, key: str) -> bytes: ...

    def exists(self, key: str) -> bool: ...

    def delete(self, key: str) -> None: ...


def _validate_key(key: str) -> str:
    normalized = str(key or "").replace("\\", "/").strip("/")
    if not normalized:
        raise ValueError("object key must not be blank")
    path = Path(normalized)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("object key must be a relative path")
    return "/".join(path.parts)


def _info(key: str, data: bytes, content_type: str | None) -> ObjectInfo:
    return ObjectInfo(
        key=key,
        size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        content_type=content_type,
    )


@dataclass
class LocalObjectStore:
    """Filesystem implementation with a traversal-safe key boundary."""

    root: Path

    def __post_init__(self) -> None:
        self.root = self.root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> tuple[str, Path]:
        normalized = _validate_key(key)
        path = (self.root / normalized).resolve()
        if self.root not in path.parents:
            raise ValueError("object key escapes object storage root")
        return normalized, path

    def put_bytes(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str | None = None,
    ) -> ObjectInfo:
        normalized, path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".part")
        temporary.write_bytes(data)
        temporary.replace(path)
        return _info(normalized, data, content_type)

    def get_bytes(self, key: str) -> bytes:
        _, path = self._path(key)
        return path.read_bytes()

    def exists(self, key: str) -> bool:
        _, path = self._path(key)
        return path.is_file()

    def delete(self, key: str) -> None:
        _, path = self._path(key)
        path.unlink(missing_ok=True)


class S3ObjectStore:
    """S3-compatible implementation; the client is created lazily."""

    def __init__(
        self,
        *,
        bucket: str,
        endpoint_url: str = "",
        region: str = "us-east-1",
        access_key: str = "",
        secret_key: str = "",
        client: Any | None = None,
    ) -> None:
        self.bucket = bucket.strip()
        if not self.bucket:
            raise ValueError("object storage bucket is required")
        self.endpoint_url = endpoint_url.strip() or None
        self.region = region.strip() or "us-east-1"
        self.access_key = access_key
        self.secret_key = secret_key
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            try:
                import boto3
            except ModuleNotFoundError as exc:
                raise RuntimeError(
                    "S3 object storage requires boto3; install the runtime dependency"
                ) from exc
            self._client = boto3.client(
                "s3",
                endpoint_url=self.endpoint_url,
                region_name=self.region,
                aws_access_key_id=self.access_key or None,
                aws_secret_access_key=self.secret_key or None,
            )
        return self._client

    def put_bytes(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str | None = None,
    ) -> ObjectInfo:
        normalized = _validate_key(key)
        kwargs: dict[str, Any] = {
            "Bucket": self.bucket,
            "Key": normalized,
            "Body": data,
        }
        if content_type:
            kwargs["ContentType"] = content_type
        self.client.put_object(**kwargs)
        return _info(normalized, data, content_type)

    def get_bytes(self, key: str) -> bytes:
        normalized = _validate_key(key)
        response = self.client.get_object(Bucket=self.bucket, Key=normalized)
        body = response["Body"]
        return body.read() if hasattr(body, "read") else bytes(body)

    def exists(self, key: str) -> bool:
        normalized = _validate_key(key)
        try:
            self.client.head_object(Bucket=self.bucket, Key=normalized)
        except Exception as exc:
            error_code = str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))
            if error_code in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise
        return True

    def delete(self, key: str) -> None:
        normalized = _validate_key(key)
        self.client.delete_object(Bucket=self.bucket, Key=normalized)


def get_object_store() -> ObjectStore:
    backend = config.OBJECT_STORAGE_BACKEND
    if backend == "local":
        return LocalObjectStore(config.DATA_DIR / "objects")
    if backend in {"s3", "minio"}:
        return S3ObjectStore(
            bucket=config.OBJECT_STORAGE_BUCKET,
            endpoint_url=config.OBJECT_STORAGE_ENDPOINT_URL,
            region=config.OBJECT_STORAGE_REGION,
            access_key=config.OBJECT_STORAGE_ACCESS_KEY,
            secret_key=config.OBJECT_STORAGE_SECRET_KEY,
        )
    raise RuntimeError(f"unsupported object storage backend: {backend}")
