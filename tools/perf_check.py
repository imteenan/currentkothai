"""Measure the assembled site's first-load transfer weight against a budget.

    python tools/build-site.sh _site && python tools/perf_check.py _site

Budgets are for what a FIRST-TIME visitor downloads before the page can answer
"where is the current?". Anything lazy-loaded (district polygons, dated schedule
snapshots) is reported separately and does not count against the critical path.

Exits non-zero if the critical path exceeds budget, so CI can fail on a
regression.
"""
from __future__ import annotations

import argparse
import gzip
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Fetched before the first answer can be rendered.
CRITICAL = [
    "index.html",
    "styles/tokens.css", "styles/base.css", "styles/components.css",
    "src/app.js", "src/util.js", "src/data.js", "src/geo.js", "src/geocode.js",
    "src/gazetteer.js", "src/schedule.js", "src/confidence.js", "src/render.js",
    "src/map.js",
    "vendor/maplibre-gl.js", "vendor/maplibre-gl.css",
    "data/registry/utilities.json", "data/registry/sources.json",
    "data/registry/official-alerts.json",
    "data/schedules/index.json",
    "data/geo/utility-territories.geojson",
    "data/geo/desco-offices.geojson",
    "data/geo/desco-divisions.geojson",
    "data/geo/gazetteer.json",
    "data/validation/calibration.json",
]

# Fetched on demand, not on the critical path.
DEFERRED = [
    "data/geo/bangladesh-admin.geojson",
    "data/geo/dhaka-neighbourhoods.geojson",  # superseded by the gazetteer; kept for the map layer
    "data/schedules/desco/latest.json",
]

#: Compressed budget for the critical path. Mobile networks in Bangladesh are
#: the target, so this is deliberately tight.
BUDGET_GZIP_KB = 420


def gz(data: bytes) -> int:
    return len(gzip.compress(data, 9))


def measure(site: Path, names: list[str]) -> tuple[list[tuple], int, int]:
    rows, raw_total, gz_total = [], 0, 0
    for name in names:
        p = site / name
        if not p.exists():
            rows.append((name, None, None))
            continue
        data = p.read_bytes()
        r, g = len(data), gz(data)
        rows.append((name, r, g))
        raw_total += r
        gz_total += g
    return rows, raw_total, gz_total


def render(title: str, rows: list[tuple], raw: int, gzt: int) -> None:
    print(f"\n{title}")
    print(f"  {'file':<48} {'raw':>10} {'gzip':>10}")
    print("  " + "-" * 70)
    for name, r, g in sorted(rows, key=lambda x: -(x[2] or 0)):
        if r is None:
            print(f"  {name:<48} {'MISSING':>10}")
            continue
        print(f"  {name:<48} {r/1024:>9.1f}K {g/1024:>9.1f}K")
    print("  " + "-" * 70)
    print(f"  {'TOTAL':<48} {raw/1024:>9.1f}K {gzt/1024:>9.1f}K")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("site", nargs="?", default=str(ROOT / "_site"))
    ap.add_argument("--budget", type=int, default=BUDGET_GZIP_KB)
    args = ap.parse_args()

    site = Path(args.site)
    if not site.exists():
        print(f"no such directory: {site}\nRun tools/build-site.sh first.", file=sys.stderr)
        return 2

    crit_rows, crit_raw, crit_gz = measure(site, CRITICAL)
    def_rows, def_raw, def_gz = measure(site, DEFERRED)

    render("CRITICAL PATH (first answer)", crit_rows, crit_raw, crit_gz)
    render("DEFERRED (on demand)", def_rows, def_raw, def_gz)

    all_files = [p for p in site.rglob("*") if p.is_file()]
    total = sum(p.stat().st_size for p in all_files)
    print(f"\n  whole site: {len(all_files)} files, {total/1024/1024:.2f} MB on disk")

    kb = crit_gz / 1024
    print(f"\n  critical path gzipped: {kb:.0f} KB  (budget {args.budget} KB)")
    if kb > args.budget:
        print(f"  FAIL: over budget by {kb - args.budget:.0f} KB", file=sys.stderr)
        return 1
    print(f"  PASS: {args.budget - kb:.0f} KB of headroom")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
