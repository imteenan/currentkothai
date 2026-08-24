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

# Stamp the service worker with a hash of the files it caches.
#
# Without this the cache name never changes, so a returning visitor keeps the
# app.js they first downloaded no matter how many times we deploy: the fix ships
# and they never see it. Hash the shell inputs, not the clock, so an unchanged
# site keeps its cache and only a real change invalidates it.
BUILD_ID="$(
  find "$OUT" -type f \( -name '*.html' -o -name '*.js' -o -name '*.css' \)     -not -path "$OUT/data/*" -print0   | sort -z | xargs -0 cat | sha256sum | cut -c1-12
)"
if [ -f "$OUT/sw.js" ]; then
  sed -i "s/__BUILD_ID__/$BUILD_ID/" "$OUT/sw.js"
  grep -q "__BUILD_ID__" "$OUT/sw.js" && { echo "sw.js placeholder not replaced" >&2; exit 1; }
  echo "  service worker build id: $BUILD_ID"
fi

touch "$OUT/.nojekyll"

test -f "$OUT/index.html"
test -f "$OUT/404.html"
test -f "$OUT/data/schedules/index.json"
test -f "$OUT/data/registry/sources.json"

echo "built $OUT"
du -sh "$OUT"
