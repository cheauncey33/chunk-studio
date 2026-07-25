from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.report_parameters_prompt import build_unified_report_parameters_prompt


def test_unified_builder_returns_optional_notes_only() -> None:
    assert build_unified_report_parameters_prompt(title="x", allow_extra=True) == ""
    text = build_unified_report_parameters_prompt(
        confusion_notes="- 勿混淆绝缘水平与耐压试验电压。",
    )
    assert text == "- 勿混淆绝缘水平与耐压试验电压。"
    assert "parameters" not in text
    assert "model" not in text
