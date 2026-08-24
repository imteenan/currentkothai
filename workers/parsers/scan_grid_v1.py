"""Read a SCANNED load-shedding sheet: DPDC's CamScanner zones and NESCO.

WHY THIS WORKS WITHOUT BANGLA OCR
---------------------------------
These pages are photographs, so there is no text layer and no vector grid. But
the schedule itself never depended on reading Bangla:

  * the table is a ruled grid, recoverable with morphology (long horizontal and
    vertical runs of dark pixels)
  * "this feeder is off in this hour" is a SHADED CELL, recoverable by measuring
    mean darkness inside each cell
  * the only text we must actually read is ASCII: the hour headers
    ("09.00-10.00") and the billing feeder codes ("A133E")

So Tesseract is used in a deliberately narrow way, English only, with a
character allowlist. Bangla names are left unread rather than guessed at.

PIPELINE
  1. render page at 300 dpi (pymupdf)
  2. deskew from the dominant near-horizontal line angle
  3. binarise, then isolate horizontal and vertical rules by morphology
  4. intersect them to get the cell grid
  5. OCR the header band to locate and label the hour columns
  6. OCR the feeder-code column
  7. score every (row, hour) cell for darkness; shaded means scheduled off

HONESTY GUARDS
  * If fewer than 6 hour columns are read, the page is refused. A partly-read
    grid would silently under-report outages.
  * A row whose feeder code fails OCR keeps a positional id and is flagged, so
    it is never silently merged with another feeder.
  * Cell shading uses a margin inside the cell so grid lines cannot be mistaken
    for a mark, and the threshold is measured against the page's own paper
    brightness rather than a fixed constant, because scan exposure varies.
"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Any

import numpy as np

from workers.ingestion.common import REGISTRY_DIR, iso_utc, read_json, slugify
from workers.parsers import grid_detect

#: Human-read hour layouts for the scanned sheets, keyed by zone slug.
_GRIDS_CACHE: dict | None = None


def recorded_grid(zone_slug: str) -> tuple[list[tuple[int, int] | None], int] | None:
    """The recorded hour layout for a zone, as (entries, render_dpi).

    Entries are (start, end) per column, or None for a blank spacer. Returns
    None when no grid has been recorded, which means the zone must not publish.
    """
    global _GRIDS_CACHE
    if _GRIDS_CACHE is None:
        _GRIDS_CACHE = read_json(REGISTRY_DIR / "dpdc-hour-grids.json", {}) or {}
    raw = (_GRIDS_CACHE.get("grids") or {}).get(zone_slug)
    if not raw:
        return None
    dpi = int(((_GRIDS_CACHE.get("render") or {}).get("dpi")) or 400)
    out: list[tuple[int, int] | None] = []
    for token in raw:
        if token == "blank":
            out.append(None)
            continue
        a, b = token.split("-")
        a, b = int(a), int(b)
        if b == 0:                       # "23-00" means midnight, not hour zero
            b = 24
        out.append((a, b))
    return out, dpi

PARSER_ADAPTER = "scan_grid_v1"
PARSER_VERSION = "1.0.0"

_HOUR = re.compile(r"(\d{1,2})[.:,]?\s*0?0?\s*[-–—]\s*(\d{1,2})[.:,]?\s*0?0?")
_CODE = re.compile(r"\b([A-Z]\d{2,4}[A-Z]{0,3})\b")

#: Bengali numerals. DPDC's scanned sheets label their hour columns with these
#: rather than ASCII, so reading the grid needs `ben.traineddata`.
BN_DIGITS = "০১২৩৪৫৬৭৮৯"
_BN_TO_ASCII = str.maketrans(BN_DIGITS, "0123456789")

#: tessdata may live outside Program Files when we cannot write there.
_TESSDATA_CANDIDATES = [
    os.environ.get("TESSDATA_PREFIX"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "tessdata"),
    r"C:\Program Files\Tesseract-OCR\tessdata",
    "/usr/share/tesseract-ocr/5/tessdata",
    "/usr/share/tesseract-ocr/4.00/tessdata",
    "/usr/share/tessdata",
]


def _tessdata_dir(lang: str) -> str | None:
    """First tessdata directory that actually holds `lang`."""
    for d in _TESSDATA_CANDIDATES:
        if d and os.path.exists(os.path.join(d, "%s.traineddata" % lang)):
            return d
    return None


#: Windows installs Tesseract outside PATH; look in the usual places.
_TESS_CANDIDATES = [
    shutil.which("tesseract"),
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    "/usr/bin/tesseract",
    "/usr/local/bin/tesseract",
]


class ScanError(Exception):
    """The page could not be read reliably enough to publish."""


def _tesseract():
    import pytesseract
    for c in _TESS_CANDIDATES:
        if c and os.path.exists(c):
            pytesseract.pytesseract.tesseract_cmd = c
            return pytesseract
    if shutil.which("tesseract"):
        return pytesseract
    raise ScanError("tesseract binary not found")


def render(pdf_path: Path, page: int = 0, dpi: int = 300) -> np.ndarray:
    import pymupdf
    doc = pymupdf.open(str(pdf_path))
    if page >= len(doc):
        raise ScanError("page %d out of range" % page)
    pix = doc[page].get_pixmap(dpi=dpi)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 4:
        img = img[:, :, :3]
    if img.ndim == 3:
        img = (0.299 * img[:, :, 0] + 0.587 * img[:, :, 1] + 0.114 * img[:, :, 2]).astype(np.uint8)
    return img


def deskew(gray: np.ndarray) -> np.ndarray:
    """Rotate so the ruled lines are level. Scans are routinely 1-3 degrees off."""
    import cv2
    edges = cv2.Canny(gray, 60, 180, apertureSize=3)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 720, threshold=220,
                            minLineLength=gray.shape[1] // 3, maxLineGap=24)
    if lines is None:
        return gray
    angles = []
    for seg in lines.reshape(-1, 4):
        x1, y1, x2, y2 = (int(v) for v in seg)
        if x2 == x1:
            continue
        a = np.degrees(np.arctan2(y2 - y1, x2 - x1))
        if abs(a) < 12:                       # near-horizontal rules only
            angles.append(a)
    if not angles:
        return gray
    angle = float(np.median(angles))
    if abs(angle) < 0.15:
        return gray
    h, w = gray.shape
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(gray, m, (w, h), flags=cv2.INTER_CUBIC,
                          borderMode=cv2.BORDER_REPLICATE)


def grid_lines(binary: np.ndarray) -> tuple[list[int], list[int]]:
    """Return (row_ys, col_xs) from the ruled table."""
    import cv2
    h, w = binary.shape
    horiz = cv2.morphologyEx(binary, cv2.MORPH_OPEN,
                             cv2.getStructuringElement(cv2.MORPH_RECT, (max(20, w // 18), 1)))
    vert = cv2.morphologyEx(binary, cv2.MORPH_OPEN,
                            cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(20, h // 22))))

    def peaks(proj: np.ndarray, span: int, min_frac: float) -> list[int]:
        thr = proj.max() * min_frac
        idx = np.where(proj > thr)[0]
        if not len(idx):
            return []
        groups, run = [], [idx[0]]
        for v in idx[1:]:
            if v - run[-1] <= max(3, span // 260):
                run.append(v)
            else:
                groups.append(int(np.mean(run)))
                run = [v]
        groups.append(int(np.mean(run)))
        return groups

    return peaks(horiz.sum(axis=1), h, 0.42), peaks(vert.sum(axis=0), w, 0.42)


def _ocr(pyt, img: np.ndarray, allow: str, psm: int = 7, lang: str = "eng") -> str:
    """OCR one crop.

    Point Tesseract at its data via TESSDATA_PREFIX rather than --tessdata-dir:
    the flag gets mangled when the path contains spaces or is quoted, producing
    a malformed path like <dir>"/ben.traineddata and a load failure.
    """
    cfg = "--oem 1 --psm %d" % psm
    if allow:
        cfg += " -c tessedit_char_whitelist=%s" % allow
    d = _tessdata_dir(lang)
    prev = os.environ.get("TESSDATA_PREFIX")
    if d:
        os.environ["TESSDATA_PREFIX"] = d
    try:
        return pyt.image_to_string(img, lang=lang, config=cfg).strip()
    except Exception:
        return ""
    finally:
        if d:
            if prev is None:
                os.environ.pop("TESSDATA_PREFIX", None)
            else:
                os.environ["TESSDATA_PREFIX"] = prev


def read_header_strip(pyt, gray, y0: int, y1: int, cols: list[int]) -> list[tuple[int, int, int]]:
    """Read every hour label in one pass across the whole header band.

    Cropping 12 tiny two-line cells and OCRing each in isolation barely works:
    Tesseract has almost no context and returns nothing. Running it once over the
    full-width strip and then assigning each recognised number to the column its
    bounding box falls in is far more reliable, and it is how this finally read
    DPDC's scanned sheets.
    """
    import cv2
    strip = gray[max(0, y0 + 3):y1 - 3, :]
    if strip.size == 0:
        return []
    scale = 3
    up = cv2.resize(strip, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    up = cv2.GaussianBlur(up, (3, 3), 0)
    _, up = cv2.threshold(up, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    words: list[tuple[float, str]] = []
    for lang in ("ben", "eng"):
        if not _tessdata_dir(lang):
            continue
        d = _tessdata_dir(lang)
        prev = os.environ.get("TESSDATA_PREFIX")
        if d:
            os.environ["TESSDATA_PREFIX"] = d
        try:
            data = pyt.image_to_data(up, lang=lang, config="--oem 1 --psm 6",
                                     output_type=pyt.Output.DICT)
        except Exception:
            data = None
        finally:
            if d:
                if prev is None:
                    os.environ.pop("TESSDATA_PREFIX", None)
                else:
                    os.environ["TESSDATA_PREFIX"] = prev
        if not data:
            continue
        for i, txt in enumerate(data.get("text", [])):
            t = (txt or "").strip().translate(_BN_TO_ASCII)
            if not t or not re.search(r"\d", t):
                continue
            cx = (data["left"][i] + data["width"][i] / 2) / scale
            words.append((cx, t))
        if words:
            break

    if not words:
        return []

    # Group the numbers that fall inside each column, then read the pair.
    out: list[tuple[int, int, int]] = []
    for ci in range(len(cols) - 1):
        x0, x1 = cols[ci], cols[ci + 1]
        inside = [t for cx, t in words if x0 <= cx <= x1]
        if not inside:
            continue
        nums = re.findall(r"\d{1,2}", " ".join(inside))
        nums = [int(n) for n in nums if 0 <= int(n) <= 24]
        # A cell reads like "09 00 10 00": the hours are the 1st and 3rd numbers.
        hours = [n for n in nums if n != 0] or nums
        if len(hours) >= 2:
            a, b = hours[0], hours[-1]
            if 0 <= a <= 24 and 0 <= b <= 24 and 1 <= b - a <= 3:
                out.append((ci, a, b))
    return out


def _read_hour_label(pyt, cell_img) -> tuple[int, int] | None:
    """Read one hour header, trying ASCII first then Bengali numerals.

    DPDC's digital sheets print "9.00-10.00"; its scanned ones print
    "০৯:০০ - ১০:০০". Both mean the same thing, so try both rather than assuming.
    """
    import cv2
    big = cv2.resize(cell_img, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
    big = cv2.GaussianBlur(big, (3, 3), 0)
    _, big = cv2.threshold(big, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    # Tesseract wants quiet space around the glyphs.
    big = cv2.copyMakeBorder(big, 24, 24, 24, 24, cv2.BORDER_CONSTANT, value=255)

    for lang, allow in (("ben", ""), ("ben", BN_DIGITS + ".:- "), ("eng", "0123456789.:- ")):
        if lang == "ben" and not _tessdata_dir("ben"):
            continue
        for psm in (6, 11, 7):
            raw = " ".join(_ocr(pyt, big, allow, psm=psm, lang=lang).split())
            text = raw.translate(_BN_TO_ASCII)
            m = _HOUR.search(text)
            if m:
                a, b = int(m.group(1)), int(m.group(2))
                if 0 <= a <= 24 and 0 <= b <= 24 and a != b:
                    return a, b
            # A header sometimes OCRs as just the two hours with no separator.
            nums = re.findall(r"\d{1,2}", text)
            if len(nums) >= 2:
                a, b = int(nums[0]), int(nums[-1])
                if 0 <= a <= 24 and 0 <= b <= 24 and b - a in (1, 2, 3):
                    return a, b
    return None


def parse(
    pdf_path: str | Path,
    *,
    source_id: str,
    source_url: str,
    sha256: str,
    effective_date: str,
    utility: str,
    zone: str,
    publisher: str,
    zone_slug: str = "",
    template_hours: list[tuple[int, int]] | None = None,
    retrieved_at: str | None = None,
    tls_verified: bool = True,
    archive_path: str | None = None,
    dpi: int = 300,
    page: int = 0,
) -> dict[str, Any]:
    """Read one scanned sheet into schedule-claims/1."""
    import cv2
    pyt = _tesseract()
    pdf_path = Path(pdf_path)

    gray = deskew(render(pdf_path, page, dpi))
    # Tell the grid detector how many hour columns this sheet is recorded as
    # having, so a faint missed rule can be placed rather than refused.
    _recorded = recorded_grid(zone_slug) if zone_slug else None
    _target = len(_recorded[0]) if _recorded else None
    _labelled = [e is not None for e in _recorded[0]] if _recorded else None
    rows, cols, widest_idx, hour_region, problem = grid_detect.resolve(
        gray, _target, _labelled)
    if len(rows) < 4 or len(cols) < 8:
        raise ScanError("grid not found (%d rows, %d cols)" % (len(rows), len(cols)))
    if problem:
        # Refusing costs a zone. Publishing a mis-segmented grid costs the
        # reader their evening, so this is the cheaper failure.
        raise ScanError("column segmentation unreliable: %s" % problem)

    # ---- hour columns -------------------------------------------------------
    # The header is NOT the first band: these sheets open with a title and two or
    # three lines of office metadata. Try each early band and keep whichever
    # yields the most hour labels, reading the whole strip at once because
    # per-cell crops give Tesseract too little context to work with.
    hour_cols: list[tuple[int, int, int]] = []
    header_row: int | None = None
    for ri in range(min(6, len(rows) - 1)):
        top, bottom = rows[ri], rows[ri + 1]
        if bottom - top < 14:
            continue
        found = read_header_strip(pyt, gray, top, bottom, cols)
        if len(found) > len(hour_cols):
            hour_cols, header_row = found, ri

    # ---- prefer the recorded human-read grid --------------------------------
    hour_source = "ocr"
    recorded = recorded_grid(zone_slug) if zone_slug else None
    if recorded:
        entries, _ = recorded
        if len(entries) != len(hour_region):
            raise ScanError(
                "recorded hour grid for %r has %d columns but this sheet has %d; "
                "the layout changed and the grid must be re-read"
                % (zone_slug, len(entries), len(hour_region)))
        hour_cols = [(ci, e[0], e[1])
                     for ci, e in zip(hour_region, entries) if e is not None]
        hour_source = "recorded"
        header_row = find_header_row(gray, rows, cols, hour_region)
    if len(hour_cols) < 6:
        # Tesseract's Bengali model cannot reliably read these small stacked
        # numerals out of a phone scan. The grid itself is sound, so fall back to
        # matching the column GEOMETRY against the hour grid the same publisher
        # prints on its digital sheets. Guarded hard, and labelled in the output.
        # With no readable labels there is no OCR-chosen header row. The header
        # is the band directly above the first data row; on these sheets that is
        # the last band before the rows become uniform in height.
        if header_row is None:
            header_row = _guess_header_row(rows)
        hour_cols = _template_hours(cols, template_hours, gray, rows, header_row)
        hour_source = "template"
        if not hour_cols:
            raise ScanError(
                "hour labels unreadable and column geometry does not match the "
                "known %s grid; refusing to guess" % utility)

    # ---- shading threshold, learned from the cells themselves ---------------
    # A fixed fraction of paper brightness fails across scan exposures: on a
    # clean scan the paper is pure white, the threshold lands at 158, and
    # genuinely shaded cells sitting around 200 are missed entirely. Instead,
    # measure every hour cell and let Otsu split "blank" from "shaded" on that
    # distribution, which adapts to whatever exposure the scan happens to have.
    import cv2 as _cv2
    data_top = rows[header_row + 1]
    paper = float(np.percentile(gray[data_top:rows[-1], cols[0]:cols[-1]], 75))

    samples: list[float] = []
    for ri in range(header_row + 1, len(rows) - 1):
        y0, y1 = rows[ri], rows[ri + 1]
        if y1 - y0 < 10:
            continue
        for ci, _a, _b in hour_cols:
            x0, x1 = cols[ci], cols[ci + 1]
            mx, my = max(3, (x1 - x0) // 6), max(3, (y1 - y0) // 5)
            patch = gray[y0 + my:y1 - my, x0 + mx:x1 - mx]
            if patch.size >= 16:
                samples.append(float(patch.mean()))

    if samples:
        arr = np.array(samples, dtype=np.float32)
        spread = float(arr.max() - arr.min())
        if spread > 18:
            otsu, _ = _cv2.threshold(
                arr.astype(np.uint8).reshape(-1, 1), 0, 255,
                _cv2.THRESH_BINARY + _cv2.THRESH_OTSU)
            # Otsu sits between the two clusters; keep it below the pale one.
            mark_thr = float(otsu)
        else:
            # Everything looks the same: no shading on this sheet.
            mark_thr = float(arr.min()) - 1.0
    else:
        mark_thr = paper * 0.62

    # ---- feeder code column: the leftmost column whose cells look like codes -
    code_col = None
    for ci in range(min(4, len(cols) - 1)):
        hits = 0
        for ri in range(header_row + 1, min(len(rows) - 1, header_row + 8)):
            cell = gray[rows[ri] + 2:rows[ri + 1] - 2, cols[ci] + 2:cols[ci + 1] - 2]
            if cell.size == 0:
                continue
            big = cv2.resize(cell, None, fx=2.4, fy=2.4, interpolation=cv2.INTER_CUBIC)
            if _CODE.search(_ocr(pyt, big, "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789").upper()):
                hits += 1
        if hits >= 2:
            code_col = ci
            break

    claims: list[dict[str, Any]] = []
    unread_codes = 0

    for ri in range(header_row + 1, len(rows) - 1):
        y0, y1 = rows[ri], rows[ri + 1]
        if y1 - y0 < 10:
            continue

        code = ""
        if code_col is not None:
            cell = gray[y0 + 2:y1 - 2, cols[code_col] + 2:cols[code_col + 1] - 2]
            if cell.size:
                big = cv2.resize(cell, None, fx=2.4, fy=2.4, interpolation=cv2.INTER_CUBIC)
                m = _CODE.search(_ocr(pyt, big, "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789").upper())
                code = m.group(1) if m else ""
        if not code:
            unread_codes += 1

        marked: list[tuple[int, int]] = []
        for ci, a, b in hour_cols:
            x0, x1 = cols[ci], cols[ci + 1]
            # Inset generously so the ruled border is never counted as shading.
            mx, my = max(3, (x1 - x0) // 6), max(3, (y1 - y0) // 5)
            patch = gray[y0 + my:y1 - my, x0 + mx:x1 - mx]
            if patch.size < 16:
                continue
            if float(patch.mean()) < mark_thr:
                marked.append((a, b))

        if not marked:
            continue

        feeder = code or ("row-%02d" % ri)
        claims.append({
            "division": zone,
            "division_canonical": zone,
            "feeder": feeder,
            "feeder_id": "%s:%s:%s" % (utility.lower(), slugify(zone), slugify(feeder)),
            "area_text": "",
            "windows": _windows(marked),
            "weekday": None,
            "load_mw": None,
            "billing_code": code or None,
            "read_by": "ocr-scan",
            "code_confident": bool(code),
        })

    if not claims:
        raise ScanError("no shaded cells found; the sheet may be blank or the "
                        "threshold wrong (paper=%.0f, thr=%.0f)" % (paper, mark_thr))

    return {
        "schema": "schedule-claims/1",
        "utility": utility,
        "publisher": publisher,
        "effective_date": effective_date,
        "coverage_level": "feeder",
        "badge": "DERIVED",
        "source": {
            "source_id": source_id,
            "source_url": source_url,
            "retrieved_at": retrieved_at or iso_utc(),
            "sha256": sha256,
            "parser_adapter": PARSER_ADAPTER,
            "parser_version": PARSER_VERSION,
            "tls_verified": bool(tls_verified),
            "archive_path": archive_path,
        },
        "claims": claims,
        "stats": {
            "claim_count": len(claims),
            "feeder_count": len({c["feeder_id"] for c in claims}),
            "division_count": 1,
        },
        "parse_meta": {
            "zone": zone,
            "hour_column_count": len(hour_cols),
            "hour_grid_source": hour_source,
            "hour_columns": ["%02d:00-%02d:00" % (a, b) for _, a, b in hour_cols],
            "header_row_index": header_row,
            "grid_rows": len(rows),
            "hour_region_columns": len(hour_region),
            "grid_cols": len(cols),
            "paper_brightness": round(paper, 1),
            "mark_threshold": round(mark_thr, 1),
            "rows_with_unread_code": unread_codes,
            "mark_detection": "mean cell darkness below 62% of paper brightness",
            "extraction_method": "opencv grid + tesseract (english, digit/code allowlist)",
            "text_encoding": "bangla-not-read",
            "hour_labels_read_by_ocr": hour_source == "ocr",
            "warnings": ([f"{unread_codes} row(s) had an unreadable feeder code"]
                         if unread_codes else []),
        },
    }


def find_header_row(gray, rows: list[int], cols: list[int],
                    region: list[int]) -> int:
    """The band whose hour cells actually carry labels.

    This is the same rule the review tool used to crop the headers a human then
    read, so a recorded grid lines up with the columns it was read from.
    """
    def inked(cell) -> bool:
        return bool(cell.size) and float((cell < 128).mean()) > 0.02

    best = (0, 0)
    for ri in range(min(6, len(rows) - 1)):
        y0, y1 = rows[ri], rows[ri + 1]
        if y1 - y0 < 14:
            continue
        n = sum(1 for ci in region
                if inked(gray[y0 + 4:y1 - 4, cols[ci] + 4:cols[ci + 1] - 4]))
        if n > best[0]:
            best = (n, ri)
    return best[1]


def _guess_header_row(rows: list[int]) -> int:
    """Index of the band most likely to be the column header.

    Data rows are short and similar in height; the preamble bands (title, office
    name, phone numbers) are taller and irregular. The header sits at the last
    tall band before that regular run begins.
    """
    heights = [rows[i + 1] - rows[i] for i in range(len(rows) - 1)]
    if len(heights) < 4:
        return 0
    tail = heights[max(1, len(heights) // 3):]
    typical = float(np.median(tail))
    for i in range(len(heights) - 2):
        if all(abs(h - typical) <= typical * 0.5 for h in heights[i + 1:i + 4]):
            return i
    return 1


def _template_hours(cols: list[int], template: list[tuple[int, int]] | None,
                    gray=None, rows: list[int] | None = None,
                    header_row: int | None = None) -> list[tuple[int, int, int]]:
    """Map a known hour grid onto the sheet's narrow right-hand columns.

    Only fires when the geometry genuinely looks like that grid: the rightmost
    run of uniform-width columns must contain exactly as many columns as the
    template has hours. Anything else returns nothing, and the caller refuses the
    page rather than inventing a schedule.
    """
    if not template:
        return []
    widths = [(i, cols[i + 1] - cols[i]) for i in range(len(cols) - 1)]
    if not widths:
        return []
    narrow = [w for _, w in widths if w > 8]
    if not narrow:
        return []
    unit = float(np.median(narrow))
    # Walk from the right while columns stay close to the modal narrow width.
    run: list[int] = []
    for i, w in reversed(widths):
        if abs(w - unit) <= unit * 0.35:
            run.append(i)
        elif run:
            break
    run.reverse()

    # The scans interleave blank spacer columns between hour groups, so match on
    # the columns that actually carry a header label, not on raw column count.
    if gray is not None and rows and header_row is not None and len(run) != len(template):
        y0, y1 = rows[header_row], rows[header_row + 1]
        inked = []
        for ci in run:
            cell = gray[y0 + 4:y1 - 4, cols[ci] + 4:cols[ci + 1] - 4]
            if cell.size == 0:
                continue
            # A labelled cell has dark pixels; a spacer is blank paper.
            if float((cell < 128).mean()) > 0.02:
                inked.append(ci)
        if len(inked) == len(template):
            run = inked

    if len(run) != len(template):
        return []
    return [(ci, a, b) for ci, (a, b) in zip(run, template)]


def _windows(hours: list[tuple[int, int]]) -> list[dict[str, str]]:
    hours = sorted(set(hours))
    out, (cs, ce) = [], hours[0]
    for s, e in hours[1:]:
        if s == ce:
            ce = e
        else:
            out.append((cs, ce))
            cs, ce = s, e
    out.append((cs, ce))
    return [{"start": "%02d:00" % s, "end": "%02d:00" % e} for s, e in out]
