from __future__ import annotations

from pathlib import Path
from decimal import Decimal
import json
import sys
import types
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks, HTTPException

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))

from app import db, embeddings, jobs, llm, llm_usage, observability, retrieval
from app.llm_pricing import ModelPrice, compute_cost_microunits
from app.llm_usage import UsageContext
from app.models import VectorSearchRequest
from app.routers import internal as internal_router
from app.routers import search as search_router
from app.storage.repositories import postgres_schema_sql
from app.storage.usage_repository import (
    SqliteUsageRepository,
    UsageEvent,
    compact_usage_summary,
    get_usage_repository,
)
import run_report_audit_workflow as workflow


PRICING = {
    "test/fake-model": {
        "currency": "USD",
        "input_per_million": "1.00",
        "output_per_million": "2.00",
        "cache_read_per_million": "0.10",
        "cache_write_per_million": "0.20",
    },
    "zhipu/glm-5.3-flash": {
        "currency": "USD",
        "input_per_million": "1.00",
        "output_per_million": "2.00",
    },
}


def _init_temp_db(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(db.config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db.config, "DB_PATH", tmp_path / "chunkstudio.db")
    monkeypatch.setattr(db, "_conn", None)
    db.init_db()


def _set_pricing(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PRICING_JSON", json.dumps(PRICING))


def _clear_llm_env(monkeypatch) -> None:
    for name in (
        "LLM_API_KEY",
        "PI_API_KEY",
        "ZHIPU_API_KEY",
        "DEEPSEEK_API_KEY",
        "DASHSCOPE_API_KEY",
        "LLM_BASE_URL",
        "PI_BASE_URL",
        "DEEPSEEK_BASE_URL",
        "LLM_MODEL",
        "PI_MODEL",
        "DEEPSEEK_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)


def _insert_job(job_id: str, *, workspace_id: str | None = None) -> None:
    workspace = workspace_id or db.config.DEFAULT_WORKSPACE_ID
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO jobs
               (id, workspace_id, type, target_type, target_id, status, priority,
                attempts, max_attempts, error, result, created_at)
               VALUES (?, ?, 'audit', 'assistant', 'assistant_x', 'running', 0,
                       1, 3, '', '{}', ?)""",
            (job_id, workspace, jobs.now_iso()),
        )


def _context(**overrides: object) -> UsageContext:
    values = {
        "workspace_id": db.config.DEFAULT_WORKSPACE_ID,
        "job_id": "job-1",
        "run_id": "run-1",
        "case_id": "c01",
        "job_attempt": 1,
        "stage": "audit_agent",
        "provider": "test",
        "model": "fake-model",
    }
    values.update(overrides)
    return UsageContext(**values)  # type: ignore[arg-type]


def _record(**overrides: object) -> None:
    event = UsageEvent(
        id=str(overrides.get("id") or overrides.get("request_id") or "evt"),
        workspace_id=str(overrides.get("workspace_id") or db.config.DEFAULT_WORKSPACE_ID),
        job_id=str(overrides.get("job_id") or "job-1"),
        run_id=str(overrides.get("run_id") or "run-1"),
        case_id=(
            None
            if overrides.get("case_id", "c01") is None
            else str(overrides.get("case_id", "c01"))
        ),
        job_attempt=int(overrides.get("job_attempt") or 1),  # type: ignore[arg-type]
        request_attempt=1,
        stage=str(overrides.get("stage") or "audit_agent"),
        provider=str(overrides.get("provider") or "test"),
        model=str(overrides.get("model") or "fake-model"),
        request_id=str(overrides.get("request_id") or "req-1"),
        status="success",
        input_tokens=overrides.get("input_tokens", 0),  # type: ignore[arg-type]
        output_tokens=overrides.get("output_tokens", 0),  # type: ignore[arg-type]
        reasoning_tokens=overrides.get("reasoning_tokens", 0),  # type: ignore[arg-type]
        cache_read_tokens=overrides.get("cache_read_tokens", 0),  # type: ignore[arg-type]
        cache_write_tokens=overrides.get("cache_write_tokens", 0),  # type: ignore[arg-type]
        total_tokens=overrides.get("total_tokens", 0),  # type: ignore[arg-type]
        cost_microunits=overrides.get("cost_microunits"),  # type: ignore[arg-type]
        pricing_missing=bool(overrides.get("pricing_missing", False)),
        pricing_snapshot=overrides.get("pricing_snapshot") if "pricing_snapshot" in overrides else None,  # type: ignore[arg-type]
        usage_source=str(overrides.get("usage_source") or "provider"),
    )
    get_usage_repository().record_usage(event)


def test_normalize_openai_usage_standard_and_glm_extensions() -> None:
    standard = llm_usage.normalize_openai_usage(
        {
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 40,
                "total_tokens": 140,
            }
        }
    )
    assert standard.input_tokens == 100
    assert standard.output_tokens == 40
    assert standard.total_tokens == 140
    assert standard.reasoning_tokens == 0
    assert standard.cache_read_tokens == 0
    assert standard.usage_source == "provider"

    glm = llm_usage.normalize_openai_usage(
        {
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 40,
                "total_tokens": 140,
                "prompt_tokens_details": {"cached_tokens": 12},
                "completion_tokens_details": {"reasoning_tokens": 8},
            }
        }
    )
    assert glm.cache_read_tokens == 12
    assert glm.reasoning_tokens == 8
    assert glm.total_tokens == 140

    missing_total = llm_usage.normalize_openai_usage(
        {"usage": {"prompt_tokens": 100, "completion_tokens": 40}}
    )
    assert missing_total.total_tokens == 140


def test_normalize_does_not_add_reasoning_into_total() -> None:
    usage = llm_usage.normalize_openai_usage(
        {
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "completion_tokens_details": {"reasoning_tokens": 5},
            }
        }
    )
    assert usage.total_tokens == 15
    assert usage.reasoning_tokens == 5


def test_cost_calculation_is_integer_microunits(monkeypatch) -> None:
    _set_pricing(monkeypatch)
    usage = llm_usage.normalize_openai_usage(
        {"usage": {"prompt_tokens": 100, "completion_tokens": 40, "total_tokens": 140}}
    )
    cost, missing, snapshot = llm_usage.price_usage(
        usage, provider="test", model="fake-model"
    )
    assert missing is False
    assert cost == 180
    assert snapshot is not None
    assert snapshot["input_per_million"] == "1.00"
    assert snapshot["output_per_million"] == "2.00"


def test_openai_cache_tokens_are_not_double_charged(monkeypatch) -> None:
    _set_pricing(monkeypatch)
    usage = llm_usage.normalize_openai_usage(
        {
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 0,
                "total_tokens": 100,
                "prompt_tokens_details": {"cached_tokens": 40},
            }
        }
    )
    cost, missing, _snapshot = llm_usage.price_usage(
        usage, provider="test", model="fake-model"
    )
    assert missing is False
    # 60 uncached * $1/M + 40 cached * $0.10/M = 64 microunits
    assert cost == 64


def test_sdk_cache_tokens_stay_exclusive_of_input(monkeypatch) -> None:
    _set_pricing(monkeypatch)
    usage = llm_usage.normalize_pi_sdk_usage(
        {"input": 100, "output": 0, "cacheRead": 40, "cacheWrite": 0, "totalTokens": 140}
    )
    cost, missing, _snapshot = llm_usage.price_usage(
        usage, provider="test", model="fake-model"
    )
    assert missing is False
    # Pi input and cacheRead are already exclusive: 100*$1 + 40*$0.10 = 104
    assert cost == 104


def test_reasoning_tokens_are_not_double_charged_when_priced() -> None:
    price = ModelPrice(
        key="test/reason",
        currency="USD",
        input_per_million=Decimal("1.00"),
        output_per_million=Decimal("2.00"),
        cache_read_per_million=Decimal("0.10"),
        cache_write_per_million=None,
        reasoning_per_million=Decimal("3.00"),
    )
    cost = compute_cost_microunits(
        input_tokens=100,
        output_tokens=40,
        cache_read_tokens=40,
        reasoning_tokens=10,
        price=price,
        usage_source="provider",
    )
    # 60*$1 + 40*$0.10 + 30*$2 + 10*$3 = 154
    assert cost == 154


def test_unknown_usage_does_not_set_pricing_missing(monkeypatch) -> None:
    _set_pricing(monkeypatch)
    cost, missing, snapshot = llm_usage.price_usage(
        llm_usage.unknown_usage(), provider="test", model="fake-model"
    )
    assert cost is None
    assert missing is False
    assert snapshot is not None


def test_pricing_missing_records_tokens_with_null_cost(monkeypatch, tmp_path: Path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    monkeypatch.delenv("LLM_PRICING_JSON", raising=False)
    monkeypatch.setattr(db, "get_setting", lambda *_a, **_k: "")
    usage = llm_usage.normalize_openai_usage(
        {"usage": {"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60}}
    )
    recorded = llm_usage.record_normalized_usage(
        context=_context(model="unknown-model-xyz"),
        request_id="req-missing-price",
        usage=usage,
        provider="test",
        model="unknown-model-xyz",
    )
    assert recorded is True
    rows = get_usage_repository().list_usage_events(
        workspace_id=db.config.DEFAULT_WORKSPACE_ID, job_id="job-1"
    )
    assert len(rows) == 1
    assert rows[0]["input_tokens"] == 50
    assert rows[0]["total_tokens"] == 60
    assert rows[0]["cost_microunits"] is None
    assert rows[0]["pricing_missing"] is True


def test_request_id_insert_is_idempotent(monkeypatch, tmp_path: Path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    _record(request_id="same-req", total_tokens=100, input_tokens=100)
    _record(request_id="same-req", total_tokens=999, input_tokens=999)
    rows = get_usage_repository().list_usage_events(
        workspace_id=db.config.DEFAULT_WORKSPACE_ID, job_id="job-1"
    )
    assert len(rows) == 1
    assert rows[0]["total_tokens"] == 100


def test_retry_attempts_sum_into_job_total(monkeypatch, tmp_path: Path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    _record(request_id="a1", job_attempt=1, total_tokens=100, input_tokens=100)
    _record(request_id="a2", job_attempt=2, total_tokens=200, input_tokens=200)
    summary = get_usage_repository().get_usage_summary(
        workspace_id=db.config.DEFAULT_WORKSPACE_ID, job_id="job-1"
    )
    assert summary["total_tokens"] == 300
    assert summary["request_count"] == 2
    by_attempt = {item["attempt"]: item["total_tokens"] for item in summary["by_attempt"]}
    assert by_attempt == {1: 100, 2: 200}


def test_case_and_job_aggregation(monkeypatch, tmp_path: Path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    _record(request_id="c01-t1", case_id="c01", total_tokens=1000, input_tokens=1000)
    _record(request_id="c01-t2", case_id="c01", total_tokens=800, input_tokens=800)
    _record(request_id="c01-t3", case_id="c01", total_tokens=400, input_tokens=400)
    _record(request_id="c02-t1", case_id="c02", total_tokens=1800, input_tokens=1800)
    repo = get_usage_repository()
    case = repo.get_usage_summary(
        workspace_id=db.config.DEFAULT_WORKSPACE_ID, job_id="job-1", case_id="c01"
    )
    job = repo.get_usage_summary(
        workspace_id=db.config.DEFAULT_WORKSPACE_ID, job_id="job-1"
    )
    assert case["total_tokens"] == 2200
    assert job["total_tokens"] == 4000
    by_case = {item["case_id"]: item["total_tokens"] for item in job["by_case"]}
    assert by_case == {"c01": 2200, "c02": 1800}


def test_stage_aggregation(monkeypatch, tmp_path: Path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    _record(request_id="s1", stage="report_parameters", total_tokens=10)
    _record(request_id="s2", stage="test_items", total_tokens=20)
    _record(request_id="s3", stage="model_decode", total_tokens=30)
    _record(request_id="s4", stage="audit_agent", total_tokens=40)
    summary = get_usage_repository().get_usage_summary(
        workspace_id=db.config.DEFAULT_WORKSPACE_ID, job_id="job-1"
    )
    by_stage = {item["stage"]: item["total_tokens"] for item in summary["by_stage"]}
    assert by_stage == {
        "report_parameters": 10,
        "test_items": 20,
        "model_decode": 30,
        "audit_agent": 40,
    }


def test_sidecar_ingest_is_idempotent_and_ignores_forged_workspace(
    monkeypatch, tmp_path: Path
) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    _insert_job("job-ingest")
    monkeypatch.setattr(internal_router.config, "AGENT_SIDECAR_TOKEN", "secret")
    body = internal_router.InternalUsageIngest(
        request_id="pi:msg-1",
        job_id="job-ingest",
        run_id="run-ingest",
        case_id="c01",
        job_attempt=1,
        stage="audit_agent",
        provider="zhipu",
        model="glm-5.3-flash",
        usage_source="sdk",
        workspace_id="forged-workspace",
        usage={"input": 1000, "output": 200, "cacheRead": 0, "cacheWrite": 0, "totalTokens": 1200},
    )
    first = internal_router.ingest_llm_usage(body, authorization="Bearer secret")
    second = internal_router.ingest_llm_usage(body, authorization="Bearer secret")
    assert first["ok"] is True
    assert second["ok"] is True
    rows = get_usage_repository().list_usage_events(
        workspace_id=db.config.DEFAULT_WORKSPACE_ID, job_id="job-ingest"
    )
    assert len(rows) == 1
    assert rows[0]["workspace_id"] == db.config.DEFAULT_WORKSPACE_ID
    assert rows[0]["total_tokens"] == 1200
    assert rows[0]["usage_source"] == "sdk"


def test_chat_sidecar_ingest_uses_conversation_not_job(monkeypatch, tmp_path: Path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    now = jobs.now_iso()
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO chat_conversations
               (id, assistant_id, workspace_id, user_id, title, created_at, updated_at)
               VALUES ('chat_ingest', 'assistant_oil_transformer_audit', ?, 'local-user',
                       't', ?, ?)""",
            (db.config.DEFAULT_WORKSPACE_ID, now, now),
        )
    monkeypatch.setattr(internal_router.config, "AGENT_SIDECAR_TOKEN", "secret")
    body = internal_router.InternalUsageIngest(
        request_id="pi:chat-1",
        conversation_id="chat_ingest",
        stage="chat_agent",
        provider="zhipu",
        model="glm-5.3-flash",
        usage_source="sdk",
        workspace_id="forged-workspace",
        usage={"input": 20, "output": 5, "cacheRead": 0, "cacheWrite": 0, "totalTokens": 25},
    )
    first = internal_router.ingest_llm_usage(body, authorization="Bearer secret")
    assert first == {"ok": True, "recorded": True}
    missing = internal_router.ingest_llm_usage(
        internal_router.InternalUsageIngest(
            request_id="pi:chat-missing",
            conversation_id="chat_does_not_exist",
            stage="chat_agent",
            usage_source="sdk",
            usage={"input": 1, "output": 0, "cacheRead": 0, "cacheWrite": 0, "totalTokens": 1},
        ),
        authorization="Bearer secret",
    )
    assert missing["recorded"] is False
    assert missing["reason"] == "conversation_not_found"
    rows = get_usage_repository().list_usage_events(
        workspace_id=db.config.DEFAULT_WORKSPACE_ID,
    )
    chat_rows = [row for row in rows if row.get("conversation_id") == "chat_ingest"]
    assert len(chat_rows) == 1
    assert chat_rows[0]["job_id"] in (None, "")
    assert chat_rows[0]["workspace_id"] == db.config.DEFAULT_WORKSPACE_ID
    assert chat_rows[0]["stage"] == "chat_agent"
    assert chat_rows[0]["total_tokens"] == 25


def test_chat_ingest_infers_stage_when_omitted(monkeypatch, tmp_path: Path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    now = jobs.now_iso()
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO chat_conversations
               (id, assistant_id, workspace_id, user_id, title, created_at, updated_at)
               VALUES ('chat_inferred', 'assistant_oil_transformer_audit', ?, 'local-user',
                       't', ?, ?)""",
            (db.config.DEFAULT_WORKSPACE_ID, now, now),
        )
    monkeypatch.setattr(internal_router.config, "AGENT_SIDECAR_TOKEN", "secret")
    body = internal_router.InternalUsageIngest(
        request_id="pi:chat-inferred",
        conversation_id="chat_inferred",
        usage_source="sdk",
        usage={"input": 4, "output": 1, "cacheRead": 0, "cacheWrite": 0, "totalTokens": 5},
    )
    assert body.stage is None
    recorded = internal_router.ingest_llm_usage(body, authorization="Bearer secret")
    assert recorded == {"ok": True, "recorded": True}
    rows = get_usage_repository().list_usage_events(
        workspace_id=db.config.DEFAULT_WORKSPACE_ID,
    )
    chat_rows = [row for row in rows if row.get("conversation_id") == "chat_inferred"]
    assert len(chat_rows) == 1
    assert chat_rows[0]["stage"] == "chat_agent"
    assert chat_rows[0]["job_id"] in (None, "")


def test_three_sidecar_turns_are_three_events(monkeypatch, tmp_path: Path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    _insert_job("job-turns")
    monkeypatch.setattr(internal_router.config, "AGENT_SIDECAR_TOKEN", "secret")
    for index, total in enumerate((1000, 800, 400), start=1):
        body = internal_router.InternalUsageIngest(
            request_id=f"pi:turn-{index}",
            job_id="job-turns",
            case_id="c01",
            job_attempt=1,
            usage_source="sdk",
            usage={"input": total, "output": 0, "cacheRead": 0, "cacheWrite": 0, "totalTokens": total},
        )
        internal_router.ingest_llm_usage(body, authorization="Bearer secret")
    summary = get_usage_repository().get_usage_summary(
        workspace_id=db.config.DEFAULT_WORKSPACE_ID,
        job_id="job-turns",
        case_id="c01",
    )
    assert summary["request_count"] == 3
    assert summary["total_tokens"] == 2200


def test_workspace_isolation_for_job_usage_api(monkeypatch, tmp_path: Path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    _insert_job("job-a")
    now = jobs.now_iso()
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO workspaces(id, name, slug, status, created_at, updated_at)
               VALUES ('ws-b', 'B', 'ws-b', 'active', ?, ?)""",
            (now, now),
        )
    _insert_job("job-b", workspace_id="ws-b")
    _record(request_id="a", job_id="job-a", total_tokens=11, workspace_id=db.config.DEFAULT_WORKSPACE_ID)
    _record(request_id="b", job_id="job-b", total_tokens=99, workspace_id="ws-b")
    visible = jobs.get_job_usage("job-a")
    assert visible["total_tokens"] == 11
    with pytest.raises(KeyError):
        jobs.get_job_usage("job-b")


def test_metering_failure_does_not_change_chat_result(monkeypatch, tmp_path: Path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    observability.metrics.reset()

    class Boom:
        def record_usage(self, event):
            raise RuntimeError("insert failed")

        def lookup_job_workspace(self, job_id):
            return db.config.DEFAULT_WORKSPACE_ID

    monkeypatch.setattr(llm_usage, "get_usage_repository", lambda: Boom())
    monkeypatch.setattr(
        llm.db,
        "get_setting",
        lambda key, default="": {
            "llm.api_key": "k",
            "llm.base_url": "https://example.test/v1",
            "llm.model": "fake-model",
        }.get(key, default),
    )
    _clear_llm_env(monkeypatch)

    def post(url, **kwargs):
        return SimpleNamespace(
            status_code=200,
            text="",
            json=lambda: {
                "choices": [{"message": {"content": '{"verdict":"match"}'}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
            },
        )

    monkeypatch.setattr(llm.httpx, "post", post)
    result = llm.chat_json(
        [{"role": "user", "content": "x"}],
        usage_context=_context(),
    )
    assert result == {"verdict": "match"}
    rendered = observability.metrics.render_prometheus()
    assert "llm_usage_record_failures_total" in rendered


def test_chat_json_records_provider_usage(monkeypatch, tmp_path: Path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    _set_pricing(monkeypatch)
    monkeypatch.setattr(
        llm.db,
        "get_setting",
        lambda key, default="": {
            "llm.api_key": "k",
            "llm.base_url": "https://open.bigmodel.cn/api/paas/v4",
            "llm.model": "glm-5.3-flash",
        }.get(key, default),
    )
    _clear_llm_env(monkeypatch)

    def post(url, **kwargs):
        return SimpleNamespace(
            status_code=200,
            text="",
            json=lambda: {
                "choices": [{"message": {"content": '{"ok": true}'}}],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 40,
                    "total_tokens": 140,
                },
            },
        )

    monkeypatch.setattr(llm.httpx, "post", post)
    result = llm.chat_json(
        [{"role": "user", "content": "x"}],
        usage_context=_context(stage="report_parameters", model="glm-5.3-flash"),
    )
    assert result == {"ok": True}
    rows = get_usage_repository().list_usage_events(
        workspace_id=db.config.DEFAULT_WORKSPACE_ID, job_id="job-1"
    )
    assert len(rows) == 1
    assert rows[0]["total_tokens"] == 140
    assert rows[0]["cost_microunits"] == 180
    assert rows[0]["stage"] == "report_parameters"
    assert rows[0]["provider"] == "zhipu"


def test_unknown_usage_does_not_invent_tokens() -> None:
    usage = llm_usage.normalize_openai_usage({"choices": []})
    assert usage.usage_source == "unknown"
    assert usage.input_tokens is None
    assert usage.total_tokens is None


def test_postgres_schema_includes_usage_ledger() -> None:
    schema = "\n".join(postgres_schema_sql())
    assert "CREATE TABLE IF NOT EXISTS llm_usage_events" in schema
    assert "request_id TEXT NOT NULL UNIQUE" in schema
    assert "cost_microunits BIGINT" in schema
    assert "conversation_id TEXT" in schema


def test_sidecar_payload_adds_execution_identity_without_prompt_changes() -> None:
    workflow._USAGE_IDENTITY.update(
        {
            "workspace_id": "ws",
            "job_id": "job-9",
            "run_id": "run-9",
            "job_attempt": 2,
        }
    )
    payload = workflow._sidecar_execution_fields("c03")
    assert payload == {
        "case_id": "c03",
        "job_id": "job-9",
        "run_id": "run-9",
        "job_attempt": 2,
    }
    agent_src = Path(ROOT / "services" / "pi-audit-sidecar" / "src" / "agent.ts").read_text(
        encoding="utf-8"
    )
    prompt_fn = agent_src.split("function casePrompt")[1].split("const TRACE_TYPES")[0]
    assert "job_id" not in prompt_fn
    assert "job_attempt" not in prompt_fn
    assert "run_id" not in prompt_fn
    workflow._USAGE_IDENTITY.update(
        {
            "workspace_id": "",
            "job_id": "",
            "run_id": "",
            "job_attempt": None,
        }
    )


def test_compact_usage_summary_is_recomputable_snapshot(monkeypatch, tmp_path: Path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    _record(request_id="one", total_tokens=12, cost_microunits=9)
    summary = get_usage_repository().get_usage_summary(
        workspace_id=db.config.DEFAULT_WORKSPACE_ID, job_id="job-1"
    )
    snapshot = compact_usage_summary(summary)
    assert snapshot == {
        "request_count": 1,
        "total_tokens": 12,
        "known_cost_microunits": 9,
        "cost_microunits": 9,
        "cost_complete": True,
        "currency": "USD",
        "pricing_missing": False,
        "pricing_missing_event_count": 0,
        "usage_unknown_event_count": 0,
    }


def test_incomplete_cost_does_not_look_like_total_cost(monkeypatch, tmp_path: Path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    _record(request_id="known", total_tokens=12, cost_microunits=9, pricing_missing=False)
    _record(
        request_id="unknown",
        total_tokens=None,
        input_tokens=None,
        output_tokens=None,
        cost_microunits=None,
        usage_source="unknown",
        pricing_missing=False,
    )
    summary = get_usage_repository().get_usage_summary(
        workspace_id=db.config.DEFAULT_WORKSPACE_ID, job_id="job-1"
    )
    assert summary["known_cost_microunits"] == 9
    assert summary["cost_microunits"] is None
    assert summary["cost_complete"] is False
    assert summary["pricing_missing"] is False
    assert summary["pricing_missing_event_count"] == 0
    assert summary["usage_unknown_event_count"] == 1


def test_usage_summary_does_not_use_list_limit(monkeypatch, tmp_path: Path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    _record(request_id="one", total_tokens=12, cost_microunits=9)

    def _should_not_list(self, **_kwargs):
        raise AssertionError("accounting aggregate must not list rows")

    monkeypatch.setattr(SqliteUsageRepository, "list_usage_events", _should_not_list)
    summary = get_usage_repository().get_usage_summary(
        workspace_id=db.config.DEFAULT_WORKSPACE_ID, job_id="job-1"
    )
    assert summary["request_count"] == 1
    assert summary["total_tokens"] == 12
    assert summary["cost_complete"] is True


def test_chat_json_parse_failure_still_records_provider_usage(
    monkeypatch, tmp_path: Path
) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    _set_pricing(monkeypatch)
    monkeypatch.setattr(
        llm.db,
        "get_setting",
        lambda key, default="": {
            "llm.api_key": "k",
            "llm.base_url": "https://open.bigmodel.cn/api/paas/v4",
            "llm.model": "glm-5.3-flash",
        }.get(key, default),
    )
    _clear_llm_env(monkeypatch)

    def post(url, **kwargs):
        return SimpleNamespace(
            status_code=200,
            text="",
            json=lambda: {
                "choices": [{"message": {"content": "not-json"}}],
                "usage": {
                    "prompt_tokens": 5000,
                    "completion_tokens": 0,
                    "total_tokens": 5000,
                },
            },
        )

    monkeypatch.setattr(llm.httpx, "post", post)
    with pytest.raises(RuntimeError, match="invalid JSON"):
        llm.chat_json(
            [{"role": "user", "content": "x"}],
            usage_context=_context(stage="report_parameters", model="glm-5.3-flash"),
        )
    rows = get_usage_repository().list_usage_events(
        workspace_id=db.config.DEFAULT_WORKSPACE_ID, job_id="job-1"
    )
    assert len(rows) == 1
    assert rows[0]["total_tokens"] == 5000
    assert rows[0]["usage_source"] == "provider"
    assert rows[0]["status"] == "failed"
    assert rows[0]["cost_microunits"] == 5000


def test_duplicate_insert_does_not_double_prometheus(monkeypatch, tmp_path: Path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    _insert_job("job-prom")
    observability.metrics.reset()
    monkeypatch.setattr(internal_router.config, "AGENT_SIDECAR_TOKEN", "secret")
    body = internal_router.InternalUsageIngest(
        request_id="pi:dup-prom",
        job_id="job-prom",
        usage_source="sdk",
        usage={"input": 10, "output": 0, "cacheRead": 0, "cacheWrite": 0, "totalTokens": 10},
    )
    first = internal_router.ingest_llm_usage(body, authorization="Bearer secret")
    second = internal_router.ingest_llm_usage(body, authorization="Bearer secret")
    assert first["recorded"] is True
    assert second["recorded"] is False
    counters = [
        item
        for item in observability.metrics.snapshot()["counters"]
        if item["name"] == "llm_requests_total"
    ]
    assert sum(item["value"] for item in counters) == 1


def test_ingest_rejects_missing_sidecar_token(monkeypatch, tmp_path: Path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    monkeypatch.setattr(internal_router.config, "AGENT_SIDECAR_TOKEN", "")
    body = internal_router.InternalUsageIngest(
        request_id="x",
        job_id="job-1",
        usage={"input": 1, "output": 0, "cacheRead": 0, "cacheWrite": 0, "totalTokens": 1},
    )
    with pytest.raises(HTTPException) as exc:
        internal_router.ingest_llm_usage(body, authorization=None)
    assert exc.value.status_code == 401


def test_non_generative_usage_puts_tokens_in_input() -> None:
    usage = llm_usage.normalize_non_generative_usage(42)
    assert usage.usage_source == "provider"
    assert usage.input_tokens == 42
    assert usage.output_tokens == 0
    assert usage.total_tokens == 42
    assert llm_usage.normalize_non_generative_usage(None).usage_source == "unknown"


def _install_fake_dashscope(monkeypatch, **attrs: object) -> None:
    module = types.ModuleType("dashscope")
    for name, value in attrs.items():
        setattr(module, name, value)
    monkeypatch.setitem(sys.modules, "dashscope", module)


def test_query_embedding_records_provider_total_tokens(
    monkeypatch, tmp_path: Path
) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "k")
    vector = [0.1] * embeddings.DEFAULT_DIMENSION

    class FakeEmbedding:
        @staticmethod
        def call(**_kwargs):
            return SimpleNamespace(
                status_code=200,
                code=None,
                message="",
                output={"embeddings": [{"text_index": 0, "embedding": vector}]},
                usage={"total_tokens": 42},
            )

    _install_fake_dashscope(monkeypatch, TextEmbedding=FakeEmbedding)
    embeddings.embed_queries_with_dashscope(["空载损耗"], usage_context=_context())
    rows = get_usage_repository().list_usage_events(
        workspace_id=db.config.DEFAULT_WORKSPACE_ID, job_id="job-1"
    )
    assert len(rows) == 1
    assert rows[0]["stage"] == "retrieval_embedding"
    assert rows[0]["provider"] == "dashscope"
    assert rows[0]["model"] == embeddings.DEFAULT_MODEL
    assert rows[0]["input_tokens"] == 42
    assert rows[0]["output_tokens"] == 0
    assert rows[0]["total_tokens"] == 42
    assert rows[0]["usage_source"] == "provider"


def test_rerank_records_provider_total_tokens_even_on_parse_failure(
    monkeypatch, tmp_path: Path
) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "k")

    class FakeReRank:
        @staticmethod
        def call(**_kwargs):
            return SimpleNamespace(
                status_code=200,
                code=None,
                message="",
                output={"results": [{"index": 0, "relevance_score": 0.9}]},
                usage={"total_tokens": 88},
            )

    _install_fake_dashscope(monkeypatch, TextReRank=FakeReRank)
    ranked = retrieval.rerank_documents(
        "query", ["document"], 1, usage_context=_context()
    )
    assert ranked == [(0, 0.9)]
    rows = get_usage_repository().list_usage_events(
        workspace_id=db.config.DEFAULT_WORKSPACE_ID, job_id="job-1"
    )
    assert rows[0]["stage"] == "rerank"
    assert rows[0]["total_tokens"] == 88
    assert rows[0]["input_tokens"] == 88
    assert rows[0]["output_tokens"] == 0
    assert rows[0]["status"] == "success"

    class BrokenReRank:
        @staticmethod
        def call(**_kwargs):
            return SimpleNamespace(
                status_code=200,
                output={"results": "not-a-list"},
                usage={"total_tokens": 50},
            )

    _install_fake_dashscope(monkeypatch, TextReRank=BrokenReRank)
    with pytest.raises(RuntimeError, match="invalid result payload"):
        retrieval.rerank_documents("query", ["document"], 1, usage_context=_context())
    rows = get_usage_repository().list_usage_events(
        workspace_id=db.config.DEFAULT_WORKSPACE_ID, job_id="job-1"
    )
    assert len(rows) == 2
    failed = [row for row in rows if row["status"] == "failed"]
    assert len(failed) == 1
    assert failed[0]["total_tokens"] == 50
    assert failed[0]["usage_source"] == "provider"


def test_missing_dashscope_key_does_not_invent_retrieval_usage(
    monkeypatch, tmp_path: Path
) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="DASHSCOPE_API_KEY"):
        embeddings.embed_queries_with_dashscope(["q"], usage_context=_context())
    with pytest.raises(RuntimeError, match="DASHSCOPE_API_KEY"):
        retrieval.rerank_documents("q", ["d"], 1, usage_context=_context())
    rows = get_usage_repository().list_usage_events(
        workspace_id=db.config.DEFAULT_WORKSPACE_ID, job_id="job-1"
    )
    assert rows == []


def test_search_api_forwards_job_identity_outside_the_query(
    monkeypatch, tmp_path: Path
) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    _insert_job("job-search")
    captured: dict[str, object] = {}

    def fake_hybrid_search(query: str, **kwargs):
        captured["query"] = query
        captured["usage_context"] = kwargs.get("usage_context")
        return {
            "query": query,
            "model": "model",
            "dimension": 1,
            "total_candidates": 0,
            "candidate_count": 0,
            "retrieval_mode": "dual_rerank",
            "query_routes": {"production": query},
            "rerank_model": None,
            "degraded": [],
            "hits": [],
        }

    monkeypatch.setattr(search_router.retrieval, "hybrid_search", fake_hybrid_search)
    monkeypatch.setattr(search_router.lexical, "production_enabled", lambda: True)
    search_router.search_chunks(
        VectorSearchRequest(
            query="空载损耗P0",
            query_routes={"production": "空载损耗P0"},
            job_id="job-search",
            run_id="run-9",
            case_id="c01",
            job_attempt=2,
        ),
        BackgroundTasks(),
    )
    ctx = captured["usage_context"]
    assert captured["query"] == "空载损耗P0"
    assert ctx is not None
    assert ctx.job_id == "job-search"
    assert ctx.run_id == "run-9"
    assert ctx.case_id == "c01"
    assert ctx.job_attempt == 2
    assert ctx.workspace_id == db.config.DEFAULT_WORKSPACE_ID


def test_search_api_ignores_unknown_job_identity(monkeypatch, tmp_path: Path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    captured: dict[str, object] = {}

    def fake_hybrid_search(query: str, **kwargs):
        captured["usage_context"] = kwargs.get("usage_context")
        return {
            "query": query,
            "model": "model",
            "dimension": 1,
            "total_candidates": 0,
            "candidate_count": 0,
            "retrieval_mode": "dual_rerank",
            "query_routes": {"production": query},
            "rerank_model": None,
            "degraded": [],
            "hits": [],
        }

    monkeypatch.setattr(search_router.retrieval, "hybrid_search", fake_hybrid_search)
    monkeypatch.setattr(search_router.lexical, "production_enabled", lambda: True)
    search_router.search_chunks(
        VectorSearchRequest(query="空载损耗P0", job_id="missing-job", case_id="c01"),
        BackgroundTasks(),
    )
    assert captured["usage_context"] is None


def test_search_api_does_not_attribute_foreign_workspace_job(
    monkeypatch, tmp_path: Path
) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    now = jobs.now_iso()
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO workspaces(id, name, slug, status, created_at, updated_at)
               VALUES ('ws-b', 'B', 'ws-b', 'active', ?, ?)""",
            (now, now),
        )
    _insert_job("job-b", workspace_id="ws-b")
    captured: dict[str, object] = {}

    def fake_hybrid_search(query: str, **kwargs):
        captured["usage_context"] = kwargs.get("usage_context")
        return {
            "query": query,
            "model": "model",
            "dimension": 1,
            "total_candidates": 0,
            "candidate_count": 0,
            "retrieval_mode": "dual_rerank",
            "query_routes": {"production": query},
            "rerank_model": None,
            "degraded": [],
            "hits": [],
        }

    monkeypatch.setattr(search_router.retrieval, "hybrid_search", fake_hybrid_search)
    monkeypatch.setattr(search_router.lexical, "production_enabled", lambda: True)
    search_router.search_chunks(
        VectorSearchRequest(query="空载损耗P0", job_id="job-b", case_id="c01"),
        BackgroundTasks(),
    )
    assert captured["usage_context"] is None


def test_search_api_sidecar_token_meters_job_workspace(
    monkeypatch, tmp_path: Path
) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    now = jobs.now_iso()
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO workspaces(id, name, slug, status, created_at, updated_at)
               VALUES ('ws-b', 'B', 'ws-b', 'active', ?, ?)""",
            (now, now),
        )
    _insert_job("job-b", workspace_id="ws-b")
    captured: dict[str, object] = {}

    def fake_hybrid_search(query: str, **kwargs):
        captured["usage_context"] = kwargs.get("usage_context")
        return {
            "query": query,
            "model": "model",
            "dimension": 1,
            "total_candidates": 0,
            "candidate_count": 0,
            "retrieval_mode": "dual_rerank",
            "query_routes": {"production": query},
            "rerank_model": None,
            "degraded": [],
            "hits": [],
        }

    monkeypatch.setattr(search_router.retrieval, "hybrid_search", fake_hybrid_search)
    monkeypatch.setattr(search_router.lexical, "production_enabled", lambda: True)
    monkeypatch.setattr(search_router.config, "AGENT_SIDECAR_TOKEN", "secret")
    search_router.search_chunks(
        VectorSearchRequest(query="空载损耗P0", job_id="job-b", case_id="c01", job_attempt=1),
        BackgroundTasks(),
        authorization="Bearer secret",
    )
    ctx = captured["usage_context"]
    assert ctx is not None
    assert ctx.job_id == "job-b"
    assert ctx.workspace_id == "ws-b"
    assert ctx.case_id == "c01"


def test_search_api_forged_sidecar_token_does_not_cross_workspace(
    monkeypatch, tmp_path: Path
) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    now = jobs.now_iso()
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO workspaces(id, name, slug, status, created_at, updated_at)
               VALUES ('ws-b', 'B', 'ws-b', 'active', ?, ?)""",
            (now, now),
        )
    _insert_job("job-b", workspace_id="ws-b")
    captured: dict[str, object] = {}

    def fake_hybrid_search(query: str, **kwargs):
        captured["usage_context"] = kwargs.get("usage_context")
        return {
            "query": query,
            "model": "model",
            "dimension": 1,
            "total_candidates": 0,
            "candidate_count": 0,
            "retrieval_mode": "dual_rerank",
            "query_routes": {"production": query},
            "rerank_model": None,
            "degraded": [],
            "hits": [],
        }

    monkeypatch.setattr(search_router.retrieval, "hybrid_search", fake_hybrid_search)
    monkeypatch.setattr(search_router.lexical, "production_enabled", lambda: True)
    monkeypatch.setattr(search_router.config, "AGENT_SIDECAR_TOKEN", "secret")
    search_router.search_chunks(
        VectorSearchRequest(query="空载损耗P0", job_id="job-b"),
        BackgroundTasks(),
        authorization="Bearer forged",
    )
    assert captured["usage_context"] is None
