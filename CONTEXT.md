# CONTEXT.md

Read this first. It is the whole project in one page, written to be cheap to
re-read. Deeper detail lives in `docs/`; `docs/INDEX.md` maps every file.

## What it is

**CurrentKothai** turns the load-shedding sheets Bangladeshi electricity
distributors already publish into a location-first answer. Independent, not
official, free to run, no server.

Live at `apps/web/`. Data at `data/`. Pipeline at `workers/`.

## Hard rules (AGENTS.md)

| # | Rule |
|---|---|
| 1 | No schedule, feeder, boundary or outage claim ships without provenance |
| 2 | Uncertainty is never converted into certainty for a cleaner UI |
| 3 | New parsers need tests and a rollback path |
| 4 | A source disappearing must not erase already-validated data |
| 5 | Never retain a bill, account or meter number |
| 6 | Never present the project as official |

## Coverage, as verified

| Utility | Sheets | Parsed | Notes |
|---|---|---|---|
| **DESCO** | 1 per weekday, digital PDF | **558 claims, 24 zones** | Only newest weekday retrievable; UI states the mismatch |
| **DPDC** | 36 per-zone PDFs | **145 claims, 19 zones** | 19 digital and parsed; 17 are scans and get linked |
| NESCO | Scanned images | none | Same layout as DPDC, but raster. Needs Bangla OCR |
| BPDB / WZPDCL | Notices | none | No machine-readable consumer schedule found |
| BREB | ~80 PBS sites | none | No national feed. Largest population gap |
| PGCB | Generation vs demand HTML | none | Parser not built. Best route to a nationwide number |

## The two parser insights worth remembering

**DESCO** — the metadata column count changed between layout generations (4 in
2022, 5 in 2026). Hardcoding it shifts every window one hour late and invents a
midnight window on all 558 feeders. `_find_meta_cols` locates the hour grid from
the header instead. Pinned by `test_2026_layout_is_not_shifted`.

**DPDC** — `pdftotext` returns mojibake because the Bangla is legacy Bijoy, and
it is tempting to call the file unreadable. It is not. Render a page and look:
feeder codes, loads and hour headers are all clean ASCII, and the shedding mark
is a **black filled rectangle**, so the schedule comes from geometry, not text.
Hour columns are irregular and vary by zone; never assume 24.

**When a PDF resists, render it to an image and read it.** That is how both of
the above were solved.

## Architecture

```
distributor sites -> GitHub Actions cron -> deterministic parser -> validator
                                                                       |
                        static JSON committed to data/ <---------- publisher
                                                                       |
                                   Cloudflare Pages -> browser does the rest
```

No database. The published JSON files are the database. The browser does
point-in-polygon, feeder ranking and all schedule maths locally.

## Commands

| Do | Run |
|---|---|
| Preview | `python tools/serve.py --port 8765` |
| Refresh data | `python -m workers.ingestion.run_ingest` |
| Re-probe sources | `python -m workers.ingestion.build_registry` |
| Rebuild DPDC zones | `python -m workers.ingestion.build_dpdc_zones` |
| Rebuild map layers | `python -m workers.geospatial.build_geo` |
| Rebuild gazetteer | `python -m workers.geospatial.build_gazetteer` |
| Tests | `python -m pytest tests -q` |
| Build site | `bash tools/build-site.sh _site` |
| Weight check | `python tools/perf_check.py _site` |
| Regenerate index | `python tools/build_index.py` |

## Design system

Apple-derived, from `DESIGN.md`. **Light theme only**, by choice.

| Token | Value | Use |
|---|---|---|
| `--apple-blue` | `#0071e3` | Filled buttons only. Never text or borders |
| `--link-blue` | `#0066cc` | Outlined actions, links |
| `--carbon` | `#1d1d1f` | Primary ink |
| `--frost` | `#f5f5f7` | Canvas |
| `--shed` / `--live-now` / `--clear` | amber / red / green | Semantic, all AA-checked on canvas |

980px pill buttons, 8px cards, hairline rules, no shadows. Hero is the one dark
band, carrying `media/hero.mp4`. Every page passes a contrast audit at AA.

## Page flow

Hero video -> What we are not -> How (3 steps) -> Your location (device only)
-> **coordinates** -> **map** -> **feeders near you + hours** -> **area near you
+ what is a feeder** -> This is an estimate -> Let them tell you.

## Scale

| Risk | Mitigation |
|---|---|
| Nominatim caps the whole app at ~1 req/s | 6,533-place gazetteer bundled; search and reverse run offline |
| CDN dependency | MapLibre vendored locally |
| Payload | 374 KB gzipped critical path, enforced by `perf_check.py` |
| Bandwidth | Cloudflare Pages, `_headers` for cache control |
| Offline | Service worker, cache-first shell |

## Known-wrong list

- DESCO weekday mismatch when only one sheet is retrievable. Stated in the UI.
- Distributor territories are Voronoi constructions, not published boundaries.
  Drawn dashed.
- Outside Dhaka, territories are administrative divisions standing in for
  service areas, which ignores the rural BREB split.
- Feeder matching is uncalibrated, so confidence is capped at Medium and no
  percentage is ever shown. The cap lifts on its own if
  `data/validation/calibration.json` gains measured accuracy.
- 17 DPDC zones and all of NESCO are scans, so they are linked, not parsed.

## Cost

$0.00. GitHub Actions, Cloudflare Pages, OpenFreeMap tiles, OpenStreetMap and
geoBoundaries data. No card, anywhere.
