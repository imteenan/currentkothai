"""Build data/geo/utility-territories.geojson.

WHY THIS IS HARDER THAN IT LOOKS
--------------------------------
No Bangladeshi distributor publishes its service territory as map data, and
OpenStreetMap does not carry Dhaka North / Dhaka South City Corporation
boundaries either -- inside Dhaka the finest administrative relation available
is a single "Dhaka Metropolitan" polygon. Nominatim's free-text search is worse
than useless here: querying "Narayanganj City Corporation" returns a business
called "Setu Corporation".

So the DESCO/DPDC boundary is CONSTRUCTED, and the construction is stated in the
output so nobody mistakes it for an official line:

  1. Take the Dhaka Metropolitan admin polygon (OSM relation 13663697), plus
     Narayanganj Sadar and Gazipur Sadar upazilas for the DPDC/DESCO fringes.
  2. Geocode the zone names each utility itself prints on its own schedules --
     DESCO's 26 S&D divisions, DPDC's 36 NOCS zones. These are real
     neighbourhood names, so they place well.
  3. Build a Voronoi partition of that combined point set and assign each cell
     to whichever utility contributed its seed point.
  4. Dissolve per utility and clip to the admin area.

The result is a nearest-zone-centre approximation. It is right in the middle of
each utility's area and genuinely uncertain near the join -- which is exactly
what the map's dashed styling and the `caveat` property tell the user.

Outside Dhaka the territories are geoBoundaries ADM1 divisions, which are
administrative areas standing in for service territories and carry a louder
caveat still, because rural addresses inside them belong to BREB.
"""
from __future__ import annotations

import time
from typing import Any

import requests
from shapely.geometry import MultiPoint, Point, mapping, shape
from shapely.ops import unary_union, voronoi_diagram

from workers.ingestion.common import DATA, USER_AGENT, iso_utc, read_json

#: Overpass mirrors, tried in order. The main instance 504s under load and a
#: timeout must not be allowed to wipe a previously-good layer.
OVERPASS_MIRRORS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
)
OVERPASS = OVERPASS_MIRRORS[0]
NOMINATIM = "https://nominatim.openstreetmap.org/search"
GEOBOUNDARIES_ADM1 = "https://www.geoboundaries.org/api/current/gbOpen/BGD/ADM1/"

_last = [0.0]


def _polite(delay: float = 1.1) -> None:
    w = delay - (time.time() - _last[0])
    if w > 0:
        time.sleep(w)
    _last[0] = time.time()


# --------------------------------------------------------------------- OSM


def osm_relation_geometry(rel_id: int) -> Any:
    """Assemble a polygon from an OSM relation via Overpass."""
    q = "[out:json][timeout:180];rel(%d);out geom;" % rel_id
    last = None
    els = None
    for mirror in OVERPASS_MIRRORS:
        try:
            r = requests.post(mirror, data={"data": q},
                              headers={"User-Agent": USER_AGENT}, timeout=240)
            r.raise_for_status()
            els = r.json().get("elements", [])
            break
        except Exception as exc:
            last = exc
            print("      . overpass mirror failed (%s), trying next"
                  % type(exc).__name__)
    if els is None:
        raise ValueError("all Overpass mirrors failed for relation %d: %s"
                         % (rel_id, last))
    if not els:
        raise ValueError("relation %d returned nothing" % rel_id)

    outers, inners = [], []
    for member in els[0].get("members", []):
        geom = member.get("geometry")
        if not geom or member.get("type") != "way":
            continue
        coords = [(p["lon"], p["lat"]) for p in geom]
        if len(coords) < 2:
            continue
        (outers if member.get("role") != "inner" else inners).append(coords)

    from shapely.ops import linemerge, polygonize
    if not outers:
        raise ValueError("relation %d has no outer ways" % rel_id)
    polys = list(polygonize(linemerge([c for c in outers if len(c) >= 2])))
    if not polys:
        raise ValueError("relation %d outer ways do not close" % rel_id)
    area = unary_union(polys)
    if inners:
        holes = list(polygonize(linemerge([c for c in inners if len(c) >= 2])))
        if holes:
            area = area.difference(unary_union(holes))
    return area.buffer(0)


