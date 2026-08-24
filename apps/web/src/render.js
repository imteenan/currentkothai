// Result markup. Pure state -> HTML string.
//
// Language rule: no jargon the reader did not bring with them. "Candidate feeder"
// became "feeders near you", and the reason we show three is stated in one line
// rather than assumed.

import { esc, icon, fmtDuration, relTime, fmtKm } from './util.js';
import { fmtWindow, freshness } from './schedule.js';

const tagClass = (b) => `tag tag-${String(b || 'unknown').toLowerCase()}`;

/* ---------------------------------------------------- the 24-hour strip */

export function renderStrip(windows, nowMin) {
  const pct = (m) => (m / 1440) * 100;

  const ticks = Array.from({ length: 24 }, (_, h) => {
    const major = h % 6 === 0;
    return `<span class="strip-hour" style="left:${pct(h * 60)}%">${
      major ? `<i>${h === 0 ? '12am' : h === 12 ? '12pm' : h < 12 ? `${h}am` : `${h - 12}pm`}</i>` : ''
    }</span>`;
  }).join('');

  const bars = windows.map((w, i) => {
    const active = nowMin >= w.startMin && nowMin < w.endMin;
    const width = pct(w.endMin - w.startMin);
    return `<div class="strip-win${active ? ' is-now' : ''}"
      style="left:${pct(w.startMin)}%;width:${width}%;animation-delay:${i * 55}ms"
      title="${esc(fmtWindow(w))}">${width > 12 ? `<span>${esc(fmtWindow(w))}</span>` : ''}</div>`;
  }).join('');

  return `<div>
    <div class="strip-track" role="img" aria-label="${esc(stripAlt(windows, nowMin))}">
      ${ticks}${bars}
      <span class="strip-now" style="left:${pct(nowMin)}%" aria-hidden="true"></span>
    </div>
    <div class="strip-legend">
      <span><i style="background:var(--shed-fill)"></i>Scheduled off</span>
      <span><i style="background:var(--live-fill)"></i>Happening now</span>
      <span><i style="background:var(--carbon);width:3px"></i>Now in Dhaka</span>
    </div>
  </div>`;
}

function stripAlt(windows, nowMin) {
  if (!windows.length) return 'No load-shedding windows published for today.';
  const active = windows.find((w) => nowMin >= w.startMin && nowMin < w.endMin);
  return `${windows.length} published window${windows.length > 1 ? 's' : ''}: ` +
    `${windows.map(fmtWindow).join('; ')}. ` +
    (active ? 'One is happening now.' : 'None is happening right now.');
}

/* ------------------------------------------------------------ the answer */

export function renderAnswer({ agreement, evalTop, now, hasSchedule }) {
  let cls = 'is-none';
  let state = 'No schedule published for here';
  let sub = 'Nothing we could read. The distributor’s own page is linked below.';

  if (hasSchedule) {
    if (agreement.active === 0) {
      cls = 'is-clear';
      state = 'Nothing scheduled right now';
      sub = evalTop?.next
        ? `The nearest feeder’s next window starts in ${fmtDuration(evalTop.minutesUntilNext)}.`
        : 'Nothing else published for the nearest feeder today.';
    } else if (agreement.active === agreement.total) {
      cls = 'is-active';
      state = 'Scheduled off right now';
      sub = evalTop?.active
        ? `Every feeder near you is in a window. The nearest one ends in ${fmtDuration(evalTop.minutesLeftInActive)}.`
        : 'Every feeder near you is inside a published window.';
    } else {
      cls = 'is-split';
      state = 'Depends which feeder you’re on';
      sub = `${agreement.active} of ${agreement.total} feeders near you are scheduled off right now.`;
    }
  }

  const facts = [];
  if (hasSchedule) {
    if (evalTop?.active) facts.push(fact('Ends in', fmtDuration(evalTop.minutesLeftInActive)));
    else if (evalTop?.next) facts.push(fact('Next window', fmtDuration(evalTop.minutesUntilNext)));
    facts.push(fact('Off today', fmtDuration(evalTop?.totalMin ?? 0)));
    facts.push(fact('Feeders off now', `${agreement.active}<small>of ${agreement.total}</small>`));
  }
  facts.push(fact('Dhaka time', esc(now.hhmm), esc(now.weekdayName)));

  return `<p class="answer-state ${cls}">${esc(state)}</p>
    <p class="answer-sub">${esc(sub)}</p>
    <dl class="facts">${facts.join('')}</dl>`;
}

