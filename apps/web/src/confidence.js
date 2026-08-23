// Candidate feeder ranking + the honesty guards around it.
//
// Design rule inherited from the blueprint: a textual match like "Road 1, Section 12"
// is EVIDENCE, not proof. Nothing in this file may turn a score into a percentage
// until `calibration.json` reports measured Top-1/Top-3 accuracy on a holdout set.

import { featuresAt, nearestPoints } from './geo.js';

/** Below this, we refuse to name a feeder at all. */
const MIN_EVIDENCE = 0.28;
/** Ceiling on confidence while the model is uncalibrated. */
const UNCALIBRATED_CEILING = 'medium';

const ORDER = ['none', 'low', 'medium', 'high'];
const capConfidence = (level, ceiling) =>
  ORDER.indexOf(level) > ORDER.indexOf(ceiling) ? ceiling : level;

// ---------------------------------------------------------------- text model

/** Words that carry no locating power on their own. */
const STOP = new Set([
  'area', 'areas', 'under', 'the', 'feeder', 'and', 'of', 'part', 'parts', 'no', 'nos',
  'dhaka', 'bangladesh', 'division', 'zone', 'sub', 'station', 'ss', 'kv', 'line',
  'adjacent', 'nearby', 'surrounding', 'etc', 'others', 'other', 'some', 'all',
]);

/** Common Bangla-to-Latin transliteration drift seen in utility documents. */
const ALIAS = new Map(Object.entries({
  kallyanpur: 'kalyanpur', kalayanpur: 'kalyanpur', kollyanpur: 'kalyanpur',
  mohammadpur: 'mohammadpur', mohammedpur: 'mohammadpur', muhammadpur: 'mohammadpur',
  uttora: 'uttara', uttar: 'uttara',
  goolshan: 'gulshan', gulsan: 'gulshan',
  bananee: 'banani',
  mirpore: 'mirpur', mirpr: 'mirpur',
  pallabi: 'pallabi', palabi: 'pallabi',
  shahali: 'shahali', shali: 'shahali',
  rupnagar: 'rupnagar', roopnagar: 'rupnagar',
  bashundhara: 'bashundhara', basundhara: 'bashundhara', bosundhora: 'bashundhara',
  badda: 'badda', vatara: 'vatara', bhatara: 'vatara',
  tongi: 'tongi', tungi: 'tongi',
  dakkhinkhan: 'dakshinkhan', dokkhinkhan: 'dakshinkhan',
  uttarkhan: 'uttarkhan', uttorkhan: 'uttarkhan',
  agargaon: 'agargaon', agargao: 'agargaon',
  kafrul: 'kafrul', cafrul: 'kafrul',
  baridhara: 'baridhara', boridhara: 'baridhara',
  khilkhet: 'khilkhet', kilkhet: 'khilkhet',
  nikunja: 'nikunja', nikunjo: 'nikunja',
  shewrapara: 'shewrapara', sewrapara: 'shewrapara',
  kazipara: 'kazipara', kajipara: 'kazipara',
  monipur: 'monipur', manipur: 'monipur',
  darussalam: 'darussalam', darusalam: 'darussalam',
  ibrahimpur: 'ibrahimpur', ibrahimpore: 'ibrahimpur',
}));