def geocode_points(names: dict[str, str], label: str) -> dict[str, Point]:
    """Geocode zone names to points, rejecting anything outside greater Dhaka."""
    out: dict[str, Point] = {}
    # Generous box around Dhaka + Narayanganj + Gazipur.
    lo_lon, lo_lat, hi_lon, hi_lat = 90.15, 23.55, 90.75, 24.15
    for name, query in names.items():
        _polite()
        try:
            rows = requests.get(NOMINATIM, params={
                "q": query, "format": "jsonv2", "limit": 1, "countrycodes": "bd",
            }, headers={"User-Agent": USER_AGENT}, timeout=45).json()
        except Exception as exc:
            print("      ! %s %s: %s" % (label, name, exc))
            continue
        if not rows:
            print("      ! %s %s: not found" % (label, name))
            continue
        lon, lat = float(rows[0]["lon"]), float(rows[0]["lat"])
        if not (lo_lon <= lon <= hi_lon and lo_lat <= lat <= hi_lat):
            print("      ! %s %s: geocoded outside greater Dhaka, dropped" % (label, name))
            continue
        out[name] = Point(lon, lat)
    print("      + %s: %d/%d points" % (label, len(out), len(names)))
    return out


# ------------------------------------------------------------- the partition


def voronoi_split(seeds: dict[str, list[Point]], clip) -> dict[str, Any]:
    """Voronoi partition of all seeds, dissolved back to one shape per owner."""
    owner_of: dict[tuple, str] = {}
    pts = []
    for owner, points in seeds.items():
        for p in points:
            owner_of[(round(p.x, 6), round(p.y, 6))] = owner
            pts.append(p)
    if len(pts) < 3:
        raise ValueError("need at least 3 seed points for a Voronoi partition")

    envelope = clip.buffer(0.25)
    cells = voronoi_diagram(MultiPoint(pts), envelope=envelope)

    buckets: dict[str, list] = {k: [] for k in seeds}
    for cell in cells.geoms:
        # A Voronoi cell contains exactly the seed that generated it.
        for key, owner in owner_of.items():
            if cell.contains(Point(key)):
                buckets[owner].append(cell)
                break

    result = {}
    for owner, cs in buckets.items():
        if not cs:
            continue
        merged = unary_union(cs).intersection(clip)
        if not merged.is_empty:
            result[owner] = merged.buffer(0)
    return result


# ------------------------------------------------------------------- inputs

DESCO_ZONES = {
    "Agargaon": "Agargaon, Dhaka", "Badda": "Badda, Dhaka",
    "Baridhara": "Baridhara, Dhaka", "Bashundhara": "Bashundhara R/A, Dhaka",
    "Dakshinkhan": "Dakshinkhan, Dhaka", "Gulshan": "Gulshan, Dhaka",
    "Ibrahimpur": "Ibrahimpur, Dhaka", "Joarshahara": "Joar Sahara, Dhaka",
    "Kafrul": "Kafrul, Dhaka", "Kallyanpur": "Kalyanpur, Dhaka",
    "Khilkhet": "Khilkhet, Dhaka", "Mirpur": "Mirpur, Dhaka",
    "Mohakhali": "Mohakhali, Dhaka", "Monipur": "Monipur, Mirpur, Dhaka",
    "Pallabi": "Pallabi, Dhaka", "Rupnagar": "Rupnagar, Mirpur, Dhaka",
    "Shah Ali": "Shah Ali, Mirpur, Dhaka", "Tongi": "Tongi, Gazipur",
    "Turag": "Turag, Dhaka", "Uttara East": "Uttara Sector 4, Dhaka",
    "Uttara West": "Uttara Sector 12, Dhaka", "Uttarkhan": "Uttarkhan, Dhaka",
    "Cantonment": "Dhaka Cantonment, Dhaka", "Banani": "Banani, Dhaka",
}

