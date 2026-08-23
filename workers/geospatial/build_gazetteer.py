"""Build the offline place gazetteer the browser searches instead of Nominatim.

    python -m workers.geospatial.build_gazetteer [--offline]

WHY THIS EXISTS
---------------
The site used to call Nominatim/Photon on every debounced keystroke and on every
location resolve. Nominatim's usage policy caps *the whole application* at about
one request per second and explicitly forbids bulk or heavy use. At even a few
hundred concurrent visitors that is a broken site and an abused volunteer
service. So the common path must never leave the origin: we ship a compact
gazetteer of Bangladeshi places and do forward search and nearest-place reverse
lookup entirely in the browser.

SOURCES (all free, all open)
  * OpenStreetMap place nodes via the Overpass API (ODbL)
  * OpenStreetMap admin relations, admin_level 7/8/9 (ODbL) - upazila/union seats
  * data/geo/dhaka-neighbourhoods.geojson, already in the repo (ODbL)
  * data/geo/bangladesh-admin.geojson (geoBoundaries, CC-BY 4.0) for the district
    each place falls in - computed locally, no extra network calls

HONESTY RULES
  1. Nothing is invented. Every entry is an OSM object that exists; the OSM id is
     recoverable from `osm` so a reader can check it.
  2. A place with no `name` is skipped rather than labelled "Unnamed".
  3. District is left UNKNOWN (not guessed) when the point falls outside every
     district polygon.
  4. If Overpass is unreachable the previous gazetteer is carried forward and the
     failure is recorded, exactly as AGENTS.md rule 4 requires for GeoJSON.

OUTPUT FORMAT (data/geo/gazetteer.json, `gazetteer/1`)
Columnar and deliberately ugly, because this file is on the critical path. Each
column is one array or one delimited string, parallel across `count` entries:

    n   '|'-joined Latin/English names
    b   '|'-joined Bangla names ("" where OSM has none)
    k   one character per entry, index into `kinds`   (see ALPHABET)
    d   one character per entry, index into `districts`, ' ' = unknown
    p   lat/lon pairs, Google-polyline-encoded at 1e5 (the same algorithm the
        rest of the world uses, ~40% smaller than a JSON array of deltas)

Per-place OSM ids live in the sibling `gazetteer-osm.json`, in the same order.
They are provenance, not something the search needs, and keeping them out of the
critical path saves ~24 KB gzipped on every first visit. Nothing is lost: the
ids stay in `data/`, and the collection-level source and licence are recorded in
the gazetteer header itself.

apps/web/src/gazetteer.js is the only consumer and documents the decode.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

import requests

from workers.ingestion.common import DATA, USER_AGENT, iso_utc

GEO = DATA / "geo"
OUT = GEO / "gazetteer.json"
OUT_OSM = GEO / "gazetteer-osm.json"
OVERPASS_ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)

#: Character alphabet for the single-char index columns. 90 printable, JSON-safe
#: characters, no backslash and no double quote so nothing ever needs escaping.
ALPHABET = (
    "0123456789"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "abcdefghijklmnopqrstuvwxyz"
    "!#$%&()*+,-./:;<=>?@[]^_`{|}~"
)
UNKNOWN_CHAR = " "

#: Ordered by how useful the place is as a search hit. The browser uses the
#: index as a tie-break, so the order is part of the format.
KINDS = [
    "city", "town", "suburb", "borough", "quarter",
    "neighbourhood", "village", "upazila", "union", "place",
]

PLACE_QUERY = """
[out:json][timeout:300];
area["ISO3166-1"="BD"][admin_level=2]->.bd;
(
  node["place"="city"](area.bd);
  node["place"="town"](area.bd);
  node["place"="borough"](area.bd);
  node["place"="suburb"](area.bd);
  node["place"="quarter"](area.bd);
  node["place"="neighbourhood"](area.bd);
  node["place"="village"](area.bd);
);
out body;
"""

ADMIN_QUERY = """
[out:json][timeout:300];
area["ISO3166-1"="BD"][admin_level=2]->.bd;
(
  relation["boundary"="administrative"]["admin_level"="7"](area.bd);
  relation["boundary"="administrative"]["admin_level"="8"](area.bd);
  relation["boundary"="administrative"]["admin_level"="9"](area.bd);
);
out center tags;
"""

ADMIN_LEVEL_KIND = {"7": "upazila", "8": "upazila", "9": "union"}


# --------------------------------------------------------------- overpass


def overpass(query: str, label: str, attempts: int = 4) -> list[dict]:
    """POST to Overpass, rotating endpoints and backing off on 429/504."""
    delay = 6.0
    last = "no attempt made"
    for i in range(attempts):
        url = OVERPASS_ENDPOINTS[i % len(OVERPASS_ENDPOINTS)]
        try:
            r = requests.post(url, data={"data": query},
                              headers={"User-Agent": USER_AGENT}, timeout=300)
            if r.status_code in (429, 504, 503):
                last = "%s -> HTTP %d" % (url, r.status_code)
                print("      . %s, backing off %.0fs" % (last, delay))
                time.sleep(delay)
                delay *= 1.8
                continue
            r.raise_for_status()
            elements = r.json().get("elements", [])
            print("      + %s: %d elements from %s" % (label, len(elements), url))
            return elements
        except Exception as exc:                      # noqa: BLE001 - report, do not crash
            last = "%s -> %s" % (url, exc)
            print("      . %s" % last)
            time.sleep(delay)
            delay *= 1.8
    raise RuntimeError("Overpass unavailable for %s (%s)" % (label, last))


# --------------------------------------------------------- district lookup


def _ring_contains(lon: float, lat: float, ring: list) -> bool:
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat):
            x_at = (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi
            if lon < x_at:
                inside = not inside
        j = i
    return inside


def _poly_contains(lon: float, lat: float, poly: list) -> bool:
    if not poly or not _ring_contains(lon, lat, poly[0]):
        return False
    return not any(_ring_contains(lon, lat, hole) for hole in poly[1:])


class DistrictIndex:
    """Point-in-polygon against the 64 ADM2 districts, with a bbox prefilter."""

    def __init__(self, path: Path):
        self.entries: list[tuple[str, tuple[float, float, float, float], list]] = []
        if not path.exists():
            print("      ! %s missing; districts will be UNKNOWN" % path.name)
            return
        fc = json.loads(path.read_text(encoding="utf-8"))
        for f in fc.get("features", []):
            name = (f.get("properties") or {}).get("name")
            geom = f.get("geometry") or {}
            polys = (geom.get("coordinates") or []) if geom.get("type") == "MultiPolygon" \
                else [geom.get("coordinates") or []]
            for poly in polys:
                if not poly or not poly[0]:
                    continue
                xs = [c[0] for c in poly[0]]
                ys = [c[1] for c in poly[0]]
                self.entries.append((name, (min(xs), min(ys), max(xs), max(ys)), poly))
        print("      + district index: %d polygons" % len(self.entries))

    def lookup(self, lon: float, lat: float) -> str | None:
        for name, (x0, y0, x1, y1), poly in self.entries:
            if lon < x0 or lon > x1 or lat < y0 or lat > y1:
                continue
            if _poly_contains(lon, lat, poly):
                return name
        return None


# ------------------------------------------------------------- collection


def _name_pair(tags: dict) -> tuple[str | None, str]:
    """(Latin/English name, Bangla name). Returns (None, _) when unnamed."""
    latin = tags.get("name:en") or tags.get("int_name")
    bangla = tags.get("name:bn") or ""
    plain = tags.get("name")
    if not latin and plain:
        # `name` in BD OSM is sometimes Bangla, sometimes Latin.
        if any("ঀ" <= ch <= "৿" for ch in plain):
            bangla = bangla or plain
        else:
            latin = plain
    if not latin and bangla:
        latin = ""            # Bangla-only entries are legitimate; keep them
    if latin is None and not bangla:
        return None, ""
    return (latin or ""), bangla


def collect(offline: bool) -> tuple[list[dict], list[str]]:
    """Return (entries, warnings). Entries are dicts, not yet encoded."""
    warnings: list[str] = []
    seen: dict[str, dict] = {}

    def add(osm_id: str, lat: float, lon: float, kind: str, tags: dict) -> None:
        latin, bangla = _name_pair(tags)
        if latin is None:
            return
        if not (20.0 <= lat <= 27.0 and 87.5 <= lon <= 93.0):
            return                                   # outside Bangladesh: skip
        prev = seen.get(osm_id)
        if prev and KINDS.index(prev["kind"]) <= KINDS.index(kind):
            return
        seen[osm_id] = {
            "osm": osm_id, "lat": lat, "lon": lon, "kind": kind,
            "name": latin, "name_bn": bangla,
        }

    if not offline:
        try:
            for e in overpass(PLACE_QUERY, "place nodes"):
                tags = e.get("tags") or {}
                kind = tags.get("place", "place")
                if kind not in KINDS:
                    kind = "place"
                add("n%s" % e["id"], e["lat"], e["lon"], kind, tags)
        except Exception as exc:                      # noqa: BLE001
            warnings.append("Overpass place-node query failed: %s" % exc)
            print("      ! %s" % warnings[-1])

        time.sleep(8)                                 # be a good Overpass citizen
        try:
            for e in overpass(ADMIN_QUERY, "admin relations"):
                tags = e.get("tags") or {}
                centre = e.get("center") or {}
                if "lat" not in centre:
                    continue
                kind = ADMIN_LEVEL_KIND.get(str(tags.get("admin_level")), "place")
                add("r%s" % e["id"], centre["lat"], centre["lon"], kind, tags)
        except Exception as exc:                      # noqa: BLE001
            warnings.append("Overpass admin-relation query failed: %s" % exc)
            print("      ! %s" % warnings[-1])

    # The 897 places already in the repo. Same OSM nodes, so dedupe by id.
    nb = GEO / "dhaka-neighbourhoods.geojson"
    if nb.exists():
        fc = json.loads(nb.read_text(encoding="utf-8"))
        n_added = 0
        for f in fc.get("features", []):
            p = f.get("properties") or {}
            lon, lat = f["geometry"]["coordinates"][:2]
            osm_id = str(p.get("id", "")).replace("osm-node-", "n")
            if not osm_id.startswith("n"):
                osm_id = "n?%s" % p.get("id")
            kind = p.get("place") or "place"
            if kind not in KINDS:
                kind = "place"
            before = len(seen)
            add(osm_id, lat, lon, kind,
                {"name": p.get("name"), "name:bn": p.get("name_bn") or ""})
            n_added += len(seen) - before
        print("      + dhaka-neighbourhoods.geojson: %d new, %d already present"
              % (n_added, len(fc.get("features", [])) - n_added))

    # DESCO S&D office points are genuine, useful search targets.
    off = GEO / "desco-offices.geojson"
    if off.exists():
        fc = json.loads(off.read_text(encoding="utf-8"))
        for f in fc.get("features", []):
            p = f.get("properties") or {}
            lon, lat = f["geometry"]["coordinates"][:2]
            add("d%s" % p.get("id"), lat, lon, "place",
                {"name": p.get("name"), "name:bn": ""})

    return list(seen.values()), warnings


# ---------------------------------------------------------------- encoding


def _poly_chunk(value: int, out: list[str]) -> None:
    v = ~(value << 1) if value < 0 else (value << 1)
    while v >= 0x20:
        out.append(chr((0x20 | (v & 0x1F)) + 63))
        v >>= 5
    out.append(chr(v + 63))


def polyline(points: list[tuple[float, float]], factor: int = 100_000) -> str:
    """Google's encoded-polyline algorithm. Same one Maps has used since 2005."""
    out: list[str] = []
    py = px = 0
    for lat, lon in points:
        y = int(round(lat * factor))
        x = int(round(lon * factor))
        _poly_chunk(y - py, out)
        _poly_chunk(x - px, out)
        py, px = y, x
    return "".join(out)


