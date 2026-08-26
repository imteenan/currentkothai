"""Catch constants that encode a fact about the data and then go quietly false.

Two production bugs came from this one mistake, and neither was visible from
the server:

  1. `sw.js` had `const VERSION = 'ck-v2'`, a hand-bumped cache key that was
     never bumped. `activate` deletes only caches that do not start with it, so
     every deploy deleted nothing and returning visitors were pinned to the
     first bundle they ever downloaded. The fix shipped and reached nobody.

  2. `map.js` had `center: [90.4074, 23.7925]`, correct when the map carried
     DESCO alone (lat 23.73-23.90). Adding DPDC took coverage down to 23.55 and
     the opening view cut Fatulla and Narayanganj off the bottom of the screen.

Both were true when written. Both were checked by nothing. The counts printed
in the marketing copy are the same shape of risk: "36 zone sheets" is a fact
about data/registry/dpdc-zones.json sitting in an HTML file that never reads it.

These tests are deliberately about the *seam* between a written-down number and
the data it describes, not about the numbers being any particular value.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "apps" / "web"
DATA = ROOT / "data"


def _read(path: Path) -> str:
    if not path.exists():
        pytest.skip("%s missing" % path.name)
    return path.read_text(encoding="utf-8")


def _json(path: Path):
    if not path.exists():
        pytest.skip("%s missing" % path.name)
    return json.loads(path.read_text(encoding="utf-8"))


def dpdc_zone_count() -> int:
    doc = _json(DATA / "registry" / "dpdc-zones.json")
    zones = doc.get("zones", doc) if isinstance(doc, dict) else doc
    return len(zones)


def scanned_zone_count() -> int:
    doc = _json(DATA / "schedules" / "dpdc" / "latest.json")
    return len({c["division"] for c in doc["claims"]
                if c.get("read_by") == "ocr-scan"})


# ------------------------------------------------------- the service worker

def test_service_worker_version_is_not_a_hand_written_literal():
    """The exact bug: a cache key someone has to remember to change.

    It must be stamped by the build from the content it caches, or every
    deploy is invisible to everyone who has already visited once.
    """
    sw = _read(WEB / "sw.js")
    assert "__BUILD_ID__" in sw, (
        "sw.js no longer carries the build-time placeholder; if the cache key "
        "went back to a literal, deploys stop reaching returning visitors")
    version = re.search(r"const VERSION = ([^;]+);", sw)
    assert version, "VERSION assignment not found in sw.js"
    assert "BUILD_ID" in version.group(1), (
        "VERSION must derive from BUILD_ID, found: %s" % version.group(1))


def test_build_stamps_and_refuses_to_ship_unstamped():
    """The stamping must happen, and must fail loudly when it does not.

    A silent no-op here puts every returning visitor back on a stale bundle
    while looking like a clean deploy, which is the failure this guards.
    """
    sh = _read(ROOT / "tools" / "build-site.sh")
    assert "stamp_build.py" in sh, "build-site.sh must run the stamper"

    py = _read(ROOT / "tools" / "stamp_build.py")
    assert "sha256" in py, "the id must be a hash of the built shell"
    assert py.count("raise SystemExit") >= 2, (
        "the stamper must abort when sw.js keeps the placeholder and when "
        "index.html comes out unversioned")


def test_stamper_versions_every_kind_of_asset_url():
    """Versioning the HTML entry point alone would miss the ES imports.

    app.js pulls in nine modules by relative path. Those URLs live inside the
    JavaScript, not the markup, so a browser holding cached copies would keep
    running the old modules behind a freshly fetched entry point.
    """
    sys.path.insert(0, str(ROOT))
    from tools import stamp_build

    for name, sample in (
        ("IMPORT_RE", "import { x } from './util.js';"),
        ("HTML_RE", '<script type="module" src="src/app.js"></script>'),
        ("HTML_RE", '<link rel="stylesheet" href="styles/base.css">'),
        ("SW_ASSET_RE", "  './src/app.js',"),
    ):
        pattern = getattr(stamp_build, name)
        assert pattern.search(sample), "%s does not match %r" % (name, sample)


# --------------------------------------------------------------- the camera

def _territory_bounds(utilities: set[str]):
    fc = _json(DATA / "geo" / "utility-territories.geojson")
    w, s, e, n = 180.0, 90.0, -180.0, -90.0
    seen = False

    def walk(c):
        nonlocal w, s, e, n, seen
        if isinstance(c[0], (int, float)):
            seen = True
            w, e = min(w, c[0]), max(e, c[0])
            s, n = min(s, c[1]), max(n, c[1])
            return
        for part in c:
            walk(part)

    for f in fc["features"]:
        if str(f["properties"].get("utility", "")).upper() in utilities:
            walk(f["geometry"]["coordinates"])
    assert seen, "no territory found for %s" % utilities
    return w, s, e, n


def test_map_fits_the_view_to_the_data():
    """A hardcoded centre is what hid south Dhaka. It must not come back."""
    js = _read(WEB / "src" / "map.js")
    assert "fitTo(bounds" in js, "map.js must expose a data-driven fit"
    app = _read(WEB / "src" / "app.js")
    assert "fitTo(boundsOf(" in app, (
        "app.js must fit the opening view to the served territories rather "
        "than trusting a literal centre")


def test_fallback_centre_still_sits_inside_the_served_area():
    """The literal that remains is a fallback, and it must not drift again.

    If a distributor is added whose territory moves the centre, this fails and
    the fallback gets updated with it.
    """
    w, s, e, n = _territory_bounds({"DESCO", "DPDC"})
    js = _read(WEB / "src" / "map.js")
    m = re.search(r"center:\s*\[\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*\]", js)
    assert m, "no fallback centre found in map.js"
    lon, lat = float(m.group(1)), float(m.group(2))
    mid_lon, mid_lat = (w + e) / 2, (s + n) / 2
    # A tenth of a degree is about 11 km. The bug was 0.07 degrees north.
    assert abs(lat - mid_lat) < 0.05, (
        "fallback centre lat %.4f is %.4f from the centre of the served area "
        "(%.4f); this is exactly how Fatulla and Narayanganj fell off screen"
        % (lat, abs(lat - mid_lat), mid_lat))
    assert abs(lon - mid_lon) < 0.05, "fallback centre lon drifted to %.4f" % lon


def test_every_zone_cell_sits_inside_the_fitted_bounds():
    """Fitting to territories must actually frame every zone we draw."""
    w, s, e, n = _territory_bounds({"DESCO", "DPDC"})
    for name in ("dpdc-zones.geojson", "desco-divisions.geojson"):
        fc = _json(DATA / "geo" / name)
        for f in fc["features"]:
            coords = f["geometry"]["coordinates"]
            while not isinstance(coords[0][0], (int, float)):
                coords = coords[0]
            lon, lat = coords[0][:2]
            assert w - 0.01 <= lon <= e + 0.01 and s - 0.01 <= lat <= n + 0.01, (
                "%s in %s falls outside the fitted view"
                % (f["properties"].get("division"), name))


# ------------------------------------------------------- numbers in the copy

def test_zone_count_in_the_copy_matches_the_registry():
    """"36 DPDC zone sheets" is a fact about a JSON file, written in HTML."""
    expected = dpdc_zone_count()
    for page in ("index.html", "about.html", "sources.html"):
        html = _read(WEB / page)
        for claimed in re.findall(r"\b(\d{2}) (?:of them|DPDC zone sheets|zones are read)", html):
            assert int(claimed) == expected, (
                "%s claims %s DPDC zones, registry has %d"
                % (page, claimed, expected))


def test_scanned_count_in_the_copy_matches_what_was_read():
    expected = scanned_zone_count()
    for page in ("index.html", "about.html", "sources.html"):
        html = _read(WEB / page)
        for claimed in re.findall(r"\b(\d{1,2})[ ]scanned photographs", html):
            assert int(claimed) == expected, (
                "%s claims %s scanned sheets, the feed reports %d"
                % (page, claimed, expected))


def test_sheet_total_in_the_copy_is_the_zones_plus_desco():
    """"37 sheets" is 36 DPDC zone sheets plus DESCO's one weekday sheet."""
    html = _read(WEB / "index.html")
    m = re.search(r"\b(\d{2}) sheets\b", html)
    if not m:
        pytest.skip("sheet total not stated in the copy")
    assert int(m.group(1)) == dpdc_zone_count() + 1, (
        "copy says %s sheets, data says %d" % (m.group(1), dpdc_zone_count() + 1))


