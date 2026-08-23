"""Build data/registry/dpdc-zones.json — DPDC's 36 NOCS zones with live PDF links.

    python -m workers.ingestion.build_dpdc_zones

DPDC publishes a schedule PDF per zone. We cannot parse them yet (legacy Bijoy
font), but we CAN hand a DPDC visitor the exact PDF for their own zone, which is
most of the value. The filename carries a revision counter that increments on
republish, so the current one is scraped from the index and never hardcoded.

Zone centroids are geocoded once so the site can pick the nearest zone to a point.
"""
from __future__ import annotations

import re
import sys
import time

import requests

from workers.ingestion.common import (
    REGISTRY_DIR, USER_AGENT, http_get, iso_utc, read_json, write_json,
)

INDEX_URL = "https://dpdc.org.bd/site/nocs/load_shedding"
BASE = "https://dpdc.org.bd"
NOMINATIM = "https://nominatim.openstreetmap.org/search"

#: Zone slug -> the query that places it. Slugs come from DPDC's own links.
ZONE_QUERY = {
    "adabor": "Adabor, Dhaka", "azimpur": "Azimpur, Dhaka",
    "banasree": "Banasree, Dhaka", "banglabazar": "Bangla Bazar, Old Dhaka",
    "bangshal": "Bangshal, Dhaka", "bashaboo": "Bashabo, Dhaka",
    "demra": "Demra, Dhaka", "dhanmondi": "Dhanmondi, Dhaka",
    "fatulla": "Fatulla, Narayanganj", "jigatola": "Jigatola, Dhaka",
    "jurain": "Jurain, Dhaka", "kakrail": "Kakrail, Dhaka",
    "kamrangirchar": "Kamrangirchar, Dhaka", "kazla": "Kazla, Dhaka",
    "khilgaon": "Khilgaon, Dhaka", "lalbag": "Lalbagh, Dhaka",
    "maniknagar": "Maniknagar, Dhaka", "matuail": "Matuail, Dhaka",
    "mogbazar": "Moghbazar, Dhaka", "motijheel": "Motijheel, Dhaka",
    "mugdapara": "Mugdapara, Dhaka", "narayangonj (east)": "Narayanganj Sadar, Narayanganj",
    "narayangonj (west)": "Narayanganj, Bangladesh", "narinda": "Narinda, Dhaka",
    "paribag": "Paribagh, Dhaka", "postogola": "Postogola, Dhaka",
    "rajarbag": "Rajarbagh, Dhaka", "ramna": "Ramna, Dhaka",
    "satmosjid": "Satmasjid Road, Dhaka", "shamoli": "Shyamoli, Dhaka",
    "sher-e-bangla nagar": "Sher-e-Bangla Nagar, Dhaka", "shyampur": "Shyampur, Dhaka",
    "siddhirgonj": "Siddhirganj, Narayanganj", "sitalakhya": "Sitalakhya, Narayanganj",
    "swamibag": "Swamibag, Dhaka", "tejgaon": "Tejgaon, Dhaka",
}

BOX = (90.15, 23.55, 90.75, 24.15)
_last = [0.0]


def _polite(gap: float = 1.1) -> None:
    w = gap - (time.time() - _last[0])
    if w > 0:
        time.sleep(w)
    _last[0] = time.time()


def title_case(slug: str) -> str:
    return " ".join(w.capitalize() for w in re.split(r"[\s_-]+", slug)).replace("(", "(")


#: Revision numbers seen in DPDC filenames, newest first. The index sometimes
#: goes empty while the PDFs stay live, so we probe these directly as a fallback.
KNOWN_REVS = (250, 200, 150, 100)


