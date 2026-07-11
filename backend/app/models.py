"""Pydantic models for API request/response bodies."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


ChunkStatus = Literal["pending", "reviewed", "approved", "rejected"]


class BBox(BaseModel):
    x: float
    y: float
    w: float
    h: float


class ChunkCreate(BaseModel):
    file_id: str
    page: int
    bbox: BBox


class ChunkUpdate(BaseModel):
    text: str | None = None
    metadata: dict[str, Any] | None = None
    business_metadata: dict[str, Any] | None = None
    metadata_llm: dict[str, Any] | None = None
    source_trace: dict[str, Any] | None = None
    chunk_logic: dict[str, Any] | None = None
    relations: dict[str, Any] | None = None
    ui_state: dict[str, Any] | None = None
    indexing: dict[str, Any] | None = None
    text_source: str | None = None
    status: ChunkStatus | None = None


class AutoTableChunkRequest(BaseModel):
    parse_id: str | None = None
    dry_run: bool = True
    include_caption: bool = True
    max_caption_gap: float = 0.04
    skip_existing: bool = True


class AutoSectionChunkRequest(BaseModel):
    parse_id: str | None = None
    dry_run: bool = True
    target_level: int = 2
    max_chars: int = 8192
    skip_existing: bool = True


class AutoImageChunkRequest(BaseModel):
    parse_id: str | None = None
    dry_run: bool = True
    include_caption: bool = True
    require_caption: bool = True
    max_caption_gap: float = 0.04
    skip_existing: bool = True


class ChunkOut(BaseModel):
    id: str
    file_id: str
    page: int
    bbox: BBox
    rotation: int
    crop_path: str | None
    crop_url: str | None = None
    text: str | None
    text_source: str
    metadata: dict[str, Any]
    business_metadata: dict[str, Any]
    metadata_llm: dict[str, Any]
    source_trace: dict[str, Any]
    chunk_logic: dict[str, Any]
    relations: dict[str, Any]
    ui_state: dict[str, Any]
    indexing: dict[str, Any]
    status: ChunkStatus
    ocr_status: str | None = None
    ocr_error: str | None = None
    ocr_job_id: str | None = None
    created_at: str
    updated_at: str


class FieldConfig(BaseModel):
    field_key: str
    display_name: str
    extract_source: str  # manual | auto | llm
    value_constraint: str  # free | enum
    label_list: list[str] = Field(default_factory=list)
    value_type: str = "text"  # text | list | structured
    llm_description: str = ""
    order_index: int = 0
    storage_path: str = ""
    accepted_storage_path: str = ""
    scope: str = "chunk"
    editable: bool = True
    filterable: bool = True
    indexable: bool = True
    visible: bool = True


class SettingsUpdate(BaseModel):
    settings: dict[str, str]


class VectorSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=8192)
    top_k: int = Field(default=10, ge=1, le=50)


class VectorSearchHit(BaseModel):
    chunk_id: str
    score: float
    file_id: str
    file_name: str
    page: int
    crop_url: str | None = None
    text: str
    business_metadata: dict[str, Any]
    source_trace: dict[str, Any]


class VectorSearchResponse(BaseModel):
    query: str
    model: str
    dimension: int
    total_candidates: int
    hits: list[VectorSearchHit]
