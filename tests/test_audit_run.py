"""Tests for in-app audit trial helpers."""
from __future__ import annotations

import pytest

from app import audit_run, config


def test_resolve_naming_rule_falls_back_to_default(tmp_path, monkeypatch):
    if not audit_run.DEFAULT_NAMING_RULE.is_file():
        pytest.skip("default naming rule prompt missing")
    path = audit_run.resolve_naming_rule_path(None)
    assert path == audit_run.DEFAULT_NAMING_RULE


def test_resolve_markdown_path_requires_done_parse(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    with pytest.raises(ValueError, match="no completed parse"):
        audit_run.resolve_markdown_path("missing-file")
