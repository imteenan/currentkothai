"""Parser + validation-gate tests against the real archived PDFs.

The important one is `test_2026_layout_is_not_shifted`: the 2026 document adds a
"Load (MW)" column, and an adapter that assumes a fixed metadata-column count
silently moves every window an hour late and invents a midnight window on every
feeder. That bug shipped once; this test exists so it cannot ship again.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from workers.ingestion import validate as V
from workers.parsers import desco_pdf_v1 as P

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "desco"
SAMPLES = ROOT / "data" / "seed" / "samples"

PDF_2022 = FIXTURES / "2022-12-26-monday.pdf"
PDF_2026 = SAMPLES / "desco-load-management-sunday-2026-07.pdf"

COMMON = dict(source_id="test", source_url="https://example.invalid/s.pdf",
              sha256="0" * 64, effective_date="2026-08-23")


def parse(path: Path):
    if not path.exists():
        pytest.skip("fixture not present: %s" % path)
    return P.parse(path, **COMMON)


@pytest.fixture(scope="module")
def doc_2022():
    return parse(PDF_2022)


@pytest.fixture(scope="module")
def doc_2026():
    return parse(PDF_2026)


# ------------------------------------------------------------ shape & volume


@pytest.mark.parametrize("name", ["doc_2022", "doc_2026"])
def test_parses_a_useful_number_of_claims(name, request):
    doc = request.getfixturevalue(name)
    assert len(doc["claims"]) > 100, "suspiciously few claims"
    assert doc["stats"]["division_count"] >= 20
    assert doc["coverage_level"] == "feeder"
    assert doc["badge"] == "DERIVED", "we restructure the data, so it is never OFFICIAL"


@pytest.mark.parametrize("name", ["doc_2022", "doc_2026"])
def test_hour_grid_is_24_columns(name, request):
    assert request.getfixturevalue(name)["parse_meta"]["hour_column_count"] == 24


def test_both_layout_generations_are_recognised(doc_2022, doc_2026):
    assert doc_2022["parse_meta"]["meta_column_counts"] == [4]
    assert doc_2026["parse_meta"]["meta_column_counts"] == [5], \
        "the 2026 layout has a Load (MW) column"


# ------------------------------------------------------------ the shift bug


def test_2026_layout_is_not_shifted(doc_2026):
    """No phantom midnight window: the 'LS' marker must not be read as hour 0."""
    midnight = [c for c in doc_2026["claims"]
                if any(w["start"] == "00:00" for w in c["windows"])]
    ratio = len(midnight) / len(doc_2026["claims"])
    assert ratio < 0.10, (
        "%d/%d claims start at 00:00 - the metadata column count is almost "
        "certainly being misread" % (len(midnight), len(doc_2026["claims"])))


def test_known_row_matches_the_source_document(doc_2026):
    """Hand-checked against the raw pdfplumber cells for the Samaj Sheba feeder."""
    rows = [c for c in doc_2026["claims"]
            if c["feeder"] == "Samaj Sheba" and c["division"] == "Agargaon"]
    assert rows, "expected the Agargaon / Samaj Sheba row"
    windows = {(w["start"], w["end"]) for w in rows[0]["windows"]}
    assert ("11:00", "12:00") in windows
    assert ("14:00", "15:00") in windows
    assert ("00:00", "01:00") not in windows
    assert rows[0]["load_mw"] == pytest.approx(1.67)


# ------------------------------------------------------------ window hygiene


@pytest.mark.parametrize("name", ["doc_2022", "doc_2026"])
def test_every_window_is_well_formed(name, request):
    for c in request.getfixturevalue(name)["claims"]:
        assert c["windows"], "a claim with no window asserts nothing"
        for w in c["windows"]:
            sh, sm = (int(x) for x in w["start"].split(":"))
            eh, em = (int(x) for x in w["end"].split(":"))
            assert 0 <= sh <= 23 and 0 <= eh <= 24
            assert sm == 0 and em == 0, "DESCO's grid is hourly"
            assert eh * 60 + em > sh * 60 + sm, "end must be after start"


def test_consecutive_hours_merge_into_one_window():
    assert P.hours_to_windows([14, 15, 16]) == [{"start": "14:00", "end": "17:00"}]
    assert P.hours_to_windows([1, 3]) == [
        {"start": "01:00", "end": "02:00"}, {"start": "03:00", "end": "04:00"}]
    assert P.hours_to_windows([23]) == [{"start": "23:00", "end": "24:00"}], \
        "the last hour ends 24:00, never 00:00"
    assert P.hours_to_windows([]) == []


@pytest.mark.parametrize("name", ["doc_2022", "doc_2026"])
def test_every_claim_carries_provenance(name, request):
    doc = request.getfixturevalue(name)
    for field in ("source_url", "retrieved_at", "sha256", "parser_adapter", "parser_version"):
        assert doc["source"][field], "missing provenance field %s" % field
    for c in doc["claims"]:
        assert c["feeder_id"].startswith("desco:")


# ------------------------------------------------------ division canonicalisation


def test_canonical_division_folds_only_known_variants():
    assert P.canonical_division("Agargaon")[0] == "Agargaon"
    assert P.canonical_division("Rupnagor")[0] == "Rupnagar"
    assert P.canonical_division("tongi central")[0] == "Tongi Central"
    # Garble is refused, not guessed at.
    canon, problem = P.canonical_division("MGuolhsahkahnali:")
    assert canon is None and problem
    # An unseen name is refused too, rather than fuzzy-matched.
    canon, problem = P.canonical_division("Someplace New")
    assert canon is None and "unrecognised" in problem


def test_overprinted_cells_are_separated(doc_2026):
    """The Area column is centre-aligned and unclipped, so a long area string
    prints straight through the division and feeder cells on the same baseline.
    Ordering a cell's glyphs by x then zips the two texts together, which is
    where "MGuolhsahkahnali:" came from -- not two divisions printed over each
    other, but "Gulshan" with an area string over it. Both rows are checked by
    feeder, because the feeder cell was corrupted the same way.
    """
    by_feeder = {c["feeder"]: c for c in doc_2026["claims"]}

    assert "Kawlar" in by_feeder, "feeder came back as 'eer ArKotawlar'"
    assert by_feeder["Kawlar"]["division_canonical"] == "Dhakkhinkhan"
    assert by_feeder["Kawlar"]["area_text"].startswith("Kaowla Bazar")

    assert "NIPSOM" in by_feeder, "feeder came back as 'WaterN PIuPmSOp.M'"
    assert by_feeder["NIPSOM"]["division_canonical"] == "Gulshan"

    # The zipped strings must not survive anywhere: a garbled division can never
    # match a map polygon, so the zone silently never lights up.
    for claim in doc_2026["claims"]:
        assert claim["division_canonical"], "unmapped division %r" % claim["division"]
    assert "Kaowla" not in {c["division"] for c in doc_2026["claims"]}
    assert "Mohakhali" not in {c["division"] for c in doc_2026["claims"]}


def test_every_division_can_reach_a_map_polygon(doc_2026):
    """app.js joins claims to zone polygons on the division string, so a name
    that no polygon carries is a zone that never lights up. Tongi East and Tongi
    West are known to have no polygon -- they sit outside the modelled DESCO
    territory; see UNPLACEABLE_DESCO in workers/geospatial/zone_cells.py.
    """
    polygons = {f["properties"]["division"] for f in
                json.loads((ROOT / "data" / "geo" / "desco-divisions.geojson")
                           .read_text(encoding="utf-8"))["features"]}
    named = {c["division_canonical"] for c in doc_2026["claims"]}
    assert named - polygons == {"Tongi East", "Tongi West"}


def test_most_claims_get_a_canonical_division(doc_2026):
    ok = [c for c in doc_2026["claims"] if c["division_canonical"]]
    assert len(ok) / len(doc_2026["claims"]) > 0.95


# ------------------------------------------------------------ validation gate


def test_a_good_document_passes(doc_2026):
    report = V.validate(doc_2026)
    assert report.verdict in (V.PASS, V.QUARANTINE), report.summary()
    assert report.verdict != V.REJECT


def test_empty_parse_of_a_non_empty_source_is_rejected(doc_2026):
    broken = copy.deepcopy(doc_2026)
    broken["claims"] = []
    broken["stats"] = {"claim_count": 0, "feeder_count": 0, "division_count": 0}
    report = V.validate(broken, source_had_content=True)
    assert report.verdict == V.REJECT
    assert any(f["check"] == "empty-parse" for f in report.findings)


def test_backwards_window_is_rejected(doc_2026):
    broken = copy.deepcopy(doc_2026)
    broken["claims"][0]["windows"] = [{"start": "18:00", "end": "09:00"}]
    report = V.validate(broken)
    assert report.verdict == V.REJECT
    assert any(f["check"] == "time-sanity" for f in report.findings)


def test_wrong_hour_column_count_is_rejected(doc_2026):
    broken = copy.deepcopy(doc_2026)
    broken["parse_meta"]["hour_column_count"] = 23
    report = V.validate(broken)
    assert report.verdict == V.REJECT
    assert any(f["check"] == "hour-columns" for f in report.findings)


def test_missing_provenance_is_rejected(doc_2026):
    broken = copy.deepcopy(doc_2026)
    broken["source"]["source_url"] = ""
    report = V.validate(broken)
    assert report.verdict == V.REJECT
    assert any(f["check"] == "provenance" for f in report.findings)


def test_an_older_document_cannot_replace_a_newer_one(doc_2026):
    older = copy.deepcopy(doc_2026)
    older["effective_date"] = "2026-08-01"
    report = V.validate(older, previous=doc_2026)
    assert report.verdict == V.REJECT
    assert any(f["check"] == "date-regression" for f in report.findings)


def test_a_sudden_collapse_in_row_count_is_quarantined(doc_2026):
    shrunk = copy.deepcopy(doc_2026)
    shrunk["claims"] = shrunk["claims"][:20]
    report = V.validate(shrunk, previous=doc_2026)
    assert any(f["check"] == "volume-drop" for f in report.findings)
    assert report.verdict in (V.QUARANTINE, V.REJECT)


# ------------------------------------------------------------ published output


def test_published_files_match_the_contract():
    latest = ROOT / "data" / "schedules" / "desco" / "latest.json"
    if not latest.exists():
        pytest.skip("no published schedule yet")
    doc = json.loads(latest.read_text(encoding="utf-8"))
    assert doc["schema"] == "schedule-claims/1"
    assert V.validate(doc).verdict != V.REJECT

    index = json.loads(
        (ROOT / "data" / "schedules" / "index.json").read_text(encoding="utf-8"))
    for row in index["utilities"]:
        assert row["status"] in ("fresh", "stale", "unavailable", "link-only")
        assert "utility" in row and "coverage_level" in row
