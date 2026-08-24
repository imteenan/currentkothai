"""Stamp a build id into the service worker and every asset URL.

    python tools/stamp_build.py _site

Three caches sat between a deploy and a visitor, and each had to be fixed
before the next became visible:

  1. The service worker's cache key was a hand-written literal that was never
     changed, so `activate` purged nothing.
  2. `/src/*` was served with `max-age=604800` on filenames that never change,
     which pinned the bundle for a week independently of the worker.
  3. Fixing (2) does not help anyone who already has a copy: a cached response
     keeps the max-age it was stored with. Every visitor from the previous week
     holds a bundle that will not revalidate until it expires on its own.

Only a URL the browser has never requested escapes all three. So the build id
goes into the query string of every stylesheet, script and relative ES import.
Filenames stay put, which keeps the source tree and the source maps readable;
nothing outside this file needs to know that versioning happens at all.

Written in Python rather than inline sed because the same substitution has to
pass through a heredoc, a shell string and a regex, and the backreferences do
not survive the trip.
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

#: Files whose bytes decide the id. Data is excluded: a schedule refresh must
#: not invalidate the code cache, or every ingest run costs every visitor a
#: full re-download.
HASHED_SUFFIXES = (".html", ".js", ".css")

#: `from './util.js'` and `from "./util.js"`, relative specifiers only.
IMPORT_RE = re.compile(r"""(from\s+['"]\./[A-Za-z0-9_./-]+\.js)(['"])""")

#: `src="src/app.js"`, `href="styles/base.css"`.
HTML_RE = re.compile(
    r"""((?:src|href)=["'](?:src|styles|vendor)/[A-Za-z0-9_./-]+\.(?:js|css))(["'])""")

#: The service worker's precache list, which must name the same URLs the page
#: requests or it warms the cache with copies nothing reads.
SW_ASSET_RE = re.compile(
    r"""(['"]\./(?:src|styles|vendor)/[A-Za-z0-9_./-]+\.(?:js|css))(['"])""")

PLACEHOLDER = "__BUILD_ID__"


def build_id(root: Path) -> str:
    """A hash of the shell, stable for identical input and nothing else."""
    digest = hashlib.sha256()
    files = sorted(
        p for p in root.rglob("*")
        if p.is_file() and p.suffix in HASHED_SUFFIXES
        and "data" not in p.relative_to(root).parts
    )
    for path in files:
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


def _sub(path: Path, pattern: re.Pattern, ident: str) -> int:
    text = path.read_text(encoding="utf-8")
    stamped, n = pattern.subn(lambda m: "%s?v=%s%s" % (m.group(1), ident, m.group(2)), text)
    if n:
        path.write_text(stamped, encoding="utf-8")
    return n


def stamp(root: Path) -> str:
    ident = build_id(root)

    for js in (root / "src").glob("*.js"):
        _sub(js, IMPORT_RE, ident)
    for html in root.glob("*.html"):
        _sub(html, HTML_RE, ident)

    sw = root / "sw.js"
    if sw.exists():
        _sub(sw, SW_ASSET_RE, ident)
        text = sw.read_text(encoding="utf-8").replace(PLACEHOLDER, ident)
        sw.write_text(text, encoding="utf-8")
        if PLACEHOLDER in text:
            raise SystemExit("sw.js still carries %s" % PLACEHOLDER)

    # Refuse to ship a build that silently did nothing. A no-op here puts every
    # returning visitor straight back on a stale bundle, which is the failure
    # this whole file exists to prevent, and it would look like a clean build.
    index = root / "index.html"
    if index.exists() and ("app.js?v=%s" % ident) not in index.read_text(encoding="utf-8"):
        raise SystemExit("index.html was not versioned; stamping did not apply")

    return ident


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        return 2
    root = Path(argv[1])
    if not root.is_dir():
        raise SystemExit("no such directory: %s" % root)
    print("  build id: %s (sw.js and every asset URL)" % stamp(root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
