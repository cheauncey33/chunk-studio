"""Hybrid retrieval API."""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, HTTPException

from .. import lexical, retrieval
from ..models import VectorSearchRequest, VectorSearchResponse


router = APIRouter(prefix="/search", tags=["search"])


@router.post("", response_model=VectorSearchResponse)
def search_chunks(body: VectorSearchRequest, background_tasks: BackgroundTasks):
    try:
        result = retrieval.hybrid_search(body.query, top_k=body.top_k)
        if not lexical.production_enabled() and lexical.shadow_enabled():
            background_tasks.add_task(
                lexical.run_shadow,
                body.query,
                result.get("query_routes") or {},
                [hit["chunk_id"] for hit in result.get("hits") or []],
            )
        return result
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc
