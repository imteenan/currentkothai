// Renders the sources page from the same static JSON the main page reads.

import { $, esc, relTime } from './util.js';
import { load, getJSON } from './data.js';

const COVERAGE = {
  feeder: ['Full schedule', 'tag-official'],
  division: ['Zone sheets only', 'tag-estimated'],
  utility: ['Notices only', 'tag-unknown'],
  unknown: ['Nothing readable', 'tag-unknown'],
};

async function main() {
  const [reg, index, utils, dpdc] = await Promise.all([
    load.sources(), load.scheduleIndex(), load.utilities(),
    getJSON('registry/dpdc-zones.json', null),
  ]);
  const sources = reg.sources || [];
  const rows = index.utilities || [];
  const utilities = utils.utilities || [];

  renderSummary(reg, rows);
  renderCoverage(utilities, rows, sources);
  renderDpdc(dpdc);
  renderSources(sources);
}

function renderSummary(reg, rows) {
  const s = reg.summary || {};
  const claims = rows.reduce((n, r) => n + (r.claim_count || 0), 0);
  const stat = (n, label) => `<div class="fact">
      <dt>${esc(label)}</dt><dd>${esc(String(n))}</dd></div>`;
  $('#summary').innerHTML = `<dl class="facts" style="margin:0">
    ${stat(`${s.reachable ?? '—'}/${s.total ?? '—'}`, 'Sources reachable')}
    ${stat(claims.toLocaleString(), 'Schedule rows live')}
    ${stat(rows.filter((r) => r.status === 'fresh').length, 'Feeds parsing')}
    ${stat(rows.filter((r) => r.status === 'link-only').length, 'Link only')}
  </dl>
  <p class="t-caption muted" style="margin-top:10px">Registry last checked ${esc(relTime(reg.generated_at))}.</p>`;
}

function renderCoverage(utilities, rows, sources) {
  const head = ['Distributor', 'What exists', 'Rows live', 'Why'];
  const body = utilities.map((u) => {
    const row = rows.find((r) => String(r.utility).toUpperCase() === String(u.id).toUpperCase());
    const mine = sources.filter((s) => String(s.utility).toUpperCase() === String(u.id).toUpperCase());
    const level = row?.status === 'fresh' ? (row.coverage_level || 'unknown')
      : row?.status === 'link-only' ? (row.coverage_level === 'division' ? 'division' : 'utility')
      : mine.length ? 'utility' : 'unknown';
    const [label, cls] = COVERAGE[level] || COVERAGE.unknown;
    return `<tr>
      <td><strong>${esc(u.name)}</strong><br><span class="muted">${esc(u.full_name)}</span></td>
      <td><span class="tag ${cls}">${esc(label)}</span></td>
      <td class="num">${(row?.claim_count || 0).toLocaleString()}</td>
      <td>${esc(row?.message || u.coverage_description || '')}</td>
    </tr>`;
  }).join('');
  $('#coverage-table').innerHTML =
    `<thead><tr>${head.map((h) => `<th>${h}</th>`).join('')}</tr></thead><tbody>${body}</tbody>`;
}

function renderDpdc(dpdc) {
  const host = $('#dpdc-zones');
  if (!host) return;
  const zones = (dpdc?.zones || []).filter((z) => z.pdf_url);
  if (!zones.length) {
    host.innerHTML = `<div class="note note-warn"><svg class="ic" viewBox="0 0 24 24"><path d="M12 2.6c.5 0 .96.27 1.2.7l9 15.9a1.4 1.4 0 0 1-1.2 2.1H3a1.4 1.4 0 0 1-1.2-2.1l9-15.9c.24-.43.7-.7 1.2-.7Z"/></svg>
      <div><b>No DPDC zone sheets are reachable right now</b>
      Their index page has been observed going empty while the files stayed live. Try
      <a href="https://dpdc.org.bd/" target="_blank" rel="noopener">dpdc.org.bd</a> directly.</div></div>`;
    return;
  }
  host.innerHTML = `<div class="card" style="padding:16px">
    <div style="display:flex;flex-wrap:wrap;gap:8px">
      ${zones.map((z) => `<a class="btn btn-sm btn-quiet" href="${esc(z.pdf_url)}"
        target="_blank" rel="noopener">${esc(z.name)} ↗</a>`).join('')}
    </div>
    <p class="t-caption muted" style="margin-top:14px">
      ${zones.length} zones · links checked ${esc(relTime(dpdc.generated_at))} ·
      discovered by ${esc(dpdc.discovery || 'index')}
    </p>
  </div>`;
}

function renderSources(sources) {
  const head = ['Source', 'Utility', 'Format', 'Last checked', 'Status'];
  const body = sources.map((s) => {
    const ok = s.http_status === 200
      ? '<span class="tag tag-official">200</span>'
      : `<span class="tag tag-stale">${esc(String(s.http_status ?? 'fail'))}</span>`;
    const tls = s.tls_ok === false
      ? ' <span class="tag tag-estimated" title="Incomplete certificate chain">TLS</span>' : '';
    return `<tr>
      <td><a href="${esc(s.source_url)}" target="_blank" rel="noopener">${esc(s.title)}</a>
        ${s.notes ? `<br><span class="muted">${esc(s.notes)}</span>` : ''}</td>
      <td>${esc(s.utility)}</td>
      <td class="mono">${esc(s.observed_format || s.format)}</td>
      <td>${esc(s.verified_at ? relTime(s.verified_at) : 'never')}</td>
      <td>${ok}${tls}</td>
    </tr>`;
  }).join('');
  $('#sources-table').innerHTML =
    `<thead><tr>${head.map((h) => `<th>${h}</th>`).join('')}</tr></thead><tbody>${body}</tbody>`;
}

main().catch((e) => {
  console.error(e);
  $('#summary').innerHTML = `<div class="note note-stop"><div>
    <b>Could not load the registry</b>${esc(e.message)}</div></div>`;
});
