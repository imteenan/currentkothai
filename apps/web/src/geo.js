// Browser-side geospatial primitives. Replaces PostGIS for a static site.
// Everything here operates on plain GeoJSON in WGS84 lon/lat order.

import { haversineKm } from './util.js';

/** Ray-casting point-in-ring. `ring` is [[lon,lat], ...]. */
function pointInRing(lon, lat, ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i];
    const [xj, yj] = ring[j];
    const intersects = (yi > lat) !== (yj > lat) &&
      lon < ((xj - xi) * (lat - yi)) / (yj - yi + Number.EPSILON) + xi;
    if (intersects) inside = !inside;
  }
  return inside;
}

/** A polygon is [outerRing, ...holes]. */
function pointInPolygon(lon, lat, polygon) {
  if (!polygon?.length || !pointInRing(lon, lat, polygon[0])) return false;
  for (let h = 1; h < polygon.length; h++) if (pointInRing(lon, lat, polygon[h])) return false;
  return true;
}

/** Works for Polygon and MultiPolygon geometries. */
export function pointInGeometry(lon, lat, geometry) {
  if (!geometry) return false;
  if (geometry.type === 'Polygon') return pointInPolygon(lon, lat, geometry.coordinates);
  if (geometry.type === 'MultiPolygon') {
    return geometry.coordinates.some((poly) => pointInPolygon(lon, lat, poly));
  }
  return false;
}

/** Cheap reject before the expensive ray cast. */
export function bboxOf(geometry) {
  let minX = 180, minY = 90, maxX = -180, maxY = -90;
  const walk = (c) => {
    if (typeof c[0] === 'number') {
      if (c[0] < minX) minX = c[0]; if (c[0] > maxX) maxX = c[0];
      if (c[1] < minY) minY = c[1]; if (c[1] > maxY) maxY = c[1];
    } else c.forEach(walk);
  };
  if (geometry?.coordinates) walk(geometry.coordinates);
  return [minX, minY, maxX, maxY];
}

const inBbox = (lon, lat, [a, b, c, d]) => lon >= a && lon <= c && lat >= b && lat <= d;

/**
 * All features of a FeatureCollection containing the point.
 * Caches a bbox on each feature the first time it is tested.
 */
export function featuresAt(fc, lon, lat) {
  if (!fc?.features?.length) return [];
  const hits = [];
  for (const f of fc.features) {
    if (!f._bbox) f._bbox = bboxOf(f.geometry);
    if (!inBbox(lon, lat, f._bbox)) continue;
    if (pointInGeometry(lon, lat, f.geometry)) hits.push(f);
  }
  return hits;
}

/** Rough polygon/multipolygon centroid, area-weighted by ring signed area. */
export function centroidOf(geometry) {
  const polys = geometry?.type === 'MultiPolygon' ? geometry.coordinates
    : geometry?.type === 'Polygon' ? [geometry.coordinates]
    : geometry?.type === 'Point' ? null : [];
  if (geometry?.type === 'Point') {
    return { lon: geometry.coordinates[0], lat: geometry.coordinates[1] };
  }
  let cx = 0, cy = 0, total = 0;
  for (const poly of polys) {
    const ring = poly[0];
    if (!ring || ring.length < 3) continue;
    let a = 0, x = 0, y = 0;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const cross = ring[j][0] * ring[i][1] - ring[i][0] * ring[j][1];
      a += cross;
      x += (ring[j][0] + ring[i][0]) * cross;
      y += (ring[j][1] + ring[i][1]) * cross;
    }
    a *= 0.5;
    if (!a) continue;
    cx += x / (6 * a) * Math.abs(a);
    cy += y / (6 * a) * Math.abs(a);
    total += Math.abs(a);
  }
  if (!total) return null;
  return { lon: cx / total, lat: cy / total };
}

/** Point features sorted by distance from the query point. */
export function nearestPoints(fc, lon, lat, limit = 5) {
  if (!fc?.features?.length) return [];
  return fc.features
    .map((f) => {
      const c = centroidOf(f.geometry);
      if (!c) return null;
      return { feature: f, km: haversineKm({ lat, lon }, c), at: c };
    })
    .filter(Boolean)
    .sort((a, b) => a.km - b.km)
    .slice(0, limit);
}

/** Bangladesh's rough bounding box — used to reject obviously-outside points. */
export const BD_BBOX = [88.0, 20.5, 92.7, 26.7];
export const isInBangladesh = (lon, lat) => inBbox(lon, lat, BD_BBOX);
