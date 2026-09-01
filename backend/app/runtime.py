"""Process/runtime coordination primitives.

Redis is used when configured.  The in-process fallback is intentionally only
for local development and tests; it must not be mistaken for a multi-instance
coordination mechanism.
"""
from __future__ import annotations

from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
from functools import partial
import json
import threading
import time
from typing import Any, Iterator

from . import config


class ConversationBusy(RuntimeError):
    """Another request is currently writing the same conversation."""


class _LocalState:
    def __init__(self) -> None:
        self.mutex = threading.RLock()
        self.locks: dict[str, threading.Lock] = {}
        self.idempotency: dict[str, tuple[float, str]] = {}
        self.streams: dict[str, deque[tuple[str, dict[str, Any]]]] = {}
        self.counters: dict[str, tuple[float, int]] = {}
        self.concurrency: dict[str, tuple[float, int]] = {}
        self.sequence = 0


_LOCAL = _LocalState()


def _redis_client() -> Any | None:
    if not config.REDIS_URL:
        return None
    try:
        import redis
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "REDIS_URL is configured but redis-py is not installed; "
            "install the runtime dependency"
        ) from exc
    return redis.Redis.from_url(config.REDIS_URL, decode_responses=True)


def _key(prefix: str, value: str) -> str:
    return f"chunk-studio:{prefix}:{value}"


class ConversationLock:
    def __init__(
        self,
        key: str,
        *,
        redis_client: Any | None = None,
        timeout_seconds: int = 120,
        blocking_timeout: float = 0,
    ) -> None:
        self.key = _key("conversation-lock", key)
        self.redis_client = redis_client
        self.timeout_seconds = max(1, int(timeout_seconds))
        self.blocking_timeout = max(0.0, float(blocking_timeout))
        self._local_lock: threading.Lock | None = None
        self._redis_lock: Any | None = None

    def acquire(self) -> bool:
        if self.redis_client is not None:
            self._redis_lock = self.redis_client.lock(
                self.key,
                timeout=self.timeout_seconds,
                blocking_timeout=self.blocking_timeout,
                # Streaming requests acquire in the request thread and release
                # in their background worker. redis-py otherwise hides the
                # ownership token in thread-local storage.
                thread_local=False,
            )
            return bool(self._redis_lock.acquire())
        with _LOCAL.mutex:
            self._local_lock = _LOCAL.locks.setdefault(self.key, threading.Lock())
        return self._local_lock.acquire(timeout=self.blocking_timeout)

    def release(self) -> None:
        if self._redis_lock is not None:
            self._redis_lock.release()
            self._redis_lock = None
        elif self._local_lock is not None:
            self._local_lock.release()
            self._local_lock = None

    def __enter__(self) -> "ConversationLock":
        if not self.acquire():
            raise ConversationBusy("conversation is already being processed")
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()


class IdempotencyStore:
    def __init__(self, redis_client: Any | None = None) -> None:
        self.redis_client = redis_client

    def _key(self, scope: str, idempotency_key: str) -> str:
        return _key("idempotency", f"{scope}:{idempotency_key}")

    def reserve(self, scope: str, idempotency_key: str, *, ttl_seconds: int = 86400) -> bool:
        key = self._key(scope, idempotency_key)
        ttl = max(1, int(ttl_seconds))
        if self.redis_client is not None:
            return bool(self.redis_client.set(key, "{}", nx=True, ex=ttl))
        now = time.monotonic()
        with _LOCAL.mutex:
            current = _LOCAL.idempotency.get(key)
            if current and current[0] > now:
                return False
            _LOCAL.idempotency[key] = (now + ttl, "{}")
            return True

    def save_response(
        self,
        scope: str,
        idempotency_key: str,
        response: dict[str, Any],
        *,
        ttl_seconds: int = 86400,
    ) -> None:
        key = self._key(scope, idempotency_key)
        encoded = json.dumps(response, ensure_ascii=False, separators=(",", ":"))
        ttl = max(1, int(ttl_seconds))
        if self.redis_client is not None:
            self.redis_client.set(key, encoded, ex=ttl)
            return
        with _LOCAL.mutex:
            _LOCAL.idempotency[key] = (time.monotonic() + ttl, encoded)

    def get_response(self, scope: str, idempotency_key: str) -> dict[str, Any] | None:
        key = self._key(scope, idempotency_key)
        raw: str | None
        if self.redis_client is not None:
            raw = self.redis_client.get(key)
        else:
            with _LOCAL.mutex:
                current = _LOCAL.idempotency.get(key)
                if not current or current[0] <= time.monotonic():
                    _LOCAL.idempotency.pop(key, None)
                    return None
                raw = current[1]
        try:
            parsed = json.loads(raw or "")
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) and parsed else None

    def delete(self, scope: str, idempotency_key: str) -> None:
        key = self._key(scope, idempotency_key)
        if self.redis_client is not None:
            self.redis_client.delete(key)
            return
        with _LOCAL.mutex:
            _LOCAL.idempotency.pop(key, None)


