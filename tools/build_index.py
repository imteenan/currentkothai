"""Generate docs/INDEX.md — the repo's table of contents.

Curated one-line purposes for known files, auto-discovery for anything new, plus
extracted public symbols so you can find the right file without opening several.

    python tools/build_index.py
"""
from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "INDEX.md"

SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", "_site", ".venv", "node_modules"}
SKIP_SUFFIX = {".pyc", ".tmp"}

# Curated purpose lines. Anything not listed still appears, marked "(undocumented)".
PURPOSE: dict[str, str] = {
    "README.md": "Start here. What works, architecture, how to run and deploy.",
    "AGENTS.md": "The six mandatory rules. Read before changing anything.",
    "pytest.ini": "Test config; sets pythonpath so `workers` imports resolve.",
    ".gitignore": "Ignores caches, venvs and the assembled _site.",

    "docs/INDEX.md": "This file. Regenerate with `python tools/build_index.py`.",
    "docs/CONTRACTS.md": "FROZEN interfaces. Every JSON/GeoJSON shape the app and pipeline share.",
    "docs/DATA_SOURCES.md": "Generated evidence package: every source, format, status. Rebuild via build_docs.py.",
    "docs/CONFIDENCE_MODEL.md": "How feeder candidates are scored and why no percentage is shown.",
    "docs/GEOSPATIAL.md": "What each map layer is, where it came from, and where it is wrong.",
    "docs/INGESTION.md": "Pipeline stages, the validation table, and operational runbooks.",
    "docs/DEPLOYMENT.md": "Hosting options and post-deploy checks. Zero-cost by design.",
    "docs/PRIVACY.md": "What leaves the browser (almost nothing) and what is never collected.",
    "docs/NATIONAL_COVERAGE.md": "Per-utility verdict on whether nationwide daily coverage is achievable.",
    "docs/PERF_HTML_CHANGES.md": "Markup snippets the perf work needs applied into the HTML pages.",

    "apps/web/index.html": "Main page: locate → distributor → feeders → timeline → sources.",
    "apps/web/sources.html": "Source registry, feed health and coverage matrix, rendered from JSON.",
    "apps/web/about.html": "Method, confidence model, geography limits, privacy, full disclaimer.",
    "apps/web/styles/tokens.css": "Design tokens: colour, type scale, spacing, motion. Both themes.",
    "apps/web/styles/base.css": "Reset, page frame, nav, footer, disclaimer ribbon.",
    "apps/web/styles/components.css": "Cards, buttons, badges, timeline, dial, map chrome, tables.",
    "apps/web/src/app.js": "Orchestrator. Wires location → utility → schedule → candidates → render.",
    "apps/web/src/data.js": "Static-JSON data layer. This is the entire 'backend'.",
    "apps/web/src/geo.js": "Browser-side point-in-polygon, bbox, centroid, nearest-point. Replaces PostGIS.",
    "apps/web/src/geocode.js": "Place search and reverse lookup; device geolocation.",
    "apps/web/src/schedule.js": "Asia/Dhaka time maths, window merging, freshness, .ics generation.",
    "apps/web/src/confidence.js": "Feeder candidate ranking + the uncalibrated confidence ceiling.",
    "apps/web/src/render.js": "All result-panel markup. Pure state → HTML string functions.",
    "apps/web/src/map.js": "MapLibre wrapper: layers, 3D extrusion, illumination, marker.",
    "apps/web/src/sources-page.js": "Renders sources.html from the registry JSON.",
    "apps/web/src/util.js": "DOM helpers, formatting, localStorage, toasts, inline icon set.",

    "workers/requirements.txt": "Python deps. All free and open source.",
    "workers/ingestion/common.py": "Shared helpers: TLS-tolerant HTTP, hashing, atomic JSON, time, slugs.",
    "workers/ingestion/build_registry.py": "Probes every official URL and writes the observed source registry.",
    "workers/ingestion/run_ingest.py": "Orchestrator: discover → fetch → archive → parse → validate → publish.",
    "workers/ingestion/validate.py": "The safety gate. Returns pass / quarantine / reject.",
    "workers/ingestion/build_docs.py": "Regenerates docs/DATA_SOURCES.md from the registry.",
    "workers/ingestion/schema/schedule-claims-1.schema.json": "JSON Schema the validator enforces.",
    "workers/parsers/desco_pdf_v1.py": "DESCO schedule PDF → claims. Handles both column layouts.",
    "workers/parsers/desco_listing_v1.py": "Finds DESCO's current PDF URL (it changes every publish).",
    "workers/parsers/dpdc_pdf_v1.py": "DPDC zone PDF → claims.",
    "workers/parsers/dpdc_listing_v1.py": "Finds DPDC's current per-zone PDF revisions.",
    "workers/parsers/bijoy.py": "Legacy Bijoy/SutonnyMJ ASCII → Unicode Bengali converter.",
    "workers/geospatial/build_geo.py": "Builds every GeoJSON layer. Entry point for map data.",
    "workers/geospatial/territories.py": "Constructs the DESCO/DPDC split via Voronoi over published zone names.",

    "tools/serve.py": "Local preview. Serves apps/web and maps /data to the repo's data dir.",
    "tools/build-site.sh": "Assembles the deployable _site directory.",
    "tools/build_index.py": "Generates this index.",
    "tools/perf_check.py": "Measures first-load transfer weight against a budget.",

    "data/registry/sources.json": "Every official source with its observed status. Generated.",
    "data/registry/utilities.json": "The 8 entities: names, coverage description, colours.",
    "data/registry/official-alerts.json": "Verified hotlines and official channels per distributor.",
    "data/registry/desco-division-aliases.json": "The ONLY place a division-name variant may be folded.",
    "data/registry/known_divisions.json": "Divisions seen in validated documents; novel ones quarantine.",
    "data/registry/state.json": "Last-run bookkeeping for the ingestion job.",
    "data/schedules/index.json": "Per-utility feed status. Drives the stale badges.",
    "data/validation/calibration.json": "The confidence gate. Empty = no percentages shown anywhere.",

    "tests/test_desco_parser.py": "Parser + validation-gate tests. Pins the column-shift bug shut.",
    "apps/web/sw.js": "Service worker. Cache-first shell, network-first freshness index.",
    "apps/web/manifest.webmanifest": "PWA manifest so it installs to a phone home screen.",
    "apps/web/icon.svg": "App icon: sodium bolt on petrol ground.",
    "apps/web/_headers": "Cloudflare/Netlify cache + security headers. Ignored by GitHub Pages.",
    "apps/web/vendor/maplibre-gl.js": "Vendored MapLibre GL (BSD). No CDN dependency at runtime.",
    "apps/web/vendor/maplibre-gl.css": "Vendored MapLibre stylesheet.",
    "apps/web/vendor/maplibre-LICENSE.txt": "MapLibre BSD licence text.",
    "apps/web/src/gazetteer.js": "Offline place search + nearest-place reverse lookup. Replaces Nominatim on the hot path.",
    "data/geo/gazetteer.json": "6,533 Bangladeshi places, polyline-encoded. Powers offline search.",
    "data/geo/gazetteer-osm.json": "OSM ids for every gazetteer entry, same order, so any row is checkable.",
    "workers/geospatial/build_gazetteer.py": "Builds the gazetteer from Overpass + existing layers.",
    "tools/perf_check.py": "Measures first-load transfer weight against a budget. Fails over budget.",
    "docs/NATIONAL_COVERAGE.md": "Per-entity verdict on nationwide daily coverage, with evidence.",
    "apps/web/src/skyline.js": "Hero canvas: 3D city silhouette with shifting edge glow.",
    "apps/web/src/sources-page.js": "Renders sources.html: coverage table, DPDC zone links, registry.",
    "data/registry/dpdc-zones.json": "DPDC's 36 NOCS zones with live per-zone PDF links and centroids.",
    "workers/ingestion/build_dpdc_zones.py": "Discovers DPDC zone PDFs (index scrape, direct probe fallback).",
    "workers/ingestion/build_docs.py": "Regenerates docs/DATA_SOURCES.md from the registry.",
    ".github/workflows/ingest.yml": "Cron ingestion, four times daily, commits refreshed data.",
    ".github/workflows/pages.yml": "Assembles and deploys the static site.",
}

