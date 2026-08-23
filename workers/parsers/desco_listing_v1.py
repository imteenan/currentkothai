"""Find the CURRENT DESCO schedule PDF from their load-management page.

The PDF lives on Oracle object storage under a UUID filename that changes on
every publish, so the URL must be discovered, never hardcoded. The page also
carries older editions, so we prefer the newest year/month path segment.
"""
from __future__ import annotations

import re
from typing import Any

from workers.ingestion.common import collapse_ws

#: .../o/office-desco/<year>/<month>/<uuid>.pdf
_DESCO_PDF = re.compile(
    r"https://objectstorage\.[^\"'\s>]+/office-desco/(\d{4})/(\d{1,2})/[^\"'\s>]+\.pdf",
    re.IGNORECASE)

_ANY_PDF = re.compile(r"https?://[^\"'\s>]+\.pdf", re.IGNORECASE)


def discover(html: str | bytes) -> list[dict[str, Any]]:
    """Return candidate schedule PDFs, newest first.

    Each candidate is ``{url, year, month, label}``. The caller decides which to
    ingest; we do not silently pick one and hide the alternatives.
    """
    if isinstance(html, bytes):
        html = html.decode("utf-8", "replace")

    found: dict[str, dict[str, Any]] = {}

    for m in _DESCO_PDF.finditer(html):
        url = m.group(0)
        found[url] = {
            "url": url,
            "year": int(m.group(1)),
            "month": int(m.group(2)),
            "label": _label_near(html, m.start()),
        }

    if not found:
        # The page may have moved to a different storage host; fall back to any
        # PDF link and let validation catch a wrong document.
        for m in _ANY_PDF.finditer(html):
            url = m.group(0)
            found.setdefault(url, {"url": url, "year": 0, "month": 0,
                                   "label": _label_near(html, m.start())})

    return sorted(found.values(), key=lambda c: (c["year"], c["month"]), reverse=True)


def _label_near(html: str, index: int, window: int = 300) -> str:
    """Best-effort human label for a link, taken from the text just before it."""
    chunk = html[max(0, index - window):index]
    chunk = re.sub(r"<[^>]+>", " ", chunk)
    text = collapse_ws(chunk)
    return text[-90:] if text else ""
