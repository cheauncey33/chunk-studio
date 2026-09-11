from __future__ import annotations

from app import config, current_user
from app.routers import analytics


def test_analytics_uses_sidecar_workspace_when_authorized(monkeypatch) -> None:
    monkeypatch.setattr(config, "AGENT_SIDECAR_TOKEN", "secret")
    workspace = analytics._workspace_for_analytics("Bearer secret", "ws-other")
    assert workspace == "ws-other"


def test_analytics_falls_back_to_current_user_without_sidecar_token(monkeypatch) -> None:
    monkeypatch.setattr(config, "AGENT_SIDECAR_TOKEN", "secret")
    monkeypatch.setattr(
        current_user,
        "get_current_user",
        lambda: current_user.CurrentUser(
            user_id="u1",
            workspace_id="ws-local",
            roles=frozenset(),
            authenticated=True,
        ),
    )
    workspace = analytics._workspace_for_analytics("Bearer other", "ws-other")
    assert workspace == "ws-local"
