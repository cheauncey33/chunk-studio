"""Tests for in-app audit trial helpers."""
from __future__ import annotations

import pytest

from app import audit_run, config
from app.audit_policy import (
    PRODUCTION_EVIDENCE_COMPRESSION_MODE,
    PRODUCTION_RECOVERY_MODE,
    RECOVERY_MAX_SEARCH_CALLS,
    RECOVERY_MAX_TOOL_CALLS,
    RECOVERY_MAX_TURNS,
)


def test_production_audit_policy_is_compression_first_and_bounded() -> None:
    assert PRODUCTION_EVIDENCE_COMPRESSION_MODE == "active"
    assert PRODUCTION_RECOVERY_MODE == "active"
    assert RECOVERY_MAX_TURNS == 3
    assert RECOVERY_MAX_TOOL_CALLS == 4
    assert RECOVERY_MAX_SEARCH_CALLS == 2


def test_in_app_runner_passes_production_runtime_flags_explicitly(monkeypatch) -> None:
    monkeypatch.setattr(config, "AUDIT_JUDGE_MODE", "workflow")
    assert audit_run.production_runtime_args() == [
        "--evidence-compression",
        "active",
        "--recovery-mode",
        "active",
        "--judge-mode",
        "workflow",
    ]


def test_in_app_runner_passes_agent_sidecar_when_configured(monkeypatch) -> None:
    monkeypatch.setattr(config, "AUDIT_JUDGE_MODE", "agent")
    monkeypatch.setattr(config, "AGENT_SIDECAR_URL", "http://127.0.0.1:8787")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("PI_MODEL", raising=False)
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)
    assert audit_run.production_runtime_args() == [
        "--evidence-compression",
        "active",
        "--recovery-mode",
        "active",
        "--judge-mode",
        "agent",
        "--agent-sidecar-url",
        "http://127.0.0.1:8787",
    ]


def test_resolve_naming_rule_falls_back_to_default(tmp_path, monkeypatch):
    if not audit_run.DEFAULT_NAMING_RULE.is_file():
        pytest.skip("default naming rule prompt missing")
    path = audit_run.resolve_naming_rule_path(None)
    assert path == audit_run.DEFAULT_NAMING_RULE


def test_resolve_markdown_path_requires_done_parse(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    with pytest.raises(ValueError, match="no completed parse"):
        audit_run.resolve_markdown_path("missing-file")
