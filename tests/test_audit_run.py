"""Tests for in-app audit trial helpers."""
from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from app import audit_run, config
from app.audit_policy import (
    PRODUCTION_EVIDENCE_COMPRESSION_MODE,
    PRODUCTION_RECOVERY_MODE,
    RECOVERY_MAX_SEARCH_CALLS,
    RECOVERY_MAX_TOOL_CALLS,
    RECOVERY_MAX_TURNS,
)
from app.job_errors import (
    AUDIT_EXIT_NON_RETRYABLE,
    AUDIT_EXIT_RETRYABLE,
    NonRetryableJobError,
    RetryableJobError,
    classify_audit_subprocess_failure,
)


def test_production_audit_policy_is_compression_first_and_bounded() -> None:
    assert PRODUCTION_EVIDENCE_COMPRESSION_MODE == "active"
    assert PRODUCTION_RECOVERY_MODE == "active"
    assert RECOVERY_MAX_TURNS == 3
    assert RECOVERY_MAX_TOOL_CALLS == 4
    assert RECOVERY_MAX_SEARCH_CALLS == 2


def test_in_app_runner_passes_production_runtime_flags_explicitly(monkeypatch) -> None:
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
        "workflow",
        "--agent-sidecar-url",
        "http://127.0.0.1:8787",
    ]


def test_in_app_runner_passes_extraction_model_when_configured(monkeypatch) -> None:
    monkeypatch.setattr(config, "AGENT_SIDECAR_URL", "http://127.0.0.1:8787")
    monkeypatch.setenv("PI_MODEL", "glm-5.3-flash")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)
    assert audit_run.production_runtime_args() == [
        "--evidence-compression",
        "active",
        "--recovery-mode",
        "active",
        "--judge-mode",
        "workflow",
        "--agent-sidecar-url",
        "http://127.0.0.1:8787",
        "--judge-model",
        "glm-5.3-flash",
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


def test_audit_run_identity_is_stable_for_the_same_run_id(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    first = audit_run.audit_run_identity(assistant_id="assistant_oil_transformer_audit", run_id="abc123")
    second = audit_run.audit_run_identity(assistant_id="assistant_oil_transformer_audit", run_id="abc123")
    assert first == second
    assert first["run_id"] == "abc123"
    assert first["report_name"] == "end_to_end_audit_oil_transformer_audit_abc123.json"
    assert first["checkpoint_path"].endswith(
        "end_to_end_audit_oil_transformer_audit_abc123.checkpoint.json"
    )


def test_classify_audit_subprocess_missing_standard_is_not_retryable() -> None:
    error = classify_audit_subprocess_failure(
        1,
        "报告检测依据中的标准未在当前知识库找到：GB/T 7595",
    )
    assert isinstance(error, NonRetryableJobError)
    assert error.retryable is False


def test_classify_audit_subprocess_exit_2_is_not_retryable() -> None:
    error = classify_audit_subprocess_failure(AUDIT_EXIT_NON_RETRYABLE, "invalid assistant config")
    assert isinstance(error, NonRetryableJobError)


def test_classify_audit_subprocess_crash_is_retryable() -> None:
    error = classify_audit_subprocess_failure(AUDIT_EXIT_RETRYABLE, "agent sidecar 5xx (502)")
    assert isinstance(error, RetryableJobError)
    assert error.retryable is True


def test_run_workflow_subprocess_timeout_is_retryable(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(*args, **kwargs):
        captured["timeout"] = kwargs.get("timeout")
        raise subprocess.TimeoutExpired(cmd=list(args[0] if args else []), timeout=int(kwargs["timeout"]))

    monkeypatch.setattr(audit_run.subprocess, "run", fake_run)
    with pytest.raises(RetryableJobError) as exc:
        audit_run.run_workflow_subprocess(
            [sys.executable, "-c", "pass"],
            cwd=".",
            env={},
            timeout_seconds=12,
        )
    assert exc.value.retryable is True
    assert exc.value.code == "timeout"
    assert captured["timeout"] == 12


def test_run_workflow_subprocess_kills_the_child_before_returning() -> None:
    started = time.monotonic()
    with pytest.raises(RetryableJobError) as exc:
        audit_run.run_workflow_subprocess(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            cwd=".",
            env=os.environ.copy(),
            timeout_seconds=1,
        )
    assert exc.value.code == "timeout"
    assert time.monotonic() - started < 8
