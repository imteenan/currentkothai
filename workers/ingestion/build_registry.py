"""Probe every candidate official source and write data/registry/sources.json.

Run this when adding a source or when a distributor reorganises their site. It
records what it ACTUALLY observed -- status code, TLS-chain validity, byte size,
content type -- so `verified_at` in the registry is evidence, not a claim.

    python -m workers.ingestion.build_registry [--offline]

--offline keeps the previously recorded observations and only refreshes the
static metadata, for when you are working without a network.
"""
from __future__ import annotations

import argparse
import sys

from workers.ingestion.common import (
    SOURCES_PATH,
    ext_for,
    http_get,
    iso_utc,
    read_json,
    write_json,
)

# ---------------------------------------------------------------------------
# The candidate list. `probe` is the URL we actually hit to prove the source is
# alive. Anything we could not verify stays in the file with its observed
# failure recorded -- a dead source is a finding, not a reason to delete a row.
# ---------------------------------------------------------------------------
CANDIDATES: list[dict] = [
    {
        "id": "desco-load-management-page",
        "utility": "DESCO",
        "source_type": "schedule",
        "title": "Load Management Schedule (লোড ব্যবস্থাপনা শিডিউল)",
        "source_url": "https://desco.gov.bd/pages/static-pages/69db2a3c6a42b12e9344d1f1",
        "format": "html",
        "language": "mixed",
        "coverage_level": "feeder",
        "expected_update_frequency": "irregular",
        "parser_adapter": "desco_listing_v1",
        "notes": "Landing page. Links out to the current schedule PDF, which is hosted on "
                 "Oracle object storage under a UUID filename that changes on every "
                 "publish - so the PDF URL must be discovered from this page, never hardcoded.",
    },
    {
        "id": "desco-load-management-pdf",
        "utility": "DESCO",
        "source_type": "schedule",
        "title": "DESCO Load Shedding Schedule on 11 KV Feeders",
        "source_url": "https://objectstorage.ap-dcc-gazipur-1.oraclecloud15.com/n/axvjbnqprylg/b/V2Ministry/o/office-desco/2026/7/81a30f11-39ac-4747-89cd-9f2bd2427fb2.pdf",
        "format": "pdf",
        "language": "en",
        "coverage_level": "feeder",
        "expected_update_frequency": "irregular",
        "parser_adapter": "desco_pdf_v1",
        "notes": "The real prize: a ruled table of S&D division, area description, feeder "
                 "name, load in MW, and a 24-hour grid. One PDF per weekday. Fully parseable.",
    },
    {
        "id": "dpdc-load-shedding-index",
        "utility": "DPDC",
        "source_type": "schedule",
        "title": "DPDC load-shedding schedule index",
        "source_url": "https://dpdc.org.bd/site/nocs/load_shedding",
        "format": "html",
        "language": "en",
        "coverage_level": "division",
        "expected_update_frequency": "daily",
        "parser_adapter": "dpdc_listing_v1",
        "notes": "Index of 36 NOCS zone PDFs. NOTE the host: schedules live on dpdc.ORG.bd, "
                 "not the dpdc.gov.bd corporate site. The filename carries a revision counter "
                 "(.../100.pdf -> .../150.pdf) that increments on republish, so the current "
                 "number must be scraped from this index rather than hardcoded.",
    },
    {
        "id": "dpdc-zone-pdf-motijheel",
        "utility": "DPDC",
        "source_type": "schedule",
        "title": "DPDC Motijheel zone load-shedding schedule",
        "source_url": "https://dpdc.org.bd/site/load_shedding/motijheel/100.pdf",
        "format": "pdf",
        "language": "bn",
        "coverage_level": "feeder",
        "expected_update_frequency": "daily",
        "parser_adapter": None,
        "notes": "Representative of all 36 zone PDFs. Feeder codes, load in MW and the hour "
                 "columns are ASCII and readable, but the Bangla area names use a legacy "
                 "Bijoy/ASCII font mapping rather than Unicode, so they extract as mojibake. "
                 "No parser adapter until that is decoded - we link the original instead.",
    },
    {
        "id": "nesco-website",
        "utility": "NESCO",
        "source_type": "notice",
        "title": "NESCO official website",
        "source_url": "https://nesco.gov.bd/",
        "format": "html",
        "language": "mixed",
        "coverage_level": "utility",
        "expected_update_frequency": "irregular",
        "parser_adapter": None,
        "notes": "Load-shedding notices are published as scanned images with zero extractable "
                 "text, so no structured schedule can be derived without OCR.",
    },
    {
        "id": "pgcb-daily-generation",
        "utility": "PGCB",
        "source_type": "grid_condition",
        "title": "PGCB daily generation and demand",
        "source_url": "https://erp.powergrid.gov.bd/w/generations/view_generations",
        "format": "html",
        "language": "en",
        "coverage_level": "utility",
        "expected_update_frequency": "hourly",
        "parser_adapter": None,
        "notes": "National generation vs demand. Context for why shedding happens; not a "
                 "location-specific schedule.",
    },
    {
        "id": "bpdb-website",
        "utility": "BPDB", "source_type": "notice", "title": "BPDB official website",
        "source_url": "https://bpdb.gov.bd/", "format": "html", "language": "mixed",
        "coverage_level": "utility", "expected_update_frequency": "irregular",
        "parser_adapter": None, "notes": "No machine-readable consumer schedule identified.",
    },
    {
        "id": "breb-website",
        "utility": "BREB", "source_type": "notice", "title": "BREB (Palli Bidyut) official website",
        "source_url": "https://reb.gov.bd/", "format": "html", "language": "mixed",
        "coverage_level": "utility", "expected_update_frequency": "irregular",
        "parser_adapter": None,
        "notes": "Rural supply is run by ~80 separate PBS cooperatives, each publishing its own "
                 "notices. There is no single national feed.",
    },
    {
        "id": "wzpdcl-website",
        "utility": "WZPDCL", "source_type": "notice", "title": "WZPDCL official website",
        "source_url": "https://wzpdcl.gov.bd/", "format": "html", "language": "mixed",
        "coverage_level": "utility", "expected_update_frequency": "irregular",
        "parser_adapter": None, "notes": "No machine-readable consumer schedule identified.",
    },
    {
        "id": "powerdivision-website",
        "utility": "POWERDIV", "source_type": "notice", "title": "Power Division",
        "source_url": "https://powerdivision.gov.bd/", "format": "html", "language": "mixed",
        "coverage_level": "utility", "expected_update_frequency": "irregular",
        "parser_adapter": None, "notes": "Ministry-level notices and the unified 16999 call centre.",
    },
]


