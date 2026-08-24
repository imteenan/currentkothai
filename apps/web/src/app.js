// Orchestrator: location -> distributor -> feeders near you -> hours.

import { $, el, esc, debounce, store, toast, icon, fmtDuration, haversineKm, relTime as relTimeOf } from './util.js';
import { bootstrap, load, findUtility, findSource, getJSON } from './data.js';
import { featuresAt, centroidOf, isInBangladesh } from './geo.js';
import { deviceLocation, searchPlaces, searchPlacesRemote, reverseGeocode, GeoError } from './geocode.js';
import { rankDivisions, rankFeeders, scheduleAgreement } from './confidence.js';
import { dhakaNow, normaliseWindows, evaluateDay, claimsForWeekday, buildICS } from './schedule.js';
import {
  renderStrip, renderAnswer, renderFeeders, renderEvidence, renderAreas,
  renderProvenance, renderFeedHealth, renderAlertCards,
} from './render.js';
import { CoverageMap } from './map.js';

const WEEKDAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
const LS = { last: 'ck.lastLocation', saved: 'ck.savedLocations' };

const state = {
  data: null, dpdcZones: null, point: null, reverse: null, utilityId: null,
  territoryHits: [], schedule: null, candidates: [], evaluations: [], selected: 0,
  ranking: null, feeders: null, map: null,
};

function readHash() {
  const m = /at=(-?\d+\.?\d*),(-?\d+\.?\d*)/.exec(location.hash);
  return m ? { lat: Number(m[1]), lon: Number(m[2]), source: 'search' } : null;
}

/* ============================================================ boot */

async function boot() {
  state.data = await bootstrap();
  getJSON('registry/dpdc-zones.json', null).then((z) => { state.dpdcZones = z; });

  $('#feed-health').innerHTML = renderFeedHealth(state.data.index);
  $('#alerts-grid').innerHTML = renderAlertCards(state.data.alerts, state.data.utilities);
  renderFooterOfficial();
  renderSaved();
  initMap();
  wireSearch();

  $('#btn-locate')?.addEventListener('click', useDeviceLocation);
  $('#btn-map-locate')?.addEventListener('click', useDeviceLocation);
  // A shared link must win over whatever this browser looked at last; both are
  // async, so choosing here avoids the two racing.
  const deep = readHash();
  const last = store.get(LS.last);
  if (deep) resolve(deep);
  else if (last?.lat) resolve({ ...last, source: 'restored' });

  setInterval(() => { if (state.point) renderResult(); }, 60_000);
}

function renderFooterOfficial() {
  const ul = $('#footer-official');
  if (!ul) return;
  const rows = state.data.utilities.filter((u) => u.website).slice(0, 5)
    .map((u) => `<li><a href="${esc(u.website)}" target="_blank" rel="noopener">${esc(u.name)}</a></li>`);
  rows.unshift('<li><a href="tel:16999">Call 16999</a></li>');
  ul.innerHTML = rows.join('');
}

/* ============================================================ map */

/**
 * Every distributor that has zone geometry, and where to find it.
 *
 * This exists because 'DESCO' was written into the map code in four separate
 * places, and each one had to be found and changed by hand when DPDC arrived.
 * One of them was missed: updateZoneLoads() kept loading only the DESCO feed,
 * so 423 DPDC claims never reached a layer. Adding a distributor is now one row
 * here, and anything the table does not list simply does not draw.
 */
/**
 * One place that decides what each distributor looks like.
 *
 * DPDC used to be #5e5ce6, a soft indigo that sat 7.8 dE from DESCO's blue on
 * the basemap - close enough to read as "not really there". Simulated, it is
 * far worse than that: 2.1 dE under deuteranopia and 1.7 under protanopia, so
 * for roughly one man in twelve the two distributors were the same colour.
 * Deep violet at a stronger fill measures 27.5 / 16.0 / 21.4 on the same three,
 * which is the largest separation of every candidate tried.
 */
const ZONE_LAYERS = [
  { utility: 'DESCO', geo: 'descoDivisions', layer: 'divisions', color: '#0071e3' },
  { utility: 'DPDC', geo: 'dpdcZones', layer: 'dpdc-zones', color: '#6d28d9' },
];