DPDC_ZONES = {
    "Adabor": "Adabor, Dhaka", "Azimpur": "Azimpur, Dhaka",
    "Banasree": "Banasree, Dhaka", "Banglabazar": "Bangla Bazar, Dhaka",
    "Bangshal": "Bangshal, Dhaka", "Bashaboo": "Bashabo, Dhaka",
    "Demra": "Demra, Dhaka", "Dhanmondi": "Dhanmondi, Dhaka",
    "Fatulla": "Fatulla, Narayanganj", "Jigatola": "Jigatola, Dhaka",
    "Jurain": "Jurain, Dhaka", "Kakrail": "Kakrail, Dhaka",
    "Kamrangirchar": "Kamrangirchar, Dhaka", "Khilgaon": "Khilgaon, Dhaka",
    "Lalbag": "Lalbagh, Dhaka", "Maniknagar": "Maniknagar, Dhaka",
    "Matuail": "Matuail, Dhaka", "Mogbazar": "Moghbazar, Dhaka",
    "Motijheel": "Motijheel, Dhaka", "Mugdapara": "Mugdapara, Dhaka",
    "Narayangonj East": "Narayanganj Sadar, Narayanganj",
    "Narinda": "Narinda, Dhaka", "Paribag": "Paribagh, Dhaka",
    "Postogola": "Postogola, Dhaka", "Rajarbag": "Rajarbagh, Dhaka",
    "Ramna": "Ramna, Dhaka", "Satmosjid": "Satmasjid Road, Dhaka",
    "Shamoli": "Shyamoli, Dhaka", "Sher-e-Bangla Nagar": "Sher-e-Bangla Nagar, Dhaka",
    "Shyampur": "Shyampur, Dhaka", "Siddhirgonj": "Siddhirganj, Narayanganj",
    "Swamibag": "Swamibag, Dhaka", "Tejgaon": "Tejgaon, Dhaka",
    "Mohammadpur": "Mohammadpur, Dhaka", "Wari": "Wari, Dhaka",
}

#: OSM relation ids verified by listing admin relations around Dhaka.
DHAKA_METRO_REL = 13663697
NARAYANGANJ_SADAR_REL = 14326722
GAZIPUR_SADAR_REL = 19687163

#: geoBoundaries ADM1 division name -> distributor, for everywhere else.
OUTSIDE_DHAKA = {
    "NESCO":  (["Rajshahi", "Rajshani", "Rangpur"], "#30d158",
               "NESCO serves only the urban and municipal parts of these divisions."),
    "WZPDCL": (["Khulna", "Barisal", "Barishal"], "#40cbe0",
               "WZPDCL serves only the urban parts of these divisions."),
    "BPDB":   (["Chittagong", "Chattogram", "Sylhet", "Mymensingh"], "#ff9f0a",
               "BPDB distributes in the urban parts of these divisions."),
}

RURAL_CAVEAT = ("Rural addresses inside this shape are almost certainly served by a local "
                "Palli Bidyut Samity (BREB), not by this distributor. This polygon is an "
                "administrative division standing in for a service territory.")


def _carry_forward(features: list[dict], wanted: set[str]) -> None:
    """Re-add utilities we failed to rebuild, from the previous published file.

    AGENTS.md rule 4: a source disappearing must not silently erase data that was
    already validated. A transient Overpass 504 is exactly that case.
    """
    have = {f["properties"]["utility"] for f in features}
    missing = wanted - have
    if not missing:
        return
    prev = read_json(DATA / "geo" / "utility-territories.geojson", {}) or {}
    for f in prev.get("features", []):
        util = f.get("properties", {}).get("utility")
        if util in missing:
            f["properties"]["carried_forward"] = True
            f["properties"]["carried_forward_note"] = (
                "Rebuild failed on %s; this geometry is the previously published "
                "version, kept rather than dropped." % iso_utc())
            features.append(f)
            print("      ~ %s carried forward from the previous build" % util)


