/* currentKothai service worker.
 *
 * Strategy, chosen so a cached page can never quietly lie about how fresh the
 * schedule is:
 *   - app shell + vendor + geo  : cache-first (they change only on deploy)
 *   - data/schedules/index.json : network-first (it carries the freshness stamps)
 *   - data/schedules/**         : stale-while-revalidate
 *   - everything else           : network, falling back to cache offline
 *
 * The UI computes staleness from `retrieved_at` inside the payload, not from
 * HTTP freshness, so a stale-served schedule still renders its own stale badge.
 */

const VERSION = 'ck-v2';
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