//: Amber belongs to the ring around the covered area, which is the one thing on
//: this map every reader needs to find. BPDB shipped as #ff9f0a and would have
//: put two amber swatches next to each other in the legend, so it moves to
//: slate: it is a link-only utility with no schedule behind it, and nothing is
//: drawn in its colour except a country-sized dashed outline.
const SERVICE_AREA_COLOUR = '#f59e0b';

//: Applied to the territory outlines and the legend so one change moves all
//: three, rather than the colour being restated in generated GeoJSON.
const UTILITY_COLOURS = {
  ...Object.fromEntries(ZONE_LAYERS.map((z) => [z.utility, z.color])),
  BPDB: '#64748b',
};

/** Overwrite the colour a generated file carries, where we have an opinion. */
function recolour(fc) {
  for (const f of fc?.features || []) {
    const c = UTILITY_COLOURS[String(f.properties?.utility || '').toUpperCase()];
    if (c) f.properties.color_hex = c;
  }
  return fc;
}

function initMap() {
  const container = $('#map');
  if (!container) return;
  state.map = new CoverageMap(container, {
    onPick: (p) => {
      resolve({ ...p, source: 'map' });
    },
  });
  state.map.init();
  // Debug handle. Everything on it is already public data the page fetched;
  // it exists so layer and join state can be inspected from the console, which
  // is how the missing DPDC zone layer was finally pinned down.
  window.__ck = state;
  state.map.onTick = () => updateZoneLoads();

  const { territories, descoOffices, serviceArea } = state.data.geo;
  recolour(territories);
  for (const z of ZONE_LAYERS) recolour(state.data.geo[z.geo]);
  // "Distributors" is the tab everyone lands on, and it draws this layer, not
  // the zone cells. Raising the zone fill alone left DPDC just as faint as
  // before on the only view most people ever see.
  //
  // The two distributors we read get a fill you can actually find; NESCO,
  // WZPDCL and BPDB stay faint on purpose. Their territories are the size of
  // the country and carry no schedule, so at equal weight they would flood the
  // map and imply coverage that does not exist.
  state.map.addPolygonLayer('territories', territories, {
    colorProperty: 'color_hex',
    fillOpacity: ['match', ['get', 'utility'],
      ZONE_LAYERS.map((z) => z.utility), 0.26, 0.07],
    lineWidth: ['match', ['get', 'utility'],
      ZONE_LAYERS.map((z) => z.utility), 2.0, 1.2],
  });
  // One glowing ring around the whole covered area, drawn before the zone
  // layers so it sits underneath them.
  state.map.addServiceArea('service-area', serviceArea, { color: SERVICE_AREA_COLOUR });

  for (const z of ZONE_LAYERS) {
    state.map.addPolygonLayer(z.layer, state.data.geo[z.geo], {
      colorProperty: 'fill_hex', color: z.color, fillOpacity: 0.30, visible: false,
    });
  }
  state.map.addPointLayer('offices', descoOffices, { color: '#0071e3', visible: false });
  load.geo.districts().then((fc) => state.map.addPolygonLayer('districts', fc, {
    color: '#707070', fillOpacity: 0.03, lineWidth: 0.6, visible: false,
  }));

  // Frame the area we actually have schedules for, not the link-only
  // utilities, whose territories span the whole country and would zoom the city
  // out to nothing.
  state.map.fitTo(boundsOf(territories, ZONE_LAYERS.map((z) => z.utility)));

  renderMapLegend(territories);
  updateZoneLoads();

  $('#map-layer-switch')?.addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-layer]');
    if (!btn) return;
    const chosen = btn.dataset.layer;
    for (const b of $('#map-layer-switch').querySelectorAll('button')) {
      b.setAttribute('aria-pressed', String(b === btn));
    }
    state.map.setLayerVisible('territories', chosen === 'territories');
    for (const z of ZONE_LAYERS) state.map.setLayerVisible(z.layer, chosen === 'zones');
    state.map.setLayerVisible('offices', chosen === 'zones');
    state.map.setLayerVisible('districts', chosen === 'districts');
  });

  $('#btn-pitch')?.addEventListener('click', (e) => {
    const on = state.map.togglePitch();
    e.currentTarget.setAttribute('aria-pressed', String(on));
    e.currentTarget.textContent = on ? '3D' : '2D';
  });
}

