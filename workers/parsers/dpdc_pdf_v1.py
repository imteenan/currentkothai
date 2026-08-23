"""DPDC per-zone load-shedding PDF -> schedule-claims/1.

WHY THIS LOOKED IMPOSSIBLE, AND WHY IT ISN'T
--------------------------------------------
`pdftotext` on these files returns mojibake (`jvWkwWs wkwWDj`) because the Bangla
is set in a legacy Bijoy/SutonnyMJ font whose glyphs are mapped onto ASCII
codepoints. It is tempting to conclude the document is unreadable. It is not.
Rendering page 1 to an image shows a perfectly ordinary ruled table, and
everything the schedule actually needs survives extraction as clean ASCII:

  * serial number            "1"
  * billing feeder code      "A133E"
  * feeder load in MW        "1.2"
  * hour column headers      "9.00-10.00" ... "23.00-24.00"

And the mark for "this feeder is off in this hour" is not text at all: it is a
BLACK FILLED RECTANGLE drawn over the cell. So the schedule is recovered from
geometry, not from the font.

Only the human-readable names (substation, feeder, area) are Bijoy-encoded. Those
are decoded best-effort via `bijoy.py`; when decoding is not confident the raw
string is kept and flagged, never presented as if it were real Bangla.

LAYOUT NOTES
  * Hour columns are IRREGULAR and vary per zone. Motijheel publishes 12 columns
    and skips 13:00-14:00 and 17:00-19:00 entirely. Never assume 24.
  * A single black rectangle often spans several consecutive rows, so cell
    marking is decided by rectangle/cell OVERLAP, not containment.
  * Rows with no feeder code still carry a feeder name; the code is the stable
    identifier, so a row without one gets an id derived from its name instead.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

from workers.ingestion.common import collapse_ws, iso_utc, slugify
from workers.parsers.bijoy import decode_bijoy, looks_like_bijoy

PARSER_ADAPTER = "dpdc_pdf_v1"
PARSER_VERSION = "1.0.0"
UTILITY = "DPDC"
PUBLISHER = "Dhaka Power Distribution Company Ltd"

#: "9.00-10.00" or "9.00-\n10.00"
_HOUR_HEADER = re.compile(r"^(\d{1,2})\.00\s*-\s*(\d{1,2})\.00$")
#: DPDC billing feeder codes look like A133E, A161FB, B12Z.
_FEEDER_CODE = re.compile(r"^[A-Z]\d{2,4}[A-Z]{0,3}$")

#: A fill of exactly 0 (DeviceGray black) is the shedding mark.
_BLACK = (0, 0.0, (0,), (0.0,), (0, 0, 0), (0.0, 0.0, 0.0), [0], [0.0], [0, 0, 0])


class ParseError(Exception):
    """The document is not the shape this adapter knows how to read."""


class ScannedDocument(ParseError):
    """The page is a raster scan, so there is no text or vector grid to read.

    DPDC publishes a MIX: some zones are digital PDFs, others are CamScanner
    photographs of a printout. Distinguishing the two matters, because a scan is
    not a bug to fix in this parser - it needs OCR, and until then the honest
    move is to hand the reader the original file.
    """


def _is_black(rect: dict) -> bool:
    if not rect.get("fill"):
        return False
    c = rect.get("non_stroking_color")
    if isinstance(c, (list, tuple)):
        c = tuple(round(float(x), 3) for x in c)
    elif c is not None:
        c = round(float(c), 3)
    return c in _BLACK or c == 0.0


def _hour_columns(header: list[str]) -> list[tuple[int, int, int]]:
    """[(column_index, start_hour, end_hour)] for every hour column present."""
    out = []
    for i, cell in enumerate(header):
        text = collapse_ws(cell).replace(" ", "")
        m = _HOUR_HEADER.match(text)
        if m:
            out.append((i, int(m.group(1)), int(m.group(2))))
    return out


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def _name(raw: str) -> tuple[str, bool]:
    """Decode a Bijoy-encoded cell. Returns (text, was_decoded)."""
    s = collapse_ws(raw)
    if not s:
        return "", False
    if looks_like_bijoy(s):
        decoded = decode_bijoy(s)
        if decoded and decoded != s:
            return decoded, True
    return s, False


def hours_to_windows(hours: list[tuple[int, int]]) -> list[dict[str, str]]:
    """Merge marked (start, end) hour pairs into contiguous windows."""
    if not hours:
        return []
    hours = sorted(set(hours))
    windows = []
    cur_start, cur_end = hours[0]
    for s, e in hours[1:]:
        if s == cur_end:                       # contiguous, extend
            cur_end = e
        else:
            windows.append((cur_start, cur_end))
            cur_start, cur_end = s, e
    windows.append((cur_start, cur_end))
    return [{"start": "%02d:00" % s, "end": "%02d:00" % e} for s, e in windows]


def parse(
    pdf_path: str | Path,
    *,
    source_id: str,
    source_url: str,
    sha256: str,
    effective_date: str,
    zone: str,
    retrieved_at: str | None = None,
    tls_verified: bool = True,
    archive_path: str | None = None,
) -> dict[str, Any]:
    """Parse one DPDC zone PDF. `zone` is the NOCS name (from the URL slug)."""
    import pdfplumber

    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise ParseError("no such file: %s" % pdf_path)

    claims: list[dict[str, Any]] = []
    warnings: list[str] = []
    decoded_any = False
    hour_sets: set[tuple] = set()
    pages = 0
    rows_seen = 0
    total_black = 0

    with pdfplumber.open(str(pdf_path)) as pdf:
        pages = len(pdf.pages)
        first = pdf.pages[0]
        # A digital sheet always draws its grid as vector rectangles (Motijheel
        # has 149). A scan has none, whatever stray characters a scanner app may
        # have stamped on it, so `rects` is the reliable discriminator.
        if not first.rects:
            raise ScannedDocument(
                "%s is a raster scan (no vector grid, %d stray chars); needs OCR"
                % (pdf_path.name, len((first.extract_text() or "").strip())))
        for page_no, page in enumerate(pdf.pages, start=1):
            tables = page.find_tables()
            if not tables:
                warnings.append("page %d: no table found" % page_no)
                continue
            table = tables[0]
            grid = table.extract()
            if not grid or len(grid) < 2:
                warnings.append("page %d: table had no rows" % page_no)
                continue

            header = [collapse_ws(c) for c in grid[0]]
            hour_cols = _hour_columns(header)
            if not hour_cols:
                warnings.append("page %d: no hour columns in header" % page_no)
                continue
            hour_sets.add(tuple((s, e) for _, s, e in hour_cols))

            blacks = [r for r in page.rects if _is_black(r)]
            total_black += len(blacks)
            cell_rows = table.rows

            for r_idx, row in enumerate(grid[1:], start=1):
                rows_seen += 1
                if r_idx >= len(cell_rows):
                    break
                cells = cell_rows[r_idx].cells

                code = collapse_ws(row[1]) if len(row) > 1 else ""
                feeder_bn, dec1 = _name(row[3] if len(row) > 3 else "")
                area_bn, dec2 = _name(row[5] if len(row) > 5 else "")
                substation_bn, dec3 = _name(row[2] if len(row) > 2 else "")
                decoded_any = decoded_any or dec1 or dec2 or dec3

                if not code and not feeder_bn:
                    continue
                if code and not _FEEDER_CODE.match(code):
                    # Header repeat or a stray row; skip rather than invent a feeder.
                    if code.lower().startswith(("wewjs", "billing")):
                        continue

                load_mw = None
                raw_load = collapse_ws(row[4]) if len(row) > 4 else ""
                if raw_load:
                    try:
                        load_mw = float(re.sub(r"\s+", "", raw_load))
                    except ValueError:
                        pass

                marked: list[tuple[int, int]] = []
                for col_idx, h_start, h_end in hour_cols:
                    if col_idx >= len(cells):
                        continue
                    cell = cells[col_idx]
                    if not cell:
                        continue
                    cx0, ctop, cx1, cbottom = cell
                    cw, ch = cx1 - cx0, cbottom - ctop
                    if cw <= 0 or ch <= 0:
                        continue
                    for rect in blacks:
                        ox = _overlap(cx0, cx1, rect["x0"], rect["x1"])
                        oy = _overlap(ctop, cbottom, rect["top"], rect["bottom"])
                        # A rect may span several rows, so require it to cover most
                        # of the cell's width and a real slice of its height.
                        if ox > cw * 0.55 and oy > ch * 0.55:
                            marked.append((h_start, h_end))
                            break

                if not marked:
                    continue

                feeder = code or feeder_bn or "Unnamed feeder"
                claims.append({
                    "division": zone,
                    "division_canonical": zone,
                    "feeder": feeder,
                    "feeder_id": "dpdc:%s:%s" % (slugify(zone), slugify(feeder)),
                    "area_text": area_bn,
                    "windows": hours_to_windows(marked),
                    "weekday": None,
                    "load_mw": load_mw,
                    "feeder_name_bn": feeder_bn or None,
                    "substation_bn": substation_bn or None,
                    "billing_code": code or None,
                })

    if not claims:
        # Distinguish "this zone has no shedding today" from "we failed to see the
        # marks". Zero black rectangles anywhere on the page means the sheet really
        # is blank; black rectangles that matched no cell means our geometry is
        # wrong, and reporting that as "no shedding" would be a dangerous lie.
        if total_black:
            raise ParseError(
                "%s: found %d black marks but none aligned to an hour cell"
                % (pdf_path.name, total_black))
        warnings.append("no shedding marked anywhere on this sheet")

    if len(hour_sets) > 1:
        warnings.append("pages disagree on hour columns: %s" % sorted(hour_sets))

    hours = sorted(hour_sets)[0] if hour_sets else ()
    return {
        "schema": "schedule-claims/1",
        "utility": UTILITY,
        "publisher": PUBLISHER,
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
            "pages": pages,
            "rows_seen": rows_seen,
            "hour_column_count": len(hours),
            "hour_columns": ["%02d:00-%02d:00" % (s, e) for s, e in hours],
            "mark_detection": "black filled rectangle overlapping the cell",
            "text_encoding": "bijoy-decoded" if decoded_any else "raw-undecoded",
            "extraction_method": "pdfplumber",
            "warnings": warnings,
        },
    }


if __name__ == "__main__":  # pragma: no cover - manual probe
    doc = parse(sys.argv[1], source_id="probe", source_url="probe", sha256="0" * 64,
                effective_date="2026-08-24", zone=sys.argv[2] if len(sys.argv) > 2 else "Zone")
    print("claims:", len(doc["claims"]))
    print("hours :", doc["parse_meta"]["hour_columns"])
    for c in doc["claims"][:6]:
        print(" ", c["feeder"], c["load_mw"], [f"{w['start']}-{w['end']}" for w in c["windows"]],
              "|", c["area_text"][:30])
