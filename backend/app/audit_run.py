"""In-app assistant audit trial runner (wraps CLI workflow script)."""
from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from . import config, db

logger = logging.getLogger(__name__)

SCRIPT_PATH = config.PROJECT_ROOT / "scripts" / "run_report_audit_workflow.py"
DEFAULT_NAMING_RULE = (
    config.PROJECT_ROOT / "evaluation" / "prompts" / "model_naming_decode_v1.md"
)
REPORTS_DIR = config.DATA_DIR / "reports"


def _latest_done_parse(file_id: str) -> dict[str, Any]:
    row = db.get_conn().execute(
        """SELECT * FROM document_parses
           WHERE file_id=? AND status='done' AND markdown_path IS NOT NULL
             AND TRIM(markdown_path) != ''
           ORDER BY created_at DESC LIMIT 1""",
        (file_id,),
    ).fetchone()
    if not row:
        raise ValueError(f"file has no completed parse with markdown: {file_id}")
    return dict(row)


def resolve_markdown_path(file_id: str) -> Path:
    parse = _latest_done_parse(file_id)
    path = config.from_rel(parse["markdown_path"])
    if not path.is_file():
        raise ValueError(f"parse markdown missing on disk: {path}")
    return path


def resolve_naming_rule_path(
    file_id: str | None,
    *,
    assistant_id: str | None = None,
) -> Path:
    resolved_id = file_id
    if not resolved_id and assistant_id:
        resolved_id = db.assistant_default_naming_file_id(assistant_id)
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
    return db.assistant_default_naming_file_id(assistant_id)


def run_assistant_audit(
    *,
    assistant_id: str,
    report_file_id: str,
    naming_rule_file_id: str | None = None,
    report_id: str | None = None,
) -> dict[str, Any]:
    """Run the end-to-end audit workflow and write a timestamped report JSON.

    Default is full-report mode (audit every extracted requirement). Passing
    ``report_id`` switches to the legacy frozen case-pool evaluation mode.
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
    ]
    if report_id:
        cmd.extend(["--case-pool", "--report-id", report_id])
    if resolved_naming_id:
        cmd.extend(["--naming-rule-file-id", resolved_naming_id])
    logger.info("starting assistant audit: %s", " ".join(cmd))
    completed = subprocess.run(
        cmd,
        cwd=str(config.PROJECT_ROOT),
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
    try:
        payload = json.loads(output_path.read_text(encoding="utf-8"))
        summary = payload.get("summary") or {}
    except (OSError, json.JSONDecodeError):
        pass

    return {
        "report_name": report_name,
        "report_path": config.to_rel(output_path),
        "summary": summary,
        "naming_rule_file_id": resolved_naming_id,
        "stdout_tail": (completed.stdout or "")[-1000:],
    }
