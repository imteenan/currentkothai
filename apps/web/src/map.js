// MapLibre wrapper.
//
// Two ideas drive this map:
//   1. It is 3D. Zone columns are extruded by the load being shed right now, so
//      the skyline of the map IS the shortfall.
//   2. It lights up. Setting a location fires a ripple from the point and the
//      serving territory illuminates. Zones inside a published window go DARK —
//      a literal lights-out map of the city.
//
// Tiles: OpenFreeMap (free, no key, no cap) with a CARTO dark style for dark
// mode and MapLibre demotiles as the last-resort fallback.

// Single light basemap: the site committed to one light theme, and a matching
// map removes a whole class of contrast problems.
const STYLE = 'https://tiles.openfreemap.org/styles/positron';
const STYLE_FALLBACK = 'https://demotiles.maplibre.org/style.json';

// Fallback view only. The real one is fitted to the served territories by
// fitTo(), because a hardcoded centre goes stale the moment coverage changes.
// This literal used to be [90.4074, 23.7925] zoom 10.2, chosen when the map
// carried DESCO alone. DESCO sits between lat 23.73 and 23.90; adding DPDC took
// the covered area down to 23.55, so that centre was 0.07 degrees too far north
// and the default view cut Fatulla and Narayanganj off the bottom of the
// screen. The data was right and the camera was pointed at the wrong half.
const DHAKA = { center: [90.4309, 23.7228], zoom: 9.6 };
const COL_SRC = 'zone-columns';
const RIPPLE_SRC = 'ripple';

/** Metres -> degrees, roughly, at Bangladesh's latitude. */
const M_LAT = 1 / 110_574;
const M_LON = 1 / (111_320 * Math.cos(23.8 * Math.PI / 180));

/** An octagon around a point, so extrusion reads as a column not a box. */
function octagon(lon, lat, radiusM = 380) {
  const ring = [];
  for (let i = 0; i <= 8; i++) {
    const a = (i / 8) * Math.PI * 2;
    ring.push([lon + Math.cos(a) * radiusM * M_LON, lat + Math.sin(a) * radiusM * M_LAT]);
  }
  return { type: 'Polygon', coordinates: [ring] };
}

function circlePolygon(lon, lat, radiusM, steps = 48) {
  const ring = [];
  for (let i = 0; i <= steps; i++) {
    const a = (i / steps) * Math.PI * 2;
    ring.push([lon + Math.cos(a) * radiusM * M_LON, lat + Math.sin(a) * radiusM * M_LAT]);
  }
  return { type: 'Polygon', coordinates: [ring] };
}

export class CoverageMap {
  constructor(container, { onPick } = {}) {
    this.container = container;
    this.onPick = onPick;
    this.map = null;
    this.layers = {};
    this.marker = null;
    this.ready = false;
    this.pitched = true;
    this._pending = [];
    this._zones = [];
    this._ripple = null;
  }

