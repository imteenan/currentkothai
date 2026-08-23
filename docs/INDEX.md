# Index

Table of contents for the whole repo, so you can find the right file without
opening several. Regenerate with `python tools/build_index.py`.

**New here?** `README.md` → `AGENTS.md` → `docs/CONTRACTS.md`, in that order.


## The 60-second version

| I want to… | Go to |
|---|---|
| Understand the rules | `AGENTS.md` |
| Change a JSON/GeoJSON shape | `docs/CONTRACTS.md` (frozen — update all producers/consumers) |
| Fix a wrong schedule | `workers/parsers/`, then `workers/ingestion/validate.py` |
| Add a distributor | `docs/INGESTION.md` § *Adding a new utility adapter* |
| Change how feeders are ranked | `apps/web/src/confidence.js` |
| Change how the page looks | `apps/web/styles/tokens.css` first |
| Change the map | `apps/web/src/map.js` + `workers/geospatial/` |
| Know why coverage is limited | `docs/DATA_SOURCES.md`, `docs/NATIONAL_COVERAGE.md` |
| Deploy it | `docs/DEPLOYMENT.md` |
| Debug a data outage | `docs/INGESTION.md` § *Runbooks* |

## Root

| File | Purpose | Size | Key exports |
|---|---|---|---|
| `.gitattributes` | _(undocumented — add to tools/build_index.py)_ | 227 B | — |
| `.gitignore` | Ignores caches, venvs and the assembled _site. | 490 B | — |
| `AGENTS.md` | The six mandatory rules. Read before changing anything. | 3 KB | — |
| `CONTEXT.md` | _(undocumented — add to tools/build_index.py)_ | 5 KB | — |
| `pytest.ini` | Test config; sets pythonpath so `workers` imports resolve. | 42 B | — |
| `README.md` | Start here. What works, architecture, how to run and deploy. | 5 KB | — |
| `SETUP.md` | _(undocumented — add to tools/build_index.py)_ | 5 KB | — |

## Docs — read these to understand the system

| File | Purpose | Size | Key exports |
|---|---|---|---|
| `docs/CONFIDENCE_MODEL.md` | How feeder candidates are scored and why no percentage is shown. | 3 KB | — |
| `docs/CONTRACTS.md` | FROZEN interfaces. Every JSON/GeoJSON shape the app and pipeline share. | 4 KB | — |
| `docs/DATA_SOURCES.md` | Generated evidence package: every source, format, status. Rebuild via build_docs.py. | 10 KB | — |
| `docs/DEPLOYMENT.md` | Hosting options and post-deploy checks. Zero-cost by design. | 3 KB | — |
| `docs/GEOSPATIAL.md` | What each map layer is, where it came from, and where it is wrong. | 3 KB | — |
| `docs/HOSTING.md` | _(undocumented — add to tools/build_index.py)_ | 4 KB | — |
| `docs/INDEX.md` | This file. Regenerate with `python tools/build_index.py`. | 14 KB | — |
| `docs/INGESTION.md` | Pipeline stages, the validation table, and operational runbooks. | 5 KB | — |
| `docs/NATIONAL_COVERAGE.md` | Per-entity verdict on nationwide daily coverage, with evidence. | 5 KB | — |
| `docs/PRIVACY.md` | What leaves the browser (almost nothing) and what is never collected. | 2 KB | — |

## Web app — apps/web