/** Structured address fragments like "sector 7", "road 12", "block c". */
const UNIT_RE = /\b(sector|sec|road|rd|block|blk|section|lane|avenue|ave|house|plot|ward)\s*[-.# ]?\s*([0-9]{1,3}|[a-z])\b/gi;
const UNIT_CANON = { sec: 'sector', rd: 'road', blk: 'block', ave: 'avenue' };

export function normaliseTokens(text) {
  const raw = String(text ?? '').toLowerCase();
  const tokens = new Set();

  for (const m of raw.matchAll(UNIT_RE)) {
    const kind = UNIT_CANON[m[1].toLowerCase()] || m[1].toLowerCase();
    tokens.add(`${kind}:${m[2].toLowerCase()}`);
  }

  for (let w of raw.split(/[^a-z0-9ঀ-৿]+/)) {
    if (!w || w.length < 3) continue;
    if (STOP.has(w)) continue;
    if (/^\d+$/.test(w)) continue;               // bare numbers are noise without their unit
    w = ALIAS.get(w) || w;
    tokens.add(w);
  }
  return tokens;
}

/** Fraction of the claim's distinctive tokens that the location evidence supports. */
function tokenCoverage(claimTokens, evidenceTokens) {
  if (!claimTokens.size) return 0;
  let hit = 0, weighted = 0, hitWeighted = 0;
  for (const t of claimTokens) {
    const w = t.includes(':') ? 2 : 1;          // "sector:7" is worth more than a bare place word
    weighted += w;
    if (evidenceTokens.has(t)) { hit++; hitWeighted += w; }
  }
  if (!hit) return 0;
  return hitWeighted / weighted;
}

// ---------------------------------------------------- geographic ranking

/**
 * Rank the utility's divisions for a point, using (a) containment in the
 * estimated division polygons and (b) distance to the S&D office points.
 * Office proximity is the more defensible of the two, so it breaks ties.
 */
export function rankDivisions({ lon, lat }, { divisionsFC, officesFC }) {
  const scores = new Map();
  const note = (name, score, why) => {
    if (!name) return;
    const key = String(name).trim();
    const prev = scores.get(key) || { name: key, score: 0, why: [] };
    prev.score = Math.max(prev.score, score);
    prev.why.push(why);
    scores.set(key, prev);
  };

  for (const f of featuresAt(divisionsFC, lon, lat)) {
    note(f.properties?.name, 1.0, 'inside estimated division boundary');
  }

  const near = nearestPoints(officesFC, lon, lat, 6);
  near.forEach((n, i) => {
    // 0 km -> 0.95, 3 km -> ~0.55, 8 km -> ~0.2
    const decay = Math.exp(-n.km / 5.5) * 0.95;
    const nm = n.feature.properties?.division || n.feature.properties?.name;
    note(nm, decay, `${n.km.toFixed(1)} km from the ${nm} S&D office`);
    if (i === 0) n.feature._isNearest = true;
  });

  const ranked = [...scores.values()].sort((a, b) => b.score - a.score);
  const margin = ranked.length > 1 ? ranked[0].score - ranked[1].score : ranked.length ? 1 : 0;
  return {
    ranked,
    nearestOffices: near,
    confidence: !ranked.length ? 'none'
      : ranked[0].score >= 0.98 && margin > 0.15 ? 'high'
      : ranked[0].score >= 0.6 ? 'medium' : 'low',
  };
}

// ---------------------------------------------------- feeder candidates

/**
 * Score every schedule claim for the point and return a ranked candidate list.
 *
 * @param {object} ctx
 *   claims          - schedule-claims/1 `claims` array for the day
 *   divisionRanking - output of rankDivisions
 *   addressText     - reverse-geocoded display name (may be empty)
 *   nearbyPlaces    - [{name, nameBn, km}] from the gazetteer (optional)
 *   point           - {lat, lon}
 *   calibration     - contents of data/validation/calibration.json, or null
 */
export function rankFeeders(ctx) {
  const {
    claims = [], divisionRanking, addressText = '', nearbyPlaces = [],
    point, calibration = null,
  } = ctx;

  // Evidence pool: the resolved address plus every named place near the point.
  // `nearbyPlaces` comes from the bundled gazetteer, which covers the whole
  // country rather than only the Dhaka neighbourhood layer.
  const evidence = normaliseTokens(addressText);
  const nearbyNames = [];
  for (const p of nearbyPlaces) {
    if (!p?.name) continue;
    if (typeof p.km === 'number' && p.km > 2.5) continue;
    nearbyNames.push({ name: p.name, km: p.km ?? null });
    for (const t of normaliseTokens(`${p.name} ${p.nameBn ?? ''}`)) evidence.add(t);
  }
  nearbyNames.sort((a, b) => (a.km ?? 99) - (b.km ?? 99));

  const divRank = new Map((divisionRanking?.ranked ?? []).map((d, i) => [d.name.toLowerCase(), i]));
  const divisionWeight = (name) => {
    if (!divRank.size) return 0.5;                        // no geometry at all: stay neutral
    const i = divRank.get(String(name ?? '').trim().toLowerCase());
    if (i === undefined) return 0.10;
    return [1.0, 0.62, 0.36][i] ?? 0.15;
  };

  const scored = claims.map((c) => {
    const claimTokens = normaliseTokens(`${c.area_text ?? ''} ${c.feeder ?? ''}`);
    const areaScore = tokenCoverage(claimTokens, evidence);
    const divScore = divisionWeight(c.division);
    const matched = [...claimTokens].filter((t) => evidence.has(t));
    return {
      claim: c,
      score: 0.5 * divScore + 0.5 * areaScore,
      divScore,
      areaScore,
      matchedTokens: matched,
    };
  }).sort((a, b) => b.score - a.score);

  const top = scored.slice(0, 3);
  const best = top[0];
  const margin = top.length > 1 ? top[0].score - top[1].score : (best ? best.score : 0);

  // ---- confidence, with the uncalibrated ceiling applied last -------------
  let level = 'none';
  if (best && best.score >= MIN_EVIDENCE) {
    level = 'low';
    const strongDivision = best.divScore >= 0.95;
    const realTextMatch = best.areaScore >= 0.45;
    if (strongDivision && realTextMatch && margin >= 0.10) level = 'medium';
    if (strongDivision && best.areaScore >= 0.7 && margin >= 0.2) level = 'high';
  }

  const isCalibrated = Boolean(calibration?.top1_accuracy != null);
  const ceiling = isCalibrated ? 'high' : UNCALIBRATED_CEILING;
  const displayed = capConfidence(level, ceiling);

  return {
    candidates: top.map((s, i) => ({ ...s, rank: i + 1 })),
    allScored: scored.length,
    confidence: displayed,
    rawConfidence: level,
    cappedByCalibration: displayed !== level,
    calibrated: isCalibrated,
    calibration,
    identified: Boolean(best && best.score >= MIN_EVIDENCE),
    margin,
    evidenceTokens: [...evidence],
    nearbyNames: nearbyNames.slice(0, 6),
  };
}

/**
 * How many of the ranked candidates currently sit inside a published window.
 *
 * This is deliberately reported as a COUNT ("2 of 3 candidate feeders"), never a
 * percentage. It describes agreement between candidates, not a probability that
 * the power will actually go out.
 */
export function scheduleAgreement(candidateEvaluations, nowMin) {
  const total = candidateEvaluations.length;
  if (!total) return { active: 0, total: 0, fraction: 0, label: 'No candidate schedule available' };
  const active = candidateEvaluations.filter((e) => e.active).length;
  return {
    active,
    total,
    fraction: active / total,
    label: active === 0 ? `None of your ${total} candidate feeder${total > 1 ? 's are' : ' is'} in a published window`
      : active === total ? `All ${total} candidate feeder${total > 1 ? 's are' : ' is'} in a published window`
      : `${active} of ${total} candidate feeders are in a published window`,
  };
}

export const CONFIDENCE_COPY = {
  high: 'High confidence. Strong geographic and text agreement.',
  medium: 'Medium confidence. The evidence points here, but other feeders remain possible.',
  low: 'Low confidence. Weak evidence, so treat this as one possibility among several.',
  none: 'Feeder not confidently identified. Showing area-level information instead.',
};

export const UNCALIBRATED_NOTE =
  'Feeder matching has not yet been calibrated against a verified address-to-feeder dataset, ' +
  'so confidence is capped at Medium and no percentage is shown.';
