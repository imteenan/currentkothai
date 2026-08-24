# Data Contracts (frozen v1)

Every agent MUST produce output matching these shapes exactly. The web app reads
these files as **static JSON over HTTP** — there is no database and no server.

Hard rules (from AGENTS.md):
1. No schedule, feeder mapping, boundary, or outage claim without provenance.
2. Uncertainty is never converted into certainty for UX convenience.
3. Never invent a feeder, a polygon, or a time window. If unknown, emit `UNKNOWN`.

---

## 1. `data/registry/sources.json`

```jsonc
{
  "schema": "source-registry/1",
  "generated_at": "2026-08-23T00:00:00Z",
  "sources": [
    {
      "id": "desco-possible-load-shedding",        // kebab-case, stable, unique
      "utility": "DESCO",                          // DESCO|DPDC|NESCO|BPDB|BREB|WZPDCL|PGCB|POWERDIV
      "source_type": "schedule",                   // schedule|notice|area_map|service_directory|grid_condition
      "title": "Possible Load Shedding Schedule",
      "source_url": "https://desco.gov.bd/...",    // EXACT url, no shortener
      "format": "html",                            // html|pdf|xlsx|csv|image|json
      "language": "en",                            // en|bn|mixed
      "coverage_level": "feeder",                  // utility|division|feeder
      "expected_update_frequency": "daily",        // hourly|daily|weekly|irregular|unknown
      "parser_adapter": "desco_pdf_v1",            // or null if no parser yet
      "verified_at": "2026-08-23T00:00:00Z",       // when YOU actually fetched it
      "http_status": 200,                          // what you actually observed
      "tls_ok": true,                              // false if cert chain fails (many .gov.bd do)
      "notes": "Landing page listing dated PDF links."
    }
  ]
}
```

## 2. `data/schedules/<utility-lowercase>/latest.json` (+ `YYYY-MM-DD.json`)

```jsonc
{
  "schema": "schedule-claims/1",
  "utility": "DESCO",
  "publisher": "Dhaka Electric Supply Company PLC",
  "effective_date": "2026-08-23",
  "coverage_level": "feeder",
  "badge": "DERIVED",                 // OFFICIAL|DERIVED|ESTIMATED|UNKNOWN
  "source": {
    "source_id": "desco-possible-load-shedding",
    "source_url": "https://.../schedule.pdf",
    "retrieved_at": "2026-08-23T04:00:00Z",
    "sha256": "…",
    "parser_adapter": "desco_pdf_v1",
    "parser_version": "1.0.0"
  },
  "claims": [
    {
      "division": "Mirpur",           // S&D division / office
      "feeder": "Kalyanpur-1",        // exact string from source
      "feeder_id": "desco:mirpur:kalyanpur-1",
      "feeder_name": "১১ কেভি নয়ামাটি",  // as printed; null when the sheet omits it
      "area_text": "Kalyanpur, Darussalam Road",   // verbatim "Area Under the feeder"
      "areas": ["Kalyanpur", "Darussalam Road"],   // area_text split on commas
      "area_search": "kaljanpur, darussalam road", // Latin fold, never displayed
      "text_source": "ocr",           // "ocr" = machine-read from a scan; else null
      "load_mw": 4.2,                 // null unless read confidently, see below
      "billing_code": "B722J",        // null when unreadable
      "windows": [ { "start": "14:00", "end": "15:00" } ],  // 24h local (Asia/Dhaka)
      "weekday": null                 // null = applies to effective_date; else 0=Sun..6=Sat
    }
  ],
  "stats": { "claim_count": 0, "feeder_count": 0, "division_count": 0 }
}
```

Time rule: all times are **Asia/Dhaka (UTC+6)**, 24h `HH:MM`. A window ending
`24:00` is written `24:00`, never `00:00`.

### Text fields on scanned sheets

Seventeen DPDC zones publish photographs rather than documents. Their
`feeder_name`, `area_text` and `areas` are Bengali OCR output and carry
occasional wrong letters. Three rules hold:

- **`text_source: "ocr"` must be surfaced wherever the text is shown.** The
  reader needs to know a name may be misspelled, and needs the source link to
  check it against.
- **Nothing is spell-corrected.** Dictionary-fixing a place name is how you
  publish an area that does not exist. A misread ships as read.
- **`area_search` is for matching only and is never rendered.** It is a lossy
  Latin fold of the Bengali, so that a user typing "noyamati" matches
  নয়ামাটি. Displaying it would show users a mangled version of their own
  neighbourhood's name.

`load_mw` is published only when the cell parses as a decimal number inside the
plausible band for an 11kV feeder (0.2 to 15 MW). Integers are rejected because
serial numbers read as integers, and an out-of-band value is a misplaced decimal
rather than a large feeder. A wrong load is worse than no load.

## 3. `data/geo/<name>.geojson`

Standard GeoJSON `FeatureCollection`. Every `Feature.properties`:

```jsonc
{
  "id": "desco",                       // stable slug
  "name": "DESCO",
  "name_bn": "ডেসকো",
  "level": "utility",                  // utility|division|feeder|admin
  "utility": "DESCO",
  "status": "official",                // official|derived|estimated
  "confidence": "high",                // high|medium|low
  "source_url": "https://...",         // where the geometry came from
  "source_license": "ODbL",            // must be free/open
  "retrieved_at": "2026-08-23T00:00:00Z",
  "notes": "Approximated from OSM admin boundaries; not a utility-published polygon."
}
```

`status: "estimated"` renders **dashed** on the map. `official` renders solid.

## 4. Cost constraint — ABSOLUTE

$0.00. Allowed: GitHub (repo + Pages + Actions), OpenStreetMap-derived open data,
OpenFreeMap / demotiles vector tiles, Nominatim/Photon public geocoders (rate-limited,
must send a descriptive User-Agent), browser Geolocation API, localStorage.
Forbidden: anything requiring a credit card, API key with a paid tier, managed DB,
paid host, paid tiles (Mapbox/Google), paid geocoder.
