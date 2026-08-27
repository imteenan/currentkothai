"""DESCO "Possible Load Shedding Schedule" PDF -> schedule-claims/1.

TWO layout generations are in circulation and both are handled:

    2022 (4 metadata columns, 28 total, 18 pages):
      | S&D Division | Area Under the feeder | Feeder Name | Time | 00:00 | ... |
    2026 (5 metadata columns, 29 total, 28 pages) -- adds Load:
      | S&D Division | Area Under the feeder | Feeder Name | Load (MW) | Time | 00:00 | ... |

* NEVER hardcode the metadata column count. Doing so shifts every window on the
  2026 layout one hour late and invents a phantom 00:00-01:00 window on every
  single feeder, because the "LS" marker column lands where hour 0 is expected.
  ``_find_meta_cols`` locates the first hour header instead, and
  ``_canonical_row`` normalises both layouts onto one internal shape.
* The table is fully ruled, so pdfplumber's cell detection is reliable and is
  the primary extraction path. ``pdftotext -layout`` is the fallback, but note
  it mis-associates wrapped "Area Under the feeder" text with the *next* row,
  so the fallback is deliberately treated as lower confidence.
* Each hour header is a two-line range ("14:00" over "15:00"), giving exactly
  24 hour columns after the metadata columns.
* The MARK in an hour cell is not a tick -- it is the load being shed in that
  hour, in kW (e.g. "140"). Some cells legitimately contain "0". Any non-empty
  cell means "this feeder is on the shedding schedule for this hour"; we do not
  reinterpret the magnitude, and we do not drop "0" rows, because doing either
  would be us inventing a reading the document does not state.
* The weekday is printed as an English word on page 1 ("Monday"). There is no
  date inside the document -- the date comes from the listing page or from the
  retrieval time, and is supplied by the caller.

Division names in the source are NOT clean, and there are two separate problems.

Typos ("Ibrhimpur", "Joarshara", "Uttara Eas") are folded through
``data/registry/desco-division-aliases.json`` and nowhere else, so that every
mapping is reviewable in one diff. The verbatim string always stays in
``division``; a name that cannot be folded yields ``division_canonical: null``
and is excluded from geographic matching rather than guessed at.

The second problem looked like the first and is not. Two rows came out as
"DhakkhKinakohwanla" and "MGuolhsahkahnali:", and were recorded as two division
cells printed over each other and unrecoverable. They are neither. The "Area
Under the feeder" column is centre-aligned and is not clipped, so an area string
wider than its cell overflows and prints straight through the division and
feeder cells beside it, on the same baseline -- the feeders came out as
"eer ArKotawlar" and "WaterN PIuPmSOp.M" for the same reason. Ordering a cell's
glyphs by x then zips the two texts together. ``_extract_table_cells`` separates
them by the runs they were printed as, so the rows read Dhakkhinkhan/Kawlar and
Gulshan/NIPSOM. There is no "Kaowla" or "Mohakhali" division in the schedule.
"""
from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from workers.ingestion.common import (
    EN_WEEKDAY,
    REGISTRY_DIR,
    collapse_ws,
    iso_utc,
    read_json,
    slugify,
)

PARSER_ADAPTER = "desco_pdf_v1"
PARSER_VERSION = "1.0.0"
UTILITY = "DESCO"
PUBLISHER = "Dhaka Electric Supply Company PLC"

#: Canonical internal row shape, independent of which layout the PDF uses:
#:   [0] S&D division  [1] area text  [2] feeder name  [3] load MW  [4] "LS" marker
#: followed by exactly HOURS hour cells.
#:
#: The number of metadata columns in the SOURCE varies by document generation:
#: the 2022 layout has 4 (no Load column), the 2026 layout has 5. Hardcoding it
#: silently shifts every window by an hour, so the extractors locate the first
#: hour-header column instead and normalise into the canonical shape above.
CANON_META = 5
HOURS = 24

#: Only used to sanity-check what the extractors detect.
KNOWN_META_COL_COUNTS = (4, 5)

_HOUR_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


class ParseError(Exception):
    """The document is not the shape this adapter knows how to read."""


# ------------------------------------------------------------------ helpers


def _norm_division(s: str) -> str:
    return collapse_ws(s)


_ALIASES_CACHE: dict[str, Any] | None = None


