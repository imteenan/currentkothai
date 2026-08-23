"""The safety gate between a parse and the public site.

Publishing a wrong schedule is worse than publishing an older validated one with
a staleness warning, so this module is deliberately unforgiving. Checks return
one of three verdicts:

    REJECT     - the document is wrong. Never publish. Keep the previous version.
    QUARANTINE - the document is suspicious. Write it aside for a human, keep
                 the previous version live.
    PASS       - safe to activate.
"""
from __future__ import annotations

import re
from typing import Any

from workers.ingestion.common import (
    KNOWN_DIVISIONS_PATH,
    QUARANTINE_DIR,
    SCHEMA_DIR,
    read_json,
    write_json,
)

REJECT, QUARANTINE, PASS = "reject", "quarantine", "pass"
_SEVERITY = {PASS: 0, QUARANTINE: 1, REJECT: 2}

_HHMM = re.compile(r"^([01]\d|2[0-4]):([0-5]\d)$")


class Report:
    def __init__(self) -> None:
        self.findings: list[dict[str, str]] = []

    def add(self, verdict: str, check: str, detail: str) -> None:
        self.findings.append({"verdict": verdict, "check": check, "detail": detail})

    @property
    def verdict(self) -> str:
        if not self.findings:
            return PASS
        return max((f["verdict"] for f in self.findings), key=lambda v: _SEVERITY[v])

    @property
    def ok(self) -> bool:
        return self.verdict == PASS

    def summary(self) -> str:
        if not self.findings:
            return "pass (no findings)"
        counts: dict[str, int] = {}
        for f in self.findings:
            counts[f["verdict"]] = counts.get(f["verdict"], 0) + 1
        return "%s (%s)" % (
            self.verdict,
            ", ".join("%d %s" % (n, v) for v, n in sorted(counts.items())))

    def to_dict(self) -> dict[str, Any]:
        return {"verdict": self.verdict, "findings": self.findings}


def _minutes(hhmm: str) -> int | None:
    m = _HHMM.match(str(hhmm or ""))
    if not m:
        return None
    h, mm = int(m.group(1)), int(m.group(2))
    if h == 24 and mm != 0:
        return None
    return h * 60 + mm


def validate(doc: dict[str, Any], *, previous: dict[str, Any] | None = None,
             source_had_content: bool = True) -> Report:
    r = Report()

    # ---- 1. schema ---------------------------------------------------------
    schema = read_json(SCHEMA_DIR / "schedule-claims-1.schema.json")
    if schema:
        try:
            import jsonschema
            jsonschema.validate(doc, schema)
        except ImportError:
            r.add(QUARANTINE, "schema", "jsonschema not installed; schema not checked")
        except Exception as exc:  # jsonschema.ValidationError
            r.add(REJECT, "schema", "schema violation: %s" % str(exc).split("\n")[0][:200])
    else:
        r.add(QUARANTINE, "schema", "schema file missing; structural check skipped")

    claims = doc.get("claims") or []

    # ---- 2. the classic silent failure -------------------------------------
    if source_had_content and not claims:
        r.add(REJECT, "empty-parse",
              "source document had content but zero claims were parsed")

    # ---- 3. hour grid ------------------------------------------------------
    # DESCO prints a full 24-column grid; DPDC prints only the hours it sheds in
    # (12 for Motijheel, and it varies by zone). So the rule is "a sane number of
    # columns", not "exactly 24" -- a hardcoded 24 rejected valid DPDC sheets.
    hours = (doc.get("parse_meta") or {}).get("hour_column_count")
    if hours is not None:
        if hours < 1 or hours > 24:
            r.add(REJECT, "hour-columns",
                  "hour column count %s is outside 1-24" % hours)
        elif hours != 24 and doc.get("utility") == "DESCO":
            r.add(REJECT, "hour-columns",
                  "DESCO publishes a 24-column grid, parser saw %s" % hours)

    # ---- 4. time sanity ----------------------------------------------------
    bad_windows = 0
    zero_window_claims = 0
    for c in claims:
        wins = c.get("windows") or []
        if not wins:
            zero_window_claims += 1
        for w in wins:
            s, e = _minutes(w.get("start")), _minutes(w.get("end"))
            if s is None or e is None or e <= s:
                bad_windows += 1
    if bad_windows:
        r.add(REJECT, "time-sanity",
              "%d malformed window(s): unparseable time, or end <= start" % bad_windows)
    if zero_window_claims:
        r.add(QUARANTINE, "empty-claims",
              "%d claim(s) carry no window and assert nothing" % zero_window_claims)

    # ---- 5. entity consistency --------------------------------------------
    known = set(read_json(KNOWN_DIVISIONS_PATH, {}).get(doc.get("utility", ""), []))
    seen = {c.get("division") for c in claims if c.get("division")}
    if known:
        novel = sorted(seen - known)
        if novel:
            r.add(QUARANTINE, "unknown-division",
                  "division name(s) not seen before, review then add to "
                  "known_divisions.json: %s" % ", ".join(novel[:8]))

    uncanonical = [c for c in claims if not c.get("division_canonical")]
    if uncanonical:
        share = len(uncanonical) / max(1, len(claims))
        verdict = REJECT if share > 0.25 else QUARANTINE
        r.add(verdict, "canonical-division",
              "%d/%d claim(s) (%.0f%%) have no canonical division"
              % (len(uncanonical), len(claims), share * 100))

    # ---- 6. duplicates -----------------------------------------------------
    seen_keys: set[tuple] = set()
    dupes = 0
    for c in claims:
        for w in c.get("windows") or []:
            key = (c.get("feeder_id"), c.get("weekday"), w.get("start"), w.get("end"))
            if key in seen_keys:
                dupes += 1
            seen_keys.add(key)
    if dupes:
        r.add(QUARANTINE, "duplicates", "%d duplicate feeder/window row(s)" % dupes)

    # ---- 7. never let an older document replace a newer one ----------------
    if previous:
        prev_date = str(previous.get("effective_date") or "")
        new_date = str(doc.get("effective_date") or "")
        if prev_date and new_date and new_date < prev_date:
            r.add(REJECT, "date-regression",
                  "incoming effective_date %s is older than the active %s"
                  % (new_date, prev_date))
        prev_n = len((previous.get("claims") or []))
        new_n = len(claims)
        if prev_n >= 50 and new_n < prev_n * 0.5:
            r.add(QUARANTINE, "volume-drop",
                  "claim count fell from %d to %d (>50%% drop) - likely a layout change"
                  % (prev_n, new_n))

    # ---- 8. provenance is mandatory ---------------------------------------
    src = doc.get("source") or {}
    for field in ("source_url", "retrieved_at", "sha256", "parser_adapter"):
        if not src.get(field):
            r.add(REJECT, "provenance", "source.%s is missing" % field)

    return r


def quarantine(doc: dict[str, Any], report: Report, name: str) -> str:
    """Write a rejected/suspicious document aside. Never touches latest.json."""
    path = QUARANTINE_DIR / ("%s.json" % name)
    write_json(path, {"report": report.to_dict(), "document": doc})
    return str(path)


def learn_divisions(doc: dict[str, Any]) -> None:
    """Record the divisions in a PASSING document so novel ones stand out later."""
    utility = doc.get("utility")
    if not utility:
        return
    known = read_json(KNOWN_DIVISIONS_PATH, {}) or {}
    current = set(known.get(utility, []))
    current |= {c["division"] for c in (doc.get("claims") or []) if c.get("division")}
    known[utility] = sorted(current)
    write_json(KNOWN_DIVISIONS_PATH, known)
