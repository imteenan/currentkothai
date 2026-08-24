/* currentKothai service worker.
 *
 * Strategy, chosen so a cached page can never quietly lie about how fresh the
 * schedule is:
 *   - navigations              : network-first (so a deploy is seen immediately)
 *   - app shell + vendor + geo : cache-first WITHIN a build, keyed by BUILD_ID
 *   - data/schedules/index.json: network-first (it carries the freshness stamps)
 *   - data/schedules/**        : stale-while-revalidate
 *   - everything else          : network, falling back to cache offline
 *
 * The UI computes staleness from `retrieved_at` inside the payload, not from
 * HTTP freshness, so a stale-served schedule still renders its own stale badge.
 *
 * WHY BUILD_ID IS STAMPED AND NOT WRITTEN BY HAND
 * ----------------------------------------------
 * This constant used to be a literal, 'ck-v2', edited by hand and therefore
 * never edited at all. `activate` only deletes caches whose key does not start
 * with it, so an unchanged VERSION deleted nothing, and cacheFirst kept serving
 * the app.js a visitor happened to download first. Every returning user was
 * pinned to their first-ever bundle for good: the site was fixed on the server
 * and permanently broken in their browser, with no way to tell from the outside.
 *
 * tools/build-site.sh replaces the placeholder below with a hash of the shell
 * files on every build, so a changed file is a changed cache name is a clean
 * re-fetch. Left unstamped (local `python tools/serve.py`), it falls back to a
 * per-load value so nothing sticks while developing.
 */

//: Replaced at build time. Keep the placeholder exactly as written.
const BUILD_ID = '__BUILD_ID__';
const VERSION = BUILD_ID.startsWith('__') ? `ck-dev-${Date.now()}` : `ck-${BUILD_ID}`;
const SHELL = `${VERSION}-shell`;
const DATA = `${VERSION}-data`;

const SHELL_ASSETS = [
  './',
  './index.html',
  './sources.html',
  './about.html',
  './styles/tokens.css',
  './styles/base.css',
  './styles/components.css',
  './src/app.js',
  './src/util.js',
  './src/data.js',
  './src/geo.js',
  './src/geocode.js',
  './src/gazetteer.js',
  './src/schedule.js',
  './src/confidence.js',
  './src/render.js',
  './src/map.js',
  './src/skyline.js',
  './icon.svg',
  './manifest.webmanifest',
  './vendor/maplibre-gl.js',
  './vendor/maplibre-gl.css',
];

self.addEventListener('install', (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(SHELL);
    // addAll is atomic: one 404 would throw away the whole install, so add
    // individually and tolerate a missing optional file.
    await Promise.all(SHELL_ASSETS.map((u) => cache.add(u).catch(() => {})));
    await self.skipWaiting();
  })());
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter((k) => !k.startsWith(VERSION)).map((k) => caches.delete(k)));
    await self.clients.claim();
    // Tell any open tab that the code under it just changed. Claiming alone
    // swaps the controller but leaves the already-parsed old modules running.
    for (const c of await self.clients.matchAll({ type: 'window' })) {
      c.postMessage({ type: 'ck-updated', version: VERSION });
    }
  })());
});

const isData = (url) => url.pathname.includes('/data/');
const isIndex = (url) => url.pathname.endsWith('/schedules/index.json');

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  // Never intercept another origin: map tiles must go straight to the network,
  // and caching a volunteer service's responses would be rude.
  if (url.origin !== self.location.origin) return;

  // A navigation is the one request that must never come from a stale cache:
  // it is what pulls in the current sw.js and the current script tags, so
  // serving yesterday's copy strands the visitor on yesterday's build.
  if (request.mode === 'navigate') { event.respondWith(networkFirst(request)); return; }
  if (isIndex(url)) { event.respondWith(networkFirst(request)); return; }
  if (isData(url)) { event.respondWith(staleWhileRevalidate(request)); return; }
  event.respondWith(cacheFirst(request));
});

async function cacheFirst(request) {
  const cached = await caches.match(request);
  if (cached) return cached;
  try {
    const res = await fetch(request);
    if (res.ok) (await caches.open(SHELL)).put(request, res.clone());
    return res;
  } catch (err) {
    const fallback = await caches.match('./index.html');
    if (fallback && request.mode === 'navigate') return fallback;
    throw err;
  }
}

async function networkFirst(request) {
  try {
    const res = await fetch(request);
    if (res.ok) (await caches.open(DATA)).put(request, res.clone());
    return res;
  } catch (err) {
    const cached = await caches.match(request);
    if (cached) return cached;
    throw err;
  }
}

async function staleWhileRevalidate(request) {
  const cache = await caches.open(DATA);
  const cached = await cache.match(request);
  const network = fetch(request).then((res) => {
    if (res.ok) cache.put(request, res.clone());
    return res;
  }).catch(() => null);
  return cached || network || fetch(request);
}
