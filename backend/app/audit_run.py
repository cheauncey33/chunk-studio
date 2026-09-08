"""In-app assistant audit trial runner (wraps CLI workflow script)."""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from . import artifacts, config, current_user, db
from .audit_policy import (
    PRODUCTION_EVIDENCE_COMPRESSION_MODE,
    PRODUCTION_RECOVERY_MODE,
)

logger = logging.getLogger(__name__)

SCRIPT_PATH = config.PROJECT_ROOT / "scripts" / "run_report_audit_workflow.py"
DEFAULT_NAMING_RULE = (
    config.PROJECT_ROOT / "evaluation" / "prompts" / "model_naming_decode_v1.md"
)
REPORTS_DIR = config.DATA_DIR / "reports"


def production_runtime_args() -> list[str]:
    """Return explicit flags used by the in-app production audit runner."""
    args = [
        "--evidence-compression",
        PRODUCTION_EVIDENCE_COMPRESSION_MODE,
        "--recovery-mode",
        PRODUCTION_RECOVERY_MODE,
        "--judge-mode",
        config.AUDIT_JUDGE_MODE,
    ]
    if config.AUDIT_JUDGE_MODE == "agent":
        args.extend(
            [
                "--agent-sidecar-url",
                config.AGENT_SIDECAR_URL,
            ]
        )
        extraction_model = (
            os.environ.get("LLM_MODEL")
            or os.environ.get("PI_MODEL")
            or os.environ.get("DEEPSEEK_MODEL")
            or ""
        ).strip()
        if extraction_model:
            args.extend(["--judge-model", extraction_model])
    return args


def _latest_done_parse(file_id: str) -> dict[str, Any]:
    from .storage.repositories import get_content_repository, get_content_write_repository

    repository = get_content_repository() or get_content_write_repository()
    if repository is not None:
        parse = repository.latest_parse(file_id)
        if parse and parse.get("status") == "done" and (
            parse.get("markdown_path") or parse.get("markdown_object_key")
        ):
            return parse
        raise ValueError(f"file has no completed parse with markdown: {file_id}")
    row = db.get_conn().execute(
        """SELECT * FROM document_parses
           WHERE file_id=? AND status='done'
             AND ((markdown_path IS NOT NULL AND TRIM(markdown_path) != '')
                  OR (markdown_object_key IS NOT NULL AND TRIM(markdown_object_key) != ''))
           ORDER BY created_at DESC LIMIT 1""",
        (file_id,),
    ).fetchone()
    if not row:
        raise ValueError(f"file has no completed parse with markdown: {file_id}")
    return dict(row)


def resolve_markdown_path(file_id: str) -> Path:
    parse = _latest_done_parse(file_id)
    path = artifacts.materialize_artifact(
        parse.get("markdown_path"),
        parse.get("markdown_object_key"),
        cache_name=f"{file_id}-{parse.get('id') or 'latest'}",
        suffix=".md",
    )
    if path is None:
        raise ValueError(f"parse markdown artifact missing: {file_id}")
    return path


def resolve_naming_rule_path(
    file_id: str | None,
    *,
    assistant_id: str | None = None,
) -> Path:
    resolved_id = file_id
    if not resolved_id and assistant_id:
        from .storage.repositories import get_content_repository, get_content_write_repository

        repository = get_content_repository() or get_content_write_repository()
        resolved_id = (
            repository.assistant_default_naming_file_id(assistant_id)
            if repository is not None
            else db.assistant_default_naming_file_id(assistant_id)
        )
    if resolved_id:
        return resolve_markdown_path(resolved_id)
    if DEFAULT_NAMING_RULE.is_file():
        return DEFAULT_NAMING_RULE
    raise ValueError(
        "naming rule not provided, no knowledge-base default, and evaluation fallback is missing"
    )


def resolve_naming_rule_file_id(
    assistant_id: str,
    naming_rule_file_id: str | None = None,
) -> str | None:
    """Explicit run override, else KB default, else None (eval fallback path)."""
    if naming_rule_file_id:
        return naming_rule_file_id
    from .storage.repositories import get_content_repository, get_content_write_repository

    repository = get_content_repository() or get_content_write_repository()
    return (
        repository.assistant_default_naming_file_id(assistant_id)
        if repository is not None
        else db.assistant_default_naming_file_id(assistant_id)
    )


