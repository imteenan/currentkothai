// Small shared helpers. No dependencies, no build step.

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

/** Create an element. `attrs.html` sets innerHTML, `attrs.text` sets textContent. */
export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'html') node.innerHTML = v;
    else if (k === 'text') node.textContent = v;
    else if (k === 'class') node.className = v;
    else if (k === 'style' && typeof v === 'object') Object.assign(node.style, v);
    else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? '' : String(v));
  }
  for (const c of [].concat(children)) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c.nodeType ? c : document.createTextNode(String(c)));
  }
  return node;
}

export const esc = (s) =>
  String(s ?? '').replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

export const clamp = (n, lo, hi) => Math.min(hi, Math.max(lo, n));

/** Great-circle distance in kilometres. */
export function haversineKm(a, b) {
  const R = 6371;
  const toRad = (d) => (d * Math.PI) / 180;
  const dLat = toRad(b.lat - a.lat);
  const dLon = toRad(b.lon - a.lon);
  const la1 = toRad(a.lat), la2 = toRad(b.lat);
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(la1) * Math.cos(la2) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}

export const fmtKm = (km) => (km < 1 ? `${Math.round(km * 1000)} m` : `${km.toFixed(km < 10 ? 1 : 0)} km`);

/** "2 hours ago" / "in 45 minutes" from an ISO string or Date. */
export function relTime(input, now = new Date()) {
  if (!input) return 'unknown';
  const then = input instanceof Date ? input : new Date(input);
  if (Number.isNaN(+then)) return 'unknown';
  const mins = Math.round((then - now) / 60000);
  const abs = Math.abs(mins);
  const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' });
  if (abs < 60) return rtf.format(mins, 'minute');
  if (abs < 60 * 36) return rtf.format(Math.round(mins / 60), 'hour');
  return rtf.format(Math.round(mins / 1440), 'day');
}

/** "3h 12m" from a minute count. */
export function fmtDuration(mins) {
  const m = Math.max(0, Math.round(mins));
  const h = Math.floor(m / 60);
  const r = m % 60;
  if (h && r) return `${h}h ${r}m`;
  if (h) return `${h}h`;
  return `${r}m`;
}

export function debounce(fn, ms = 260) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

/** localStorage that never throws (Safari private mode, disabled storage). */
export const store = {
  get(key, fallback = null) {
    try { const v = localStorage.getItem(key); return v === null ? fallback : JSON.parse(v); }
    catch { return fallback; }
  },
  set(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); return true; } catch { return false; }
  },
  del(key) { try { localStorage.removeItem(key); } catch { /* ignore */ } },
};

let toastHost;
export function toast(message, ms = 2600) {
  if (!toastHost) {
    toastHost = el('div', { class: 'toast-host', role: 'status', 'aria-live': 'polite' });
    document.body.append(toastHost);
  }
  const t = el('div', { class: 'toast', text: message });
  toastHost.append(t);
  setTimeout(() => { t.style.opacity = '0'; t.style.transition = 'opacity .3s'; setTimeout(() => t.remove(), 320); }, ms);
}

/** Inline SF-Symbols-flavoured icon set. */
export const icon = (name, cls = '') => {
  const p = ICONS[name] || ICONS.info;
  // `ic` gives every inline icon an intrinsic 1em box; without it an SVG with no
  // width/height fills its container.
  return `<svg class="ic ${cls}" viewBox="0 0 24 24" aria-hidden="true" focusable="false">${p}</svg>`;
};