def probe_zone(slug: str) -> str | None:
    """Highest revision of a zone PDF that actually responds."""
    import urllib.parse
    quoted = urllib.parse.quote(slug)
    for rev in KNOWN_REVS:
        url = "%s/site/load_shedding/%s/%d.pdf" % (BASE, quoted, rev)
        try:
            r = requests.head(url, headers={"User-Agent": USER_AGENT},
                              timeout=25, verify=False, allow_redirects=True)
        except Exception:
            continue
        if r.status_code == 200 and "pdf" in r.headers.get("Content-Type", "").lower():
            return url
    return None


def scrape_links() -> dict[str, str]:
    """slug -> absolute PDF url, with whatever revision is current today."""
    res = http_get(INDEX_URL, timeout=60)
    if not res.ok:
        print("  ! index unreachable: %s" % (res.error or res.status))
        return {}
    html = res.content.decode("utf-8", "replace")
    out: dict[str, str] = {}
    for m in re.finditer(r'href="(/site/load_shedding/([^"/]+)/(\d+)\.pdf)"', html):
        path, slug, rev = m.group(1), m.group(2), m.group(3)
        slug = requests.utils.unquote(slug).lower()
        out[slug] = BASE + path
    print("  index listed %d zone PDF links" % len(out))
    return out


def geocode(query: str) -> tuple[float, float] | None:
    _polite()
    try:
        rows = requests.get(NOMINATIM, params={
            "q": query, "format": "jsonv2", "limit": 1, "countrycodes": "bd",
        }, headers={"User-Agent": USER_AGENT}, timeout=45).json()
    except Exception as exc:
        print("    ! %s: %s" % (query, exc))
        return None
    if not rows:
        return None
    lon, lat = float(rows[0]["lon"]), float(rows[0]["lat"])
    if not (BOX[0] <= lon <= BOX[2] and BOX[1] <= lat <= BOX[3]):
        print("    ! %s geocoded outside greater Dhaka, dropped" % query)
        return None
    return lat, lon


def main() -> int:
    print("DPDC zones")
    try:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    except Exception:
        pass

    links = scrape_links()
    if not links:
        print("  index empty - probing zone PDFs directly")
    previous = {z["slug"]: z for z in
                (read_json(REGISTRY_DIR / "dpdc-zones.json", {}) or {}).get("zones", [])}

    zones = []
    for slug, query in sorted(ZONE_QUERY.items()):
        url = links.get(slug) or probe_zone(slug)
        old = previous.get(slug, {})
        # Reuse a good coordinate rather than re-geocoding every run.
        lat, lon = old.get("lat"), old.get("lon")
        if lat is None or lon is None:
            got = geocode(query)
            if got:
                lat, lon = got
        zones.append({
            "slug": slug,
            "name": title_case(slug),
            "pdf_url": url or old.get("pdf_url"),
            "pdf_live": bool(url),
            "lat": lat, "lon": lon,
            "geocoded_query": query,
        })
        print("  %-22s %s %s" % (slug, "pdf" if url else "-- ",
                                 "%.4f,%.4f" % (lat, lon) if lat else "no coords"))

    doc = {
        "schema": "dpdc-zones/1",
        "generated_at": iso_utc(),
        "utility": "DPDC",
        "index_url": INDEX_URL,
        "source_license": "Published by Dhaka Power Distribution Company Ltd",
        "note": ("DPDC's per-zone load-shedding PDFs. We cannot parse them yet (legacy "
                 "Bijoy font), so the site links a visitor straight to the PDF for the "
                 "zone nearest their point. The revision number in each filename changes "
                 "on republish; it is scraped from index_url when that page lists links, "
                 "and probed directly when it does not - the index has been observed empty "
                 "while every zone PDF stayed live."),
        "discovery": "index" if links else "direct-probe",
        "zone_count": len(zones),
        "with_live_pdf": sum(1 for z in zones if z["pdf_live"]),
        "zones": zones,
    }
    write_json(REGISTRY_DIR / "dpdc-zones.json", doc)
    print("wrote %s (%d zones, %d live PDFs)"
          % (REGISTRY_DIR / "dpdc-zones.json", len(zones), doc["with_live_pdf"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