async function updateZoneLoads() {
  if (!state.map) return;
  const now = dhakaNow();

  const feeds = await Promise.all(
    ZONE_LAYERS.map((z) => load.schedule(z.utility).catch(() => null)));

  /** zone name (lowercased) -> { mw, shedding } for one utility's sheet. */
  const statusOf = (sched) => {
    const byZone = new Map();
    for (const c of claimsForWeekday(sched?.claims || [], now.weekday)) {
      const zone = c.division_canonical || c.division;
      if (!zone) continue;
      const on = normaliseWindows(c.windows)
        .some((w) => now.minutes >= w.startMin && now.minutes < w.endMin);
      const prev = byZone.get(zone.toLowerCase()) || { mw: 0, shedding: false };
      // Feeders on the scanned sheets often have no readable load. A nominal
      // 0.5 MW keeps the zone's column visible rather than flat; the figure is
      // only ever used for relative column height.
      if (on) { prev.mw += c.load_mw || 0.5; prev.shedding = true; }
      byZone.set(zone.toLowerCase(), prev);
    }
    return byZone;
  };

  const zones = [];

  const paint = (fc, layerId, status, z) => {
    if (!fc?.features?.length) return false;
    const features = fc.features.map((f) => {
      const name = f.properties?.division || f.properties?.name || '';
      const hit = status.get(name.toLowerCase()) || { mw: 0, shedding: false };
      const c = centroidOf(f.geometry);
      if (c) zones.push({ name, lat: c.lat, lon: c.lon, mw: hit.mw, shedding: hit.shedding });
      return {
        ...f,
        properties: {
          ...f.properties,
          shedding: hit.shedding,
          // Dark where the sheet says off, the utility's own colour otherwise.
          fill_hex: hit.shedding ? '#1a1a1e' : z.color,
        },
      };
    });
    state.map.setPolygonData(layerId, { ...fc, features });
    return true;
  };

  const drew = {};
  ZONE_LAYERS.forEach((z, i) => {
    drew[z.utility] = paint(state.data.geo[z.geo], z.layer, statusOf(feeds[i]), z);
  });

  // Fall back to the office points for DESCO if its cells are missing, so a
  // failed geo build costs the fill and not the columns too.
  if (!drew.DESCO) {
    const descoStatus = statusOf(feeds[ZONE_LAYERS.findIndex((z) => z.utility === 'DESCO')]);
    for (const f of state.data.geo.descoOffices?.features || []) {
      const c = centroidOf(f.geometry);
      const name = f.properties?.division || f.properties?.name;
      if (!c || !name) continue;
      const hit = descoStatus.get(name.toLowerCase()) || { mw: 0, shedding: false };
      zones.push({ name, lat: c.lat, lon: c.lon, mw: hit.mw, shedding: hit.shedding });
    }
  }

  state.map.setZoneLoads(zones);

  const off = zones.filter((z) => z.shedding);
  const mw = off.reduce((n, z) => n + z.mw, 0);
  const live = $('#map-live');
  if (live) {
    live.innerHTML = off.length
      ? `<span class="mono">${off.length}</span> of ${zones.length} zones scheduled off right now ·
         <span class="mono">${mw.toFixed(0)}</span> MW of feeder load`
      : `No zone is scheduled off right now · ${zones.length} zones tracked`;
  }
}

/** [[w, s], [e, n]] around the named utilities' territories, or null. */
function boundsOf(fc, utilities) {
  const want = new Set(utilities.map((u) => u.toUpperCase()));
  let w = 180, s = 90, e = -180, n = -90, seen = false;
  for (const f of fc?.features || []) {
    if (!want.has(String(f.properties?.utility || '').toUpperCase())) continue;
    // Walk the coordinate nesting rather than special-casing Polygon vs
    // MultiPolygon: DESCO is one and DPDC is the other.
    const walk = (c) => {
      if (typeof c[0] === 'number') {
        seen = true;
        if (c[0] < w) w = c[0];
        if (c[0] > e) e = c[0];
        if (c[1] < s) s = c[1];
        if (c[1] > n) n = c[1];
        return;
      }
      for (const part of c) walk(part);
    };
    walk(f.geometry.coordinates);
  }
  return seen ? [[w, s], [e, n]] : null;
}