| File | Purpose | Size | Key exports |
|---|---|---|---|
| `apps/web/_headers` | Cloudflare/Netlify cache + security headers. Ignored by GitHub Pages. | 1 KB | — |
| `apps/web/_redirects` | _(undocumented — add to tools/build_index.py)_ | 369 B | — |
| `apps/web/about.html` | Method, confidence model, geography limits, privacy, full disclaimer. | 8 KB | — |
| `apps/web/icon.svg` | App icon: sodium bolt on petrol ground. | 615 B | — |
| `apps/web/index.html` | Main page: locate → distributor → feeders → timeline → sources. | 15 KB | — |
| `apps/web/logo.svg` | _(undocumented — add to tools/build_index.py)_ | 802 B | — |
| `apps/web/manifest.webmanifest` | PWA manifest so it installs to a phone home screen. | 586 B | — |
| `apps/web/media/hero.mp4` | _(undocumented — add to tools/build_index.py)_ | 4332 KB | — |
| `apps/web/sources.html` | Source registry, feed health and coverage matrix, rendered from JSON. | 5 KB | — |
| `apps/web/src/app.js` | Orchestrator. Wires location → utility → schedule → candidates → render. | 26 KB | — |
| `apps/web/src/confidence.js` | Feeder candidate ranking + the uncalibrated confidence ceiling. | 10 KB | `normaliseTokens`, `rankDivisions`, `rankFeeders`, `scheduleAgreement`, `CONFIDENCE_COPY`, `UNCALIBRATED_NOTE` |
| `apps/web/src/data.js` | Static-JSON data layer. This is the entire 'backend'. | 3 KB | `DATA_ROOT`, `getJSON`, `load`, `bootstrap`, `findSource`, `findUtility` |
| `apps/web/src/gazetteer.js` | Offline place search + nearest-place reverse lookup. Replaces Nominatim on the hot path. | 8 KB | `foldLatin`, `ready`, `search`, `nearest`, `reverseLocal`, `status` |
| `apps/web/src/geo.js` | Browser-side point-in-polygon, bbox, centroid, nearest-point. Replaces PostGIS. | 4 KB | `pointInGeometry`, `bboxOf`, `featuresAt`, `centroidOf`, `nearestPoints`, `BD_BBOX`, `isInBangladesh` |
| `apps/web/src/geocode.js` | Place search and reverse lookup; device geolocation. | 7 KB | `remoteBudget`, `deviceLocation`, `GeoError`, `searchPlaces`, `searchPlacesRemote`, `reverseGeocode`, `ATTRIBUTION` |
| `apps/web/src/map.js` | MapLibre wrapper: layers, 3D extrusion, illumination, marker. | 12 KB | `CoverageMap` |
| `apps/web/src/render.js` | All result-panel markup. Pure state → HTML string functions. | 10 KB | `renderStrip`, `renderAnswer`, `renderFeeders`, `renderEvidence`, `renderProvenance`, `renderFeedHealth`, `renderAlertCards` |
| `apps/web/src/schedule.js` | Asia/Dhaka time maths, window merging, freshness, .ics generation. | 7 KB | `DHAKA_TZ`, `dhakaNow`, `toMinutes`, `fromMinutes`, `fmtWindow`, `normaliseWindows`, `evaluateDay`, `claimsForWeekday`, `freshness`, `buildICS` |
| `apps/web/src/skyline.js` | Hero canvas: 3D city silhouette with shifting edge glow. | 6 KB | `Skyline`, `mountSkyline` |
| `apps/web/src/sources-page.js` | Renders sources.html: coverage table, DPDC zone links, registry. | 5 KB | — |
| `apps/web/src/util.js` | DOM helpers, formatting, localStorage, toasts, inline icon set. | 8 KB | `el`, `esc`, `clamp`, `haversineKm`, `fmtKm`, `relTime`, `fmtDuration`, `debounce`, `store`, `toast` |
| `apps/web/styles/base.css` | Reset, page frame, nav, footer, disclaimer ribbon. | 7 KB | — |
| `apps/web/styles/components.css` | Cards, buttons, badges, timeline, dial, map chrome, tables. | 17 KB | — |
| `apps/web/styles/tokens.css` | Design tokens: colour, type scale, spacing, motion. Both themes. | 4 KB | — |
| `apps/web/sw.js` | Service worker. Cache-first shell, network-first freshness index. | 4 KB | — |
| `apps/web/vendor/maplibre-gl.css` | Vendored MapLibre stylesheet. | 64 KB | — |
| `apps/web/vendor/maplibre-gl.js` | Vendored MapLibre GL (BSD). No CDN dependency at runtime. | 784 KB | — |
| `apps/web/vendor/maplibre-LICENSE.txt` | MapLibre BSD licence text. | 6 KB | — |

## Pipeline — workers