const fact = (label, value, hint = '') => `<div class="fact">
  <dt>${esc(label)}</dt><dd>${value}${hint ? `<small>${hint}</small>` : ''}</dd></div>`;

/* ---------------------------------------------------------- feeder list */

/** First few areas, with a count for the rest. Keeps the row one line tall. */
function areaSummary(areas, limit = 3) {
  if (areas.length <= limit) return areas.join(', ');
  return `${areas.slice(0, limit).join(', ')} +${areas.length - limit} more`;
}

/**
 * The areas a feeder serves, as the sheet lists them.
 *
 * Marked as machine-read when it came from a scan. These names are OCR output
 * from a photographed sheet and contain occasional wrong letters, so the note
 * and the link to the original are not decoration: they are how a reader checks
 * a name that looks not-quite-right.
 */
export function renderAreas(claim) {
  const areas = Array.isArray(claim?.areas) ? claim.areas : [];
  const name = claim?.feeder_name || '';
  if (!areas.length && !name) return '';
  // Only call it an area list when it is one. Where the sheet gives a feeder
  // name and no areas, labelling the name "areas on this feeder" would be a
  // small lie about what the source says.
  const heading = areas.length ? 'Areas on this feeder' : 'Feeder name on the sheet';
  return `<div class="areas-served">
    <b class="t-caption">${heading}</b>
    <p lang="bn" style="margin:4px 0 0">${esc(areas.join(', ') || name)}</p>
    ${claim?.text_source === 'ocr'
      ? `<p class="t-caption muted" style="margin:6px 0 0">Read automatically from a scanned
         sheet, so spellings may be imperfect. Check against the original below.</p>`
      : ''}
  </div>`;
}

export function renderFeeders(candidates, evaluations, selectedIndex) {
  if (!candidates.length) return '';
  return `<div class="feeders">${candidates.map((c, i) => {
    const ev = evaluations[i];
    const when = !ev ? '—'
      : ev.active ? 'Off now'
      : ev.next ? `in ${fmtDuration(ev.minutesUntilNext)}`
      : 'Clear today';
    const zone = c.claim.division_canonical || c.claim.division || 'Unknown zone';
    // A billing code identifies the feeder; the Bengali name and the area list
    // are what let a reader recognise it as theirs. Scanned sheets often give
    // one and not the other, so show whichever exist rather than a row number.
    const code = c.claim.billing_code || '';
    const name = c.claim.feeder_name || '';
    // `feeder` falls back to a row position when nothing on the sheet could be
    // read. "row-05" identifies nothing to a reader, so say so plainly and let
    // the area list below do the identifying.
    const positional = /^row-\d+$/.test(c.claim.feeder || '');
    const label = code || name || (positional ? 'Unnamed feeder' : c.claim.feeder)
                  || 'Unnamed feeder';
    const sub = code && name ? name : '';
    const areas = Array.isArray(c.claim.areas) ? c.claim.areas : [];
    return `<button type="button" class="feeder" data-cand="${i}" aria-pressed="${i === selectedIndex}">
      <span>
        <span class="feeder-name">${esc(label)}</span>
        ${sub ? `<span class="feeder-sub" lang="bn">${esc(sub)}</span>` : ''}
        <span class="feeder-meta">${esc(zone)}${c.claim.load_mw ? ` · ${c.claim.load_mw} MW` : ''}</span>
        ${areas.length ? `<span class="feeder-areas" lang="bn">${esc(areaSummary(areas))}</span>` : ''}
      </span>
      <span class="feeder-when${ev?.active ? ' on' : ''}">${esc(when)}</span>
    </button>`;
  }).join('')}</div>`;
}

/* -------------------------------------------------------------- evidence */

export function renderEvidence({ ranking, feeders, reverse }) {
  const zones = (ranking?.ranked || []).slice(0, 3).map((d, i) =>
    `<li><b>${i + 1}. ${esc(d.name)}</b>: ${esc([...new Set(d.why)].join('; '))}</li>`).join('');
  const tokens = (feeders?.candidates?.[0]?.matchedTokens || [])
    .map((t) => `<code>${esc(t.replace(':', ' '))}</code>`).join(' ');

  return `<details class="fold">
    <summary>Show the working</summary>
    <div class="body stack" style="--flow:14px">
      <div><b>Where we placed you</b><br>${esc(reverse?.display || 'No nearby place name resolved.')}</div>
      ${zones ? `<div><b>Zones, nearest first</b><ol style="padding-left:18px;margin-top:6px">${zones}</ol></div>` : ''}
      ${tokens
        ? `<div><b>Words that matched a feeder’s area description</b><br>${tokens}
           <br><span class="muted">A text match is evidence, not proof.</span></div>`
        : `<div class="muted">No area-description words matched, so this ranking rests on distance alone.</div>`}
      <div class="muted">Confidence is capped at Medium until feeder matching is checked against
        verified addresses, so no percentage is shown anywhere.</div>
    </div>
  </details>`;
}