function renderMapLegend(territories) {
  const legend = $('#map-legend');
  if (!legend) return;
  const rows = (territories.features || []).map((f) => {
    const p = f.properties;
    return `<span class="lg"><i class="sw" style="border-color:${esc(p.color_hex || '#707070')};
      background:${esc(p.color_hex || '#707070')}22;border-style:dashed"></i>${esc(p.name)}</span>`;
  });
  // A <details> so it can be folded away. Eight stacked rows covered the whole
  // lower-left of the map, which on a narrow screen meant the legend sat on top
  // of Fatulla and Narayanganj - the exact corner that had just been fixed.
  // Open on a wide screen where there is room, shut on a phone where there is not.
  legend.innerHTML = `<details class="legend-fold"${window.innerWidth >= 900 ? ' open' : ''}>
    <summary><b>Legend</b></summary>
    <div class="legend-grid">${rows.join('')}</div>
    <div class="legend-grid">
      <span class="lg"><i class="sw" style="border-color:#c47b00;background:#ff9500"></i>Zone supplied</span>
      <span class="lg"><i class="sw" style="border-color:#555;background:#1a1a1e"></i>Off now</span>
    </div>
    <span class="lg"><i class="sw" style="border-color:${SERVICE_AREA_COLOUR};background:${SERVICE_AREA_COLOUR}55;border-style:solid"></i>Area we cover</span>
    <span class="muted">Dashed borders are our estimates.</span>
  </details>`;
}

/* ============================================================ search */

function wireSearch() {
  const input = $('#q');
  const pop = $('#q-results');
  if (!input) return;

  const show = (results) => {
    input.setAttribute('aria-expanded', 'true');
    pop.innerHTML = `<ul class="suggest">${results.map((r, i) => `
      <li role="option"><button type="button" data-i="${i}">${esc(r.label)}
        <small>${esc(r.detail || r.kind || '')}</small></button></li>`).join('')}</ul>`;
    pop.querySelectorAll('button[data-i]').forEach((b) => {
      b.addEventListener('click', () => {
        const r = results[Number(b.dataset.i)];
        input.value = r.label;
        pop.innerHTML = '';
        input.setAttribute('aria-expanded', 'false');
        resolve({ lat: r.lat, lon: r.lon, source: 'search', label: r.label });
      });
    });
  };

  const run = debounce(async (q) => {
    if (q.trim().length < 2) { pop.innerHTML = ''; input.setAttribute('aria-expanded', 'false'); return; }
    const results = await searchPlaces(q, state.point);
    if (results.length) { show(results); return; }
    pop.innerHTML = `<ul class="suggest">
      <li><button type="button" disabled>Not in the offline place list.</button></li>
      <li><button type="button" data-widen="1">Search the wider map →</button></li></ul>`;
    pop.querySelector('[data-widen]')?.addEventListener('click', async (ev) => {
      ev.currentTarget.textContent = 'Searching…';
      try {
        const wide = await searchPlacesRemote(q);
        if (wide.length) show(wide);
        else pop.innerHTML = '<ul class="suggest"><li><button type="button" disabled>Nothing matched.</button></li></ul>';
      } catch (err) { toast(err.message); pop.innerHTML = ''; }
    });
  }, 300);

  input.addEventListener('input', () => run(input.value));
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { pop.innerHTML = ''; input.setAttribute('aria-expanded', 'false'); }
    if (e.key === 'Enter') { e.preventDefault(); pop.querySelector('button[data-i]')?.click(); }
  });
  $('#btn-search')?.addEventListener('click', () => {
    const first = pop.querySelector('button[data-i]');
    if (first) first.click(); else run(input.value);
  });
  document.addEventListener('click', (e) => {
    if (!e.target.closest('#q-results') && !e.target.closest('.search')) pop.innerHTML = '';
  });
}

async function useDeviceLocation() {
  const btns = [$('#btn-locate'), $('#btn-map-locate')].filter(Boolean);
  const originals = btns.map((b) => b.innerHTML);
  btns.forEach((b) => { b.disabled = true; b.textContent = 'Locating…'; });
  try {
    await resolve(await deviceLocation());
  } catch (err) {
    // Surface the real reason. Failing silently is why this looked broken.
    const msg = err instanceof GeoError ? err.message : (err?.message || 'Location failed.');
    toast(msg, 5000);
    const host = $('#feed-health');
    if (host) {
      $('#geo-err')?.remove();
      host.insertAdjacentHTML('beforebegin',
        `<div class="note note-warn" id="geo-err" style="margin-top:12px;text-align:left">
           ${icon('warn')}<div><b>Couldn't use your device location</b>${esc(msg)}
           Search your area above, or drop a pin on the map instead.</div></div>`);
      setTimeout(() => $('#geo-err')?.remove(), 12000);
    }
  } finally {
    btns.forEach((b, i) => { b.disabled = false; b.innerHTML = originals[i]; });
  }
}

