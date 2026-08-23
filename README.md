# currentKothai — unofficial Bangladesh load-shedding schedules by location

An independent, **non-official** visualization of the load-shedding schedules that Bangladesh's
electricity distributors publish themselves. You give it a location; it tells you which distributor
serves that point, which feeders most likely cover it, and what windows those feeders have
published — always with a link back to the original document.

**It is not affiliated with DESCO, DPDC, NESCO, BPDB, BREB, WZPDCL, Power Grid Bangladesh, or the
Government of Bangladesh.**

---

## What actually works today

| Distributor | Coverage | State |
|---|---|---|
| **DESCO** | Feeder-level | **Live.** 558 claims across 24 S&D divisions |
| **DPDC** | Feeder-level | **Live.** 145 claims from 19 digital zone sheets; the other 17 are scans and are linked |
| NESCO | Utility-level | Link-only. Sheets are **scanned images**; same layout as DPDC but raster, so it needs OCR |
| BPDB / BREB / WZPDCL | Utility-level | Link-only. No machine-readable consumer schedule identified |
| PGCB | Grid context | Registered. National generation/demand, not location-specific |

10/10 registered sources were reachable at the last probe. The [Sources page](apps/web/sources.html)
renders this live from `data/registry/sources.json`.

## Architecture

There is no server and no database. That is a deliberate consequence of the zero-cost constraint.

```
official sites → GitHub Actions cron → deterministic parser → validator
                                                                  ↓
                            static JSON committed to data/ ← publisher
                                                                  ↓
                                    GitHub Pages → browser does the rest
```

The browser does point-in-polygon, feeder ranking and all schedule maths locally. The published
JSON files *are* the database.

```
power-bd/
  apps/web/            static site — no build step, plain ES modules
    src/               geo.js, schedule.js, confidence.js, render.js, map.js, app.js
    styles/            Apple-HIG-derived token system
  data/
    registry/          sources, utilities, official contacts, division aliases
    schedules/         published schedule-claims JSON (the "API")
    geo/               GeoJSON layers the browser fetches
    seed/archive/      immutable copies of every source file ever ingested
  workers/
    ingestion/         fetch → validate → publish orchestration
    parsers/           one adapter per utility per document family
    geospatial/        GeoJSON layer builders
  docs/                CONTRACTS.md is the frozen interface between all of the above
  tests/
```

## Run it locally

```bash
pip install -r workers/requirements.txt
```

```bash
python tools/serve.py --port 8765
```

`tools/serve.py` serves `apps/web` and maps `/data/*` onto the repo's `data/` directory, which is
exactly the layout the Pages workflow assembles. Open <http://127.0.0.1:8765>.

Refresh the data yourself:

```bash
python -m workers.ingestion.run_ingest
```

Re-probe every official source URL and rewrite the registry:

```bash
python -m workers.ingestion.build_registry
```

Rebuild the map layers (slow — it rate-limits itself to be polite to OSM):

```bash
python -m workers.geospatial.build_geo
```

Run the tests:

```bash
python -m pytest tests -q
```

## Deploying

Push to `main`. `.github/workflows/pages.yml` assembles `apps/web` + `data` into `_site` and
publishes to GitHub Pages; `.github/workflows/ingest.yml` refreshes the data four times a day and
commits it back. Enable Pages with source **GitHub Actions** in repository settings. Nothing else
to configure — no secrets, no API keys.

## The cost constraint

**$0.00, permanently.** Every dependency has a free tier that needs no credit card:

| Need | Choice | Why it's free |
|---|---|---|
| Hosting | GitHub Pages | Free for public repos |
| Scheduled jobs | GitHub Actions | Unlimited minutes on public repos |
| Database | Static JSON in git | There isn't one |
| Map tiles | OpenFreeMap + CARTO basemaps | No key, no cap |
| Map library | MapLibre GL | BSD |
| Geocoding | Photon + Nominatim | Volunteer-run; we debounce, cache, and identify honestly |
| Boundaries | OpenStreetMap (ODbL), geoBoundaries (CC-BY) | Open data |
| Alerts | `.ics` calendar download | The user's own calendar app does the reminding |

Push notifications were deliberately **not** built: they need a server, an account system and
contact details. A calendar file achieves the same outcome and keeps us knowing nothing about you.

## The rules this project is built under

See [AGENTS.md](AGENTS.md). The short version: never turn uncertainty into certainty for the sake
of a cleaner interface, never show a schedule without its provenance, and never let a failed fetch
erase data that was already validated.

## Licence

Code MIT. The schedule data belongs to the distributors that published it; this project only
restructures and links to it. Map data © OpenStreetMap contributors (ODbL); district boundaries
© geoBoundaries (CC-BY 4.0).