| File | Purpose | Size | Key exports |
|---|---|---|---|
| `workers/__init__.py` | _(undocumented — add to tools/build_index.py)_ | 0 B | — |
| `workers/geospatial/__init__.py` | _(undocumented — add to tools/build_index.py)_ | 0 B | — |
| `workers/geospatial/build_gazetteer.py` | Builds the gazetteer from Overpass + existing layers. | 17 KB | `overpass()`, `DistrictIndex`, `collect()`, `polyline()`, `encode()`, `main()` |
| `workers/geospatial/build_geo.py` | Builds every GeoJSON layer. Entry point for map data. | 16 KB | `nominatim()`, `round_coords()`, `simplify()`, `feature()`, `fc()`, `save()`, `fetch_polygon()`, `build_districts()`, `build_territories()`, `build_desco_points()` |
| `workers/geospatial/territories.py` | Constructs the DESCO/DPDC split via Voronoi over published zone names. | 16 KB | `osm_relation_geometry()`, `geocode_points()`, `voronoi_split()`, `build()` |
| `workers/ingestion/__init__.py` | _(undocumented — add to tools/build_index.py)_ | 0 B | — |
| `workers/ingestion/build_docs.py` | Regenerates docs/DATA_SOURCES.md from the registry. | 6 KB | `main()` |
| `workers/ingestion/build_dpdc_zones.py` | Discovers DPDC zone PDFs (index scrape, direct probe fallback). | 7 KB | `title_case()`, `probe_zone()`, `scrape_links()`, `geocode()`, `main()` |
| `workers/ingestion/build_registry.py` | Probes every official URL and writes the observed source registry. | 9 KB | `probe()`, `main()` |
| `workers/ingestion/common.py` | Shared helpers: TLS-tolerant HTTP, hashing, atomic JSON, time, slugs. | 9 KB | `utcnow()`, `iso_utc()`, `parse_iso()`, `dhaka_today()`, `collapse_ws()`, `slugify()`, `bn_digits_to_ascii()`, `weekday_index_for_date()`, `sha256_bytes()`, `read_json()` |
| `workers/ingestion/run_ingest.py` | Orchestrator: discover → fetch → archive → parse → validate → publish. | 16 KB | `ingest_desco()`, `ingest_dpdc()`, `link_only_row()`, `main()` |
| `workers/ingestion/schema/schedule-claims-1.schema.json` | JSON Schema the validator enforces. | 5 KB | — |
| `workers/ingestion/validate.py` | The safety gate. Returns pass / quarantine / reject. | 8 KB | `Report`, `validate()`, `quarantine()`, `learn_divisions()` |
| `workers/parsers/__init__.py` | _(undocumented — add to tools/build_index.py)_ | 0 B | — |
| `workers/parsers/bijoy.py` | Legacy Bijoy/SutonnyMJ ASCII → Unicode Bengali converter. | 6 KB | `looks_like_bijoy()`, `decode_bijoy()`, `is_plausible_bangla()`, `decode_if_confident()` |
| `workers/parsers/desco_listing_v1.py` | Finds DESCO's current PDF URL (it changes every publish). | 2 KB | `discover()` |
| `workers/parsers/desco_pdf_v1.py` | DESCO schedule PDF → claims. Handles both column layouts. | 20 KB | `ParseError`, `canonical_division()`, `feeder_id()`, `hours_to_windows()`, `parse()`, `parse_bytes()` |
| `workers/parsers/dpdc_pdf_v1.py` | DPDC zone PDF → claims. | 12 KB | `ParseError`, `ScannedDocument`, `hours_to_windows()`, `parse()` |
| `workers/parsers/scan_grid_v1.py` | _(undocumented — add to tools/build_index.py)_ | 21 KB | `ScanError`, `render()`, `deskew()`, `grid_lines()`, `read_header_strip()`, `parse()` |
| `workers/requirements.txt` | Python deps. All free and open source. | 338 B | — |

## Data — the published 'API'