# ---------------------------------------------------------- the HTTP cache

def test_unhashed_code_is_never_cached_long():
    """A long max-age is only safe on a filename that changes with its content.

    Nothing this project serves is content-hashed: /src/map.js keeps that name
    forever. It was served with max-age=604800, so every browser pinned the
    bundle for a week and no deploy could reach a returning visitor. Fixing the
    service worker did not help, because this cache sits in front of it. The
    two failures looked identical from the outside and had to be found twice.
    """
    headers = _read(WEB / "_headers")

    rules: dict[str, str] = {}
    current: list[str] = []
    for line in headers.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith((" ", "\t")):
            current = [line.strip()]
            continue
        if "cache-control" in line.lower():
            for path in current:
                rules[path] = line.split(":", 1)[1].strip().lower()

    #: Paths whose filenames never change, so their contents must be revalidated.
    unhashed_code = ["/src/*", "/styles/*", "/*.html", "/"]
    for path in unhashed_code:
        assert path in rules, "%s has no Cache-Control rule" % path
        value = rules[path]
        assert "no-cache" in value or "max-age=0" in value, (
            "%s serves unhashed code with %r; a returning visitor would keep "
            "their old bundle for that long and never see a fix" % (path, value))

    # Nothing anywhere should claim immutability, since no filename carries a
    # content hash to justify it.
    for path, value in rules.items():
        assert "immutable" not in value, (
            "%s is marked immutable but its filename never changes" % path)