def _division_aliases() -> dict[str, Any]:
    """Load the reviewable alias table once per process."""
    global _ALIASES_CACHE
    if _ALIASES_CACHE is None:
        _ALIASES_CACHE = read_json(
            REGISTRY_DIR / "desco-division-aliases.json", default={}) or {}
    return _ALIASES_CACHE


def canonical_division(raw: str) -> tuple[str | None, str | None]:
    """Fold a verbatim division string onto a canonical name.

    Returns ``(canonical_or_None, problem_or_None)``. Only exact canonical names
    and entries in the alias table are folded -- we never fuzzy-match, because a
    wrong fold silently attributes a real feeder to the wrong part of the city.
    """
    table = _division_aliases()
    name = collapse_ws(raw)
    if not name:
        return None, "empty division"
    key = name.lower()
    for canon in table.get("canonical", []):
        if key == canon.lower():
            return canon, None
    if key in table.get("aliases", {}):
        return table["aliases"][key], None
    if key in table.get("unusable", {}):
        return None, "unusable division string: %s" % table["unusable"][key]
    return None, "unrecognised division %r (add it to desco-division-aliases.json)" % name


def _norm_feeder(s: str) -> str:
    return collapse_ws(s)


def feeder_id(division: str, feeder: str) -> str:
    """``desco:<division-slug>:<feeder-slug>`` per CONTRACTS.md section 2."""
    return "desco:{}:{}".format(slugify(division), slugify(feeder))


def hours_to_windows(marked_hours: list[int]) -> list[dict[str, str]]:
    """Merge consecutive marked hours into windows.

    Hours 14, 15, 16 -> one window 14:00-17:00. A window that runs to the end of
    the day ends "24:00", never "00:00", per the contract's time rule.
    """
    if not marked_hours:
        return []
    hrs = sorted(set(h for h in marked_hours if 0 <= h < HOURS))
    windows: list[dict[str, str]] = []
    start = prev = hrs[0]
    for h in hrs[1:]:
        if h == prev + 1:
            prev = h
            continue
        windows.append({"start": "%02d:00" % start, "end": "%02d:00" % (prev + 1)})
        start = prev = h
    windows.append({"start": "%02d:00" % start, "end": "%02d:00" % (prev + 1)})
    return windows


def _is_hour_header(cell: str) -> bool:
    """A header cell like "14:00\\n15:00", "14:00 15:00", or a single "14:00".

    Callers may hand us the cell either raw or already whitespace-collapsed, so
    split on any whitespace rather than on newlines specifically.
    """
    parts = (cell or "").split()
    return bool(parts) and all(_HOUR_RE.match(p) for p in parts)


def _find_meta_cols(header: list[str]) -> int | None:
    """Index of the first hour-header column = number of metadata columns.

    Returns None when the header does not look like this table at all, so the
    caller can skip the page rather than guess an offset.
    """
    for i, cell in enumerate(header):
        if _is_hour_header(cell):
            # Require the run to actually continue -- a stray "12:00" in a
            # title cell must not be mistaken for the start of the hour grid.
            run = 0
            for c in header[i:]:
                if not _is_hour_header(c):
                    break
                run += 1
            if run >= HOURS - 2:
                return i
    return None


#: Overprint repair.
#:
#: The "Area Under the feeder" column is CENTRE-aligned and DESCO's typesetter
#: does not clip it, so an area string wider than its cell overflows both sides
#: and is drawn straight through the neighbouring "S&D Division" and "Feeder
#: Name" cells. The overflow lands on the SAME baseline as the text already
#: there, so any extractor that orders a cell's glyphs by x -- which is what
#: pdfplumber does -- returns the two texts zipped together: "Gulshan" printed
#: under "Mohakhali:" comes back as "MGuolhsahkahnali:", and the feeder
#: "NIPSOM" comes back as "WaterN PIuPmSOp.M".
#:
#: The two texts are separable because each was printed as a contiguous run:
#: inside one run every glyph starts where the previous glyph ended, whereas an
#: overprinted run restarts well to the left. Measured across both document
#: generations the largest legitimate backward kern is 0.05 em and the smallest
#: overprint step-back is 0.19 em, so the cut below sits between the two.
#:
#: Glyphs are still placed in cells exactly the way pdfplumber places them, so
#: every cell that is not overprinted extracts byte-for-byte as before. Runs are
#: used only to decide which glyphs in a *conflicted* cell do not belong there.
_RUN_BACK_KERN = 0.10        # em; more backward travel than this starts a new run
_RUN_FORWARD_GAP = 0.55      # em; slack for lines that space words by position
_RUN_BASELINE_TOL = 0.6      # pt; further apart vertically means a different line
_CELL_EDGE_TOL = 0.5         # pt; slack when asking "does this run fit the cell"