| File | Purpose | Size | Key exports |
|---|---|---|---|
| `data/geo/bangladesh-admin.geojson` | _(undocumented — add to tools/build_index.py)_ | 636 KB | — |
| `data/geo/desco-divisions.geojson` | _(undocumented — add to tools/build_index.py)_ | 470 B | — |
| `data/geo/desco-offices.geojson` | _(undocumented — add to tools/build_index.py)_ | 25 KB | — |
| `data/geo/dhaka-neighbourhoods.geojson` | _(undocumented — add to tools/build_index.py)_ | 533 KB | — |
| `data/geo/gazetteer-osm.json` | OSM ids for every gazetteer entry, same order, so any row is checkable. | 77 KB | — |
| `data/geo/gazetteer.json` | 6,533 Bangladeshi places, polyline-encoded. Powers offline search. | 236 KB | — |
| `data/geo/utility-territories.geojson` | _(undocumented — add to tools/build_index.py)_ | 143 KB | — |
| `data/registry/desco-division-aliases.json` | The ONLY place a division-name variant may be folded. | 2 KB | — |
| `data/registry/dpdc-zones.json` | DPDC's 36 NOCS zones with live per-zone PDF links and centroids. | 10 KB | — |
| `data/registry/known_divisions.json` | Divisions seen in validated documents; novel ones quarantine. | 587 B | — |
| `data/registry/official-alerts.json` | Verified hotlines and official channels per distributor. | 4 KB | — |
| `data/registry/sources.json` | Every official source with its observed status. Generated. | 8 KB | — |
| `data/registry/state.json` | Last-run bookkeeping for the ingestion job. | 41 B | — |
| `data/registry/utilities.json` | The 8 entities: names, coverage description, colours. | 5 KB | — |
| `data/schedules/_quarantine/desco-2026-08-23-review.json` | _(undocumented — add to tools/build_index.py)_ | 402 KB | — |
| `data/schedules/_quarantine/desco-2026-08-24-review.json` | _(undocumented — add to tools/build_index.py)_ | 402 KB | — |
| `data/schedules/_quarantine/dpdc-2026-08-24.json` | _(undocumented — add to tools/build_index.py)_ | 97 KB | — |
| `data/schedules/desco/2026-08-23.json` | _(undocumented — add to tools/build_index.py)_ | 369 KB | — |
| `data/schedules/desco/2026-08-24.json` | _(undocumented — add to tools/build_index.py)_ | 369 KB | — |
| `data/schedules/desco/latest.json` | _(undocumented — add to tools/build_index.py)_ | 369 KB | — |
| `data/schedules/dpdc/2026-08-24.json` | _(undocumented — add to tools/build_index.py)_ | 146 KB | — |
| `data/schedules/dpdc/latest.json` | _(undocumented — add to tools/build_index.py)_ | 146 KB | — |
| `data/schedules/index.json` | Per-utility feed status. Drives the stale badges. | 3 KB | — |
| `data/validation/calibration.json` | The confidence gate. Empty = no percentages shown anywhere. | 1 KB | — |

## Tooling

| File | Purpose | Size | Key exports |
|---|---|---|---|
| `tools/build-site.sh` | Assembles the deployable _site directory. | 979 B | — |
| `tools/build_index.py` | Generates this index. | 13 KB | `py_symbols()`, `js_symbols()`, `human_size()`, `main()` |
| `tools/perf_check.py` | Measures first-load transfer weight against a budget. Fails over budget. | 4 KB | `gz()`, `measure()`, `render()`, `main()` |
| `tools/serve.py` | Local preview. Serves apps/web and maps /data to the repo's data dir. | 2 KB | `Handler`, `main()` |

## CI

| File | Purpose | Size | Key exports |
|---|---|---|---|
| `.github/workflows/ingest.yml` | Cron ingestion, four times daily, commits refreshed data. | 4 KB | — |
| `.github/workflows/pages.yml` | Assembles and deploys the static site. | 2 KB | — |

## Tests

| File | Purpose | Size | Key exports |
|---|---|---|---|
| `tests/test_desco_parser.py` | Parser + validation-gate tests. Pins the column-shift bug shut. | 8 KB | `parse()`, `doc_2022()`, `doc_2026()`, `test_parses_a_useful_number_of_claims()`, `test_hour_grid_is_24_columns()`, `test_both_layout_generations_are_recognised()`, `test_2026_layout_is_not_shifted()`, `test_known_row_matches_the_source_document()`, `test_every_window_is_well_formed()`, `test_consecutive_hours_merge_into_one_window()` |

## Current data snapshot

| Utility | Status | Claims | Coverage |
|---|---|---|---|
| DESCO | fresh | 558 | feeder |
| DPDC | fresh | 269 | feeder |
| BPDB | link-only | 0 | utility |
| BREB | link-only | 0 | utility |
| NESCO | link-only | 0 | utility |
| WZPDCL | link-only | 0 | utility |

_Generated 2026-08-23T20:24:49Z._
