"""Recover the ruled table from a scanned schedule sheet.

Split out of scan_grid_v1 because grid recovery turned out to be the hard part,
not the OCR. Three things happen here that a single fixed threshold cannot do:

1. **Adaptive parameters.** A crisp scan resolves with a strict threshold; faint
   ones (bashaboo, paribag) produce no grid at all until the peak fraction drops
   to ~0.18. Trying settings in order, best-first, recovers both.

2. **Merged-column recovery.** Where one vertical rule is too faint to detect,
   two hour columns become one cell. That is worse than a missing zone: the cell
   reads as "off" whenever EITHER hour is shaded, and every column after it maps
   to the wrong time. Wide cells are re-scanned at close range for the faint rule.

3. **A refusal.** If a cell is still far wider than its neighbours after that,
   the segmentation is wrong and the caller must not publish. Being short a zone
   is recoverable; publishing the wrong hours is not.
"""
from __future__ import annotations

import numpy as np

#: Threshold and morphology combinations, best-first.
GRID_PARAMS = [
    # blocksize, C, horiz frac, vert frac, peak frac
    (35, 12, 1 / 18, 1 / 22, 0.42),
    (25, 8, 1 / 18, 1 / 22, 0.28),
    (25, 8, 1 / 28, 1 / 26, 0.18),
    (35, 8, 1 / 18, 1 / 22, 0.18),
    (51, 12, 1 / 28, 1 / 26, 0.22),
    (75, 18, 1 / 40, 1 / 30, 0.18),
]

#: A cell wider than this multiple of the median is a merge, not a wide column.
MERGE_RATIO = 1.8


def _peaks(proj: np.ndarray, span: int, frac: float) -> list[int]:
    top = float(proj.max()) if proj.size else 0.0
    if top <= 0:
        return []
    idx = np.where(proj > top * frac)[0]
    if not idx.size:
        return []
    groups: list[int] = []
    run = [int(idx[0])]
    for v in idx[1:]:
        if int(v) - run[-1] <= max(3, span // 260):
            run.append(int(v))
        else:
            groups.append(int(np.mean(run)))
            run = [int(v)]
    groups.append(int(np.mean(run)))
    return groups


def _attempt(gray: np.ndarray, blocksize: int, C: int,
             hfrac: float, vfrac: float, peakfrac: float):
    import cv2
    binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                                   cv2.THRESH_BINARY_INV, blocksize, C)
    h, w = binary.shape
    horiz = cv2.morphologyEx(binary, cv2.MORPH_OPEN, cv2.getStructuringElement(
        cv2.MORPH_RECT, (max(15, int(w * hfrac)), 1)))
    vert = cv2.morphologyEx(binary, cv2.MORPH_OPEN, cv2.getStructuringElement(
        cv2.MORPH_RECT, (1, max(15, int(h * vfrac)))))
    return _peaks(horiz.sum(axis=1), h, peakfrac), _peaks(vert.sum(axis=0), w, peakfrac)


def find_grid(gray: np.ndarray) -> tuple[list[int], list[int]]:
    """Best (rows, cols) across the parameter set. Empty lists if none work."""
    best: tuple[int, list[int], list[int]] | None = None
    for params in GRID_PARAMS:
        rows, cols = _attempt(gray, *params)
        if len(rows) >= 5 and len(cols) >= 8:
            score = len(rows) * len(cols)
            if best is None or score > best[0]:
                best = (score, rows, cols)
            if len(rows) >= 8 and len(cols) >= 12:
                break
    return (best[1], best[2]) if best else ([], [])


def hour_region(cols: list[int]) -> tuple[int, list[int]]:
    """(index of the widest column, indices of the columns right of it).

    On every DPDC sheet seen, the widest column is the free-text area-name
    column and the hour grid is everything to its right.
    """
    widths = [(i, cols[i + 1] - cols[i]) for i in range(len(cols) - 1)]
    if not widths:
        return 0, []
    # Look for the area-name column only in the left portion. On maniknagar the
    # widest column of all is the LAST one, which left no hour region at all.
    left = [t for t in widths if t[0] < max(1, int(len(widths) * 0.62))] or widths
    widest = max(left, key=lambda t: t[1])[0]
    region = [i for i, _ in widths if i > widest]
    # Drop a trailing column far wider than the rest: it is a margin, not an hour.
    while len(region) > 4:
        w = [cols[ci + 1] - cols[ci] for ci in region]
        if w[-1] > float(np.median(w)) * 2.0:
            region = region[:-1]
        else:
            break
    return widest, region


def split_merged(gray: np.ndarray, cols: list[int], region: list[int]) -> list[int]:
    """Insert vertical rules that the global threshold missed."""
    if not region:
        return cols
    widths = [cols[ci + 1] - cols[ci] for ci in region]
    unit = float(np.median(widths))
    if unit <= 0:
        return cols

    extra: list[int] = []
    for ci in region:
        x0, x1 = cols[ci], cols[ci + 1]
        span = x1 - x0
        parts = int(round(span / unit))
        if parts < 2 or span < unit * 1.6:
            continue
        band = gray[:, x0 + 4:x1 - 4]
        if band.size == 0:
            continue
        darkness = (band < 150).mean(axis=0)
        for k in range(1, parts):
            want = span * k / parts - 4
            lo, hi = int(max(0, want - unit * 0.3)), int(min(len(darkness), want + unit * 0.3))
            if hi <= lo:
                continue
            window = darkness[lo:hi]
            if not window.size:
                continue
            peak = int(np.argmax(window)) + lo
            if darkness[peak] > 0.45:      # actually looks like a ruled line
                extra.append(x0 + 4 + peak)

    return sorted(set(cols) | set(extra)) if extra else cols


def segmentation_problem(cols: list[int], region: list[int]) -> str | None:
    """Describe why these columns cannot be trusted, or None if they can."""
    if not region:
        return "no hour columns found to the right of the widest column"
    widths = [cols[ci + 1] - cols[ci] for ci in region]
    median = float(np.median(widths))
    widest = max(widths)
    if median <= 0:
        return "degenerate column widths"
    if widest / median > MERGE_RATIO:
        return ("widest hour cell is %.1fx the median (%dpx vs %.0fpx), so a "
                "vertical rule was missed and two hours share one cell"
                % (widest / median, widest, median))
    return None


def resolve(gray: np.ndarray):
    """Full recovery: find, split, then judge.

    Returns (rows, cols, widest_idx, region, problem). `problem` is None when the
    grid is safe to read.
    """
    rows, cols = find_grid(gray)
    if not rows or not cols:
        return rows, cols, 0, [], "no ruled grid found at any threshold"
    widest, region = hour_region(cols)
    split = split_merged(gray, cols, region)
    if len(split) != len(cols):
        cols = split
        widest, region = hour_region(cols)
    return rows, cols, widest, region, segmentation_problem(cols, region)
