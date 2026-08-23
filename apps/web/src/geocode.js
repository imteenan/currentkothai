// Place lookup.
//
// SCALE RULE: the common path never leaves the origin. Forward search and
// reverse lookup are answered from the bundled gazetteer (6,500+ OSM places,
// see gazetteer.js). Nominatim and Photon are volunteer-run and Nominatim caps
// the WHOLE application at ~1 request/second, so they are only ever reached
// when a person explicitly asks to widen a search that found nothing locally.

import * as gaz from './gazetteer.js';

const PHOTON = 'https://photon.komoot.io';
const NOMINATIM = 'https://nominatim.openstreetmap.org';

const BIAS = { lat: 23.78, lon: 90.40 };
const BD_BOX = '88.0,26.7,92.7,20.5'; // minLon,maxLat,maxLon,minLat (Photon order)

const cache = new Map();
const cached = (key, fn) => {
  if (!cache.has(key)) cache.set(key, fn());
  return cache.get(key);
};

const timeout = (ms) => {
  const c = new AbortController();
  setTimeout(() => c.abort(), ms);
  return c.signal;
};

/**
 * Client-side throttle for the remote services. Even opt-in use must not burst:
 * one call per 1.5 s per browser, and a hard ceiling per page view.
 */
const remote = { last: 0, used: 0, MAX: 12, MIN_GAP: 1500 };

function remoteAllowed() {
  const now = Date.now();
  if (remote.used >= remote.MAX) return false;
  if (now - remote.last < remote.MIN_GAP) return false;
  return true;
}
function remoteSpend() { remote.last = Date.now(); remote.used += 1; }

export const remoteBudget = () => ({ used: remote.used, max: remote.MAX });

/* ------------------------------------------------------------ device */

export function deviceLocation({ highAccuracy = true, timeoutMs = 12000 } = {}) {
  return new Promise((resolve, reject) => {
    if (!('geolocation' in navigator)) {
      reject(new GeoError('unsupported', 'This browser does not expose a location API.'));
      return;
    }
    if (!window.isSecureContext) {
      reject(new GeoError('insecure', 'Location needs a secure (https) connection.'));
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (pos) => resolve({
        lat: pos.coords.latitude,
        lon: pos.coords.longitude,
        accuracyM: pos.coords.accuracy,
        source: 'device',
      }),
      (err) => {
        const map = {
          1: ['denied', 'Location permission was denied. You can still type an address.'],
          2: ['unavailable', 'Your device could not determine a position right now.'],
          3: ['timeout', 'Locating took too long. Try again, or type an address.'],
        };
        const [code, msg] = map[err.code] || ['error', err.message || 'Location failed.'];
        reject(new GeoError(code, msg));
      },
      { enableHighAccuracy: highAccuracy, timeout: timeoutMs, maximumAge: 60_000 },
    );
  });
}

export class GeoError extends Error {
  constructor(code, message) { super(message); this.name = 'GeoError'; this.code = code; }
}

/* ------------------------------------------------------------ search */

/** Local-only forward search. This is what the search box calls on every keystroke. */
export async function searchPlaces(query, near = null) {
  const q = query.trim();
  if (q.length < 2) return [];
  return gaz.search(q, 7, near);
}

/**
 * Opt-in widening. Only call this from an explicit user action ("search the
 * wider map"), never automatically.
 */
export async function searchPlacesRemote(query) {
  const q = query.trim();
  if (q.length < 2) return [];
  if (!remoteAllowed()) {
    throw new GeoError('throttled',
      'Wider search is rate-limited to protect the free OpenStreetMap services. Try again in a moment.');
  }
  remoteSpend();
  return cached(`s:${q.toLowerCase()}`, async () => {
    try {
      const url = `${PHOTON}/api/?q=${encodeURIComponent(q)}&limit=6&lang=en` +
        `&lat=${BIAS.lat}&lon=${BIAS.lon}&bbox=${BD_BOX}`;
      const res = await fetch(url, { signal: timeout(7000) });
      if (!res.ok) throw new Error(String(res.status));
      const json = await res.json();
      const out = (json.features ?? [])
        .filter((f) => (f.properties?.countrycode ?? 'BD') === 'BD')
        .map(fromPhoton);
      if (out.length) return out;
      throw new Error('empty');
    } catch {
      return searchNominatim(q);
    }
  });
}

function fromPhoton(f) {
  const p = f.properties ?? {};
  const detail = [p.district, p.city, p.county, p.state].filter(Boolean);
  return {
    label: p.name || p.street || p.city || 'Unnamed place',
    detail: [...new Set(detail)].join(', '),
    lat: f.geometry.coordinates[1],
    lon: f.geometry.coordinates[0],
    kind: p.osm_value || p.type || 'place',
    source: 'photon',
  };
}

async function searchNominatim(q) {
  try {
    const url = `${NOMINATIM}/search?format=jsonv2&addressdetails=1&limit=6&countrycodes=bd` +
      `&q=${encodeURIComponent(q)}`;
    const res = await fetch(url, { signal: timeout(8000), headers: { Accept: 'application/json' } });
    if (!res.ok) throw new Error(String(res.status));
    const json = await res.json();
    return json.map((r) => ({
      label: r.name || r.display_name.split(',')[0],
      detail: r.display_name.split(',').slice(1, 4).join(',').trim(),
      lat: Number(r.lat),
      lon: Number(r.lon),
      kind: r.type,
      source: 'nominatim',
    }));
  } catch {
    return [];
  }
}

/* ----------------------------------------------------------- reverse */

/**
 * Reverse lookup, local by default.
 *
 * The feeder text matcher wants nearby place NAMES, which the gazetteer answers
 * directly and without a network call. `precise: true` opts into Nominatim for a
 * street-level address, and is throttled.
 */
export async function reverseGeocode(lat, lon, { precise = false } = {}) {
  const key = `r:${lat.toFixed(4)},${lon.toFixed(4)}:${precise ? 'p' : 'l'}`;
  return cached(key, async () => {
    if (!precise) {
      const local = await gaz.reverseLocal(lat, lon);
      if (local.locality.length) return local;
      // Nothing within 6 km is genuinely unusual; fall through rather than
      // returning an empty address that would starve the text matcher.
    }
    if (!remoteAllowed()) return gaz.reverseLocal(lat, lon);
    remoteSpend();
    try {
      const url = `${NOMINATIM}/reverse?format=jsonv2&zoom=17&addressdetails=1&lat=${lat}&lon=${lon}`;
      const res = await fetch(url, { signal: timeout(8000), headers: { Accept: 'application/json' } });
      if (!res.ok) throw new Error(String(res.status));
      const j = await res.json();
      const a = j.address ?? {};
      return {
        display: j.display_name ?? '',
        parts: a,
        locality: [a.neighbourhood, a.suburb, a.quarter, a.city_district, a.residential,
          a.road, a.village, a.town, a.city].filter(Boolean),
        district: a.state_district || a.district || a.county || null,
        city: a.city || a.town || a.village || null,
        source: 'nominatim',
      };
    } catch {
      return gaz.reverseLocal(lat, lon);
    }
  });
}

export const ATTRIBUTION = {
  text: 'Place data from OpenStreetMap contributors',
  links: [
    { label: 'OpenStreetMap', url: 'https://www.openstreetmap.org/copyright' },
    { label: 'Photon', url: 'https://photon.komoot.io/' },
    { label: 'Nominatim', url: 'https://nominatim.openstreetmap.org/' },
  ],
};
