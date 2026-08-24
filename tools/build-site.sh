#!/usr/bin/env bash
# Assemble the deployable static site into _site/.
# Same steps the Pages workflow runs, so any host can reproduce the layout.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${1:-$ROOT/_site}"

rm -rf "$OUT"
mkdir -p "$OUT/data"
cp -r "$ROOT/apps/web/." "$OUT/"

for d in registry geo schedules validation; do
  [ -d "$ROOT/data/$d" ] && cp -r "$ROOT/data/$d" "$OUT/data/"
done

# Maintainer-only material must not ship.
rm -rf "$OUT/data/schedules/_quarantine"

# Keep the newest seven dated snapshots per utility next to latest.json.
for dir in "$OUT"/data/schedules/*/; do
  [ -d "$dir" ] || continue
  ls -1 "$dir" 2>/dev/null | grep -E '^[0-9]{4}-[0-9]{2}-[0-9]{2}\.json$' \
    | sort -r | tail -n +8 | while read -r old; do rm -f "$dir$old"; done
done

# Stamp the build id into the service worker and every asset URL.
#
# Three caches sit between a deploy and a visitor: the service worker, the HTTP
# cache, and the copy a browser already holds, which keeps whatever max-age it
# was stored with. Only a URL nobody has requested before escapes all three, so
# every script, stylesheet and relative import gets ?v=<id>. See the module for
# the full account; it fails the build rather than shipping an unstamped site.
python tools/stamp_build.py "$OUT"

touch "$OUT/.nojekyll"

test -f "$OUT/index.html"
test -f "$OUT/404.html"
test -f "$OUT/data/schedules/index.json"
test -f "$OUT/data/registry/sources.json"

echo "built $OUT"
du -sh "$OUT"