def probe(entry: dict) -> dict:
    res = http_get(entry["source_url"], timeout=45)
    observed = {
        "verified_at": iso_utc(),
        "http_status": res.status,
        "tls_ok": bool(res.tls_verified) and res.status == 200,
        "observed_bytes": len(res.content),
        "observed_format": ext_for(res.content, res.content_type, res.final_url) if res.content else None,
        "final_url": res.final_url if res.final_url != entry["source_url"] else None,
        "probe_error": res.error or None,
    }
    if res.ok and observed["observed_format"] and observed["observed_format"] != entry["format"]:
        observed["probe_error"] = "declared format %r but served %r" % (
            entry["format"], observed["observed_format"])
    return observed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--offline", action="store_true",
                    help="keep previously recorded observations")
    args = ap.parse_args(argv)

    previous = {s["id"]: s for s in (read_json(SOURCES_PATH, {}) or {}).get("sources", [])}
    sources = []

    for entry in CANDIDATES:
        row = dict(entry)
        if args.offline and entry["id"] in previous:
            old = previous[entry["id"]]
            for k in ("verified_at", "http_status", "tls_ok", "observed_bytes",
                      "observed_format", "final_url", "probe_error"):
                row[k] = old.get(k)
            print("  = %-32s (kept)" % entry["id"])
        else:
            row.update(probe(row))
            flag = "OK " if row["http_status"] == 200 else "!! "
            print("  %s%-32s status=%-4s tls=%-5s %s" % (
                flag, entry["id"], row["http_status"], row["tls_ok"],
                row["probe_error"] or ""))
        sources.append(row)

    reachable = sum(1 for s in sources if s.get("http_status") == 200)
    doc = {
        "schema": "source-registry/1",
        "generated_at": iso_utc(),
        "summary": {
            "total": len(sources),
            "reachable": reachable,
            "with_parser": sum(1 for s in sources if s.get("parser_adapter")),
            "tls_chain_ok": sum(1 for s in sources if s.get("tls_ok")),
        },
        "sources": sources,
    }
    write_json(SOURCES_PATH, doc)
    print("\nwrote %s  (%d/%d reachable)" % (SOURCES_PATH, reachable, len(sources)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