def encode(entries: list[dict], districts: list[str], warnings: list[str]) -> dict:
    if len(districts) > len(ALPHABET) or len(KINDS) > len(ALPHABET):
        raise RuntimeError("index alphabet too small")

    names, banglas, kinds, dchars, osm = [], [], [], [], []
    for e in entries:
        names.append(e["name"].replace("|", "/"))
        banglas.append(e["name_bn"].replace("|", "/"))
        kinds.append(ALPHABET[KINDS.index(e["kind"])])
        d = e.get("district")
        dchars.append(ALPHABET[districts.index(d)] if d in districts else UNKNOWN_CHAR)
        osm.append(e["osm"])

    out: dict[str, Any] = {
        "schema": "gazetteer/1",
        "generated_at": iso_utc(),
        "source": "OpenStreetMap place nodes and admin relations via the Overpass API",
        "source_license": "ODbL (OpenStreetMap contributors)",
        "district_source_license": "CC-BY 4.0 (geoBoundaries gbOpen ADM2)",
        "note": (
            "Columnar transport format. Columns are parallel across `count` entries. "
            "n/b are '|'-joined strings; k/d are one character per entry indexing "
            "`kinds`/`districts` through the `alphabet`; p is the lat/lon sequence in "
            "Google encoded-polyline form at 1e5. Per-place OSM ids are in the sibling "
            "gazetteer-osm.json, same order. Decoded by apps/web/src/gazetteer.js."
        ),
        "alphabet": ALPHABET,
        "unknown": UNKNOWN_CHAR,
        "precision": 5,
        "kinds": KINDS,
        "districts": districts,
        "count": len(entries),
        "k": "".join(kinds),
        "d": "".join(dchars),
        "p": polyline([(e["lat"], e["lon"]) for e in entries]),
        "n": "|".join(names),
        "b": "|".join(banglas),
    }
    if warnings:
        out["warnings"] = warnings
    return out, {
        "schema": "gazetteer-osm/1",
        "generated_at": out["generated_at"],
        "note": "Per-place OSM object ids, same order as gazetteer.json. "
                "Prefix n = node, r = relation, d = a DESCO S&D office point.",
        "source_license": "ODbL (OpenStreetMap contributors)",
        "count": len(entries),
        "o": "|".join(osm),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--offline", action="store_true",
                    help="skip Overpass; build only from files already in data/geo")
    args = ap.parse_args(argv)

    print("Building %s" % OUT.relative_to(DATA.parent))
    entries, warnings = collect(args.offline)
    if not entries:
        if OUT.exists():
            print("  ! nothing collected; keeping the previous gazetteer (AGENTS.md rule 4)")
            return 1
        print("  ! nothing collected and no previous file to keep")
        return 1

    idx = DistrictIndex(GEO / "bangladesh-admin.geojson")
    unknown = 0
    for e in entries:
        d = idx.lookup(e["lon"], e["lat"])
        e["district"] = d
        if d is None:
            unknown += 1
    districts = sorted({e["district"] for e in entries if e["district"]})
    print("      + %d districts used, %d places outside every district polygon"
          % (len(districts), unknown))

    # Sort into 0.05-degree latitude bands, west to east inside each band. The
    # delta columns then stay near zero, which both shrinks the raw JSON and
    # gives gzip long runs to work with.
    entries.sort(key=lambda e: (round(e["lat"] * 20), e["lon"]))

    payload, provenance = encode(entries, districts, warnings)
    # Written minified on purpose: this is a transport format on the critical
    # path, and `indent=2` would put every column element on its own line.
    # Key order is fixed, so git diffs are still meaningful.
    _write_min(OUT, payload)
    _write_min(OUT_OSM, provenance)

    size = OUT.stat().st_size
    print("  wrote %d places, %.1f KB (+ %.1f KB provenance, not on the critical path)"
          % (len(entries), size / 1024, OUT_OSM.stat().st_size / 1024))
    if size > 1_000_000:
        print("  ! over the 1 MB budget - tighten the place selection")
        return 1
    return 0


def _write_min(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
        fh.write("\n")
    os.replace(tmp, path)


if __name__ == "__main__":
    raise SystemExit(main())
