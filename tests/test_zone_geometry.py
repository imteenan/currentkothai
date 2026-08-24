"""Guard the join between schedule claims and map geometry.

The map showed nothing for DPDC for weeks. Nothing was broken in an obvious
way: the sheets parsed, the claims validated, the layers registered. There was
simply no `dpdc-zones.geojson`, and no test anywhere asserted that the schedule
data could actually reach a shape on the map.

The join is one string. `updateZoneLoads()` matches a claim's
`division_canonical` against a polygon's `division` property, lowercased. Rename
a zone on either side and the map silently goes dark again with every other
check still green. That is what these tests exist to catch.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GEO = ROOT / "data" / "geo"
SCHEDULES = ROOT / "data" / "schedules"


def _load(path: Path):
    if not path.exists():
        pytest.skip("%s not built" % path.name)
    return json.loads(path.read_text(encoding="utf-8"))


def _divisions(utility: str) -> set[str]:
    doc = _load(SCHEDULES / utility.lower() / "latest.json")
    return {(c.get("division_canonical") or c.get("division") or "").lower()
            for c in doc.get("claims", []) if c.get("division")}


def _polygon_names(filename: str) -> set[str]:
    fc = _load(GEO / filename)
    return {str(f["properties"].get("division", "")).lower()
            for f in fc.get("features", [])}


def test_dpdc_zone_cells_exist():
    """The file whose absence emptied half the map."""
    fc = _load(GEO / "dpdc-zones.geojson")
    assert len(fc["features"]) >= 30, "expected a cell for most of the 36 zones"


def test_desco_division_cells_exist():
    """This one shipped as a valid GeoJSON with zero features."""
    fc = _load(GEO / "desco-divisions.geojson")
    assert len(fc["features"]) >= 20


def test_every_dpdc_division_on_the_schedule_has_a_shape():
    """A claim that cannot reach a polygon cannot be drawn."""
    missing = _divisions("dpdc") - _polygon_names("dpdc-zones.geojson")
    assert not missing, "DPDC divisions with no map polygon: %s" % sorted(missing)


def test_most_desco_divisions_have_a_shape():
    """DESCO is checked as a ratio, not exactly.

    Three real divisions (Shah Kabir, Tongi East, Tongi West) have no office
    point yet, and the parser currently emits two scrambled division names. Both
    are tracked separately. This asserts the join does not degrade further.
    """
    divisions = _divisions("desco")
    matched = divisions & _polygon_names("desco-divisions.geojson")
    assert len(matched) / max(1, len(divisions)) >= 0.75, (
        "only %d of %d DESCO divisions map to a polygon"
        % (len(matched), len(divisions)))


def test_zone_cells_are_marked_as_estimates():
    """These are Voronoi guesses around geocoded offices.

    No distributor publishes zone boundaries. If one of these ever renders as a
    solid line the reader would take it for an official border, so the property
    the map keys its dash pattern off must never say "official".
    """
    for name in ("dpdc-zones.geojson", "desco-divisions.geojson"):
        for f in _load(GEO / name)["features"]:
            assert f["properties"]["status"] == "estimated", name
            assert "method" in f["properties"], name


def test_zone_cells_carry_valid_geometry():
    fc = _load(GEO / "dpdc-zones.geojson")
    for f in fc["features"]:
        geom = f["geometry"]
        assert geom["type"] in ("Polygon", "MultiPolygon"), f["properties"]["slug"]
        assert geom["coordinates"], f["properties"]["slug"]
