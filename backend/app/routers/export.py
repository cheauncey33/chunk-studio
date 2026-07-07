"""Export bundle (JSON) — implemented in phase 8. Stub for now."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/export", tags=["export"])


@router.get("")
def export_bundle(file_id: str | None = None):
    raise HTTPException(501, "export not implemented yet (phase 8)")