  init() {
    if (!window.maplibregl) {
      this.container.innerHTML =
        '<div style="display:grid;place-items:center;height:100%;padding:2rem;text-align:center" ' +
        'class="dim-t">Map library unavailable. Everything else on this page still works.</div>';
      return null;
    }
    this.map = new maplibregl.Map({
      container: this.container,
      style: STYLE,
      center: DHAKA.center,
      zoom: DHAKA.zoom,
      pitch: 46,
      bearing: -14,
      antialias: true,
      attributionControl: false,
      maxZoom: 16,
      minZoom: 5,
    });

    this.map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), 'top-left');
    this.map.addControl(new maplibregl.AttributionControl({ compact: true }), 'bottom-right');

    this.map.on('load', () => {
      this.ready = true;
      this._installDynamicSources();
      this._pending.forEach((fn) => fn());
      this._pending = [];
      this._tick();
    });

    this.map.on('click', (e) => {
      if (this.onPick) this.onPick({ lat: e.lngLat.lat, lon: e.lngLat.lng });
    });
    this.map.getCanvas().style.cursor = 'crosshair';
    return this.map;
  }

  _whenReady(fn) { if (this.ready) fn(); else this._pending.push(fn); }

  /* ------------------------------------------------- dynamic layers */

  _installDynamicSources() {
    const empty = { type: 'FeatureCollection', features: [] };

    // Extruded zone columns: height = MW being shed right now.
    this.map.addSource(COL_SRC, { type: 'geojson', data: empty });
    this.map.addLayer({
      id: 'zone-columns',
      type: 'fill-extrusion',
      source: COL_SRC,
      paint: {
        'fill-extrusion-color': [
          'case', ['get', 'shedding'], ['get', 'colorOff'], ['get', 'colorOn'],
        ],
        'fill-extrusion-height': ['get', 'height'],
        'fill-extrusion-base': 0,
        'fill-extrusion-opacity': 0.82,
        'fill-extrusion-vertical-gradient': true,
      },
    });

    // Glow discs under the columns, so lit zones read as lit ground.
    this.map.addLayer({
      id: 'zone-glow',
      type: 'circle',
      source: COL_SRC,
      paint: {
        // Tuned for 23 zones filling the screen. Fitting both distributors
        // opens at zoom ~9.2 with 58 of them, where a 9px disc blurred by 1.1
        // spreads into its neighbours and the city reads as one orange stain.
        // Small and tight when zoomed out, generous once they are apart.
        'circle-radius': ['interpolate', ['linear'], ['zoom'], 9, 3.5, 11, 11, 14, 30],
        'circle-color': ['case', ['get', 'shedding'], '#1a1a1e', ['get', 'colorOn']],
        'circle-opacity': ['interpolate', ['linear'], ['zoom'],
          9, 0.18, 11.5, ['case', ['get', 'shedding'], 0.30, 0.34]],
        'circle-blur': ['interpolate', ['linear'], ['zoom'], 9, 0.55, 12, 1.1],
      },
    }, 'zone-columns');

    // Ripple that fires when a location is set.
    this.map.addSource(RIPPLE_SRC, { type: 'geojson', data: empty });
    this.map.addLayer({
      id: 'ripple-fill', type: 'fill', source: RIPPLE_SRC,
      paint: { 'fill-color': '#0071e3', 'fill-opacity': 0.10 },
    });
    this.map.addLayer({
      id: 'ripple-line', type: 'line', source: RIPPLE_SRC,
      paint: { 'line-color': '#0071e3', 'line-width': 2, 'line-opacity': 0.85, 'line-blur': 1 },
    });
  }

  /**
   * @param {Array} zones [{name, lat, lon, mw, shedding}]
   * Column height is scaled from MW; a shedding zone is drawn dark, a supplied
   * zone sodium-lit.
   */
  setZoneLoads(zones) {
    this._zones = zones || [];
    this._whenReady(() => {
      const src = this.map.getSource(COL_SRC);
      if (!src) return;
      const maxMw = Math.max(1, ...this._zones.map((z) => z.mw || 0));
      src.setData({
        type: 'FeatureCollection',
        features: this._zones.map((z) => ({
          type: 'Feature',
          geometry: octagon(z.lon, z.lat),
          properties: {
            name: z.name,
            mw: z.mw || 0,
            shedding: Boolean(z.shedding),
            // 80 m floor so every zone stays visible; the worst-hit zone tops
            // out around 900 m, which reads as a skyline rather than a wall.
            height: 80 + (Math.sqrt((z.mw || 0) / maxMw) * 820),
            colorOn: '#ff9500',
            // Scheduled off reads as a dark block against the light map.
            colorOff: '#1a1a1e',
          },
        })),
      });
    });
  }

  /** Ripple outward from a point, then fade. Purely decorative, reduced-motion aware. */
  pulse(lat, lon) {
    this._whenReady(() => {
      const src = this.map.getSource(RIPPLE_SRC);
      if (!src) return;
      if (matchMedia('(prefers-reduced-motion: reduce)').matches) return;
      cancelAnimationFrame(this._ripple);
      const start = performance.now();
      const DURATION = 1500;
      const MAX_M = 4200;
      const step = (t) => {
        const k = Math.min(1, (t - start) / DURATION);
        const eased = 1 - Math.pow(1 - k, 3);
        src.setData({
          type: 'FeatureCollection',
          features: [{ type: 'Feature', properties: {},
                       geometry: circlePolygon(lon, lat, 60 + eased * MAX_M) }],
        });
        this.map.setPaintProperty('ripple-line', 'line-opacity', 0.85 * (1 - k));
        this.map.setPaintProperty('ripple-fill', 'fill-opacity', 0.10 * (1 - k));
        if (k < 1) this._ripple = requestAnimationFrame(step);
        else src.setData({ type: 'FeatureCollection', features: [] });
      };
      this._ripple = requestAnimationFrame(step);
    });
  }

  /** Brighten the matched distributor and mute the others. */
  illuminate(utilityId) {
    this._whenReady(() => {
      if (!this.map.getLayer('territories-fill')) return;
      const id = String(utilityId || '').toUpperCase();
      this.map.setPaintProperty('territories-fill', 'fill-opacity',
        ['case', ['==', ['upcase', ['coalesce', ['get', 'utility'], '']], id], 0.34, 0.06]);
      this.map.setPaintProperty('territories-line', 'line-width',
        ['case', ['==', ['upcase', ['coalesce', ['get', 'utility'], '']], id], 3, 1.2]);
    });
    this.container.parentElement?.classList.add('is-lit');
    setTimeout(() => this.container.parentElement?.classList.remove('is-lit'), 1600);
  }

  /** Keep the columns honest as the clock moves. */
  _tick() {
    clearInterval(this._ticker);
    this._ticker = setInterval(() => {
      if (this.onTick) this.onTick();
    }, 60_000);
  }

  togglePitch() {
    this._whenReady(() => {
      this.pitched = !this.pitched;
      this.map.easeTo({ pitch: this.pitched ? 46 : 0, bearing: this.pitched ? -14 : 0, duration: 700 });
    });
    return this.pitched;
  }


  /* ------------------------------------------------------ static layers */

  addPolygonLayer(id, data, opts = {}) {
    if (!data?.features?.length) return;
    this.layers[id] = { data, opts };
    this._whenReady(() => {
      if (this.map.getLayer(`${id}-fill`)) return;
      if (!this.map.getSource(id)) this.map.addSource(id, { type: 'geojson', data });

      const color = opts.colorProperty
        ? ['coalesce', ['get', opts.colorProperty], opts.color || '#45c4b0']
        : (opts.color || '#45c4b0');
      const vis = opts.visible === false ? 'none' : 'visible';
      const below = this.map.getLayer('zone-glow') ? 'zone-glow' : undefined;

      this.map.addLayer({
        id: `${id}-fill`, type: 'fill', source: id,
        layout: { visibility: vis },
        paint: { 'fill-color': color, 'fill-opacity': opts.fillOpacity ?? 0.12 },
      }, below);
      this.map.addLayer({
        id: `${id}-line`, type: 'line', source: id,
        layout: { visibility: vis, 'line-join': 'round' },
        paint: {
          'line-color': color,
          'line-width': opts.lineWidth ?? 1.4,
          'line-opacity': 0.9,
          // Solid where a source published the boundary, dashed where we estimated it.
          'line-dasharray': ['case', ['==', ['get', 'status'], 'official'],
            ['literal', [1]], ['literal', [2.2, 1.6]]],
        },
      }, below);

    });
  }

  /** Frame a [[w, s], [e, n]] box, so the view follows the data.
   *
   * Called once on load with the union of the served territories. Anything that
   * moves the camera afterwards (a resolved pin, a search result) wins, which
   * is why this is a plain fit with no animation rather than a constraint.
   */
  fitTo(bounds, opts = {}) {
    if (!bounds) return;
    this._whenReady(() => {
      // fitBounds computes a whole camera, and a camera has a pitch. Leave it
      // out and MapLibre flattens the map to 0: the extruded columns collapse
      // into flat octagons and the 3D skyline turns into orange smears. Carry
      // the current angle through the fit rather than letting it be reset.
      const pitch = this.map.getPitch();
      const bearing = this.map.getBearing();
      this.map.fitBounds(bounds, {
        padding: opts.padding ?? { top: 40, bottom: 40, left: 40, right: 40 },
        duration: 0,
        maxZoom: opts.maxZoom ?? 11,
        pitch,
        bearing,
      });
      // Not every version honours pitch inside fitBounds, and a silently flat
      // map is the whole bug, so assert it rather than trusting the option.
      if (Math.abs(this.map.getPitch() - pitch) > 1) this.map.setPitch(pitch);
      if (Math.abs(this.map.getBearing() - bearing) > 1) this.map.setBearing(bearing);
      this.pitched = this.map.getPitch() > 1;
    });
  }

  /** Replace an existing polygon layer's data, keeping its paint rules.
   *
   * Used to recolour zone cells as the clock moves: a zone inside a published
   * window is filled dark, the rest stay tinted in their utility's colour. The
   * geometry never changes, only the properties the paint expression reads.
   */
  setPolygonData(id, data) {
    if (this.layers[id]) this.layers[id].data = data;
    this._whenReady(() => {
      const src = this.map.getSource(id);
      if (src) src.setData(data);
    });
  }

  addPointLayer(id, data, opts = {}) {
    if (!data?.features?.length) return;
    this.layers[id] = { data, opts, point: true };
    this._whenReady(() => {
      if (this.map.getLayer(id)) return;
      if (!this.map.getSource(id)) this.map.addSource(id, { type: 'geojson', data });
      this.map.addLayer({
        id, type: 'circle', source: id,
        layout: { visibility: opts.visible === false ? 'none' : 'visible' },
        paint: {
          'circle-radius': ['interpolate', ['linear'], ['zoom'], 9, 3, 14, 6],
          'circle-color': opts.color || '#45c4b0',
          'circle-stroke-width': 1.2,
          'circle-stroke-color': 'rgba(255,255,255,.85)',
        },
      });
    });
  }

  setLayerVisible(id, visible) {
    this._whenReady(() => {
      for (const suffix of ['-fill', '-line', '']) {
        const lid = `${id}${suffix}`;
        if (this.map.getLayer(lid)) {
          this.map.setLayoutProperty(lid, 'visibility', visible ? 'visible' : 'none');
        }
      }
    });
  }

  setColumnsVisible(visible) {
    this._whenReady(() => {
      for (const lid of ['zone-columns', 'zone-glow']) {
        if (this.map.getLayer(lid)) {
          this.map.setLayoutProperty(lid, 'visibility', visible ? 'visible' : 'none');
        }
      }
    });
  }

  setMarker(lat, lon) {
    this._whenReady(() => {
      const node = document.createElement('div');
      node.style.cssText =
        'width:18px;height:18px;border-radius:50%;background:#0071e3;border:3px solid #fff;' +
        'box-shadow:0 0 0 4px rgba(0,113,227,.25),0 2px 8px rgba(0,0,0,.3)';
      if (this.marker) this.marker.remove();
      this.marker = new maplibregl.Marker({ element: node }).setLngLat([lon, lat]).addTo(this.map);
    });
  }

  flyTo(lat, lon, zoom = 11.7) {
    this._whenReady(() => this.map.flyTo({
      center: [lon, lat], zoom, pitch: this.pitched ? 50 : 0, speed: 1.05, curve: 1.4,
    }));
  }
}

const escapeHtml = (s) => String(s ?? '').replace(/[&<>"]/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