def run_assistant_audit(
    *,
    assistant_id: str,
    report_file_id: str,
    naming_rule_file_id: str | None = None,
    job_id: str | None = None,
    started_at: str | None = None,
) -> dict[str, Any]:
    """Run the end-to-end audit workflow and write a timestamped report JSON.

    The workflow audits every extracted requirement in the supplied report.
    """
    if not SCRIPT_PATH.is_file():
        raise RuntimeError(f"audit workflow script missing: {SCRIPT_PATH}")

    resolved_naming_id = resolve_naming_rule_file_id(assistant_id, naming_rule_file_id)
    report_md = resolve_markdown_path(report_file_id)
    naming_md = resolve_naming_rule_path(resolved_naming_id, assistant_id=assistant_id)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    short = assistant_id.replace("assistant_", "")[:24] or "audit"
    report_name = f"end_to_end_audit_{short}_{stamp}.json"
    output_path = REPORTS_DIR / report_name
    run_started = (started_at or "").strip() or time.strftime("%Y-%m-%dT%H:%M:%S")

    from .storage.repositories import get_content_repository, get_content_write_repository

    repository = get_content_repository() or get_content_write_repository()
    if repository is not None:
        file_row = repository.get_file(report_file_id)
        report_file_name = str(file_row.get("name") or "") or None if file_row else None
    else:
        file_row = db.get_conn().execute(
            "SELECT name FROM files WHERE id=? AND workspace_id=?",
            (report_file_id, current_user.get_current_user().workspace_id),
        ).fetchone()
        report_file_name = str(file_row["name"]) if file_row and file_row["name"] else None

    cmd = [
        sys.executable,
        str(SCRIPT_PATH),
        str(report_md),
        "--naming-rule",
        str(naming_md),
        "--assistant-id",
        assistant_id,
        "--report-file-id",
        report_file_id,
        "--output",
        str(output_path),
        "--started-at",
        run_started,
    ]
    cmd.extend(production_runtime_args())
    if job_id:
        cmd.extend(["--job-id", job_id])
    if resolved_naming_id:
        cmd.extend(["--naming-rule-file-id", resolved_naming_id])
    run_env = os.environ.copy()
    identity = current_user.get_current_user()
    run_env["AUDIT_JUDGE_MODE"] = config.AUDIT_JUDGE_MODE
    run_env["AGENT_SIDECAR_URL"] = config.AGENT_SIDECAR_URL
    if config.AGENT_SIDECAR_TOKEN:
        run_env["AGENT_SIDECAR_TOKEN"] = config.AGENT_SIDECAR_TOKEN
    run_env["CHUNK_STUDIO_DEFAULT_WORKSPACE_ID"] = identity.workspace_id
    run_env["DEFAULT_WORKSPACE_ID"] = identity.workspace_id
    run_env["CHUNK_STUDIO_DEFAULT_USER_ID"] = identity.user_id
    run_env["DEFAULT_USER_ID"] = identity.user_id
    logger.info("starting assistant audit: %s", " ".join(cmd))
    completed = subprocess.run(
        cmd,
        cwd=str(config.PROJECT_ROOT),
        env=run_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(
            f"audit workflow failed (exit {completed.returncode}): {detail[-2000:]}"
        )
    if not output_path.is_file():
        raise RuntimeError("audit workflow finished but report file was not written")

    summary: dict[str, Any] = {}
    finished_at = time.strftime("%Y-%m-%dT%H:%M:%S")
    try:
        payload = json.loads(output_path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            # Ensure identity fields survive even if the CLI path omitted them.
            changed = False
            for key, value in (
                ("report_file_id", report_file_id),
                ("report_file_name", report_file_name),
                ("workspace_id", current_user.get_current_user().workspace_id),
                ("job_id", job_id),
                ("started_at", run_started),
                ("finished_at", finished_at),
            ):
                if value and not payload.get(key):
                    payload[key] = value
                    changed = True
            if changed:
                output_path.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            summary = payload.get("summary") or {}
            finished_at = str(payload.get("finished_at") or finished_at)
    except (OSError, json.JSONDecodeError):
        pass

    return {
        "report_name": report_name,
        "report_path": config.to_rel(output_path),
        "report_file_id": report_file_id,
        "report_file_name": report_file_name,
        "started_at": run_started,
        "finished_at": finished_at,
        "summary": summary,
        "naming_rule_file_id": resolved_naming_id,
        "stdout_tail": (completed.stdout or "")[-1000:],
    }
