"""Build per-zone map polygons: data/geo/dpdc-zones.geojson and desco-divisions.

The map had nothing to draw for DPDC. `utility-territories.geojson` carries one
polygon per distributor, and `desco-offices.geojson` carries 23 DESCO points,
so the "Zones" layer showed a scatter of DESCO markers over north Dhaka and left
the entire southern half of the city - all 36 DPDC zones, 423 claims - blank.
The schedule data was fine. It simply had no geometry to attach itself to.

`territories.py` already computes exactly what is needed: it Voronoi-splits
DESCO's division names against DPDC's zone names to find the boundary between
the two utilities. It then dissolves those cells into two blobs and throws the
per-zone detail away. This module keeps it.

Two differences from that build:

1. **It runs offline.** territories.py geocodes through Nominatim, which is
   rate-limited and was the original reason zone geometry never got published.
   The coordinates already exist: 34 of 36 DPDC zones carry lat/lon in
   data/registry/dpdc-zones.json, and the DESCO points are already a GeoJSON.
2. **Each utility is split within its own territory**, not against the other.
   The inter-utility boundary is already decided by territories.py; re-deriving
   it here would let a rounding difference put a zone on the wrong side of it.

These cells are CONSTRUCTED. No distributor publishes zone boundaries, and a
Voronoi cell around a geocoded office is a guess about catchment, not a service
area. Every feature says so in `status` and `method`, and the map draws them
dashed with the legend line "Dashed borders are our estimates."
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from shapely.geometry import MultiPoint, Point, mapping, shape
from shapely.ops import unary_union, voronoi_diagram

ROOT = Path(__file__).resolve().parents[2]
GEO_DIR = ROOT / "data" / "geo"
REGISTRY_DIR = ROOT / "data" / "registry"

#: Simplification tolerance in degrees. Enough to cut file size without moving
#: a border far enough to change which cell a point falls in.
SIMPLIFY = 0.0003

#: Cells are drawn from office locations, which are points, so a cell says
#: "nearest zone office", not "service area". Never claim better than this.
CONFIDENCE = "low"
STATUS = "estimated"

METHOD = (
    "Constructed, not published. {n} {util} zone offices were geocoded from the "
    "zone names {util} itself prints on its schedules, then split by a Voronoi "
    "partition and clipped to the {util} service territory. A cell is the area "
    "closer to that zone office than to any other. It is a catchment estimate, "
    "not a service boundary: {util} does not publish one."
)


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _dump(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n",
                    encoding="utf-8")


def territory(utility: str):
    """The clip polygon for one distributor."""
    fc = _load(GEO_DIR / "utility-territories.geojson")
    for f in fc.get("features", []):
        if str(f["properties"].get("utility", "")).upper() == utility.upper():
            return shape(f["geometry"]).buffer(0)
    raise SystemExit("no territory polygon for %s" % utility)


def dpdc_seeds() -> list[dict]:
    """DPDC zones that carry coordinates, with their sheet URL."""
    zones = _load(REGISTRY_DIR / "dpdc-zones.json")
    zones = zones.get("zones", zones) if isinstance(zones, dict) else zones
    out = []
    for z in zones:
        if z.get("lat") is None or z.get("lon") is None:
            continue
        out.append({"slug": z["slug"], "name": z["name"],
                    "lat": float(z["lat"]), "lon": float(z["lon"]),
                    "pdf_url": z.get("pdf_url")})
    return out


def desco_seeds() -> list[dict]:
    fc = _load(GEO_DIR / "desco-offices.geojson")
    out = []
    for f in fc.get("features", []):
        if f["geometry"]["type"] != "Point":
            continue
        lon, lat = f["geometry"]["coordinates"][:2]
        p = f["properties"]
        name = p.get("division") or p.get("name")
        if not name:
            continue
        out.append({"slug": p.get("id") or name.lower().replace(" ", "-"),
                    "name": name, "lat": float(lat), "lon": float(lon),
                    "pdf_url": None})
    return out


def cells(seeds: list[dict], clip) -> dict[str, Any]:
    """Voronoi cell per seed, clipped to the territory. Keyed by slug.

    Seeds outside the territory still get a cell: a zone office can sit just
    over the clip line without the zone stopping at it. Only a cell that is
    empty after clipping is dropped.
    """
    if len(seeds) < 3:
        raise SystemExit("need at least 3 seeds for a Voronoi partition")

    pts, owner = [], {}
    for s in seeds:
        key = (round(s["lon"], 6), round(s["lat"], 6))
        if key in owner:            # two zones geocoded to the same spot
            continue
        owner[key] = s["slug"]
        pts.append(Point(*key))

    diagram = voronoi_diagram(MultiPoint(pts), envelope=clip.buffer(0.3))

    out: dict[str, Any] = {}
    for cell in diagram.geoms:
        for key, slug in owner.items():
            if not cell.contains(Point(key)):
                continue
            piece = cell.intersection(clip).buffer(0)
            if not piece.is_empty:
                out[slug] = piece
            break
    return out


def build_layer(utility: str, seeds: list[dict], color: str) -> dict:
    clip = territory(utility)
    shapes = cells(seeds, clip)
    by_slug = {s["slug"]: s for s in seeds}
    method = METHOD.format(n=len(shapes), util=utility)

    features = []
    for slug, geom in sorted(shapes.items()):
        s = by_slug[slug]
        props = {
            "id": "%s-zone-%s" % (utility.lower(), slug),
            "slug": slug,
            "name": s["name"],
            # The schedule keys claims by division name, so the map can only
            # join the two if it carries the same string.
            "division": s["name"],
            "level": "zone",
            "utility": utility,
            "status": STATUS,
            "confidence": CONFIDENCE,
            "color_hex": color,
            "source_url": "https://nominatim.openstreetmap.org/",
            "source_license": "ODbL (OpenStreetMap contributors)",
            "method": method,
        }
        if s.get("pdf_url"):
            props["pdf_url"] = s["pdf_url"]
        features.append({
            "type": "Feature",
            "geometry": mapping(geom.simplify(SIMPLIFY, preserve_topology=True)),
            "properties": props,
        })

    return {"type": "FeatureCollection",
            "features": features,
            "properties": {"utility": utility, "level": "zone",
                           "count": len(features), "method": method}}


def build() -> None:
    print("  zone cells")
    for utility, seeds, color, out_name in (
        ("DPDC", dpdc_seeds(), "#5e5ce6", "dpdc-zones.geojson"),
        ("DESCO", desco_seeds(), "#0a84ff", "desco-divisions.geojson"),
    ):
        if not seeds:
            print("      ! %s: no seed points, left as is" % utility)
            continue
        fc = build_layer(utility, seeds, color)
        _dump(GEO_DIR / out_name, fc)
        covered = unary_union([shape(f["geometry"]) for f in fc["features"]]) \
            if fc["features"] else None
        pct = (covered.area / territory(utility).area * 100) if covered else 0
        print("      + %-5s %2d zones -> %s (%.0f%% of territory)"
              % (utility, len(fc["features"]), out_name, pct))


if __name__ == "__main__":
    build()
