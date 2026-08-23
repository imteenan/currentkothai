"""Orchestrator: discover -> fetch -> archive -> parse -> validate -> publish.

    python -m workers.ingestion.run_ingest              # all utilities
    python -m workers.ingestion.run_ingest --only DESCO
    python -m workers.ingestion.run_ingest --offline    # reuse archived bytes

One utility failing must never take down the others, and a failed run must never
blank previously published data -- it flips that feed's status instead.
"""
from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

from workers.ingestion import validate as V
from workers.ingestion.common import (
    ARCHIVE_DIR,
    REGISTRY_DIR,
    SCHEDULES_DIR,
    STALE_AFTER_HOURS,
    STATE_PATH,
    dhaka_today,
    ext_for,
    http_get,
    iso_utc,
    read_json,
    sha256_bytes,
    weekday_index_for_date,
    write_json,
)
from workers.parsers import desco_listing_v1, desco_pdf_v1, dpdc_pdf_v1, scan_grid_v1


def _archive(utility: str, content: bytes, content_type: str, url: str) -> Path:
    ext = ext_for(content, content_type, url)
    digest = sha256_bytes(content)
    path = ARCHIVE_DIR / utility.lower() / ("%s-%s.%s" % (dhaka_today(), digest[:8], ext))
    if not path.exists():                     # archives are immutable
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return path


def _newest_archive(utility: str, ext: str = "pdf") -> Path | None:
    d = ARCHIVE_DIR / utility.lower()
    files = sorted(d.glob("*.%s" % ext)) if d.exists() else []
    return files[-1] if files else None


# --------------------------------------------------------------------- DESCO


def ingest_desco(*, offline: bool = False) -> dict:
    """Returns the index row for DESCO."""
    out = {
        "utility": "DESCO",
        "coverage_level": "feeder",
        "status": "unavailable",
        "latest_date": None,
        "claim_count": 0,
        "retrieved_at": None,
        "source_url": None,
        "stale_after_hours": STALE_AFTER_HOURS,
        "message": None,
    }
    latest_path = SCHEDULES_DIR / "desco" / "latest.json"
    previous = read_json(latest_path)

    listing_url = "https://desco.gov.bd/pages/static-pages/69db2a3c6a42b12e9344d1f1"
    pdf_bytes: bytes | None = None
    pdf_url = None
    tls_ok = True
    archive_path: Path | None = None

    if not offline:
        page = http_get(listing_url, timeout=60)
        if not page.ok:
            out["message"] = "listing page unreachable: %s" % (page.error or page.status)
        else:
            candidates = desco_listing_v1.discover(page.content)
            if not candidates:
                out["message"] = "no schedule PDF link found on the listing page"
            else:
                pdf_url = candidates[0]["url"]
                got = http_get(pdf_url, timeout=120)
                if got.ok:
                    pdf_bytes, tls_ok = got.content, got.tls_verified
                    archive_path = _archive("desco", got.content, got.content_type, pdf_url)
                else:
                    out["message"] = "schedule PDF unreachable: %s" % (got.error or got.status)

    if pdf_bytes is None:
        # Offline, or the network failed. Fall back to the newest archived copy
        # so the pipeline is still exercised -- but say so.
        archive_path = _newest_archive("desco") or Path(
            "data/seed/samples/desco-load-management-sunday-2026-07.pdf")
        if not archive_path.exists():
            out["message"] = out["message"] or "no archived DESCO PDF available"
            return _finish_unavailable(out, previous)
        pdf_bytes = archive_path.read_bytes()
        pdf_url = pdf_url or "https://desco.gov.bd/pages/static-pages/69db2a3c6a42b12e9344d1f1"
        out["message"] = (out["message"] or "") + " (parsed from local archive)"

    effective_date = dhaka_today()
    doc = desco_pdf_v1.parse(
        archive_path,
        source_id="desco-load-management-pdf",
        source_url=pdf_url,
        sha256=sha256_bytes(pdf_bytes),
        effective_date=effective_date,
        retrieved_at=iso_utc(),
        tls_verified=tls_ok,
        archive_path=str(archive_path).replace("\\", "/"),
        weekday_hint=weekday_index_for_date(effective_date),
    )

    report = V.validate(doc, previous=previous, source_had_content=bool(pdf_bytes))
    print("    validation: %s" % report.summary())
    for f in report.findings:
        print("      [%s] %s: %s" % (f["verdict"], f["check"], f["detail"]))

    if report.verdict == V.REJECT:
        p = V.quarantine(doc, report, "desco-%s" % effective_date)
        out["status"] = "unavailable"
        out["message"] = "parse rejected by validation; kept previous version. See %s" % p
        return _finish_unavailable(out, previous)

    if report.verdict == V.QUARANTINE:
        V.quarantine(doc, report, "desco-%s-review" % effective_date)
        doc.setdefault("parse_meta", {})["quarantine_findings"] = report.to_dict()

    doc["validation"] = report.to_dict()
    V.learn_divisions(doc)

    write_json(SCHEDULES_DIR / "desco" / ("%s.json" % effective_date), doc)
    write_json(latest_path, doc)

    out.update({
        "status": "fresh",
        "latest_date": effective_date,
        "claim_count": len(doc["claims"]),
        "retrieved_at": doc["source"]["retrieved_at"],
        "source_url": pdf_url,
        "coverage_level": doc["coverage_level"],
        "division_count": doc["stats"]["division_count"],
        "feeder_count": doc["stats"]["feeder_count"],
        "tls_verified": tls_ok,
        "validation_verdict": report.verdict,
    })
    return out


def _finish_unavailable(out: dict, previous: dict | None) -> dict:
    """Keep serving the last good version; only the status changes."""
    if previous:
        out["latest_date"] = previous.get("effective_date")
        out["claim_count"] = len(previous.get("claims") or [])
        out["retrieved_at"] = (previous.get("source") or {}).get("retrieved_at")
        out["source_url"] = (previous.get("source") or {}).get("source_url")
        out["status"] = "stale"
    return out


#: The hour grid DPDC prints on its digital sheets. Used only as a geometric
#: cross-check when a scan's labels cannot be OCR'd, and never without the
#: column geometry matching first.
DPDC_HOUR_TEMPLATE = [(9, 10), (10, 11), (11, 12), (12, 13), (14, 15), (15, 16),
                      (16, 17), (19, 20), (20, 21), (21, 22), (22, 23), (23, 24)]


# ---------------------------------------------------------------------- DPDC


def ingest_dpdc(*, offline: bool = False) -> dict:
    """Parse every DPDC NOCS zone sheet into one combined document.

    The Bangla is Bijoy-encoded, but the schedule itself is recoverable without
    it: feeder codes and hour headers are ASCII and the shedding mark is a black
    rectangle. See workers/parsers/dpdc_pdf_v1 for the detail.
    """
    out = {
        "utility": "DPDC", "coverage_level": "feeder", "status": "unavailable",
        "latest_date": None, "claim_count": 0, "retrieved_at": None,
        "source_url": "https://dpdc.org.bd/site/nocs/load_shedding",
        "stale_after_hours": STALE_AFTER_HOURS, "message": None,
    }
    latest_path = SCHEDULES_DIR / "dpdc" / "latest.json"
    previous = read_json(latest_path)

    zones = (read_json(REGISTRY_DIR / "dpdc-zones.json", {}) or {}).get("zones", [])
    zones = [z for z in zones if z.get("pdf_url")]
    if not zones:
        out["message"] = "no DPDC zone PDFs registered; run build_dpdc_zones first"
        return _finish_unavailable(out, previous)

    effective_date = dhaka_today()
    claims, ok, failed, scanned = [], 0, [], []
    ocr_ok = 0
    hour_sets, tls_all = set(), True
    part_hashes: list[str] = []

    for z in zones:
        try:
            if offline:
                archive = _newest_archive("dpdc-%s" % z["slug"])
                if not archive:
                    continue
                content = archive.read_bytes()
                tls_ok = True
            else:
                got = http_get(z["pdf_url"], timeout=90)
                if not got.ok:
                    failed.append("%s (%s)" % (z["slug"], got.error or got.status))
                    continue
                content, tls_ok = got.content, got.tls_verified
                archive = _archive("dpdc-%s" % z["slug"], content, got.content_type, z["pdf_url"])
            tls_all = tls_all and tls_ok

            doc = dpdc_pdf_v1.parse(
                archive, source_id="dpdc-zone-%s" % z["slug"], source_url=z["pdf_url"],
                sha256=sha256_bytes(content), effective_date=effective_date,
                zone=z["name"], retrieved_at=iso_utc(), tls_verified=tls_ok,
                archive_path=str(archive).replace("\\", "/"))
            claims.extend(doc["claims"])
            hour_sets.add(tuple(doc["parse_meta"]["hour_columns"]))
            part_hashes.append("%s:%s" % (z["slug"], doc["source"]["sha256"]))
            ok += 1
        except dpdc_pdf_v1.ScannedDocument:
            # A scan is not a dead end: read the grid off the image instead.
            try:
                doc = scan_grid_v1.parse(
                    archive, source_id="dpdc-zone-%s" % z["slug"], source_url=z["pdf_url"],
                    sha256=sha256_bytes(content), effective_date=effective_date,
                    utility="DPDC", zone=z["name"], publisher=dpdc_pdf_v1.PUBLISHER,
                    retrieved_at=iso_utc(), tls_verified=tls_ok,
                    archive_path=str(archive).replace("\\", "/"),
                    template_hours=DPDC_HOUR_TEMPLATE)
                claims.extend(doc["claims"])
                part_hashes.append("%s:%s" % (z["slug"], doc["source"]["sha256"]))
                ocr_ok += 1
            except Exception as exc:  # noqa: BLE001
                scanned.append("%s (%s)" % (z["slug"], type(exc).__name__))
        except Exception as exc:  # noqa: BLE001 - one bad zone must not stop the rest
            failed.append("%s (%s)" % (z["slug"], type(exc).__name__))

    if not claims:
        out["message"] = "no DPDC zone parsed: %s" % ("; ".join(failed[:4]) or "unknown")
        return _finish_unavailable(out, previous)

    doc = {
        "schema": "schedule-claims/1", "utility": "DPDC",
        "publisher": dpdc_pdf_v1.PUBLISHER, "effective_date": effective_date,
        "coverage_level": "feeder", "badge": "DERIVED",
        "source": {
            "source_id": "dpdc-load-shedding-index",
            "source_url": "https://dpdc.org.bd/site/nocs/load_shedding",
            # The aggregate has no single file, so its hash is the digest of the
            # per-zone hashes: still reproducible, still verifiable.
            "retrieved_at": iso_utc(),
            "sha256": sha256_bytes("|".join(sorted(part_hashes)).encode("utf-8")),
            "part_hashes": sorted(part_hashes),
            "parser_adapter": dpdc_pdf_v1.PARSER_ADAPTER,
            "parser_version": dpdc_pdf_v1.PARSER_VERSION,
            "tls_verified": tls_all, "archive_path": None,
        },
        "claims": claims,
        "stats": {
            "claim_count": len(claims),
            "feeder_count": len({c["feeder_id"] for c in claims}),
            "division_count": len({c["division"] for c in claims}),
        },
        "parse_meta": {
            "zones_parsed": ok, "zones_read_by_ocr": ocr_ok,
            "zones_failed": failed, "zones_unreadable": scanned,
            # Zones publish different hour grids, and a zone with nothing marked
            # contributes an empty tuple; report the widest grid seen, not the first.
            "hour_column_count": max((len(h) for h in hour_sets), default=0),
            "hour_grids_seen": len(hour_sets),
            "extraction_method": "pdfplumber + black-rectangle mark detection",
            "warnings": (["zones disagree on hour columns"] if len(hour_sets) > 1 else []),
        },
    }

    report = V.validate(doc, previous=previous, source_had_content=True)
    print("    validation: %s" % report.summary())
    if report.verdict == V.REJECT:
        V.quarantine(doc, report, "dpdc-%s" % effective_date)
        out["message"] = "parse rejected by validation; kept previous version"
        return _finish_unavailable(out, previous)

    doc["validation"] = report.to_dict()
    write_json(SCHEDULES_DIR / "dpdc" / ("%s.json" % effective_date), doc)
    write_json(latest_path, doc)

    out.update({
        "status": "fresh", "latest_date": effective_date,
        "claim_count": len(claims), "retrieved_at": doc["source"]["retrieved_at"],
        "coverage_level": "feeder",
        "division_count": doc["stats"]["division_count"],
        "feeder_count": doc["stats"]["feeder_count"],
        "tls_verified": tls_all, "validation_verdict": report.verdict,
        "message": ("%d of %d zone sheets read: %d digital, %d scanned and read by OCR"
                    % (ok + ocr_ok, len(zones), ok, ocr_ok)
                    + ("; %d scans still unreadable and linked instead" % len(scanned)
                       if scanned else "")),
        "zones_parsed": sorted({c["division"] for c in claims}),
        "zones_scanned": scanned,
    })
    return out


# ------------------------------------------------- utilities without a parser


def link_only_row(utility: str, coverage: str, url: str, message: str) -> dict:
    """A utility we can point at but cannot parse. Honest placeholder."""
    return {
        "utility": utility,
        "coverage_level": coverage,
        "status": "link-only",
        "latest_date": None,
        "claim_count": 0,
        "retrieved_at": None,
        "source_url": url,
        "stale_after_hours": STALE_AFTER_HOURS,
        "message": message,
    }


LINK_ONLY = [
    ("NESCO", "utility", "https://nesco.gov.bd/",
     "Schedules are published as scanned images. The layout matches DPDC's (hour grid "
     "with shaded cells) but the page is raster, so reading it needs Bangla OCR."),
    ("BPDB", "utility", "https://bpdb.gov.bd/",
     "No machine-readable consumer load-shedding schedule identified."),
    ("BREB", "utility", "https://reb.gov.bd/",
     "Rural supply runs through ~80 separate Palli Bidyut Samity offices, each "
     "publishing independently. No national feed exists."),
    ("WZPDCL", "utility", "https://wzpdcl.gov.bd/",
     "No machine-readable consumer load-shedding schedule identified."),
]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only", help="ingest a single utility, e.g. DESCO")
    ap.add_argument("--offline", action="store_true",
                    help="parse the newest archived file instead of fetching")
    args = ap.parse_args(argv)

    rows: list[dict] = []
    failures = 0

    targets = {"DESCO": ingest_desco, "DPDC": ingest_dpdc}
    for name, fn in targets.items():
        if args.only and args.only.upper() != name:
            continue
        print("  %s ..." % name)
        try:
            rows.append(fn(offline=args.offline))
        except Exception:
            failures += 1
            traceback.print_exc()
            rows.append({
                "utility": name, "status": "unavailable", "claim_count": 0,
                "coverage_level": "unknown", "latest_date": None,
                "retrieved_at": None, "source_url": None,
                "stale_after_hours": STALE_AFTER_HOURS,
                "message": "ingestion raised an exception; see workflow logs",
            })

    if not args.only:
        rows.extend(link_only_row(*x) for x in LINK_ONLY)

    if args.only:
        old = read_json(SCHEDULES_DIR / "index.json", {}) or {}
        keep = [r for r in (old.get("utilities") or [])
                if r["utility"].upper() != args.only.upper()]
        rows = rows + keep

    write_json(SCHEDULES_DIR / "index.json", {
        "schema": "schedule-index/1",
        "generated_at": iso_utc(),
        "stale_after_hours": STALE_AFTER_HOURS,
        "utilities": sorted(rows, key=lambda r: (r["status"] != "fresh", r["utility"])),
    })

    state = read_json(STATE_PATH, {}) or {}
    state["last_run"] = iso_utc()
    write_json(STATE_PATH, state)

    for r in rows:
        print("  %-8s %-11s claims=%-5s %s" % (
            r["utility"], r["status"], r.get("claim_count", 0), r.get("message") or ""))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
