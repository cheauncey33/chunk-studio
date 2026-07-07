"""PyMuPDF wrappers: page rendering, region cropping, embedded-text extraction.

All functions are sync and CPU-bound; routers wrap them in run_in_executor so
the event loop is never blocked.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import fitz  # PyMuPDF

from . import config

_PREVIEW_DPI = 150
_CROP_DPI = 300

# Heuristic for "is the extracted text actually usable". Some Chinese PDFs
# (notably standards/GBT) embed fonts without a ToUnicode CMap, so get_text
# returns glyph codes that look like "!#\$&" garbage. We count word characters
# (re.UNICODE includes CJK); if too few relative to total non-space chars, we
# treat the text as not-meaningful and mark the chunk as pending OCR.
_WORD_RE = re.compile(r"[\w]", re.UNICODE)


def _is_meaningful_text(text: str) -> bool:
    if not text:
        return False
    non_space = [c for c in text if not c.isspace()]
    if not non_space:
        return False
    word_count = sum(1 for c in non_space if _WORD_RE.match(c))
    return word_count / len(non_space) >= 0.4


def _open(file_rel: str) -> fitz.Document:
    return fitz.open(str(config.from_rel(file_rel)))


def page_count(file_rel: str) -> int:
    with _open(file_rel) as doc:
        return doc.page_count


def render_page_png(file_rel: str, page_no: int, dpi: int = _PREVIEW_DPI) -> bytes:
    """Render a page to PNG bytes. Cached on disk keyed by file_sha/page/dpi."""
    sha = _file_sha(file_rel)
    cache = config.PAGECACHE_DIR / f"{sha}_p{page_no}_d{dpi}.png"
    if cache.exists():
        return cache.read_bytes()
    with _open(file_rel) as doc:
        page = doc.load_page(page_no)
        pix = page.get_pixmap(dpi=dpi)
        data = pix.tobytes("png")
    cache.write_bytes(data)
    return data


@dataclass
class CropResult:
    crop_rel: str       # relative path to 300DPI PNG under DATA_DIR
    text: str           # embedded text from clip (may be empty)
    text_source: str    # 'digital' if text non-empty else 'pending'
    width: int          # crop pixel width
    height: int         # crop pixel height


def crop_region(file_rel: str, page_no: int, bbox: dict, dpi: int = _CROP_DPI) -> CropResult:
    """Crop a normalized bbox region from a page.

    bbox is {x,y,w,h} in 0..1 relative to the page's PDF-point dimensions.
    Returns a 300DPI PNG path + any embedded text intersecting the clip.
    """
    sha = _file_sha(file_rel)
    bbox_key = f"{bbox['x']:.5f}_{bbox['y']:.5f}_{bbox['w']:.5f}_{bbox['h']:.5f}"
    name = f"{sha}_p{page_no}_{bbox_key}_d{dpi}.png"
    crop_path = config.CROPS_DIR / name
    # content-addressed: re-selecting same region dedupes automatically.

    with _open(file_rel) as doc:
        page = doc.load_page(page_no)
        rect = page.rect  # PDF-point rect (handles mediabox origin)
        x0 = rect.x0 + bbox["x"] * rect.width
        y0 = rect.y0 + bbox["y"] * rect.height
        x1 = x0 + bbox["w"] * rect.width
        y1 = y0 + bbox["h"] * rect.height
        clip = fitz.Rect(x0, y0, x1, y1)

        pix = page.get_pixmap(clip=clip, dpi=dpi)
        w, h = pix.width, pix.height
        if not crop_path.exists():
            crop_path.write_bytes(pix.tobytes("png"))

        # Embedded text intersecting the clip. For born-digital PDFs this often
        # eliminates the need for OCR entirely. If the font lacks ToUnicode the
        # result is glyph-code garbage — _is_meaningful_text catches that and we
        # fall back to pending (waiting for OCR) rather than storing junk.
        raw_text = page.get_text("text", clip=clip).strip()
        text = raw_text if _is_meaningful_text(raw_text) else ""

    text_source = "digital" if text else "pending"
    return CropResult(
        crop_rel=config.to_rel(crop_path),
        text=text,
        text_source=text_source,
        width=int(w),
        height=int(h),
    )


def _file_sha(file_rel: str) -> str:
    """Content sha (first 16 hex chars) for caching keys."""
    p = config.from_rel(file_rel)
    h = hashlib.sha256()
    h.update(p.name.encode("utf-8"))
    h.update(str(p.stat().st_mtime).encode("utf-8"))
    return h.hexdigest()[:16]


# Eagerly verify the import works (PyMuPDF raises a clear error if missing).
try:
    fitz.VersionBind
except AttributeError:
    pass
