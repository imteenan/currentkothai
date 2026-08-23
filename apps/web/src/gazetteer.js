// Offline place search. Decodes data/geo/gazetteer.json and answers both forward
// search and nearest-place reverse lookup entirely in the browser.
//
// This exists because Nominatim's usage policy caps the WHOLE application at
// roughly one request per second. Calling it per keystroke breaks at a few
// hundred concurrent visitors and abuses a volunteer service. Everything here
// runs locally, so the common path makes zero third-party requests.

import { getJSON } from './data.js';
import { haversineKm } from './util.js';

let indexPromise = null;

/** Google encoded-polyline decode, matching the builder's 1e5 factor. */
function decodePolyline(str, factor = 1e5) {
  const out = [];
  let i = 0, lat = 0, lon = 0;
  while (i < str.length) {
    let result = 0, shift = 0, b;
    do { b = str.charCodeAt(i++) - 63; result |= (b & 0x1f) << shift; shift += 5; } while (b >= 0x20);
    lat += (result & 1) ? ~(result >> 1) : (result >> 1);
    result = 0; shift = 0;
    do { b = str.charCodeAt(i++) - 63; result |= (b & 0x1f) << shift; shift += 5; } while (b >= 0x20);
    lon += (result & 1) ? ~(result >> 1) : (result >> 1);
    out.push([lat / factor, lon / factor]);
  }
  return out;
}

/**
 * Fold transliteration drift so "Kollyanpur", "Kalyanpur" and "Kallyanpur" all
 * collide. Deliberately aggressive — recall matters more than precision when the
 * result list is short and the user picks from it.
 */
export function foldLatin(s) {
  return String(s || '')
    .toLowerCase()
    .normalize('NFKD').replace(/[̀-ͯ]/g, '')
    .replace(/[^a-z0-9ঀ-৿ ]+/g, ' ')
    .replace(/\bph\b/g, 'f')
    .replace(/kh/g, 'k').replace(/gh/g, 'g').replace(/th/g, 't')
    .replace(/dh/g, 'd').replace(/bh/g, 'b').replace(/sh/g, 's')
    .replace(/ch/g, 'c').replace(/ph/g, 'f')
    .replace(/([a-z])\1+/g, '$1')        // kallyanpur -> kalyanpur
    .replace(/[wy]/g, '')                 // shyamoli -> shamoli
    // Romanised Bengali vowels are wildly inconsistent (Uttara/Uttora,
    // Mohakhali/Mahakhali), so collapse every vowel to one class. This
    // over-matches on purpose: the list is short and the reader picks.
    .replace(/[aeiou]+/g, 'a')
    .replace(/\s+/g, ' ')
    .trim();
}

async function build() {
  const raw = await getJSON('geo/gazetteer.json', null);
  if (!raw || !raw.count) {
    return { ok: false, count: 0, entries: [], reason: 'gazetteer unavailable' };
  }
  const names = raw.n.split('|');
  const bangla = (raw.b || '').split('|');
  const coords = decodePolyline(raw.p, Math.pow(10, raw.precision ?? 5));
  const alphabet = raw.alphabet;
  const kinds = raw.kinds || [];
  const districts = raw.districts || [];

  const entries = new Array(raw.count);
  for (let i = 0; i < raw.count; i++) {
    const [lat, lon] = coords[i] || [0, 0];
    const dIdx = alphabet.indexOf(raw.d[i]);
    entries[i] = {
      name: names[i] || '',
      nameBn: bangla[i] || '',
      kind: kinds[alphabet.indexOf(raw.k[i])] || 'place',
      district: dIdx >= 0 ? districts[dIdx] : null,
      lat, lon,
      fold: foldLatin(names[i]),
    };
  }

  // Bucket by first folded character so a keystroke scans a slice, not 6,533.
  const buckets = new Map();
  entries.forEach((e, i) => {
    const c = e.fold[0] || '?';
    if (!buckets.has(c)) buckets.set(c, []);
    buckets.get(c).push(i);
  });

  return { ok: true, count: raw.count, entries, buckets, meta: raw };
}

export function ready() {
  if (!indexPromise) indexPromise = build();
  return indexPromise;
}

