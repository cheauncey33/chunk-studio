"""MinerU cloud OCR adapter.

Flow per chunk:
  1. Wrap the chunk's 300-DPI crop PNG into a one-page PDF.
  2. POST /api/v4/file-urls/batch to get a batch id and presigned PUT URL.
  3. PUT the PDF bytes to the presigned URL without auth headers.
  4. Poll GET /api/v4/extract-results/batch/{batch_id}.
  5. Fetch the markdown URL or result zip and return markdown text.
"""
from __future__ import annotations

import asyncio
import io
import os
import time
import zipfile
from dataclasses import dataclass
from typing import Any

import fitz
import httpx

from .. import config, db

DEFAULT_BASE = "https://mineru.net"
DEFAULT_MODEL = "vlm"
POLL_INTERVAL = 3.0
POLL_MAX_SECONDS = 240


@dataclass
class OCRResult:
    text: str
    ok: bool
    error: str = ""


@dataclass
class ExtractResult:
    text: str
    ok: bool
    error: str = ""
    zip_bytes: bytes | None = None


def _get_settings() -> dict[str, str]:
    token = db.get_setting("mineru.token", "") or os.environ.get("MINERU_TOKEN", "")
    base = db.get_setting("mineru.base_url", DEFAULT_BASE)
    model = db.get_setting("mineru.model_version", DEFAULT_MODEL)
    return {
        "token": token.strip(),
        "base": base.strip().rstrip("/") or DEFAULT_BASE,
        "model": model.strip() or DEFAULT_MODEL,
    }


def _json_or_error(res: httpx.Response) -> tuple[dict[str, Any], str]:
    try:
        data = res.json()
    except Exception:
        return {}, f"HTTP {res.status_code}: {res.text[:300]}"
    if not isinstance(data, dict):
        return {}, f"HTTP {res.status_code}: unexpected JSON {type(data).__name__}"
    return data, ""


def _crop_to_pdf_bytes(crop_abs: str) -> bytes:
    """Wrap a PNG crop into a one-page PDF at native pixel size."""
    doc = fitz.open()
    try:
        pix = fitz.Pixmap(crop_abs)
        page = doc.new_page(width=pix.width, height=pix.height)
        page.insert_image(fitz.Rect(0, 0, pix.width, pix.height), filename=crop_abs)
        return doc.tobytes()
    finally:
        doc.close()


async def ocr_chunk(chunk_id: str) -> OCRResult:
    s = _get_settings()
    if not s["token"]:
        return OCRResult("", False, "mineru.token is not configured")

    row = db.get_conn().execute(
        "SELECT crop_path FROM chunks WHERE id=?", (chunk_id,)
    ).fetchone()
    if not row or not row["crop_path"]:
        return OCRResult("", False, "chunk has no crop image")

    crop_abs = str(config.from_rel(row["crop_path"]))
    pdf_bytes = await asyncio.to_thread(_crop_to_pdf_bytes, crop_abs)
    result = await extract_pdf_bytes(pdf_bytes, f"{chunk_id}.pdf", chunk_id)
    return OCRResult(result.text, result.ok, result.error)


async def parse_file(file_id: str) -> ExtractResult:
    row = db.get_conn().execute(
        "SELECT name, path FROM files WHERE id=?", (file_id,)
    ).fetchone()
    if not row:
        return ExtractResult("", False, "file not found")
    pdf_path = config.from_rel(row["path"])
    try:
        pdf_bytes = await asyncio.to_thread(pdf_path.read_bytes)
    except OSError as exc:
        return ExtractResult("", False, f"read pdf failed: {exc}")
    return await extract_pdf_bytes(pdf_bytes, row["name"], file_id)


