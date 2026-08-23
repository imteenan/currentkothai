# Ingestion pipeline and runbooks

## Flow

```
build_registry.py   probe every official URL, record what was observed
       ↓
run_ingest.py       discover → fetch → archive → parse → validate → publish
       ↓
data/schedules/     <utility>/latest.json, <utility>/<date>.json, index.json
```

Runs in `.github/workflows/ingest.yml` on cron at 00:17, 06:17, 12:17, 18:17 UTC
(06:17, 12:17, 18:17, 00:17 Dhaka), plus manual dispatch.

## The stages

**Discovery.** DESCO's schedule PDF lives on Oracle object storage under a UUID filename that
changes on every publish, so `desco_listing_v1.discover()` scrapes the landing page and returns
candidates newest-first. Never hardcode the PDF URL.

**Fetch.** `common.http_get` tries a verified TLS handshake first, then retries unverified. Many
`.gov.bd` hosts serve an incomplete certificate chain, so a strict check fails on a genuine host.
The observed result is propagated into `source.tls_verified` and surfaced in the UI — it is
recorded, never hidden.

**Archive.** Raw bytes go to `data/seed/archive/<utility>/<date>-<sha8>.<ext>` and are never
deleted or overwritten. This is what makes a disputed reading checkable later.

**Parse.** One adapter per utility per document family, in `workers/parsers/`.

**Validate.** `validate.py` returns `pass`, `quarantine`, or `reject`.

| Check | Failure | Action |
|---|---|---|
| `schema` | shape violates `schedule-claims-1.schema.json` | reject |
| `empty-parse` | non-empty source produced zero claims | reject |
| `hour-columns` | hour grid is not 24 wide | reject |
| `time-sanity` | unparseable time, or end ≤ start | reject |
| `provenance` | missing source URL / hash / timestamp / adapter | reject |
| `date-regression` | incoming document older than the live one | reject |
| `canonical-division` | >25% of claims have no canonical division | reject (else quarantine) |
| `unknown-division` | division name never seen before | quarantine |
| `volume-drop` | claim count fell by more than half | quarantine |
| `duplicates` | same feeder + window twice | quarantine |
| `empty-claims` | a claim with no window | quarantine |

Quarantined documents land in `data/schedules/_quarantine/` with the full finding list and are
never promoted to `latest.json`. That directory is excluded from the deployed site.

**Publish.** Writes the dated file and `latest.json`, then rebuilds `index.json`. On total failure
the previous version stays live and only `status` changes.

## Adding a new utility adapter

1. Add the source to `CANDIDATES` in `workers/ingestion/build_registry.py`, run it, confirm a 200.
2. Save a sample to `data/seed/samples/` and inspect the real layout before writing any code —
   `pdfplumber` `extract_tables()` and `pdftotext -layout` disagree in useful ways.
3. Write `workers/parsers/<utility>_<format>_v1.py` exposing `parse(path, *, source_id, source_url,
   sha256, effective_date, ...) -> schedule-claims/1`.
4. **Detect the column layout from the header.** Do not hardcode column counts.
5. Add fixtures and tests, including one for a layout you expect to change.
6. Register the ingest function in `run_ingest.py`'s `targets`, and remove the utility from
   `LINK_ONLY`.

## Runbooks

**Official source is down.** Expected and handled: the feed's `status` flips, `latest.json` stays,
the UI shows a stale badge. If it persists more than a few days, check whether the URL moved.

**Official source URL changed.** This happens often. DPDC moved its schedules from `dpdc.gov.bd`
to `dpdc.org.bd`, and its zone PDFs carry a revision counter in the filename
(`.../100.pdf` → `.../150.pdf`). Fix: scrape the index rather than hardcoding, update
`build_registry.py`, re-run it, commit the registry.

**PDF layout changed.** The validator will reject or quarantine rather than publish garbage. Pull
the archived file from `data/seed/archive/`, diff the header against the fixtures, extend
`_find_meta_cols` / add a new adapter version, add a fixture for both layouts, then re-run.

**Parser produced an invalid result.** Look in `data/schedules/_quarantine/` — the report lists
every finding with its verdict. Nothing was published.

**Two conflicting schedules published.** `date-regression` blocks the older one from activating.
If both are same-dated, the newest retrieval wins; check the archive hashes to see what changed.

**Feeder renamed, split or merged.** `feeder_id` is derived from division + feeder slug, so a
rename produces a new id. `unknown-division` quarantines genuinely new division names for review.
Add real variants to `data/registry/desco-division-aliases.json`; never fuzzy-match in code.

**Rollback.** Every dated snapshot is in `data/schedules/<utility>/`. Copy the good one over
`latest.json`, commit, and Pages redeploys. The source archive is immutable, so a re-parse from
original bytes is always possible.
