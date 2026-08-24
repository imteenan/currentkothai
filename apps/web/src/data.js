// Static-JSON data layer. This is the whole "backend": files published by the
// GitHub Actions ingestion job, fetched straight from the CDN that serves the site.

const meta = document.querySelector('meta[name="data-root"]');
export const DATA_ROOT = new URL(meta?.content || 'data/', document.baseURI).href;

const memo = new Map();

/**
 * Fetch a JSON file under data/. Returns `fallback` instead of throwing when the
 * file is missing — a half-built dataset must degrade, not blank the page.
 */
export async function getJSON(path, fallback = null) {
  if (memo.has(path)) return memo.get(path);
  const p = (async () => {
    try {
      const res = await fetch(new URL(path, DATA_ROOT).href, { cache: 'no-cache' });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      return await res.json();
    } catch (err) {
      console.warn(`[data] ${path} unavailable:`, err.message);
      return fallback;
    }
  })();
  memo.set(path, p);
  return p;
}

const EMPTY_FC = { type: 'FeatureCollection', features: [] };

export const load = {
  utilities:    () => getJSON('registry/utilities.json', { utilities: [] }),
  sources:      () => getJSON('registry/sources.json', { schema: 'source-registry/1', sources: [] }),
  alerts:       () => getJSON('registry/official-alerts.json', { alerts: [] }),
  scheduleIndex:() => getJSON('schedules/index.json', { utilities: [] }),
  schedule:     (utility) => getJSON(`schedules/${utility.toLowerCase()}/latest.json`, null),
  calibration:  () => getJSON('validation/calibration.json', null),
  geo: {
    territories:    () => getJSON('geo/utility-territories.geojson', EMPTY_FC),
    descoDivisions: () => getJSON('geo/desco-divisions.geojson', EMPTY_FC),
    descoOffices:   () => getJSON('geo/desco-offices.geojson', EMPTY_FC),
    dpdcZones:      () => getJSON('geo/dpdc-zones.geojson', EMPTY_FC),
    neighbourhoods: () => getJSON('geo/dhaka-neighbourhoods.geojson', EMPTY_FC),
    districts:      () => getJSON('geo/bangladesh-admin.geojson', EMPTY_FC),
    divisionsAdmin: () => getJSON('geo/bangladesh-divisions.geojson', EMPTY_FC),
  },
};

/** Everything the location panel needs, loaded once, in parallel. */
export async function bootstrap() {
  const [utilities, sources, alerts, index, calibration, territories, descoDivisions, descoOffices,
         dpdcZones] =
    await Promise.all([
      load.utilities(), load.sources(), load.alerts(), load.scheduleIndex(), load.calibration(),
      load.geo.territories(), load.geo.descoDivisions(), load.geo.descoOffices(),
      load.geo.dpdcZones(),
    ]);
  return {
    utilities: utilities.utilities ?? utilities ?? [],
    sources: sources.sources ?? [],
    sourcesMeta: sources,
    alerts: alerts.alerts ?? alerts ?? [],
    index: index.utilities ?? index ?? [],
    calibration,
    geo: { territories, descoDivisions, descoOffices, dpdcZones },
  };
}

/** Look up a source registry entry by id, for provenance rows. */
export const findSource = (sources, id) => sources.find((s) => s.id === id) || null;

/** Utility metadata by id, with a safe default so the UI never renders "undefined". */
export function findUtility(utilities, id) {
  const key = String(id ?? '').toUpperCase();
  return utilities.find((u) => String(u.id).toUpperCase() === key) || {
    id: key || 'UNKNOWN', name: key || 'Unknown', full_name: key || 'Unknown distributor',
    website: null, color_hex: '#8e8e93',
  };
}