/* -------------------------------------------------------------- source */

export function renderProvenance(schedule, sourceEntry) {
  if (!schedule?.source) {
    return `<div class="prov">No source record for this view.</div>`;
  }
  const s = schedule.source;
  const f = freshness(s.retrieved_at);
  const stale = f.state === 'fresh' ? ''
    : `<span class="tag tag-stale">${f.state === 'very-stale' ? 'Very stale' : 'Stale'}</span>`;
  return `<div class="prov">
    <span class="${tagClass(schedule.badge)}">${esc(schedule.badge || 'UNKNOWN')}</span>
    ${stale}
    <span>${esc(schedule.publisher || schedule.utility)} · read ${esc(relTime(s.retrieved_at))}</span>
    <span class="grow"></span>
    <a href="${esc(s.source_url)}" target="_blank" rel="noopener noreferrer">
      Open the original ${esc(sourceEntry?.format?.toUpperCase() || 'document')} ↗</a>
  </div>
  <p class="prov-meta">${esc(s.parser_adapter || 'n/a')}@${esc(s.parser_version || '0')}
    · sha ${esc((s.sha256 || '').slice(0, 12) || 'n/a')}
    · effective ${esc(schedule.effective_date || 'unknown')}</p>`;
}

/* --------------------------------------------------------- feed health */

export function renderFeedHealth(index) {
  if (!index?.length) return '';
  const pill = (u) => {
    const f = freshness(u.retrieved_at, u.stale_after_hours ?? 36);
    let cls = 'tag-unknown', label = 'unknown';
    if (u.status === 'link-only') { cls = 'tag-unknown'; label = 'PDF link only'; }
    else if (u.status === 'unavailable') { cls = 'tag-stale'; label = 'source down'; }
    else if (f.state === 'fresh') { cls = 'tag-official'; label = `read ${relTime(u.retrieved_at)}`; }
    else if (f.state !== 'unknown') { cls = 'tag-estimated'; label = `stale · ${relTime(u.retrieved_at)}`; }
    return `<span class="tag ${cls}" title="${esc(u.message || u.source_url || '')}">${esc(u.utility)} · ${esc(label)}</span>`;
  };
  return `<div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;justify-content:center">
    ${index.map(pill).join('')}
    <a class="t-caption" href="sources.html#health">All sources</a>
  </div>`;
}

/* -------------------------------------------------------------- alerts */

export function renderAlertCards(alerts, utilities) {
  if (!alerts?.length) return '';
  return alerts.map((a) => {
    const u = utilities.find((x) => String(x.id).toUpperCase() === String(a.utility).toUpperCase());
    const rows = [];
    if (a.hotline?.length) rows.push(`<li>Hotline ${a.hotline.map((h) =>
      `<a href="tel:${esc(String(h).replace(/[^\d+]/g, ''))}" class="mono">${esc(h)}</a>`).join(', ')}</li>`);
    if (a.complaint_url) rows.push(`<li><a href="${esc(a.complaint_url)}" target="_blank" rel="noopener">Report an outage ↗</a></li>`);
    if (a.website_url) rows.push(`<li><a href="${esc(a.website_url)}" target="_blank" rel="noopener">Official site ↗</a></li>`);

    return `<div class="card">
      <h3 style="font-size:var(--text-subheading);font-weight:var(--w-semibold)">${esc(u?.name || a.utility)}</h3>
      <p class="t-caption muted" style="margin-top:2px">${esc(u?.full_name || '')}</p>
      <ul style="list-style:none;padding:0;display:grid;gap:8px;margin-top:14px;font-size:var(--text-body-sm)">
        ${rows.length ? rows.join('') : '<li class="muted">No verified channel.</li>'}
      </ul>
      ${a.notes ? `<p class="t-caption muted" style="margin-top:12px">${esc(a.notes)}</p>` : ''}
    </div>`;
  }).join('');
}