/** Bigger settlements should outrank a tiny para of the same name. */
const KIND_WEIGHT = {
  city: 30, town: 22, upazila: 18, borough: 14, suburb: 12,
  union: 10, quarter: 8, neighbourhood: 6, village: 4, place: 2,
};

/**
 * Local forward search. Returns the same shape as the remote geocoder so the
 * caller does not care where a result came from.
 */
export async function search(query, limit = 7, near = null) {
  const idx = await ready();
  if (!idx.ok) return [];
  const q = foldLatin(query);
  if (q.length < 2) return [];

  const bnQuery = /[ঀ-৿]/.test(query);
  const raw = query.trim();
  const scored = [];

  // Scan the first-letter bucket plus, for short queries, everything (cheap
  // enough at this size and avoids missing "sector 7" style inputs).
  const candidates = q.length <= 3 || bnQuery
    ? idx.entries.keys()
    : (idx.buckets.get(q[0]) || []);

  for (const i of candidates) {
    const e = idx.entries[i];
    let score = 0;
    if (bnQuery) {
      if (!e.nameBn) continue;
      if (e.nameBn === raw) score = 100;
      else if (e.nameBn.startsWith(raw)) score = 70;
      else if (e.nameBn.includes(raw)) score = 40;
      else continue;
    } else {
      if (e.fold === q) score = 100;
      else if (e.fold.startsWith(q)) score = 70;
      else if (e.fold.includes(` ${q}`)) score = 55;
      else if (e.fold.includes(q)) score = 35;
      else continue;
    }
    score += KIND_WEIGHT[e.kind] || 0;
    score -= Math.min(10, Math.abs(e.fold.length - q.length) / 2);
    // Proximity bias: to the caller's point if known, otherwise toward Dhaka,
    // because Dhaka is where feeder-level schedule data actually exists.
    const anchor = near || { lat: 23.78, lon: 90.40 };
    const km = haversineKm(anchor, { lat: e.lat, lon: e.lon });
    score += 26 * Math.exp(-km / 45);
    scored.push({ e, score });
  }

  scored.sort((a, b) => b.score - a.score);
  const seen = new Set();
  const out = [];
  for (const { e } of scored) {
    const key = `${e.name}|${e.district}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({
      label: e.name || e.nameBn,
      detail: [e.kind, e.district].filter(Boolean).join(' · '),
      lat: e.lat, lon: e.lon,
      kind: e.kind,
      district: e.district,
      nameBn: e.nameBn,
      source: 'local',
    });
    if (out.length >= limit) break;
  }
  return out;
}

/**
 * Nearest known places to a point — the local stand-in for reverse geocoding.
 * The feeder text matcher wants nearby place NAMES, which is exactly this.
 */
export async function nearest(lat, lon, limit = 8, maxKm = 6) {
  const idx = await ready();
  if (!idx.ok) return [];
  // Pre-filter by a crude degree box before doing any trig.
  const dLat = maxKm / 110.6;
  const dLon = maxKm / (111.3 * Math.cos(lat * Math.PI / 180));
  const near = [];
  for (const e of idx.entries) {
    if (Math.abs(e.lat - lat) > dLat || Math.abs(e.lon - lon) > dLon) continue;
    const km = haversineKm({ lat, lon }, { lat: e.lat, lon: e.lon });
    if (km <= maxKm) near.push({ ...e, km });
  }
  near.sort((a, b) => a.km - b.km);
  return near.slice(0, limit);
}

/** A reverse-geocode-shaped result assembled purely from local data. */
export async function reverseLocal(lat, lon) {
  const near = await nearest(lat, lon, 8, 6);
  if (!near.length) {
    return { display: '', parts: {}, locality: [], district: null, city: null, source: 'local-none' };
  }
  const locality = [...new Set(near.map((n) => n.name).filter(Boolean))];
  const district = near.find((n) => n.district)?.district || null;
  const city = near.find((n) => ['city', 'town'].includes(n.kind))?.name || null;
  const head = near[0];
  return {
    display: [head.name, district].filter(Boolean).join(', '),
    parts: { neighbourhood: head.name, district },
    locality,
    district,
    city,
    nearest: near,
    source: 'local',
  };
}

export async function status() {
  const idx = await ready();
  return { ok: idx.ok, count: idx.count, generatedAt: idx.meta?.generated_at || null };
}