/* ============================================================ saved */

function renderSaved() {
  const row = $('#saved-row');
  if (!row) return;
  const saved = store.get(LS.saved, []);
  row.innerHTML = saved.map((s, i) => `
    <button type="button" class="btn btn-sm btn-quiet" data-saved="${i}">${esc(s.name)}
      <span data-del="${i}" role="button" tabindex="0" aria-label="Remove ${esc(s.name)}"
        style="opacity:.5;margin-left:2px">✕</span></button>`).join('');
  row.querySelectorAll('[data-saved]').forEach((b) => {
    b.addEventListener('click', (e) => {
      if (e.target.dataset.del !== undefined) {
        const next = store.get(LS.saved, []);
        next.splice(Number(e.target.dataset.del), 1);
        store.set(LS.saved, next);
        renderSaved();
        return;
      }
      const s = store.get(LS.saved, [])[Number(b.dataset.saved)];
      if (s) resolve({ lat: s.lat, lon: s.lon, source: 'saved', label: s.name });
    });
  });
}

function saveCurrent() {
  if (!state.point) return;
  const name = prompt('Name this location (Home, Office…)');
  if (!name) return;
  const saved = store.get(LS.saved, []);
  saved.push({ name: name.slice(0, 24), lat: state.point.lat, lon: state.point.lon });
  store.set(LS.saved, saved.slice(0, 8));
  renderSaved();
  toast('Saved in this browser only');
}

/* ============================================================ resolve */

async function resolve(point) {
  if (!isInBangladesh(point.lon, point.lat)) return showOutside(point);

  state.point = point;
  store.set(LS.last, { lat: point.lat, lon: point.lon });
  state.map?.setMarker(point.lat, point.lon);
  state.map?.flyTo(point.lat, point.lon);
  state.map?.pulse(point.lat, point.lon);

  showSkeleton();

  const hits = featuresAt(state.data.geo.territories, point.lon, point.lat);
  state.territoryHits = hits;
  state.utilityId = hits[0]?.properties?.utility || null;
  if (state.utilityId) state.map?.illuminate(state.utilityId);

  const [reverse, schedule] = await Promise.all([
    reverseGeocode(point.lat, point.lon),
    state.utilityId ? load.schedule(state.utilityId) : Promise.resolve(null),
  ]);
  state.reverse = reverse;
  state.schedule = schedule;

  const now = dhakaNow();
  state.ranking = rankDivisions(point, {
    divisionsFC: state.data.geo.descoDivisions,
    officesFC: state.data.geo.descoOffices,
  });

  if (schedule?.claims?.length) {
    state.feeders = rankFeeders({
      claims: claimsForWeekday(schedule.claims, now.weekday),
      divisionRanking: state.ranking,
      addressText: [reverse.display, ...(reverse.locality || [])].join(', '),
      nearbyPlaces: reverse.nearest || (reverse.locality || []).map((n) => ({ name: n })),
      point,
      calibration: state.data.calibration,
    });
    state.candidates = state.feeders.candidates;
    state.evaluations = state.candidates.map((c) =>
      evaluateDay(normaliseWindows(c.claim.windows), now.minutes));
  } else {
    state.feeders = null; state.candidates = []; state.evaluations = [];
  }
  state.selected = 0;
  renderResult();
  updateZoneLoads();
}