def _text_runs(chars: list[dict]) -> list[dict]:
    """Reassemble glyphs into the contiguous runs they were printed as.

    Ordering glyphs by x is exactly what corrupts an overprinted cell, so a
    glyph instead joins the run whose end it continues (smallest gap), and opens
    a new run when nothing on its baseline ends near it. Blank glyphs are kept so
    that word spacing is preserved.
    """
    runs: list[dict] = []
    for ch in sorted(chars, key=lambda c: (round(c["top"], 1), c["x0"])):
        em = float(ch.get("size") or 8.0)
        back, forward = _RUN_BACK_KERN * em, _RUN_FORWARD_GAP * em
        best: dict | None = None
        best_gap = 0.0
        for run in runs:
            if abs(run["top"] - ch["top"]) > _RUN_BASELINE_TOL:
                continue
            gap = ch["x0"] - run["x1"]
            if -back <= gap <= forward and (best is None or abs(gap) < abs(best_gap)):
                best, best_gap = run, gap
        if best is None:
            runs.append({"top": ch["top"], "x0": ch["x0"], "x1": ch["x1"],
                         "chars": [ch]})
        else:
            best["chars"].append(ch)
            best["x1"] = max(best["x1"], ch["x1"])
    for run in runs:
        run["text"] = collapse_ws("".join(c["text"] for c in run["chars"]))
        run["center"] = (run["x0"] + run["x1"]) / 2.0
    return runs


def _midpoint_in(glyph: dict, bbox: tuple) -> bool:
    """pdfplumber's own glyph-in-box test, reproduced so placement is identical."""
    x0, top, x1, bottom = bbox
    return (x0 <= (glyph["x0"] + glyph["x1"]) / 2.0 < x1
            and top <= (glyph["top"] + glyph["bottom"]) / 2.0 < bottom)


def _drop_overprint(cell_chars: list[dict], cell: tuple, runs: list[dict],
                    run_of: dict[int, int]) -> tuple[list[dict], list[int]]:
    """Remove glyphs that only pass through ``cell`` because they overprint it.

    A conflict is two runs on one baseline whose x-extents overlap. The run that
    stays inside the cell is the cell's own text; a run that carries on past the
    cell edge is a neighbouring centre-aligned column overflowing, and is
    evicted. When no run fits the cell there is nothing to prefer, so everything
    is kept and the cell is left as the source printed it.
    """
    grouped: dict[int, list[dict]] = {}
    for glyph in cell_chars:
        grouped.setdefault(run_of[id(glyph)], []).append(glyph)
    if len(grouped) < 2:
        return cell_chars, []

    fits = [i for i in grouped
            if runs[i]["x0"] >= cell[0] - _CELL_EDGE_TOL
            and runs[i]["x1"] <= cell[2] + _CELL_EDGE_TOL]
    if not fits:
        return cell_chars, []

    evicted: list[int] = []
    for i in grouped:
        if i in fits:
            continue
        over, keep = runs[i], None
        for j in fits:
            keep = runs[j]
            if (abs(over["top"] - keep["top"]) <= _RUN_BASELINE_TOL
                    and over["x0"] < keep["x1"] and keep["x0"] < over["x1"]):
                evicted.append(i)
                break
    if not evicted:
        return cell_chars, []
    return [g for g in cell_chars if run_of[id(g)] not in evicted], evicted