class StreamEventStore:
    def __init__(self, redis_client: Any | None = None, *, max_events: int = 1000) -> None:
        self.redis_client = redis_client
        self.max_events = max(10, int(max_events))

    def append(self, stream: str, event: dict[str, Any]) -> str:
        key = _key("sse", stream)
        encoded = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        if self.redis_client is not None:
            event_id = self.redis_client.xadd(
                key,
                {"payload": encoded},
                maxlen=self.max_events,
                approximate=True,
            )
            return str(event_id)
        with _LOCAL.mutex:
            _LOCAL.sequence += 1
            event_id = f"{_LOCAL.sequence}-0"
            items = _LOCAL.streams.setdefault(key, deque(maxlen=self.max_events))
            items.append((event_id, event))
            return event_id

    def read_since(
        self,
        stream: str,
        last_event_id: str | None = None,
        *,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        key = _key("sse", stream)
        bounded_limit = max(1, min(int(limit), self.max_events))
        cursor = last_event_id or "0-0"
        if self.redis_client is not None:
            rows = self.redis_client.xrange(
                key,
                min=f"({cursor}",
                max="+",
                count=bounded_limit,
            )
            output: list[dict[str, Any]] = []
            for event_id, fields in rows:
                raw = fields.get("payload")
                try:
                    payload = json.loads(raw or "{}")
                except json.JSONDecodeError:
                    payload = {}
                output.append({"id": str(event_id), "payload": payload})
            return output
        with _LOCAL.mutex:
            items = list(_LOCAL.streams.get(key, ()))
        def numeric(event_id: str) -> int:
            try:
                return int(event_id.split("-", 1)[0])
            except (TypeError, ValueError):
                return -1
        after = numeric(cursor)
        return [
            {"id": event_id, "payload": payload}
            for event_id, payload in items
            if numeric(event_id) > after
        ][:bounded_limit]


class RateLimiter:
    def __init__(self, redis_client: Any | None = None) -> None:
        self.redis_client = redis_client

    def allow(self, key: str, *, limit: int, window_seconds: int) -> tuple[bool, int]:
        bounded_limit = max(1, int(limit))
        window = max(1, int(window_seconds))
        redis_key = _key("rate", key)
        if self.redis_client is not None:
            count = int(self.redis_client.incr(redis_key))
            if count == 1:
                self.redis_client.expire(redis_key, window)
            return count <= bounded_limit, count
        now = time.monotonic()
        with _LOCAL.mutex:
            started, count = _LOCAL.counters.get(redis_key, (now, 0))
            if now - started >= window:
                started, count = now, 0
            count += 1
            _LOCAL.counters[redis_key] = (started, count)
            return count <= bounded_limit, count


class AgentConcurrencyLimiter:
    """A lease counter with an expiry so crashed workers do not hold slots forever."""

    def __init__(self, redis_client: Any | None = None) -> None:
        self.redis_client = redis_client

    def acquire(self, key: str, *, limit: int, lease_seconds: int = 300) -> bool:
        bounded_limit = max(1, int(limit))
        ttl = max(1, int(lease_seconds))
        lease_key = _key("agent-concurrency", key)
        if self.redis_client is not None:
            count = int(self.redis_client.incr(lease_key))
            if count == 1:
                self.redis_client.expire(lease_key, ttl)
            if count > bounded_limit:
                self.redis_client.decr(lease_key)
                return False
            return True
        now = time.monotonic()
        with _LOCAL.mutex:
            started, count = _LOCAL.concurrency.get(lease_key, (now, 0))
            if now - started >= ttl:
                started, count = now, 0
            if count >= bounded_limit:
                return False
            _LOCAL.concurrency[lease_key] = (started, count + 1)
            return True

    def release(self, key: str) -> None:
        lease_key = _key("agent-concurrency", key)
        if self.redis_client is not None:
            self.redis_client.decr(lease_key)
        else:
            with _LOCAL.mutex:
                started, count = _LOCAL.concurrency.get(lease_key, (0, 0))
                if count <= 1:
                    _LOCAL.concurrency.pop(lease_key, None)
                else:
                    _LOCAL.concurrency[lease_key] = (started, count - 1)

    @contextmanager
    def lease(
        self,
        key: str,
        *,
        limit: int,
        lease_seconds: int = 300,
    ) -> Iterator[None]:
        if not self.acquire(key, limit=limit, lease_seconds=lease_seconds):
            raise RuntimeError("agent concurrency limit exceeded")
        try:
            yield
        finally:
            self.release(key)


@dataclass(frozen=True)
class RuntimeServices:
    conversation_lock: Any
    idempotency: IdempotencyStore
    events: StreamEventStore
    rate_limits: RateLimiter
    agent_concurrency: AgentConcurrencyLimiter


_RUNTIME: RuntimeServices | None = None


def get_runtime() -> RuntimeServices:
    global _RUNTIME
    if _RUNTIME is None:
        client = _redis_client()
        _RUNTIME = RuntimeServices(
            conversation_lock=partial(ConversationLock, redis_client=client),
            idempotency=IdempotencyStore(client),
            events=StreamEventStore(client),
            rate_limits=RateLimiter(client),
            agent_concurrency=AgentConcurrencyLimiter(client),
        )
    return _RUNTIME


def reset_runtime_for_tests() -> None:
    global _RUNTIME
    _RUNTIME = None
