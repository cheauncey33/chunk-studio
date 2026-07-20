"""Small-model metadata suggestion endpoints."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import keyword_extraction
from ..models import KeywordBulkExtractRequest, KeywordExtractRequest


router = APIRouter(prefix="/extract", tags=["extract"])


@router.post("/bulk")
def extract_bulk(body: KeywordBulkExtractRequest):
    return _extract(
        force=body.force,
        limit=body.limit,
        batch_size=body.batch_size,
    )


@router.post("/{chunk_id}")
def extract_chunk(chunk_id: str, body: KeywordExtractRequest):
    result = _extract(force=body.force, chunk_ids=[chunk_id], batch_size=1)
    if result["eligible"] == 0:
        return {**result, "skipped": 1}
    return result


def _extract(**kwargs):
    try:
        return keyword_extraction.extract_suggestions(**kwargs)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc
