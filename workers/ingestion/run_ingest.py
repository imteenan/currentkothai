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
    """The most recently fetched archived file for a utility.

    Archive names are "<date>-<hash>.<ext>", so sorting by name alone lets the
    content hash decide the winner whenever a utility republishes on the same
    day. That is not a tie-break, it is a coin flip: DPDC reissued Ramna and the
    name sort picked the superseded morning sheet, dropping 13 feeders. Order by
    date prefix first, then by mtime, which is when we actually retrieved it.
    """
    d = ARCHIVE_DIR / utility.lower()
    if not d.exists():
        return None
    files = list(d.glob("*.%s" % ext))
    if not files:
        return None
    return max(files, key=lambda f: (f.name[:10], f.stat().st_mtime))


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

    def _try_pdf(url: str) -> bool:
        """Fetch one PDF URL into the enclosing scope. True when it worked."""
        nonlocal pdf_bytes, pdf_url, tls_ok, archive_path
        got = http_get(url, timeout=120)
        if not got.ok:
            return False
        pdf_bytes, tls_ok = got.content, got.tls_verified
        pdf_url = url
        archive_path = _archive("desco", got.content, got.content_type, url)
        return True

    if not offline:
        page = http_get(listing_url, timeout=60)
        if not page.ok:
            out["message"] = "listing page unreachable: %s" % (page.error or page.status)
            # The listing lives on desco.gov.bd, which times out from GitHub's
            # runners; the PDF itself lives on Oracle object storage, which does
            # not. Losing the page should not mean losing the schedule, so try
            # the last PDF URL we successfully read. It is only a route to the
            # same publisher, and if DESCO has since replaced the file this
            # fetches the old one and the run is marked stale rather than fresh.
            last = ((previous or {}).get("source") or {}).get("source_url") or ""
            if last.lower().endswith(".pdf") and _try_pdf(last):
                out["message"] += " (reached the PDF directly at its last known URL)"
        else:
            candidates = desco_listing_v1.discover(page.content)
            if not candidates:
                out["message"] = "no schedule PDF link found on the listing page"
            else:
                if not _try_pdf(candidates[0]["url"]):
                    out["message"] = "schedule PDF unreachable: %s" % candidates[0]["url"]

    fetched_live = pdf_bytes is not None

    if pdf_bytes is None:
        # Nothing was fetched this run.
        #
        # This fallback used to end at data/seed/samples/...-sunday-2026-07.pdf.
        # data/seed/archive is gitignored, so a CI runner has no archive at all
        # and always reached that sample - and desco.gov.bd times out from
        # GitHub's runners. The live site therefore served a July fixture, on a
        # Wednesday, as Sunday's schedule, reported as "fresh".
        #
        # A seed sample is a test fixture and must never reach a reader. An
        # offline run may use one because the operator asked for offline. A
        # scheduled run must fall back to the last thing we really published and
        # say that it is stale, which is the honest answer to "we could not
        # reach them today".
        archive_path = _newest_archive("desco")
        if archive_path is None and offline:
            sample = Path("data/seed/samples/desco-load-management-sunday-2026-07.pdf")
            archive_path = sample if sample.exists() else None
        if archive_path is None or not archive_path.exists():
            out["message"] = (out["message"] or "") + " no DESCO schedule could be fetched"
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

    changed = publish_schedule(
        latest_path, SCHEDULES_DIR / "desco" / ("%s.json" % effective_date), doc)
    if not changed:
        print("    unchanged since last publish; left as is")
        # Keep reporting the timestamp the live file actually carries, not now.
        doc = read_json(latest_path) or doc

    # DESCO publishes one sheet per weekday and prints the weekday on it, so the
    # sheet says whether it is today's. That is a stronger test than provenance:
    # a fetch can succeed and still hand back a superseded file, which is what
    # the "reached the PDF at its last known URL" route can do. Freshness is
    # therefore about the schedule being for today, not about our luck with the
    # network. The live site was showing Sunday's sheet on a Wednesday and
    # calling it fresh.
    sheet_weekday = next((c.get("weekday") for c in doc["claims"]
                          if c.get("weekday") is not None), None)
    sheet_is_today = sheet_weekday is None or sheet_weekday == weekday_index_for_date(effective_date)
    if not sheet_is_today:
        out["message"] = ((out["message"] or "")
                          + " sheet is for %s, not today"
                          % (doc.get("parse_meta", {}).get("weekday_name") or "another day"))

    out.update({
        # Only a run that reached the publisher AND came back with today's sheet
        # may call itself fresh. Saying "fresh" otherwise is what let a July
        # fixture reach readers with nothing on the page to warn them.
        "status": "fresh" if (fetched_live and sheet_is_today) else "stale",
        "latest_date": effective_date,
        "claim_count": len(doc["claims"]),
        # When these bytes were first seen. Distinct from the index's
        # generated_at, which is when we last looked at all.
        "content_changed_at": doc["source"]["retrieved_at"],
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
                    zone_slug=z["slug"],
                    retrieved_at=iso_utc(), tls_verified=tls_ok,
                    archive_path=str(archive).replace("\\", "/"),
                    dpi=400,
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
    if not publish_schedule(
            latest_path, SCHEDULES_DIR / "dpdc" / ("%s.json" % effective_date), doc):
        print("    unchanged since last publish; left as is")
        doc = read_json(latest_path) or doc

    out.update({
        "status": "fresh", "latest_date": effective_date,
        "claim_count": len(claims),
        # When these bytes were first seen, which is NOT when we last looked:
        # an unchanged schedule is not rewritten, so this stops advancing while
        # the ingest keeps checking. The index's generated_at is the check.
        "content_changed_at": doc["source"]["retrieved_at"],
        "retrieved_at": doc["source"]["retrieved_at"],
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
    # This used to say reading NESCO "needs Bangla OCR", which stopped being
    # the obstacle the day scan_grid_v1 started reading DPDC's 17 scanned
    # sheets. The reader exists and the layout is the same. What is missing is
    # the sheets: no per-zone schedule URL and no zone list have been found on
    # nesco.gov.bd, so there is nothing to point the reader at.
    ("NESCO", "utility", "https://nesco.gov.bd/",
     "Schedules are published as scanned images in the same hour-grid layout as "
     "DPDC's, which we already read. The blocker is finding the sheets: no "
     "per-zone schedule URL or zone list has been located on their site yet."),
    ("BPDB", "utility", "https://bpdb.gov.bd/",
     "No machine-readable consumer load-shedding schedule identified."),
    ("BREB", "utility", "https://reb.gov.bd/",
     "Rural supply runs through ~80 separate Palli Bidyut Samity offices, each "
     "publishing independently. No national feed exists."),
    ("WZPDCL", "utility", "https://wzpdcl.gov.bd/",
     "No machine-readable consumer load-shedding schedule identified."),
]


#: Fields that move on every run even when the published schedule is identical.
_VOLATILE = ("retrieved_at",)


def _schedule_unchanged(old: dict | None, new: dict) -> bool:
    """True when a freshly parsed document says exactly what the live one says.

    Compares the claims and the source identity, ignoring only fields that tick
    with the clock. The source SHA-256 is part of the comparison, so if the
    distributor republishes even one byte this returns False and we republish.
    """
    if not old:
        return False
    import copy

    def strip(d: dict) -> dict:
        c = copy.deepcopy(d)
        c.pop("parse_meta", None)
        c.pop("validation", None)
        src = c.get("source") or {}
        for k in _VOLATILE:
            src.pop(k, None)
        return c

    return strip(old) == strip(new)


def publish_schedule(latest_path: Path, dated_path: Path, doc: dict) -> bool:
    """Write a schedule only when it differs from what is already published.

    Without this, every scheduled run rewrites identical files with a new
    timestamp, producing a commit and a rebuild for no reason.
    """
    old = read_json(latest_path)
    if _schedule_unchanged(old, doc):
        return False
    write_json(dated_path, doc)
    write_json(latest_path, doc)
    return True


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

    index_path = SCHEDULES_DIR / "index.json"
    new_index = {
        "schema": "schedule-index/1",
        "generated_at": iso_utc(),
        "stale_after_hours": STALE_AFTER_HOURS,
        "utilities": sorted(rows, key=lambda r: (r["status"] != "fresh", r["utility"])),
    }
    # The index is always written, even when nothing changed.
    #
    # publish_schedule deliberately skips rewriting an unchanged schedule, which
    # is right: it avoids a commit and a rebuild for a file that says the same
    # thing. But `retrieved_at` lives inside that file, so it froze at whenever
    # the content last changed, and the UI read it as "when did we last look".
    # DPDC had not republished for two days, so the site told visitors it had
    # not been read in two days and raised a Stale badge - while the ingest was
    # in fact fetching it every six hours and finding it identical.
    #
    # `generated_at` here is the honest answer to "when did we last check", and
    # it is only true if this file is rewritten on every run. It is ~2KB.
    write_json(index_path, new_index)
    state = read_json(STATE_PATH, {}) or {}
    state["last_run"] = new_index["generated_at"]
    write_json(STATE_PATH, state)

    for r in rows:
        print("  %-8s %-11s claims=%-5s %s" % (
            r["utility"], r["status"], r.get("claim_count", 0), r.get("message") or ""))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
