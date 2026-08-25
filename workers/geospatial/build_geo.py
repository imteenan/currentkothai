"""Build every GeoJSON layer the browser needs, from free open data only.

    python -m workers.geospatial.build_geo [--skip-districts] [--layer NAME]

Sources, all free and openly licensed:
  * geoBoundaries gbOpen (CC-BY 4.0)   - Bangladesh district polygons
  * OpenStreetMap via Nominatim (ODbL) - city-corporation polygons, place points

HONESTY RULES BAKED IN HERE
  1. No distributor publishes GIS boundaries for its service territory. Every
     utility polygon we emit is therefore `status: "estimated"` or `"derived"`,
     never `"official"`.
  2. Where a polygon is an administrative boundary standing in for a service
     territory, the feature carries a `caveat` string that the UI must show.
  3. A layer that cannot be built is written as an empty FeatureCollection with
     a `note` explaining why -- never faked.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import requests

from statistics import median

from workers.ingestion.common import DATA, USER_AGENT, iso_utc, write_json

GEO = DATA / "geo"
NOMINATIM = "https://nominatim.openstreetmap.org"
GEOBOUNDARIES = "https://www.geoboundaries.org/api/current/gbOpen/BGD/ADM2/"

#: Nominatim asks for max 1 request/second from a single client.
POLITE_DELAY = 1.1
_last_call = [0.0]


def _polite() -> None:
    wait = POLITE_DELAY - (time.time() - _last_call[0])
    if wait > 0:
        time.sleep(wait)
    _last_call[0] = time.time()


def nominatim(params: dict[str, Any]) -> list[dict]:
    _polite()
    r = requests.get("%s/search" % NOMINATIM, params={"format": "jsonv2", **params},
                     headers={"User-Agent": USER_AGENT}, timeout=60)
    r.raise_for_status()
    return r.json()


def round_coords(obj: Any, nd: int = 5) -> Any:
    """Shrink the payload: 5 dp is ~1.1 m, far finer than any of this data."""
    if isinstance(obj, float):
        return round(obj, nd)
    if isinstance(obj, list):
        return [round_coords(x, nd) for x in obj]
    return obj


def simplify(geom: dict, tol: float = 0.0006) -> dict:
    try:
        from shapely.geometry import mapping, shape
        g = shape(geom)
        if not g.is_valid:
            g = g.buffer(0)
        s = g.simplify(tol, preserve_topology=True)
        if s.is_empty:
            return geom
        return mapping(s)
    except Exception:
        return geom


def feature(geom: dict, props: dict, tol: float = 0.0006, nd: int = 5) -> dict:
    g = simplify(geom, tol)
    g["coordinates"] = round_coords(g.get("coordinates", []), nd)
    return {"type": "Feature", "geometry": g, "properties": props}


def fc(features: list[dict], note: str | None = None) -> dict:
    out: dict[str, Any] = {"type": "FeatureCollection", "features": features}
    if note:
        out["note"] = note
    out["generated_at"] = iso_utc()
    return out


def save(name: str, collection: dict) -> None:
    path = GEO / name
    write_json(path, collection)
    kb = path.stat().st_size / 1024
    print("    %-34s %6.1f KB  %d feature(s)"
          % (name, kb, len(collection.get("features", []))))


def fetch_polygon(query: str, **extra) -> dict | None:
    """One OSM polygon by name. Returns None rather than a wrong shape."""
    try:
        rows = nominatim({"q": query, "polygon_geojson": 1, "limit": 1,
                          "countrycodes": "bd", **extra})
    except Exception as exc:
        print("      ! %s: %s" % (query, exc))
        return None
    if not rows or rows[0].get("geojson", {}).get("type") not in ("Polygon", "MultiPolygon"):
        print("      ! %s: no polygon returned" % query)
        return None
    return rows[0]


# --------------------------------------------------------------- layer 1


def build_districts() -> None:
    print("  districts (geoBoundaries ADM2, CC-BY 4.0)")
    try:
        meta = requests.get(GEOBOUNDARIES, headers={"User-Agent": USER_AGENT},
                            timeout=90).json()
        url = meta.get("gjDownloadURL")
        raw = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=180).json()
    except Exception as exc:
        print("      ! failed: %s" % exc)
        save("bangladesh-admin.geojson",
             fc([], "geoBoundaries download failed: %s" % exc))
        return

    feats = []
    for f in raw.get("features", []):
        p = f.get("properties", {})
        name = p.get("shapeName") or p.get("shapeISO") or "Unknown district"
        feats.append(feature(f["geometry"], {
            "id": "bgd-adm2-%s" % (p.get("shapeID") or name).lower().replace(" ", "-"),
            "name": name,
            "level": "admin",
            "admin_level": "district",
            "status": "official",
            "confidence": "high",
            "source_url": GEOBOUNDARIES,
            "source_license": "CC-BY 4.0 (geoBoundaries gbOpen)",
            "retrieved_at": iso_utc(),
        }, tol=0.012, nd=4))
    save("bangladesh-admin.geojson", fc(feats))


# --------------------------------------------------------------- layer 2

#: Each distributor, and the OSM areas we compose its territory from.
TERRITORY_PLAN = [
    {
        "id": "desco", "name": "DESCO", "name_bn": "ডেসকো", "color": "#0a84ff",
        "queries": ["Dhaka North City Corporation, Bangladesh",
                    "Tongi, Gazipur, Bangladesh"],
        "confidence": "medium",
        "notes": "Composed from the Dhaka North City Corporation boundary plus Tongi. "
                 "DESCO's real territory follows its S&D division boundaries, which are "
                 "not published as map data.",
        "caveat": "Near the DNCC/DSCC edge the distributor can differ from what this "
                  "polygon suggests - check your bill.",
    },
    {
        "id": "dpdc", "name": "DPDC", "name_bn": "ডিপিডিসি", "color": "#5e5ce6",
        "queries": ["Dhaka South City Corporation, Bangladesh",
                    "Narayanganj City Corporation, Bangladesh"],
        "confidence": "medium",
        "notes": "Composed from the Dhaka South City Corporation boundary plus "
                 "Narayanganj City Corporation.",
        "caveat": "DPDC also serves some areas inside Dhaka North (parts of Mohammadpur "
                  "and Adabor among them), which this polygon does not capture.",
    },
    {
        "id": "nesco", "name": "NESCO", "name_bn": "নেসকো", "color": "#30d158",
        "queries": ["Rajshahi Division, Bangladesh", "Rangpur Division, Bangladesh"],
        "confidence": "low",
        "notes": "Administrative divisions standing in for a service territory. NESCO "
                 "serves only the URBAN/municipal parts of this area.",
        "caveat": "Rural addresses inside this shape are almost certainly served by a "
                  "local Palli Bidyut Samity (BREB), not NESCO.",
    },
    {
        "id": "wzpdcl", "name": "WZPDCL", "name_bn": "ওজোপাডিকো", "color": "#40cbe0",
        "queries": ["Khulna Division, Bangladesh", "Barisal Division, Bangladesh"],
        "confidence": "low",
        "notes": "Administrative divisions standing in for a service territory. WZPDCL "
                 "serves only the URBAN parts of this area.",
        "caveat": "Rural addresses inside this shape are almost certainly served by a "
                  "local Palli Bidyut Samity (BREB), not WZPDCL.",
    },
    {
        "id": "bpdb", "name": "BPDB", "name_bn": "বিপিডিবি", "color": "#ff9f0a",
        "queries": ["Chittagong Division, Bangladesh", "Sylhet Division, Bangladesh",
                    "Mymensingh Division, Bangladesh"],
        "confidence": "low",
        "notes": "Administrative divisions standing in for a service territory. BPDB "
                 "distributes in the URBAN parts of this area.",
        "caveat": "Rural addresses inside this shape are almost certainly served by a "
                  "local Palli Bidyut Samity (BREB), not BPDB.",
    },
]


def build_territories() -> None:
    """Delegates to territories.py -- the construction is involved enough to
    deserve its own module and its own explanation."""
    from workers.geospatial import territories
    save("utility-territories.geojson", territories.build())


def _build_territories_nominatim_DEPRECATED() -> None:
    print("  utility territories (OSM via Nominatim, ODbL)")
    try:
        from shapely.geometry import mapping, shape
        from shapely.ops import unary_union
    except ImportError:
        save("utility-territories.geojson", fc([], "shapely not installed"))
        return

    feats = []
    for plan in TERRITORY_PLAN:
        parts, used = [], []
        for q in plan["queries"]:
            row = fetch_polygon(q)
            if not row:
                continue
            g = shape(row["geojson"])
            if not g.is_valid:
                g = g.buffer(0)
            parts.append(g)
            used.append({"query": q, "osm": "%s/%s" % (row.get("osm_type"), row.get("osm_id")),
                         "display_name": row.get("display_name", "")[:120]})
        if not parts:
            print("      ! %s: no geometry, skipped" % plan["name"])
            continue
        merged = unary_union(parts)
        feats.append(feature(mapping(merged), {
            "id": plan["id"],
            "name": plan["name"],
            "name_bn": plan["name_bn"],
            "level": "utility",
            "utility": plan["name"],
            "status": "estimated",
            "confidence": plan["confidence"],
            "color_hex": plan["color"],
            "source_url": "https://www.openstreetmap.org/",
            "source_license": "ODbL (OpenStreetMap contributors)",
            "retrieved_at": iso_utc(),
            "composed_from": used,
            "notes": plan["notes"],
            "caveat": plan["caveat"],
        }))
        print("      + %-7s from %d part(s)" % (plan["name"], len(parts)))

    save("utility-territories.geojson", fc(feats,
        "NO distributor publishes its service-territory GIS data. Every polygon here "
        "is our own estimate composed from OpenStreetMap administrative boundaries. "
        "Treat boundary areas as genuinely uncertain."))


# --------------------------------------------------------------- layer 3

#: Nominatim ranks a bare locality name against the whole planet, so the
#: multi-query entries below are bounded to the Dhaka/Gazipur region.
_REGION_VIEWBOX = {"viewbox": "90.34,23.96,90.50,23.84", "bounded": 1}

#: DESCO S&D divisions, exactly as they appear in DESCO's own schedule PDF.
#: The value is the query we geocode to place the office/area point.
#:
#: THREE divisions are not places OSM knows. "Tongi East, Gazipur", "Tongi West,
#: Gazipur" and "Shah Kabir Mazar Road, Uttara, Dhaka" each returned zero hits,
#: the loop below skipped them, and the file shipped with 23 of 26 points -- so
#: those three divisions had no polygon and could never light up on the map. For
#: them the value is a LIST of localities that DESCO's own schedule names under
#: that division, and the point is the median of whichever ones geocode. That
#: keeps the anchor sourced: every locality is a real OSM feature that DESCO
#: itself printed against the division, rather than a guess at where "east"
#: Tongi is. The medians land west-to-east in the order the names claim --
#: Tongi West 90.377, Tongi Central 90.394, Tongi East 90.410.
DESCO_DIVISIONS = {
    "Agargaon": "Agargaon, Dhaka", "Badda": "Badda, Dhaka",
    "Baridhara": "Baridhara, Dhaka", "Bashundhara": "Bashundhara R/A, Dhaka",
    "Dhakkhinkhan": "Dakshinkhan, Dhaka", "Eastern Housing": "Eastern Housing, Pallabi, Dhaka",
    "Gulshan": "Gulshan, Dhaka", "Ibrahimpur": "Ibrahimpur, Dhaka",
    "Joarshahara": "Joar Sahara, Dhaka", "Kafrul": "Kafrul, Dhaka",
    "Kallyanpur": "Kalyanpur, Dhaka", "Khilkhet": "Khilkhet, Dhaka",
    "Mirpur": "Mirpur, Dhaka", "Mohakhali": "Mohakhali, Dhaka",
    "Monipur": "Monipur, Mirpur, Dhaka", "Pallabi": "Pallabi, Dhaka",
    "Rupnagar": "Rupnagar, Mirpur, Dhaka", "Shah Ali": "Shah Ali, Mirpur, Dhaka",
    "Shah Kabir": ["Shah Kabir Mazar", "Chalabon, Dhaka", "Faidabad, Dhaka",
                   "Atipara, Dhaka"],
    "Tongi Central": "Tongi, Gazipur",
    "Tongi East": ["Morkun, Tongi", "BSCIC Industrial Area, Tongi",
                   "Bonomala, Tongi"],
    "Tongi West": ["Auchpara, Tongi", "Sataish, Tongi", "Dewra, Tongi"],
    "Turag": "Turag, Dhaka",
    "Uttara East": "Uttara Sector 4, Dhaka", "Uttara West": "Uttara Sector 12, Dhaka",
    "Uttarkhan": "Uttarkhan, Dhaka",
}


def build_desco_points() -> None:
    print("  DESCO division reference points (OSM via Nominatim, ODbL)")
    feats = []
    for name, query in DESCO_DIVISIONS.items():
        queries = [query] if isinstance(query, str) else list(query)
        extra = {} if isinstance(query, str) else _REGION_VIEWBOX
        hits = []
        for one in queries:
            try:
                rows = nominatim({"q": one, "limit": 1, **extra})
            except Exception as exc:
                print("      ! %s (%s): %s" % (name, one, exc))
                continue
            if rows:
                hits.append(rows[0])
        if not hits:
            print("      ! %s: not found" % name)
            continue
        row = hits[0]
        lat = median(float(h["lat"]) for h in hits)
        lon = median(float(h["lon"]) for h in hits)
        if len(queries) > 1:
            print("      . %s: median of %d/%d localities"
                  % (name, len(hits), len(queries)))
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point",
                         "coordinates": [round(lon, 5), round(lat, 5)]},
            "properties": {
                "id": "desco-div-%s" % name.lower().replace(" ", "-"),
                "name": name,
                "division": name,
                "level": "division",
                "utility": "DESCO",
                "status": "estimated",
                "confidence": "low",
                "source_url": "https://nominatim.openstreetmap.org/",
                "source_license": "ODbL (OpenStreetMap contributors)",
                "retrieved_at": iso_utc(),
                "geocoded_query": " | ".join(queries),
                "osm_display_name": row.get("display_name", "")[:140],
                "notes": "Centre of the NAMED NEIGHBOURHOOD, geocoded from OpenStreetMap. "
                         "This is NOT the location of DESCO's S&D office and NOT a service "
                         "boundary. It exists only to rank which division a point is nearest to."
                         + ("" if len(queries) == 1 else
                            " OSM does not carry this division as a place, so the point is the "
                            "median of %d localities DESCO's own schedule lists under it."
                            % len(hits)),
            },
        })
    print("      + %d/%d divisions placed" % (len(feats), len(DESCO_DIVISIONS)))
    save("desco-offices.geojson", fc(feats,
        "Reference points for DESCO's S&D divisions, geocoded from neighbourhood names "
        "that match the division names printed in DESCO's schedule PDF. Approximate by "
        "construction."))


# --------------------------------------------------------------- layer 4


def build_neighbourhoods() -> None:
    """Named places around Dhaka, for matching feeder area descriptions."""
    print("  Dhaka neighbourhoods (Overpass, ODbL)")
    query = """
    [out:json][timeout:180];
    (
      node["place"~"^(suburb|neighbourhood|quarter)$"](23.60,90.28,24.05,90.60);
      node["place"="village"](23.60,90.28,24.05,90.60);
    );
    out body;
    """
    try:
        r = requests.post("https://overpass-api.de/api/interpreter",
                          data={"data": query},
                          headers={"User-Agent": USER_AGENT}, timeout=200)
        r.raise_for_status()
        elements = r.json().get("elements", [])
    except Exception as exc:
        print("      ! Overpass failed: %s" % exc)
        save("dhaka-neighbourhoods.geojson",
             fc([], "Overpass query failed: %s" % exc))
        return

    feats = []
    for e in elements:
        tags = e.get("tags", {})
        name = tags.get("name:en") or tags.get("name")
        if not name:
            continue
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point",
                         "coordinates": [round(e["lon"], 5), round(e["lat"], 5)]},
            "properties": {
                "id": "osm-node-%s" % e["id"],
                "name": name,
                "name_bn": tags.get("name:bn"),
                "level": "place",
                "place": tags.get("place"),
                "status": "official",
                "confidence": "high",
                "source_url": "https://www.openstreetmap.org/node/%s" % e["id"],
                "source_license": "ODbL (OpenStreetMap contributors)",
                "retrieved_at": iso_utc(),
            },
        })
    print("      + %d named places" % len(feats))
    save("dhaka-neighbourhoods.geojson", fc(feats))


def build_zone_cells() -> None:
    """Per-zone polygons for both distributors.

    Runs after `territories` and `desco-points`, because it clips its cells to
    the territory polygons and seeds the DESCO ones from the office points.
    """
    from workers.geospatial import zone_cells
    zone_cells.build()


LAYERS = {
    "districts": build_districts,
    "territories": build_territories,
    "desco-points": build_desco_points,
    "neighbourhoods": build_neighbourhoods,
    "zone-cells": build_zone_cells,
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--layer", choices=sorted(LAYERS), action="append",
                    help="build only these layers (repeatable)")
    args = ap.parse_args(argv)

    GEO.mkdir(parents=True, exist_ok=True)
    chosen = args.layer or list(LAYERS)
    failed = 0
    for name in chosen:
        try:
            LAYERS[name]()
        except Exception as exc:
            failed += 1
            print("  ! layer %s crashed: %s" % (name, exc))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
