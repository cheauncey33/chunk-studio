from __future__ import annotations

from pathlib import Path
import inspect
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app import config, current_user, db
from app.routers import workspaces


def _init_temp_db(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(db.config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db.config, "DB_PATH", tmp_path / "chunkstudio.db")
    monkeypatch.setattr(db, "_conn", None)
    db.init_db()


def test_local_workspace_identity_is_seeded(monkeypatch, tmp_path) -> None:
    _init_temp_db(monkeypatch, tmp_path)
    user = current_user.local_current_user()

    assert db.is_active_workspace_member(
        workspace_id=user.workspace_id,
        user_id=user.user_id,
    )
    profile = workspaces.current_workspace()
    assert profile["workspace"]["id"] == config.DEFAULT_WORKSPACE_ID
    assert profile["user"]["id"] == config.DEFAULT_USER_ID
    assert profile["membership"]["role"] == "owner"

    db.get_conn().close()
    monkeypatch.setattr(db, "_conn", None)


def test_workspace_members_api_never_accepts_client_workspace_argument(
    monkeypatch,
    tmp_path,
) -> None:
    _init_temp_db(monkeypatch, tmp_path)

    assert not inspect.signature(workspaces.current_workspace).parameters
    assert not inspect.signature(workspaces.current_workspace_members).parameters

    db.get_conn().close()
    monkeypatch.setattr(db, "_conn", None)