SECTIONS = [
    ("Root", lambda p: "/" not in p),
    ("Docs — read these to understand the system", lambda p: p.startswith("docs/")),
    ("Web app — apps/web", lambda p: p.startswith("apps/web/")),
    ("Pipeline — workers", lambda p: p.startswith("workers/")),
    ("Data — the published 'API'", lambda p: p.startswith("data/") and "/seed/" not in p),
    ("Tooling", lambda p: p.startswith("tools/")),
    ("CI", lambda p: p.startswith(".github/")),
    ("Tests", lambda p: p.startswith("tests/")),
]


def py_symbols(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    out = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not node.name.startswith("_"):
                out.append(node.name + ("()" if not isinstance(node, ast.ClassDef) else ""))
    return out[:10]


def js_symbols(path: Path) -> list[str]:
    try:
        src = path.read_text(encoding="utf-8")
    except Exception:
        return []
    pat = re.compile(
        r"^export\s+(?:async\s+)?(?:function\s+(\w+)|class\s+(\w+)|const\s+(\w+))", re.M)
    return [next(g for g in m.groups() if g) for m in pat.finditer(src)][:10]


def human_size(n: int) -> str:
    return f"{n/1024:.0f} KB" if n >= 1024 else f"{n} B"


def main() -> int:
    files: list[tuple[str, Path]] = []
    for p in sorted(ROOT.rglob("*")):
        if not p.is_file() or p.suffix in SKIP_SUFFIX:
            continue
        rel = p.relative_to(ROOT).as_posix()
        if any(part in SKIP_DIRS for part in p.relative_to(ROOT).parts):
            continue
        if "/seed/" in rel or rel.startswith("data/seed"):
            continue
        if rel.startswith("tests/fixtures/"):
            continue
        files.append((rel, p))

    lines: list[str] = []
    w = lines.append
    w("# Index\n")
    w("Table of contents for the whole repo, so you can find the right file without")
    w("opening several. Regenerate with `python tools/build_index.py`.\n")
    w("**New here?** `README.md` → `AGENTS.md` → `docs/CONTRACTS.md`, in that order.\n")

    # quick orientation
    w("\n## The 60-second version\n")
    w("| I want to… | Go to |")
    w("|---|---|")
    w("| Understand the rules | `AGENTS.md` |")
    w("| Change a JSON/GeoJSON shape | `docs/CONTRACTS.md` (frozen — update all producers/consumers) |")
    w("| Fix a wrong schedule | `workers/parsers/`, then `workers/ingestion/validate.py` |")
    w("| Add a distributor | `docs/INGESTION.md` § *Adding a new utility adapter* |")
    w("| Change how feeders are ranked | `apps/web/src/confidence.js` |")
    w("| Change how the page looks | `apps/web/styles/tokens.css` first |")
    w("| Change the map | `apps/web/src/map.js` + `workers/geospatial/` |")
    w("| Know why coverage is limited | `docs/DATA_SOURCES.md`, `docs/NATIONAL_COVERAGE.md` |")
    w("| Deploy it | `docs/DEPLOYMENT.md` |")
    w("| Debug a data outage | `docs/INGESTION.md` § *Runbooks* |")

    claimed: set[str] = set()
    for title, match in SECTIONS:
        rows = [(rel, p) for rel, p in files if match(rel) and rel not in claimed]
        if not rows:
            continue
        claimed.update(rel for rel, _ in rows)
        w(f"\n## {title}\n")
        w("| File | Purpose | Size | Key exports |")
        w("|---|---|---|---|")
        for rel, p in rows:
            purpose = PURPOSE.get(rel, "_(undocumented — add to tools/build_index.py)_")
            syms = py_symbols(p) if p.suffix == ".py" else (
                js_symbols(p) if p.suffix == ".js" else [])
            w("| `%s` | %s | %s | %s |" % (
                rel, purpose, human_size(p.stat().st_size),
                ", ".join(f"`{s}`" for s in syms) if syms else "—"))

    leftover = [(rel, p) for rel, p in files if rel not in claimed]
    if leftover:
        w("\n## Other\n")
        w("| File | Purpose | Size |")
        w("|---|---|---|")
        for rel, p in leftover:
            w("| `%s` | %s | %s |" % (
                rel, PURPOSE.get(rel, "_(undocumented)_"), human_size(p.stat().st_size)))

    # live data snapshot
    w("\n## Current data snapshot\n")
    try:
        import json
        idx = json.loads((ROOT / "data" / "schedules" / "index.json").read_text(encoding="utf-8"))
        w("| Utility | Status | Claims | Coverage |")
        w("|---|---|---|---|")
        for r in idx.get("utilities", []):
            w("| %s | %s | %s | %s |" % (
                r.get("utility"), r.get("status"), r.get("claim_count", 0),
                r.get("coverage_level", "?")))
        w("\n_Generated %s._" % idx.get("generated_at", "unknown"))
    except Exception as exc:
        w("_No schedule index available (%s)._" % exc)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print("wrote %s (%d files indexed)" % (OUT, len(files)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