async def extract_pdf_bytes(pdf_bytes: bytes, name: str, data_id: str) -> ExtractResult:
    s = _get_settings()
    if not s["token"]:
        return ExtractResult("", False, "mineru.token is not configured")
    headers = {"Authorization": f"Bearer {s['token']}", "Accept": "*/*"}
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            f"{s['base']}/api/v4/file-urls/batch",
            headers={**headers, "Content-Type": "application/json"},
            json={
                "files": [{"name": name, "data_id": data_id}],
                "model_version": s["model"],
            },
        )
        data, parse_error = _json_or_error(response)
        if parse_error:
            return ExtractResult("", False, f"file-urls failed: {parse_error}")
        if data.get("code") != 0:
            return ExtractResult(
                "",
                False,
                "file-urls failed: "
                f"HTTP {response.status_code}, code={data.get('code')}, msg={data.get('msg')}",
            )

        payload = data.get("data") or {}
        batch_id = payload.get("batch_id")
        file_urls = payload.get("file_urls") or []
        if not batch_id or not file_urls:
            return ExtractResult("", False, f"file-urls returned incomplete data: {payload}")

        put_response = await client.put(file_urls[0], content=pdf_bytes)
        if put_response.status_code not in (200, 201):
            return ExtractResult(
                "",
                False,
                f"upload failed: HTTP {put_response.status_code} {put_response.text[:200]}",
            )

        deadline = time.monotonic() + POLL_MAX_SECONDS
        last_state = None
        while time.monotonic() < deadline:
            await asyncio.sleep(POLL_INTERVAL)
            query = await client.get(
                f"{s['base']}/api/v4/extract-results/batch/{batch_id}",
                headers=headers,
            )
            query_data, parse_error = _json_or_error(query)
            if parse_error:
                return ExtractResult("", False, f"query failed: {parse_error}")
            if query_data.get("code") != 0:
                return ExtractResult(
                    "",
                    False,
                    "query failed: "
                    f"HTTP {query.status_code}, code={query_data.get('code')}, msg={query_data.get('msg')}",
                )

            task_data = query_data.get("data")
            results = _extract_results(task_data)
            state = _extract_state(task_data)
            item = results[0] if results else task_data
            if state is None:
                state = _extract_state(item)
            last_state = state

            if state in ("fail", "failed"):
                return ExtractResult("", False, f"task failed: {_find_key(item, 'err_msg') or state}")
            if state in ("done", "success") or results:
                markdown_url = _find_key(item, "markdown_url")
                zip_url = _find_key(item, "full_zip_url")
                if not markdown_url and not zip_url:
                    continue
                if markdown_url:
                    markdown = await client.get(markdown_url)
                    if markdown.status_code != 200:
                        return ExtractResult(
                            "",
                            False,
                            f"fetch markdown failed: HTTP {markdown.status_code}",
                        )
                    zip_bytes = await _fetch_zip_bytes(client, zip_url) if zip_url else None
                    return ExtractResult(_decode_markdown_bytes(markdown.content), True, zip_bytes=zip_bytes)
                return await _fetch_markdown_from_zip(client, zip_url)

        return ExtractResult("", False, f"timeout (last state={last_state})")


async def _fetch_zip_bytes(client: httpx.AsyncClient, zip_url: str) -> bytes | None:
    response = await client.get(zip_url)
    if response.status_code != 200:
        return None
    return response.content


async def _fetch_markdown_from_zip(client: httpx.AsyncClient, zip_url: str) -> ExtractResult:
    zip_bytes = await _fetch_zip_bytes(client, zip_url)
    if zip_bytes is None:
        return ExtractResult("", False, "fetch result zip failed")

    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            names = zf.namelist()
            markdown_name = _pick_markdown_name(names)
            if not markdown_name:
                return ExtractResult("", False, f"result zip has no markdown file: {names[:20]}")
            data = zf.read(markdown_name)
    except zipfile.BadZipFile:
        return ExtractResult("", False, "result zip is not a valid zip file")

    return ExtractResult(_decode_markdown_bytes(data), True, zip_bytes=zip_bytes)


def _decode_markdown_bytes(data: bytes) -> str:
    for encoding in ("utf-8", "utf-8-sig", "gb18030"):
        try:
            return _repair_mojibake(data.decode(encoding))
        except UnicodeDecodeError:
            continue
    return _repair_mojibake(data.decode("utf-8", errors="replace"))


def _repair_mojibake(text: str) -> str:
    """Repair common UTF-8-as-Latin-1 mojibake from downloaded result zips."""
    markers = ("Ã", "Â", "â", "æ", "ç", "è", "é")
    if sum(text.count(marker) for marker in markers) < 3:
        return text
    try:
        repaired = text.encode("latin1").decode("utf-8")
    except UnicodeError:
        return text
    return repaired if _mojibake_score(repaired) < _mojibake_score(text) else text


def _mojibake_score(text: str) -> int:
    return sum(text.count(marker) for marker in ("Ã", "Â", "â", "æ", "ç", "è", "é", "�"))


def _pick_markdown_name(names: list[str]) -> str | None:
    for name in names:
        if name.endswith("/full.md") or name == "full.md":
            return name
    for name in names:
        if name.lower().endswith(".md"):
            return name
    return None


def _extract_results(data: Any) -> list[Any]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        results = data.get("extract_result") or data.get("result") or data.get("results") or []
        return results if isinstance(results, list) else []
    return []


def _extract_state(data: Any) -> str | None:
    if isinstance(data, list):
        if data and isinstance(data[0], dict):
            return data[0].get("state") or data[0].get("status")
        return None
    if isinstance(data, dict):
        return data.get("state") or data.get("status")
    return None


def _find_key(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for value in obj.values():
            result = _find_key(value, key)
            if result:
                return result
    elif isinstance(obj, list):
        for value in obj:
            result = _find_key(value, key)
            if result:
                return result
    return None
