"""Read-only current-workspace and membership APIs.

Workspace selection is derived from the authenticated request identity. There
is deliberately no client-provided workspace_id parameter in these endpoints.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import current_user, db


router = APIRouter(prefix="/workspaces", tags=["workspaces"])


@router.get("/current")
def current_workspace() -> dict:
    user = current_user.get_current_user()
    row = db.get_conn().execute(
        """SELECT w.id, w.name, w.slug, w.status, wm.role, wm.status AS member_status
           FROM workspaces w
           JOIN workspace_members wm ON wm.workspace_id=w.id
           WHERE w.id=? AND wm.user_id=? AND w.status='active'""",
        (user.workspace_id, user.user_id),
    ).fetchone()
    if not row:
        raise HTTPException(403, "current user is not an active workspace member")
    return {
        "workspace": {
            "id": row["id"],
            "name": row["name"],
            "slug": row["slug"],
            "status": row["status"],
        },
        "user": {
            "id": user.user_id,
            "roles": sorted(user.roles),
            "authenticated": user.authenticated,
        },
        "membership": {
            "role": row["role"],
            "status": row["member_status"],
        },
    }


@router.get("/current/members")
def current_workspace_members() -> dict:
    user = current_user.get_current_user()
    if not db.is_active_workspace_member(
        workspace_id=user.workspace_id,
        user_id=user.user_id,
    ):
        raise HTTPException(403, "current user is not an active workspace member")
    rows = db.get_conn().execute(
        """SELECT u.id, u.display_name, wm.role, wm.status
           FROM workspace_members wm
           JOIN users u ON u.id=wm.user_id
           WHERE wm.workspace_id=?
           ORDER BY u.display_name, u.id""",
        (user.workspace_id,),
    ).fetchall()
    return {"items": [dict(row) for row in rows]}