def _extract_table_cells(page: Any) -> list[list[str]] | None:
    """``page.extract_table()`` with overprinted cells repaired.

    Returns None when the page carries no table, so the caller skips the page
    exactly as it did before.
    """
    from pdfplumber import utils

    tables = page.find_tables()
    if not tables:
        return None
    table = max(tables,
                key=lambda t: (t.bbox[2] - t.bbox[0]) * (t.bbox[3] - t.bbox[1]))
    if not table.rows:
        return None

    chars = page.chars
    runs = _text_runs(chars)
    run_of = {id(g): i for i, run in enumerate(runs) for g in run["chars"]}

    out: list[list[str]] = []
    for row in table.rows:
        row_chars = [c for c in chars if _midpoint_in(c, row.bbox)]
        line: list[str] = []
        evicted_here: dict[int, int] = {}      # run index -> cell it was evicted from
        for c_idx, cell in enumerate(row.cells):
            if cell is None:
                line.append("")
                continue
            cell_chars = [c for c in row_chars if _midpoint_in(c, cell)]
            kept, evicted = _drop_overprint(cell_chars, cell, runs, run_of)
            for i in evicted:
                evicted_here[i] = c_idx
            line.append(utils.extract_text(kept) if kept else "")

        # An evicted run is a column overflowing its own cell, so put it back
        # whole in the column it is centred on -- but only when that cell holds
        # nothing except this run's own fragment, so no real text is displaced.
        for i in evicted_here:
            run = runs[i]
            for c_idx, cell in enumerate(row.cells):
                if cell is None or not cell[0] <= run["center"] < cell[2]:
                    continue
                home = [c for c in row_chars if _midpoint_in(c, cell)]
                if home and all(run_of[id(c)] == i for c in home):
                    line[c_idx] = run["text"]
                break
        out.append(line)
    return out


def _canonical_row(raw: list, meta_cols: int) -> list[str]:
    """Map a source row onto [division, area, feeder, load, marker, h0..h23]."""
    cells = [("" if c is None else str(c)) for c in raw]
    cells += [""] * (meta_cols + HOURS - len(cells))
    out = [""] * (CANON_META + HOURS)
    out[0] = cells[0]
    out[1] = cells[1] if meta_cols > 1 else ""
    out[2] = cells[2] if meta_cols > 2 else ""
    if meta_cols >= 5:
        out[3] = cells[3]          # Load (MW)
        out[4] = cells[4]          # "LS"
    elif meta_cols == 4:
        out[3] = ""                # this generation has no Load column
        out[4] = cells[3]
    out[CANON_META:] = cells[meta_cols:meta_cols + HOURS]
    return out


def _weekday_from_text(text: str) -> tuple[int | None, str | None]:
    """Find the English weekday word printed on page 1."""
    for line in (text or "").split("\n")[:12]:
        token = collapse_ws(line).strip().lower().rstrip(":")
        if token in EN_WEEKDAY:
            return EN_WEEKDAY[token], token.capitalize()
    match = re.search(
        r"\b(sunday|monday|tuesday|wednesday|thursday|friday|saturday)\b",
        (text or "")[:2000], re.IGNORECASE)
    if match:
        word = match.group(1).lower()
        return EN_WEEKDAY[word], word.capitalize()
    return None, None


# ------------------------------------------------------- primary: pdfplumber


def _extract_rows_pdfplumber(pdf_path: Path) -> tuple[list[list[str]], dict[str, Any]]:
    import pdfplumber

    rows: list[list[str]] = []
    meta: dict[str, Any] = {
        "extraction_method": "pdfplumber",
        "pages": 0,
        "hour_column_count": 0,
        "warnings": [],
        "weekday": None,
        "weekday_name": None,
    }

    with pdfplumber.open(str(pdf_path)) as pdf:
        meta["pages"] = len(pdf.pages)
        if not pdf.pages:
            raise ParseError("PDF has no pages")

        wd, wd_name = _weekday_from_text(pdf.pages[0].extract_text() or "")
        meta["weekday"], meta["weekday_name"] = wd, wd_name

        hour_counts: set[int] = set()
        meta_col_counts: set[int] = set()
        for page_no, page in enumerate(pdf.pages, start=1):
            table = _extract_table_cells(page)
            if not table or len(table) < 2:
                meta["warnings"].append("page %d: no table detected" % page_no)
                continue

            header = [collapse_ws(c) for c in table[0]]
            meta_cols = _find_meta_cols(header)
            if meta_cols is None:
                meta["warnings"].append(
                    "page %d: no hour-column grid in header; page skipped" % page_no)
                continue
            meta_col_counts.add(meta_cols)
            if meta_cols not in KNOWN_META_COL_COUNTS:
                meta["warnings"].append(
                    "page %d: unexpected metadata column count %d (known: %s)"
                    % (page_no, meta_cols, ", ".join(map(str, KNOWN_META_COL_COUNTS))))

            n_hours = sum(1 for c in header[meta_cols:] if _is_hour_header(c))
            hour_counts.add(n_hours)
            if n_hours != HOURS:
                meta["warnings"].append(
                    "page %d: found %d hour columns, expected %d"
                    % (page_no, n_hours, HOURS))

            for raw in table[1:]:
                rows.append(_canonical_row(list(raw), meta_cols))

        meta["hour_column_count"] = max(hour_counts) if hour_counts else 0
        meta["meta_column_counts"] = sorted(meta_col_counts)
    return rows, meta