# ------------------------------------------------- fixtures reaching readers

def _sample_hashes() -> dict:
    import hashlib
    out = {}
    d = DATA / "seed" / "samples"
    if not d.is_dir():
        pytest.skip("no seed samples")
    for f in sorted(d.glob("*.pdf")):
        out[hashlib.sha256(f.read_bytes()).hexdigest()] = f.name
    return out


def test_no_published_schedule_is_a_seed_sample():
    """A test fixture must never be served as a live schedule.

    data/seed/archive is gitignored, so a CI runner has no archive and the
    DESCO fallback chain always reached
    data/seed/samples/desco-load-management-sunday-2026-07.pdf. desco.gov.bd
    times out from GitHub's runners, so the live site served that July fixture
    as the current schedule - Sunday's sheet, on a Wednesday, reported "fresh".

    This compares what is published against every sample byte-for-byte, which
    is what makes it catch the substitution rather than the excuse for it.
    """
    samples = _sample_hashes()
    for utility in ("desco", "dpdc"):
        doc = _json(DATA / "schedules" / utility / "latest.json")
        sha = (doc.get("source") or {}).get("sha256")
        assert sha not in samples, (
            "%s is publishing the seed fixture %s as a live schedule"
            % (utility.upper(), samples.get(sha)))


def test_seed_samples_are_only_reachable_offline():
    """The fallback that produced the above must stay behind the offline flag."""
    src = _read(ROOT / "workers" / "ingestion" / "run_ingest.py")
    for line in src.splitlines():
        if "seed/samples" in line and not line.lstrip().startswith("#"):
            assert "offline" in src[max(0, src.index(line) - 400):src.index(line)], (
                "a seed sample is referenced outside an offline-only branch: %s"
                % line.strip())


def test_freshness_requires_the_sheet_to_be_for_today():
    """DESCO prints its weekday, so the sheet says whether it is current.

    A fetch can succeed and still return a superseded file, so provenance alone
    cannot decide freshness.
    """
    src = _read(ROOT / "workers" / "ingestion" / "run_ingest.py")
    assert "sheet_is_today" in src, "DESCO freshness must check the sheet's weekday"
    assert '"status": "fresh" if (fetched_live and sheet_is_today) else "stale"' in src, (
        "status must require both a live fetch and today's sheet")
