"""Retry taxonomy for long-running jobs.

Three layers already exist; this module names them so callers do not mix
business outcomes into infrastructure retries.

L0 request retry (``llm.py``)
    ConnectError / ReadError / Timeout → 1–4 attempts, second-scale.

L1 case retry (``run_report_audit_workflow`` sidecar call)
    Sidecar crash / timeout / invalid response → 1–2 extra attempts.

L2 audit job retry (``jobs`` worker)
    Process exit / worker crash / whole-run infrastructure failure → 2–3
    attempts, same ``run_id`` / output / checkpoint.

Never retry at L2:

    missing standard in the knowledge base
    missing report fields / parse markdown
    invalid assistant config
    per-case unevaluable / out_of_scope / applicability_undetermined
"""
from __future__ import annotations

RETRYABLE = "retryable"
NON_RETRYABLE = "non_retryable"

AUDIT_EXIT_RETRYABLE = 1
AUDIT_EXIT_NON_RETRYABLE = 2

_NON_RETRYABLE_MARKERS = (
    "报告检测依据中的标准未在当前知识库找到",
    "报告检测依据中没有可识别的标准号",
    "报告检测依据没有匹配到可检索的知识库文件",
    "助手知识库没有可用标准文件",
    "assistant has no enabled files",
    "file has no completed parse",
    "parse markdown artifact missing",
    "naming rule not provided",
    "active assistant version not found",
    "only DeepSeek assistant versions",
    "assistant version omitted prompt",
    "fresh extraction did not reproduce",
    "invalid audit report_name",
    "audit job missing report_file_id",
)


class JobFailure(Exception):
    """A job-level failure with an explicit retry policy."""

    retryable: bool = True
    code: str = "runtime"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code:
            self.code = code


class RetryableJobError(JobFailure):
    retryable = True
    code = "transient"


class NonRetryableJobError(JobFailure):
    retryable = False
    code = "business"


def looks_non_retryable(detail: str) -> bool:
    text = str(detail or "")
    return any(marker in text for marker in _NON_RETRYABLE_MARKERS)


def classify_audit_subprocess_failure(returncode: int, detail: str) -> JobFailure:
    """Map a workflow process exit onto L2 retry policy."""
    text = str(detail or "").strip()
    message = f"audit workflow failed (exit {returncode}): {text[-2000:]}"
    if int(returncode or 0) == AUDIT_EXIT_NON_RETRYABLE or looks_non_retryable(text):
        return NonRetryableJobError(message, code="business")
    return RetryableJobError(message, code="workflow_crash")
