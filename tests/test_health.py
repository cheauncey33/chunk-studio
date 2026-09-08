from __future__ import annotations

from app import health


def test_run_checks_reports_latency_without_exposing_error_message() -> None:
    def fail() -> None:
        raise RuntimeError("secret connection detail")

    ready, checks = health.run_checks([
        ("database", lambda: None),
        ("redis", fail),
    ])

    assert ready is False
    assert checks["database"]["status"] == "ok"
    assert checks["redis"]["status"] == "error"
    assert checks["redis"]["error_type"] == "RuntimeError"
    assert "secret connection detail" not in str(checks)
    assert checks["redis"]["latency_ms"] >= 0


def test_readiness_checks_selects_configured_dependencies(monkeypatch) -> None:
    called: list[str] = []
    monkeypatch.setattr(health.config, "REDIS_URL", "redis://configured")
    monkeypatch.setattr(health.config, "OBJECT_STORAGE_BACKEND", "minio")
    monkeypatch.setattr(health.config, "AUDIT_JUDGE_MODE", "workflow")
    monkeypatch.setattr(health, "_check_database", lambda: called.append("database"))
    monkeypatch.setattr(health, "_check_redis", lambda: called.append("redis"))
    monkeypatch.setattr(health, "_check_object_storage", lambda: called.append("object_storage"))

    ready, checks = health.readiness_checks()

    assert ready is True
    assert called == ["database", "redis", "object_storage"]
    assert set(checks) == set(called)


def test_readiness_checks_include_agent_sidecar_in_agent_mode(monkeypatch) -> None:
    called: list[str] = []
    monkeypatch.setattr(health.config, "REDIS_URL", "")
    monkeypatch.setattr(health.config, "OBJECT_STORAGE_BACKEND", "local")
    monkeypatch.setattr(health.config, "AUDIT_JUDGE_MODE", "agent")
    monkeypatch.setattr(health, "_check_database", lambda: called.append("database"))
    monkeypatch.setattr(health, "_check_object_storage", lambda: called.append("object_storage"))
    monkeypatch.setattr(health, "_check_agent_sidecar", lambda: called.append("agent_sidecar"))

    ready, checks = health.readiness_checks()

    assert ready is True
    assert "agent_sidecar" in called
    assert set(checks) == set(called)