function showSkeleton() {
  const head = $('#result-head');
  head.hidden = false;
  head.innerHTML = `<div class="wrap narrow">
    <div class="skeleton" style="height:20px;width:34%"></div>
    <div class="skeleton" style="height:52px;margin-top:18px;width:64%"></div>
    <div class="skeleton" style="height:72px;margin-top:20px"></div>
  </div>`;
  head.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function showOutside(point) {
  const s = $('#result-head');
  s.hidden = false;
  $('#result-body').hidden = true;
  s.innerHTML = `<div class="wrap narrow"><div class="note note-warn">${icon('warn')}<div>
    <b>That point is outside Bangladesh</b>
    ${point.lat.toFixed(3)}, ${point.lon.toFixed(3)} falls outside the country.</div></div></div>`;
}

/* ============================================================ render */

/** Nearest DPDC zone to a point, so a DPDC visitor still gets their own PDF. */
function nearestDpdcZone(point) {
  const zones = (state.dpdcZones?.zones || []).filter((z) => z.lat && z.pdf_url);
  if (!zones.length || !point) return null;
  let best = null;
  for (const z of zones) {
    const km = haversineKm(point, { lat: z.lat, lon: z.lon });
    if (!best || km < best.km) best = { ...z, km };
  }
  return best;
}

function renderResult() {
  const head = $('#result-head');
  const body = $('#result-body');
  const now = dhakaNow();
  const utility = findUtility(state.data.utilities, state.utilityId);
  const indexRow = state.data.index.find(
    (r) => String(r.utility).toUpperCase() === String(state.utilityId).toUpperCase());

  const evalTop = state.evaluations[state.selected] || null;
  const agreement = scheduleAgreement(state.evaluations, now.minutes);
  const hasSchedule = Boolean(state.schedule?.claims?.length);

  // Order the reader asked for: where you are, then the map, then the feeders.
  head.hidden = false;
  head.innerHTML = `<div class="wrap narrow enter">
    ${renderHeader(utility)}
    <p class="t-body-sm muted" style="margin-top:20px">
      The map below is centred on this point. Tap it anywhere to move your pin.</p>
  </div>`;

  body.hidden = false;
  body.innerHTML = `<div class="wrap narrow enter">
    ${renderAnswer({ agreement, evalTop, now, hasSchedule })}
    ${hasSchedule ? renderScheduleBody(now) : renderNoSchedule(utility, indexRow)}
    ${renderAreaNearYou()}
  </div>`;

  wireResultEvents();
}

/** Plain-language explainer plus the places we matched against. */
function renderAreaNearYou() {
  const near = (state.reverse?.nearest || []).slice(0, 8);
  const chips = near.map((n) => `<span class="tag tag-unknown">${esc(n.name)}${
    typeof n.km === 'number' ? ` · ${n.km < 1 ? Math.round(n.km * 1000) + ' m' : n.km.toFixed(1) + ' km'}` : ''
  }</span>`).join(' ');
  return `<h3 class="t-heading-sm" style="margin-top:48px">The area near you</h3>
    <p class="t-body-sm muted" style="margin:6px 0 14px;max-width:62ch">
      These are the named places within a couple of kilometres of your pin. They are what we
      match against the area descriptions printed on the schedule sheets.</p>
    ${chips ? `<div style="display:flex;gap:8px;flex-wrap:wrap">${chips}</div>`
            : '<p class="t-body-sm muted">No named places found near this pin.</p>'}

    <details class="fold" style="margin-top:20px">
      <summary>What is a feeder?</summary>
      <div class="body">
        Electricity reaches your street through an 11 kV line called a <b>feeder</b>. One feeder
        typically serves a few hundred to a few thousand connections: a cluster of roads, a
        market, a housing block.
        <br><br>
        Load shedding is switched <b>per feeder</b>, not per house and not per neighbourhood. That
        is why the sheets list feeders rather than addresses, and why two houses on the same road
        can be on different feeders and lose power at different times.
        <br><br>
        Your bill does not print your feeder. Your building manager or your distributor's local
        office can tell you, and once you know the name this site becomes exact instead of
        estimated.
      </div>
    </details>`;
}

function renderHeader(utility) {
  const p = state.point;
  const where = p.label || state.reverse?.display || 'Selected point';
  const via = { device: 'your device', search: 'search', map: 'a pin you dropped',
                saved: 'a saved place', restored: 'last time',
                demo: 'the demo point' }[p.source] || 'selection';
  const hits = state.territoryHits;
  const tags = hits.length
    ? `<span class="tag tag-derived">${esc(utility.name)}</span>
       <span class="tag tag-estimated">Area estimated</span>`
    : '<span class="tag tag-unknown">Distributor unknown</span>';
  return `<div style="display:flex;gap:16px;flex-wrap:wrap;align-items:start;justify-content:space-between">
      <div>
        <h2 class="t-heading-sm">${esc(where)}</h2>
        <p class="t-caption muted mono" style="margin-top:4px">
          ${p.lat.toFixed(5)}, ${p.lon.toFixed(5)}${p.accuracyM ? ` · ±${Math.round(p.accuracyM)} m` : ''}
          · from ${esc(via)}</p>
        <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:12px">${tags}</div>
      </div>
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        <button class="btn btn-sm btn-quiet" id="btn-save">Save</button>
        <button class="btn btn-sm btn-quiet" id="btn-share">Copy link</button>
      </div>
    </div>
    ${!hits.length ? `<div class="note note-warn" style="margin-top:16px">${icon('warn')}<div>
      <b>No distributor area covers this point</b>
      Most rural addresses are served by a local Palli Bidyut Samity (BREB). Your bill names yours.
    </div></div>` : ''}`;
}

function renderScheduleBody(now) {
  const sched = state.schedule;
  const sourceEntry = findSource(state.data.sources, sched.source?.source_id);
  const sel = state.candidates[state.selected];
  const selEval = state.evaluations[state.selected];
  const docWeekday = sel?.claim?.weekday;
  const mismatch = docWeekday !== null && docWeekday !== undefined && docWeekday !== now.weekday;

  return `
    ${mismatch ? `<div class="note note-warn" style="margin-top:24px">${icon('warn')}<div>
      <b>This is the ${esc(WEEKDAYS[docWeekday])} sheet. Today is ${esc(now.weekdayName)}</b>
      ${esc(sched.publisher)} publishes one per weekday; this is the newest we could read.
    </div></div>` : ''}

    <h3 class="t-heading-sm" style="margin-top:40px">Today: ${esc(
      sel?.claim?.billing_code || sel?.claim?.feeder_name || sel?.claim?.feeder || 'this area')}</h3>
    <p class="t-body-sm muted" style="margin:6px 0 16px">
      Hours this feeder is scheduled off. Pick another feeder below to compare.</p>
    ${renderAreas(sel?.claim)}
    ${renderStrip(selEval?.windows || [], now.minutes)}

    <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:20px">
      <button class="btn btn-sm btn-outline" id="btn-ics">Add today to my calendar</button>
      <a class="btn btn-sm btn-quiet" href="#official">Get official alerts</a>
    </div>

    <h3 class="t-heading-sm" style="margin-top:48px">Feeders near you</h3>
    <p class="t-body-sm muted" style="margin:6px 0 16px;max-width:62ch">
      Your meter sits on one feeder, but no distributor publishes which one. These are the
      ${state.candidates.length} closest matches out of ${state.feeders?.allScored ?? 0}. If they
      disagree, treat the answer as uncertain and check all of them.</p>
    ${renderFeeders(state.candidates, state.evaluations, state.selected)}
    <div style="margin-top:12px">${renderEvidence({
      ranking: state.ranking, feeders: state.feeders, reverse: state.reverse })}</div>

    <h3 class="t-heading-sm" style="margin-top:48px">Where this came from</h3>
    <div style="margin-top:12px">${renderProvenance(sched, sourceEntry)}</div>
    <div class="note" style="margin-top:16px">${icon('info')}<div>
      <b>No guarantees, and here is exactly what we used</b>
      Every hour shown above was read from
      <a href="${esc(sched.source?.source_url || '#')}" target="_blank" rel="noopener">this file</a>,
      published by ${esc(sched.publisher)}, retrieved ${esc(relTimeOf(sched.source?.retrieved_at))},
      SHA-256 <span class="mono">${esc((sched.source?.sha256 || '').slice(0, 16) || 'n/a')}</span>,
      parsed by <span class="mono">${esc(sched.source?.parser_adapter || 'n/a')}
      v${esc(sched.source?.parser_version || '0')}</span>.
      It is a published plan, not a promise: power may stay on through a window, or go out
      outside one. <a href="#disclaimer">Why this is an estimate</a></div></div>`;
}

function renderNoSchedule(utility, indexRow) {
  const zone = String(utility.id).toUpperCase() === 'DPDC' ? nearestDpdcZone(state.point) : null;
  const alert = state.data.alerts.find(
    (a) => String(a.utility).toUpperCase() === String(utility.id).toUpperCase());

  return `<div style="margin-top:32px">
    ${zone ? `<div class="card">
        <h3 class="t-heading-sm">Your DPDC zone: ${esc(zone.name)}</h3>
        <p class="t-body-sm muted" style="margin-top:8px;max-width:62ch">
          DPDC publishes a sheet per zone, and all 36 are normally read here. Today's
          could not be retrieved, so nothing is shown above. This is the sheet for the
          zone nearest you, ${esc(zone.km.toFixed(1))} km away. Open it and read it
          directly.</p>
        <a class="btn btn-primary" style="margin-top:16px" href="${esc(zone.pdf_url)}"
           target="_blank" rel="noopener">Open the ${esc(zone.name)} sheet ↗</a>
        <p class="t-caption muted" style="margin-top:12px">
          Not your zone? <a href="sources.html#dpdc">All 36 DPDC zone sheets</a></p>
      </div>` : `
      <div class="note note-warn">${icon('warn')}<div>
        <b>${esc(utility.name)} publishes nothing we can read</b>
        ${esc(indexRow?.message || 'No machine-readable schedule was found.')}</div></div>`}

    <h3 class="t-heading-sm" style="margin-top:40px">What to do instead</h3>
    <ul style="margin:12px 0 0 20px;display:grid;gap:8px" class="muted">
      ${indexRow?.source_url ? `<li><a href="${esc(indexRow.source_url)}" target="_blank" rel="noopener">
        Open ${esc(utility.name)}'s own load-shedding page ↗</a></li>` : ''}
      <li>Call <a href="tel:16999" class="mono">16999</a> and choose ${esc(utility.name)}.</li>
      ${alert?.hotline?.length ? `<li>Or ${esc(utility.name)} direct:
        <a href="tel:${esc(alert.hotline[0].replace(/[^\d+]/g, ''))}" class="mono">${esc(alert.hotline[0])}</a></li>` : ''}
    </ul>`;
}

function wireResultEvents() {
  $('#btn-save')?.addEventListener('click', saveCurrent);
  $('#btn-share')?.addEventListener('click', async () => {
    const url = new URL(location.href);
    url.hash = `at=${state.point.lat.toFixed(5)},${state.point.lon.toFixed(5)}`;
    try {
      await navigator.clipboard.writeText(url.toString());
      toast('Link copied. It contains only coordinates.');
    } catch { toast('Your browser blocked clipboard access.'); }
  });

  document.querySelectorAll('#result-body [data-cand]').forEach((b) => {
    b.addEventListener('click', () => { state.selected = Number(b.dataset.cand); renderResult(); });
  });

  $('#btn-ics')?.addEventListener('click', () => {
    const ev = state.evaluations[state.selected];
    const cand = state.candidates[state.selected];
    if (!ev?.windows?.length) return toast('No windows published for this feeder today.');
    const blob = new Blob([buildICS({
      windows: ev.windows, date: dhakaNow().date,
      label: cand?.claim?.feeder, feeder: cand?.claim?.feeder,
      utility: state.schedule?.publisher, sourceUrl: state.schedule?.source?.source_url,
    })], { type: 'text/calendar;charset=utf-8' });
    const a = el('a', { href: URL.createObjectURL(blob),
                        download: `load-shedding-${dhakaNow().date}.ics` });
    document.body.append(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
    toast('Downloaded. Reminders fire 15 minutes before each window.');
  });
}

/* ============================================================ offline */

function registerServiceWorker() {
  if (!('serviceWorker' in navigator)) return;

  // A new worker claiming this page swaps the cache under it, but the modules
  // already parsed keep running, so the tab stays on the old build until the
  // visitor happens to reload. Reload it for them, once.
  //
  // `controllerchange` also fires on the very first install, when there was no
  // previous controller and nothing has gone stale. Reloading then would throw
  // away a perfectly good first paint, so that case is skipped.
  let reloading = false;
  const hadController = Boolean(navigator.serviceWorker.controller);
  navigator.serviceWorker.addEventListener('controllerchange', () => {
    if (reloading || !hadController) return;
    reloading = true;
    window.location.reload();
  });

  window.addEventListener('load', () => {
    navigator.serviceWorker.register('sw.js')
      .then((reg) => reg.update().catch(() => {}))
      .catch((err) => console.info('[sw] not registered:', err.message));
  });
}

boot().then(() => {
  registerServiceWorker();
}).catch((err) => {
  console.error(err);
  const host = $('#feed-health');
  if (host) {
    host.innerHTML = `<div class="note note-stop">${icon('warn')}<div>
      <b>Something failed to load</b>${esc(err.message)}</div></div>`;
  }
});
