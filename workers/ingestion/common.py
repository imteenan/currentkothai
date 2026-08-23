"""Shared helpers for the power-bd ingestion pipeline.

No paid services, no API keys. Everything here runs inside GitHub Actions on the
free tier for public repos.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------- constants

#: Bangladesh Standard Time. Every time in the published JSON is Asia/Dhaka.
DHAKA = timezone(timedelta(hours=6))

#: Identifies this project to the servers we poll. We do NOT pretend to be a
#: browser -- operators should be able to see who we are and contact us.
USER_AGENT = (
    "power-bd-ingest/1.0 (independent, non-official Bangladesh load-shedding "
    "visualiser; +https://github.com/currentkothai/currentkothai)"
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA = REPO_ROOT / "data"
REGISTRY_DIR = DATA / "registry"
SOURCES_PATH = REGISTRY_DIR / "sources.json"
STATE_PATH = REGISTRY_DIR / "state.json"
KNOWN_DIVISIONS_PATH = REGISTRY_DIR / "known_divisions.json"
ARCHIVE_DIR = DATA / "seed" / "archive"
SCHEDULES_DIR = DATA / "schedules"
QUARANTINE_DIR = SCHEDULES_DIR / "_quarantine"
SCHEMA_DIR = Path(__file__).resolve().parent / "schema"

#: A source is shown as "stale" in the UI once its newest good fetch is older
#: than this. 36h = one missed daily publish plus slack.
STALE_AFTER_HOURS = 36

# ---------------------------------------------------------------- time


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(dt: datetime | None = None) -> str:
    """RFC3339 UTC timestamp with a literal Z, as the contract shows."""
    dt = dt or utcnow()
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None


def dhaka_today() -> str:
    return utcnow().astimezone(DHAKA).strftime("%Y-%m-%d")


# ---------------------------------------------------------------- text

_WS = re.compile(r"\s+")


def collapse_ws(s: str | None) -> str:
    """Trim and collapse all runs of whitespace (incl. newlines) to one space."""
    if not s:
        return ""
    return _WS.sub(" ", str(s).replace(" ", " ")).strip()


def slugify(s: str) -> str:
    """ASCII kebab-case slug, stable across runs. Empty input -> 'unknown'."""
    s = collapse_ws(s).lower()
    s = unicodedata.normalize("NFKD", s)
    s = s.encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "unknown"


BENGALI_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯",
                               "0123456789")


def bn_digits_to_ascii(s: str) -> str:
    return s.translate(BENGALI_DIGITS)


#: Bengali weekday name -> contract weekday index (0=Sunday .. 6=Saturday).
BN_WEEKDAY = {
    "রবিবার": 0,               # Robibar / Sunday
    "সোমবার": 1,               # Sombar / Monday
    "মঙ্গলবার": 2,   # Mongolbar / Tuesday
    "বুধবার": 3,               # Budhbar / Wednesday
    "বৃহস্পতিবার": 4,  # Brihospotibar
    "শুক্রবার": 5,   # Shukrobar / Friday
    "শনিবার": 6,               # Shonibar / Saturday
}

EN_WEEKDAY = {
    "sunday": 0,
    "monday": 1,
    "tuesday": 2,
    "wednesday": 3,
    "thursday": 4,
    "friday": 5,
    "saturday": 6,
}

WEEKDAY_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday",
                 "Friday", "Saturday"]


def weekday_index_for_date(date_str: str) -> int | None:
    """0=Sunday..6=Saturday for an ISO YYYY-MM-DD string."""
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
    except (ValueError, TypeError):
        return None
    # Python: Monday=0..Sunday=6 -> contract: Sunday=0..Saturday=6
    return (d.weekday() + 1) % 7


# ---------------------------------------------------------------- hashing / io


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def read_json(path: Path, default: Any = None) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path: Path, obj: Any) -> None:
    """Atomic, deterministic write.

    Deterministic formatting means git only sees a diff when the data actually
    changed -- important because the workflow commits on every run and we do not
    want empty churn commits.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2, sort_keys=False)
        fh.write("\n")
    os.replace(tmp, path)


# ---------------------------------------------------------------- http


class FetchResult:
    __slots__ = ("url", "final_url", "status", "content", "content_type",
                 "tls_verified", "error")

    def __init__(self, url: str, final_url: str = "", status: int = 0,
                 content: bytes = b"", content_type: str = "",
                 tls_verified: bool = True, error: str = "") -> None:
        self.url = url
        self.final_url = final_url or url
        self.status = status
        self.content = content
        self.content_type = content_type
        self.tls_verified = tls_verified
        self.error = error

    @property
    def ok(self) -> bool:
        return self.status == 200 and bool(self.content) and not self.error

    @property
    def sha256(self) -> str:
        return sha256_bytes(self.content) if self.content else ""

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return ("<FetchResult status={} tls={} bytes={} {}>"
                .format(self.status, self.tls_verified, len(self.content), self.url))


def http_get(url: str, timeout: int = 90,
             max_bytes: int = 40 * 1024 * 1024) -> FetchResult:
    """GET a URL, falling back to an unverified TLS handshake.

    Many .gov.bd hosts serve an incomplete certificate chain, so a strict verify
    raises SSLError even though the host is the real one. We try verified first
    and only then retry unverified -- and the caller MUST propagate
    ``tls_verified`` into the published provenance so the site can warn users.
    """
    import requests  # lazy so the module imports without deps installed

    headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    last_error = "unreachable"

    for verify in (True, False):
        if not verify:
            try:
                import urllib3
                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            except Exception:  # pragma: no cover
                pass
        try:
            resp = requests.get(url, headers=headers, timeout=timeout,
                                verify=verify, stream=True, allow_redirects=True)
            chunks: list[bytes] = []
            total = 0
            for chunk in resp.iter_content(65536):
                chunks.append(chunk)
                total += len(chunk)
                if total > max_bytes:
                    resp.close()
                    return FetchResult(
                        url, resp.url, resp.status_code, b"",
                        resp.headers.get("Content-Type", ""), verify,
                        "response exceeded {} bytes".format(max_bytes))
            return FetchResult(url, resp.url, resp.status_code, b"".join(chunks),
                               resp.headers.get("Content-Type", ""), verify, "")
        except Exception as exc:  # noqa: BLE001 - any transport failure
            last_error = "{}: {}".format(type(exc).__name__, exc)
            if verify:
                # Retry unverified. Covers the incomplete-chain case and is
                # harmless otherwise.
                continue
            return FetchResult(url, url, 0, b"", "", False, last_error)
    return FetchResult(url, url, 0, b"", "", False, last_error)


def looks_like_pdf(content: bytes) -> bool:
    return content[:5] == b"%PDF-"


def looks_like_html(content: bytes) -> bool:
    head = content[:1024].lstrip().lower()
    return head.startswith(b"<!doctype html") or head.startswith(b"<html")


def ext_for(content: bytes, content_type: str, url: str) -> str:
    if looks_like_pdf(content):
        return "pdf"
    if looks_like_html(content):
        return "html"
    ct = (content_type or "").split(";")[0].strip().lower()
    known = {
        "application/pdf": "pdf",
        "text/html": "html",
        "application/json": "json",
        "text/csv": "csv",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    }
    if ct in known:
        return known[ct]
    tail = url.rsplit(".", 1)[-1].lower()
    if tail.isalnum() and 2 <= len(tail) <= 5:
        return tail
    return "bin"