# ------------------------------------------- fallback: pdftotext -layout


def _extract_rows_pdftotext(pdf_path: Path) -> tuple[list[list[str]], dict[str, Any]]:
    """Column-position heuristic over ``pdftotext -layout``.

    This exists so the pipeline still produces *something* if pdfplumber cannot
    be installed, but it is strictly worse: long wrapped area strings land on
    their own output line and get attributed to the wrong feeder. We flag that
    in the warnings so validate.py can downgrade confidence.
    """
    try:
        out = subprocess.run(
            ["pdftotext", "-layout", str(pdf_path), "-"],
            capture_output=True, timeout=180, check=True)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ParseError("pdftotext fallback failed: %s" % exc) from exc

    text = out.stdout.decode("utf-8", "replace")
    wd, wd_name = _weekday_from_text(text)
    meta: dict[str, Any] = {
        "extraction_method": "pdftotext-layout",
        "pages": text.count("\f") + 1,
        "hour_column_count": 0,
        "weekday": wd,
        "weekday_name": wd_name,
        "warnings": [
            "pdftotext fallback in use: wrapped area text may be attributed to "
            "the wrong feeder row"
        ],
    }

    rows: list[list[str]] = []
    hour_spans: list[tuple[int, int]] = []

    for raw_line in text.split("\n"):
        if not raw_line.strip():
            continue

        # Locate the header line and lock in the character column of each hour.
        if "Area Under the feeder" in raw_line and "Feeder Name" in raw_line:
            hour_spans = []
            for m in re.finditer(r"\d{1,2}:\d{2}", raw_line):
                hour_spans.append((m.start(), m.end()))
            # Header carries a "Time" label plus the 24 hour labels.
            if len(hour_spans) > HOURS:
                hour_spans = hour_spans[-HOURS:]
            meta["hour_column_count"] = len(hour_spans)
            continue

        if not hour_spans or len(hour_spans) != HOURS:
            continue
        if "LS" not in raw_line and "L S" not in raw_line:
            continue

        first_hour_col = hour_spans[0][0]
        head = raw_line[:first_hour_col]
        # head is "<division> <area...> <feeder> LS"
        head = re.sub(r"\bL\s?S\s*$", "", head).strip()
        parts = re.split(r"\s{2,}", head)
        parts = [p for p in parts if p.strip()]
        if len(parts) < 2:
            continue
        division = parts[0]
        feeder = parts[-1]
        area = " ".join(parts[1:-1]) if len(parts) > 2 else ""

        cells = [""] * (CANON_META + HOURS)
        cells[0], cells[1], cells[2], cells[4] = division, area, feeder, "LS"
        for idx, (cs, ce) in enumerate(hour_spans):
            # Widen the slice a little; numbers are centred under the label.
            chunk = raw_line[max(0, cs - 3):ce + 3].strip()
            m = re.search(r"\d+", chunk)
            if m:
                cells[CANON_META + idx] = m.group(0)
        rows.append(cells)

    return rows, meta


# ------------------------------------------------------------------ public


