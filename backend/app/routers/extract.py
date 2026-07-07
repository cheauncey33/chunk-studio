"""LLM metadata extraction — implemented in phase 6. Stub for now."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/extract", tags=["extract"])


@router.post("/{chunk_id}")
def extract_chunk(chunk_id: str):
    raise HTTPException(501, "LLM extraction not implemented yet (phase 6)")


@router.post("/bulk")
def extract_bulk():
    raise HTTPException(501, "bulk extraction not implemented yet (phase 6)")
