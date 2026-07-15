"""Hybrid retrieval API."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import retrieval
from ..models import VectorSearchRequest, VectorSearchResponse


router = APIRouter(prefix="/search", tags=["search"])


@router.post("", response_model=VectorSearchResponse)
def search_chunks(body: VectorSearchRequest):
    try:
        return retrieval.hybrid_search(body.query, top_k=body.top_k)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc
