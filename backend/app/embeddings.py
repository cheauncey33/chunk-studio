"""Incremental dense embeddings for approved chunks."""
from __future__ import annotations

import hashlib
import math
import os
import struct
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any

from . import chunk_schema, db


DEFAULT_MODEL = "text-embedding-v4"
DEFAULT_DIMENSION = 1024
MAX_BATCH_SIZE = 10


@dataclass(frozen=True)
class EmbeddingDocument:
    chunk_id: str
    text: str
    text_sha256: str


def build_document(row: Any) -> EmbeddingDocument:
    business = chunk_schema.parse_json_object(row["business_metadata"])
    labels = [
        business.get("standard_no"),
        business.get("section"),
        business.get("section_title"),
        business.get("table_no"),
        business.get("table_title"),
        business.get("figure_no"),
        business.get("figure_title"),
    ]
    prefix = " | ".join(str(value).strip() for value in labels if value)
    body = str(row["text"] or "").strip()
    text = f"{prefix}\n\n{body}" if prefix and body else prefix or body
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return EmbeddingDocument(str(row["id"]), text, digest)


def pending_documents(*, model: str, dimension: int, force: bool = False) -> list[EmbeddingDocument]:
    rows = db.get_conn().execute(
        """SELECT id, text, business_metadata
           FROM chunks
           WHERE status='approved'
           ORDER BY file_id, page, created_at"""
    ).fetchall()
    documents = [build_document(row) for row in rows]
    if force:
        return documents
    existing = {
        row["chunk_id"]: row["text_sha256"]
        for row in db.get_conn().execute(
            """SELECT chunk_id, text_sha256 FROM chunk_embeddings
               WHERE model=? AND dimension=?""",
            (model, dimension),
        ).fetchall()
    }
    return [doc for doc in documents if existing.get(doc.chunk_id) != doc.text_sha256]


def embed_with_dashscope(
    texts: list[str], *, model: str = DEFAULT_MODEL, dimension: int = DEFAULT_DIMENSION
) -> tuple[list[list[float]], int]:
    if not os.environ.get("DASHSCOPE_API_KEY"):
        raise RuntimeError("DASHSCOPE_API_KEY is not set")
    if not 1 <= len(texts) <= MAX_BATCH_SIZE:
        raise ValueError(f"batch size must be between 1 and {MAX_BATCH_SIZE}")

    from dashscope import TextEmbedding

    response = TextEmbedding.call(
        model=model,
        input=texts,
        dimension=dimension,
        text_type="document",
        output_type="dense",
    )
    if response.status_code != HTTPStatus.OK:
        raise RuntimeError(
            f"DashScope embedding failed: status={response.status_code} "
            f"code={response.code} message={response.message}"
        )
    items = sorted(response.output["embeddings"], key=lambda item: item["text_index"])
    vectors = [item["embedding"] for item in items]
    if len(vectors) != len(texts) or any(len(vector) != dimension for vector in vectors):
        raise RuntimeError("DashScope returned an unexpected embedding count or dimension")
    return vectors, int(response.usage.get("total_tokens") or 0)


def store_embeddings(
    documents: list[EmbeddingDocument],
    vectors: list[list[float]],
    *,
    model: str,
    dimension: int,
    token_count: int = 0,
) -> None:
    if len(documents) != len(vectors):
        raise ValueError("document and vector counts differ")
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    per_document_tokens = math.ceil(token_count / len(documents)) if documents else 0
    rows = []
    for document, vector in zip(documents, vectors, strict=True):
        if len(vector) != dimension:
            raise ValueError(f"unexpected vector dimension for {document.chunk_id}")
        blob = struct.pack(f"<{dimension}f", *vector)
        rows.append(
            (
                document.chunk_id,
                model,
                dimension,
                document.text_sha256,
                blob,
                per_document_tokens,
                now,
                now,
            )
        )
    with db.transaction() as conn:
        conn.executemany(
            """INSERT INTO chunk_embeddings
               (chunk_id, model, dimension, text_sha256, embedding,
                token_count, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(chunk_id, model, dimension) DO UPDATE SET
                 text_sha256=excluded.text_sha256,
                 embedding=excluded.embedding,
                 token_count=excluded.token_count,
                 updated_at=excluded.updated_at""",
            rows,
        )


def build_embeddings(
    *,
    model: str = DEFAULT_MODEL,
    dimension: int = DEFAULT_DIMENSION,
    batch_size: int = MAX_BATCH_SIZE,
    force: bool = False,
    limit: int | None = None,
    embedder: Callable[..., tuple[list[list[float]], int]] = embed_with_dashscope,
    on_batch: Callable[[int, int, int], None] | None = None,
) -> dict[str, int | str]:
    if not 1 <= batch_size <= MAX_BATCH_SIZE:
        raise ValueError(f"batch_size must be between 1 and {MAX_BATCH_SIZE}")
    documents = pending_documents(model=model, dimension=dimension, force=force)
    if limit is not None:
        documents = documents[:limit]
    embedded = 0
    total_tokens = 0
    for batch in _batches(documents, batch_size):
        vectors, token_count = embedder(
            [document.text for document in batch], model=model, dimension=dimension
        )
        store_embeddings(
            batch,
            vectors,
            model=model,
            dimension=dimension,
            token_count=token_count,
        )
        embedded += len(batch)
        total_tokens += token_count
        if on_batch:
            on_batch(embedded, len(documents), total_tokens)
    return {
        "model": model,
        "dimension": dimension,
        "eligible": len(documents),
        "embedded": embedded,
        "total_tokens": total_tokens,
    }


def _batches(items: list[EmbeddingDocument], size: int) -> Iterable[list[EmbeddingDocument]]:
    for start in range(0, len(items), size):
        yield items[start:start + size]