def build() -> dict:
    print("  utility territories")
    features: list[dict] = []

    # ---- Dhaka: the constructed DESCO/DPDC split ---------------------------
    try:
        metro = osm_relation_geometry(DHAKA_METRO_REL)
        print("      + Dhaka Metropolitan relation loaded")
        extras = []
        for rid, nm in ((NARAYANGANJ_SADAR_REL, "Narayanganj Sadar"),
                        (GAZIPUR_SADAR_REL, "Gazipur Sadar")):
            try:
                extras.append(osm_relation_geometry(rid))
                print("      + %s relation loaded" % nm)
            except Exception as exc:
                print("      ! %s: %s" % (nm, exc))
        clip = unary_union([metro, *extras]).buffer(0)

        desco_pts = geocode_points(DESCO_ZONES, "DESCO")
        dpdc_pts = geocode_points(DPDC_ZONES, "DPDC")
        split = voronoi_split(
            {"DESCO": list(desco_pts.values()), "DPDC": list(dpdc_pts.values())}, clip)

        meta = {
            "DESCO": ("desco", "ডেসকো", "#0a84ff", sorted(desco_pts),
                      "Constructed: Voronoi partition of DESCO's own 24 S&D division "
                      "names against DPDC's 35 NOCS zone names, clipped to the Dhaka "
                      "Metropolitan / Gazipur Sadar administrative area."),
            "DPDC": ("dpdc", "ডিপিডিসি", "#5e5ce6", sorted(dpdc_pts),
                     "Constructed: Voronoi partition of DPDC's own 35 NOCS zone names "
                     "against DESCO's 24 S&D division names, clipped to the Dhaka "
                     "Metropolitan / Narayanganj Sadar administrative area."),
        }
        for util, geom in split.items():
            slug, bn, color, seeds, how = meta[util]
            features.append({
                "type": "Feature",
                "geometry": mapping(geom.simplify(0.0004, preserve_topology=True)),
                "properties": {
                    "id": slug, "name": util, "name_bn": bn,
                    "level": "utility", "utility": util,
                    "status": "estimated", "confidence": "medium",
                    "color_hex": color,
                    "source_url": "https://www.openstreetmap.org/relation/%d" % DHAKA_METRO_REL,
                    "source_license": "ODbL (OpenStreetMap contributors)",
                    "retrieved_at": iso_utc(),
                    "method": how,
                    "seed_zones": seeds,
                    "notes": "Not a published boundary. No distributor releases service-"
                             "territory GIS data, and OpenStreetMap has no Dhaka North / "
                             "Dhaka South City Corporation boundaries to fall back on.",
                    "caveat": "Near the DESCO/DPDC join this line is a nearest-zone guess. "
                              "Your bill names your actual distributor - trust that instead.",
                },
            })
            print("      + %s territory built" % util)
    except Exception as exc:
        print("      ! Dhaka split failed: %s" % exc)

    # ---- everywhere else: ADM1 divisions -----------------------------------
    try:
        meta = requests.get(GEOBOUNDARIES_ADM1, headers={"User-Agent": USER_AGENT},
                            timeout=90).json()
        adm1 = requests.get(meta["gjDownloadURL"], headers={"User-Agent": USER_AGENT},
                            timeout=180).json()
        by_name = {}
        for f in adm1.get("features", []):
            by_name[str(f["properties"].get("shapeName", "")).strip()] = f
        print("      + geoBoundaries ADM1: %s" % ", ".join(sorted(by_name)))

        for util, (names, color, note) in OUTSIDE_DHAKA.items():
            parts = [shape(by_name[n]["geometry"]).buffer(0)
                     for n in names if n in by_name]
            if not parts:
                print("      ! %s: no matching ADM1 division" % util)
                continue
            geom = unary_union(parts).simplify(0.004, preserve_topology=True)
            features.append({
                "type": "Feature",
                "geometry": mapping(geom),
                "properties": {
                    "id": util.lower(), "name": util, "level": "utility",
                    "utility": util, "status": "estimated", "confidence": "low",
                    "color_hex": color,
                    "source_url": GEOBOUNDARIES_ADM1,
                    "source_license": "CC-BY 4.0 (geoBoundaries gbOpen)",
                    "retrieved_at": iso_utc(),
                    "method": "geoBoundaries ADM1 divisions: %s" % ", ".join(names),
                    "notes": note,
                    "caveat": RURAL_CAVEAT,
                },
            })
            print("      + %s territory built" % util)
    except Exception as exc:
        print("      ! ADM1 territories failed: %s" % exc)

    _carry_forward(features, {"DESCO", "DPDC", "NESCO", "WZPDCL", "BPDB"})

    return {
        "type": "FeatureCollection",
        "generated_at": iso_utc(),
        "note": "EVERY polygon here is an estimate. No Bangladeshi electricity "
                "distributor publishes service-territory GIS data. Dhaka's DESCO/DPDC "
                "line is a Voronoi construction from each utility's own published zone "
                "names; everything outside Dhaka is an administrative division standing "
                "in for a service area, which ignores the urban/rural BREB split.",
        "features": features,
    }
