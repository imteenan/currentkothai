"""Build data/geo/service-area.geojson: one outline around everything we cover.

The map draws a separate polygon per distributor, which answers "who supplies
this street" but never answers "is my city in this at all". Five dashed
territory outlines, two of them overlapping around Dhaka, read as a diagram
rather than a boundary.

This is the union of the DESCO and DPDC territories dissolved into a single
ring, so the map can draw one glowing edge around the area that actually has
schedules behind it. Interior borders disappear: where the two utilities meet,
there is no line, because to a resident there is no edge there.

Only the two distributors we read are included. NESCO, WZPDCL and BPDB have
country-spanning territories and no schedule data, so folding them in would
draw a glowing border around most of Bangladesh and promise coverage that does
not exist.

The result inherits its honesty from its inputs: those territories are
themselves constructed (a Voronoi split of geocoded office names, clipped to an
OSM administrative area), so this outline is an estimate of an estimate and
says so in its properties.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from shapely.geometry import mapping, shape
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parents[2]
GEO_DIR = ROOT / "data" / "geo"

#: The distributors whose sheets we actually read.
COVERED = ("DESCO", "DPDC")

#: Degrees. Enough to drop the sawtooth where two Voronoi cells meet without
#: moving the visible edge.
SIMPLIFY = 0.0006

#: Degrees, about 55m at this latitude. Closes hairline gaps along the shared
#: DESCO/DPDC border so the union dissolves into one ring instead of leaving a
#: seam down the middle of the city.
HEAL = 0.0005

#: Square degrees. The grow/shrink heal leaves pinpricks behind - one came out
#: at 0.0 km2, a single repeated coordinate - and each one draws its own glowing
#: ring. Anything under about a tenth of a square kilometre is an artifact of
#: the buffer, not a piece of the city.
MIN_RING_AREA = 1e-5


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def build() -> None:
    print("  service area outline")
    fc = _load(GEO_DIR / "utility-territories.geojson")

    geoms = [shape(f["geometry"]).buffer(0) for f in fc.get("features", [])
             if str(f["properties"].get("utility", "")).upper() in COVERED]
    if not geoms:
        raise SystemExit("no DESCO/DPDC territories to union")

    # Grow, merge, shrink back. A plain union leaves a visible seam wherever the
    # two territories were clipped from the same source and rounded differently.
    merged = unary_union([g.buffer(HEAL) for g in geoms]).buffer(-HEAL)
    merged = merged.simplify(SIMPLIFY, preserve_topology=True)

    parts = list(getattr(merged, "geoms", [merged]))
    kept = [p for p in parts if p.area >= MIN_RING_AREA]
    if len(kept) < len(parts):
        print("      - dropped %d buffer artifact(s)" % (len(parts) - len(kept)))
    if not kept:
        raise SystemExit("union collapsed to nothing")
    merged = kept[0] if len(kept) == 1 else unary_union(kept)

    out = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": mapping(merged),
            "properties": {
                "id": "service-area",
                "name": "Area with published schedules",
                "level": "service-area",
                "utilities": list(COVERED),
                "status": "estimated",
                "confidence": "low",
                "method": (
                    "The DESCO and DPDC service territories dissolved into one "
                    "ring. Those territories are themselves constructed - a "
                    "Voronoi split of the zone office names each utility prints "
                    "on its own schedules, clipped to the Dhaka Metropolitan "
                    "administrative area - so this edge is an estimate of an "
                    "estimate. It marks where we have schedules to show, not a "
                    "boundary anyone publishes."),
            },
        }],
    }
    (GEO_DIR / "service-area.geojson").write_text(
        json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("      + %s ring(s) -> service-area.geojson"
          % (len(merged.geoms) if hasattr(merged, "geoms") else 1))


if __name__ == "__main__":
    build()
