"""Request identity boundary used by workspace-aware repositories.

Authentication is intentionally kept separate from business routers. The
current local deployment uses one explicit development user. A reverse proxy
may provide an already authenticated user only when the operator explicitly
enables ``AUTH_MODE=trusted_proxy`` and ``TRUST_PROXY_AUTH=1``.
"""
from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Mapping

from . import config


_current_user: ContextVar["CurrentUser | None"] = ContextVar(
    "chunk_studio_current_user",
    default=None,
)


class CurrentUserError(RuntimeError):
    """The request has no trusted authenticated user."""


@dataclass(frozen=True)
class CurrentUser:
    user_id: str
    workspace_id: str
    roles: frozenset[str] = field(default_factory=frozenset)
    authenticated: bool = False

    def can(self, role: str) -> bool:
        return role in self.roles or "admin" in self.roles


def local_current_user() -> CurrentUser:
    return CurrentUser(
        user_id=config.DEFAULT_USER_ID,
        workspace_id=config.DEFAULT_WORKSPACE_ID,
        roles=frozenset({"developer"}),
        authenticated=False,
    )


def set_current_user(user: CurrentUser) -> Token[CurrentUser | None]:
    """Bind the authenticated user to the current request/task context."""
    return _current_user.set(user)


def reset_current_user(token: Token[CurrentUser | None]) -> None:
    _current_user.reset(token)


def get_current_user() -> CurrentUser:
    """Return request identity, with local fallback for direct unit calls."""
    return _current_user.get() or local_current_user()


def _header(headers: Mapping[str, str], name: str) -> str:
    # ASGI headers are case-insensitive, while a plain mapping used by tests
    # may not be. Normalize once at this boundary.
    wanted = name.casefold()
    for key, value in headers.items():
        if str(key).casefold() == wanted:
            return str(value or "").strip()
    return ""


def from_headers(headers: Mapping[str, str]) -> CurrentUser:
    """Resolve a current user according to the explicitly selected auth mode."""
    mode = config.AUTH_MODE
    if mode == "dev":
        return local_current_user()
    if mode not in {"trusted_proxy", "proxy"} or not config.TRUST_PROXY_AUTH:
        raise CurrentUserError(
            "no authentication adapter is configured; set AUTH_MODE=dev only for local use"
        )

    user_id = _header(headers, "x-auth-user")
    workspace_id = _header(headers, "x-auth-workspace")
    if not user_id or not workspace_id:
        raise CurrentUserError(
            "trusted proxy did not provide x-auth-user and x-auth-workspace"
        )
    roles = frozenset(
        role.strip()
        for role in _header(headers, "x-auth-roles").split(",")
        if role.strip()
    )
    return CurrentUser(
        user_id=user_id,
        workspace_id=workspace_id,
        roles=roles,
        authenticated=True,
    )


def workspace_matches(current_user: CurrentUser, workspace_id: str) -> bool:
    """Keep workspace comparisons in one place for repositories and tests."""
    return bool(workspace_id) and current_user.workspace_id == workspace_id


def current_user_for_headers(headers: Mapping[str, str]) -> CurrentUser:
    """Named alias for web adapters; keeps auth parsing out of routers."""
    return from_headers(headers)