const ICONS = {
  bolt: '<path d="M13.4 2.1a.6.6 0 0 1 1.05.53l-1.3 6.06h4.4c.62 0 .95.73.54 1.2l-8.5 9.8a.6.6 0 0 1-1.04-.52l1.3-6.07H5.45a.7.7 0 0 1-.53-1.16l8.48-9.84Z"/>',
  location: '<path d="M12 2a7 7 0 0 0-7 7c0 5 6.3 12.4 6.57 12.7a.58.58 0 0 0 .86 0C12.7 21.4 19 14 19 9a7 7 0 0 0-7-7Zm0 9.6A2.6 2.6 0 1 1 14.6 9 2.6 2.6 0 0 1 12 11.6Z"/>',
  crosshair: '<path d="M12 2a1 1 0 0 1 1 1v1.06A8.01 8.01 0 0 1 19.94 11H21a1 1 0 1 1 0 2h-1.06A8.01 8.01 0 0 1 13 19.94V21a1 1 0 1 1-2 0v-1.06A8.01 8.01 0 0 1 4.06 13H3a1 1 0 1 1 0-2h1.06A8.01 8.01 0 0 1 11 4.06V3a1 1 0 0 1 1-1Zm0 4a6 6 0 1 0 0 12 6 6 0 0 0 0-12Zm0 3.5a2.5 2.5 0 1 1 0 5 2.5 2.5 0 0 1 0-5Z"/>',
  search: '<path d="M10.5 3a7.5 7.5 0 1 1-4.6 13.42l-3.2 3.2a1 1 0 0 1-1.42-1.42l3.2-3.2A7.5 7.5 0 0 1 10.5 3Zm0 2a5.5 5.5 0 1 0 0 11 5.5 5.5 0 0 0 0-11Z"/>',
  clock: '<path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20Zm.9 4.6v5.02l3.6 2.14a.9.9 0 1 1-.92 1.55l-4.04-2.4a.9.9 0 0 1-.44-.78V6.6a.9.9 0 0 1 1.8 0Z"/>',
  warn: '<path d="M12 2.6c.5 0 .96.27 1.2.7l9 15.9a1.4 1.4 0 0 1-1.2 2.1H3a1.4 1.4 0 0 1-1.2-2.1l9-15.9c.24-.43.7-.7 1.2-.7Zm0 5.3a1 1 0 0 0-1 1.05l.25 4.6a.75.75 0 0 0 1.5 0l.25-4.6A1 1 0 0 0 12 7.9Zm0 8.1a1.15 1.15 0 1 0 0 2.3 1.15 1.15 0 0 0 0-2.3Z"/>',
  info: '<path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20Zm0 4.2a1.3 1.3 0 1 1 0 2.6 1.3 1.3 0 0 1 0-2.6Zm1.1 11.3h-2.2v-6.9h2.2Z"/>',
  link: '<path d="M10.6 13.4a1 1 0 0 1 0-1.42l3.54-3.54a1 1 0 1 1 1.42 1.42l-3.54 3.54a1 1 0 0 1-1.42 0ZM8.1 18.9a4.6 4.6 0 0 1 0-6.5l2.1-2.1a1 1 0 1 1 1.42 1.42l-2.1 2.1a2.6 2.6 0 0 0 3.66 3.66l2.1-2.1a1 1 0 1 1 1.42 1.42l-2.1 2.1a4.6 4.6 0 0 1-6.5 0Zm7.7-14.3a4.6 4.6 0 0 1 3.25 7.85l-2.1 2.1a1 1 0 1 1-1.42-1.42l2.1-2.1a2.6 2.6 0 0 0-3.66-3.66l-2.1 2.1A1 1 0 1 1 10.45 8l2.1-2.1a4.58 4.58 0 0 1 3.25-1.3Z"/>',
  bell: '<path d="M12 2.2a5.8 5.8 0 0 1 5.8 5.8v3.1l1.4 2.6a1.1 1.1 0 0 1-.97 1.63H5.77a1.1 1.1 0 0 1-.97-1.63l1.4-2.6V8A5.8 5.8 0 0 1 12 2.2Zm2.4 14.9a2.4 2.4 0 0 1-4.8 0Z"/>',
  shield: '<path d="M12 2.3 4.6 5.1v6.1c0 4.6 3.1 8.8 7.4 10.5 4.3-1.7 7.4-5.9 7.4-10.5V5.1Zm-.9 12.5-3-3 1.3-1.3 1.7 1.7 4-4 1.3 1.3Z"/>',
  doc: '<path d="M6.4 2.2h7l4.6 4.6v12.4a2.6 2.6 0 0 1-2.6 2.6H6.4a2.6 2.6 0 0 1-2.6-2.6V4.8a2.6 2.6 0 0 1 2.6-2.6Zm6.4 1.9v3.5h3.5Z"/>',
  calendar: '<path d="M7 2.4a1 1 0 0 1 1 1v1h8v-1a1 1 0 1 1 2 0v1h.4A2.6 2.6 0 0 1 21 7v11.4a2.6 2.6 0 0 1-2.6 2.6H5.6A2.6 2.6 0 0 1 3 18.4V7a2.6 2.6 0 0 1 2.6-2.6H6v-1a1 1 0 0 1 1-1ZM5 9.6v8.8c0 .33.27.6.6.6h12.8a.6.6 0 0 0 .6-.6V9.6Z"/>',
  moon: '<path d="M20.3 14.4A8.6 8.6 0 0 1 9.6 3.7a.7.7 0 0 0-.93-.83 9.7 9.7 0 1 0 12.46 12.46.7.7 0 0 0-.83-.93Z"/>',
  sun: '<path d="M12 6.6a5.4 5.4 0 1 1 0 10.8 5.4 5.4 0 0 1 0-10.8Zm0-5.1a1 1 0 0 1 1 1v1.6a1 1 0 1 1-2 0V2.5a1 1 0 0 1 1-1Zm0 18.4a1 1 0 0 1 1 1v1.6a1 1 0 1 1-2 0v-1.6a1 1 0 0 1 1-1ZM1.5 12a1 1 0 0 1 1-1h1.6a1 1 0 1 1 0 2H2.5a1 1 0 0 1-1-1Zm18.4 0a1 1 0 0 1 1-1h1.6a1 1 0 1 1 0 2h-1.6a1 1 0 0 1-1-1ZM4.6 4.6a1 1 0 0 1 1.42 0l1.13 1.13A1 1 0 0 1 5.73 7.15L4.6 6.02a1 1 0 0 1 0-1.42Zm12.25 12.25a1 1 0 0 1 1.42 0l1.13 1.13a1 1 0 0 1-1.42 1.42l-1.13-1.13a1 1 0 0 1 0-1.42Zm2.55-12.25a1 1 0 0 1 0 1.42l-1.13 1.13a1 1 0 1 1-1.42-1.42L18 4.6a1 1 0 0 1 1.4 0ZM7.15 16.85a1 1 0 0 1 0 1.42L6.02 19.4A1 1 0 0 1 4.6 17.98l1.13-1.13a1 1 0 0 1 1.42 0Z"/>',
  external: '<path d="M14 3.6a1 1 0 0 1 1-1h5.4a1 1 0 0 1 1 1V9a1 1 0 1 1-2 0V6.02l-7.3 7.3a1 1 0 0 1-1.4-1.42l7.28-7.3H15a1 1 0 0 1-1-1ZM4.6 6.2h5a1 1 0 1 1 0 2H5.6v10.2h10.2V14.4a1 1 0 1 1 2 0v4.6a1.6 1.6 0 0 1-1.6 1.6H5a1.6 1.6 0 0 1-1.6-1.6V7.8A1.6 1.6 0 0 1 5 6.2Z"/>',
  phone: '<path d="M6.6 2.7c.7 0 1.33.42 1.6 1.06l1.16 2.7a1.7 1.7 0 0 1-.4 1.94l-1.1 1a12.6 12.6 0 0 0 5.74 5.74l1-1.1a1.7 1.7 0 0 1 1.94-.4l2.7 1.16c.64.27 1.06.9 1.06 1.6v2.5a2 2 0 0 1-2.16 2A17.6 17.6 0 0 1 2.6 6.86 2 2 0 0 1 4.6 4.7Z"/>',
  check: '<path d="M20.3 6.3a1 1 0 0 1 0 1.4l-9.6 9.6a1 1 0 0 1-1.4 0l-4.6-4.6a1 1 0 1 1 1.4-1.4l3.9 3.9 8.9-8.9a1 1 0 0 1 1.4 0Z"/>',
  layers: '<path d="M12 2.4 22 8l-10 5.6L2 8Zm7.7 8.4 2.3 1.3-10 5.6-10-5.6 2.3-1.3L12 15.1Zm0 4.5 2.3 1.3-10 5.6-10-5.6 2.3-1.3L12 19.6Z"/>',
};