def parse(
    pdf_path: str | Path,
    *,
    source_id: str,
    source_url: str,
    sha256: str,
    effective_date: str,
    retrieved_at: str | None = None,
    tls_verified: bool = True,
    archive_path: str | None = None,
    published_date: str | None = None,
    weekday_hint: int | None = None,
    prefer: str = "pdfplumber",
) -> dict[str, Any]:
    """Parse a DESCO schedule PDF into a schedule-claims/1 document.

    ``effective_date`` is supplied by the caller (the listing page date, or the
    retrieval date) because the PDF body carries only a weekday word, no date.
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise ParseError("no such file: %s" % pdf_path)

    rows: list[list[str]] = []
    meta: dict[str, Any] = {}
    errors: list[str] = []

    order = ["pdfplumber", "pdftotext"] if prefer == "pdfplumber" else ["pdftotext", "pdfplumber"]
    for method in order:
        try:
            if method == "pdfplumber":
                rows, meta = _extract_rows_pdfplumber(pdf_path)
            else:
                rows, meta = _extract_rows_pdftotext(pdf_path)
            if rows:
                break
            errors.append("%s produced 0 rows" % method)
        except Exception as exc:  # noqa: BLE001 - try the other path
            errors.append("%s: %s: %s" % (method, type(exc).__name__, exc))

    if not rows:
        raise ParseError("no table rows extracted (%s)" % "; ".join(errors))

    warnings: list[str] = list(meta.get("warnings") or [])
    warnings.extend(errors)

    weekday = meta.get("weekday")
    if weekday is None:
        weekday = weekday_hint
        if weekday is not None:
            warnings.append(
                "weekday not found in PDF text; used listing-page hint %s" % weekday)
    elif weekday_hint is not None and weekday_hint != weekday:
        warnings.append(
            "weekday mismatch: PDF says %s, listing page says %s; kept the PDF value"
            % (weekday, weekday_hint))

    # ---- rows -> claims
    claims: list[dict[str, Any]] = []
    rows_seen = 0
    unmapped_divisions: dict[str, str] = {}
    skipped_no_feeder = 0
    skipped_no_marks = 0

    for cells in rows:
        rows_seen += 1
        division = _norm_division(cells[0])
        area_text = collapse_ws(cells[1])
        feeder = _norm_feeder(cells[2])

        # Header rows repeat on every page; drop them.
        if feeder.lower() in ("feeder name", "feeder") or division.lower().startswith("s&d"):
            rows_seen -= 1
            continue
        if not feeder or not division:
            skipped_no_feeder += 1
            continue

        marked = [i for i in range(HOURS)
                  if collapse_ws(cells[CANON_META + i]) != ""]
        if not marked:
            # Feeder listed but no hour marked -> no shedding claimed. We emit
            # nothing rather than an empty-window claim.
            skipped_no_marks += 1
            continue

        load_mw = None
        raw_load = collapse_ws(cells[3])
        if raw_load:
            # The typesetter sprays spaces inside numbers ("1 . 8 5"), so strip
            # whitespace before parsing. Anything still unreadable is reported,
            # not guessed -- load is metadata, so a bad value never drops a claim.
            try:
                load_mw = float(re.sub(r"\s+", "", raw_load))
            except ValueError:
                warnings.append("row %d: unreadable Load value %r" % (rows_seen, raw_load))

        canon, div_problem = canonical_division(division)
        if div_problem:
            unmapped_divisions[division] = div_problem

        claims.append({
            "division": division,
            "division_canonical": canon,
            "feeder": feeder,
            "feeder_id": feeder_id(division, feeder),
            "area_text": area_text,
            "windows": hours_to_windows(marked),
            "weekday": weekday,
            "load_mw": load_mw,
        })

    for bad, why in sorted(unmapped_divisions.items()):
        warnings.append("division not canonicalised: %s" % why)

    if skipped_no_feeder:
        warnings.append("%d row(s) skipped: missing division or feeder name"
                        % skipped_no_feeder)

    divisions = {c["division"] for c in claims}
    canon_divisions = {c["division_canonical"] for c in claims if c["division_canonical"]}
    feeders = {c["feeder_id"] for c in claims}

    return {
        "schema": "schedule-claims/1",
        "utility": UTILITY,
        "publisher": PUBLISHER,
        "effective_date": effective_date,
        "coverage_level": "feeder",
        # DERIVED, not OFFICIAL: the numbers are DESCO's, but the JSON shape,
        # the hour->window merge and the feeder_id are ours.
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
            "published_date": published_date,
        },
        "claims": claims,
        "stats": {
            "claim_count": len(claims),
            "feeder_count": len(feeders),
            "division_count": len(divisions),
            "canonical_division_count": len(canon_divisions),
            "unmapped_division_count": len(unmapped_divisions),
        },
        "parse_meta": {
            "hour_column_count": int(meta.get("hour_column_count") or 0),
            "meta_column_counts": meta.get("meta_column_counts") or [],
            "extraction_method": meta.get("extraction_method", "unknown"),
            "pages": int(meta.get("pages") or 0),
            "rows_seen": rows_seen,
            "rows_without_marks": skipped_no_marks,
            "weekday_name": meta.get("weekday_name"),
            "unmapped_divisions": sorted(unmapped_divisions),
            "warnings": warnings,
        },
    }


def parse_bytes(content: bytes, **kwargs: Any) -> dict[str, Any]:
    """Convenience wrapper for in-memory PDF bytes."""
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as fh:
        fh.write(content)
        tmp = Path(fh.name)
    try:
        return parse(tmp, **kwargs)
    finally:
        try:
            tmp.unlink()
        except OSError:  # pragma: no cover
            pass
