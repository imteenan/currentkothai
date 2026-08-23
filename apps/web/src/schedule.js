// Schedule window maths. All published schedules are Asia/Dhaka (UTC+6) local time,
// regardless of where the visitor's device clock is set.

export const DHAKA_TZ = 'Asia/Dhaka';
const WEEKDAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];

const partsFmt = new Intl.DateTimeFormat('en-CA', {
  timeZone: DHAKA_TZ, year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', weekday: 'short', hourCycle: 'h23',
});

/** Current wall-clock state in Dhaka, independent of the device timezone. */
export function dhakaNow(at = new Date()) {
  const p = Object.fromEntries(partsFmt.formatToParts(at).map((x) => [x.type, x.value]));
  const weekday = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].indexOf(p.weekday);
  const minutes = Number(p.hour) * 60 + Number(p.minute);
  return {
    date: `${p.year}-${p.month}-${p.day}`,
    hhmm: `${p.hour}:${p.minute}`,
    minutes,
    weekday,
    weekdayName: WEEKDAYS[weekday] ?? '',
    isDeviceTzDhaka: Intl.DateTimeFormat().resolvedOptions().timeZone === DHAKA_TZ,
  };
}

/** "14:30" -> 870. "24:00" -> 1440. Returns null on malformed input. */
export function toMinutes(hhmm) {
  const m = /^(\d{1,2}):(\d{2})$/.exec(String(hhmm ?? '').trim());
  if (!m) return null;
  const h = Number(m[1]), mm = Number(m[2]);
  if (h > 24 || mm > 59 || (h === 24 && mm !== 0)) return null;
  return h * 60 + mm;
}

export function fromMinutes(mins) {
  const m = ((Math.round(mins) % 1440) + 1440) % 1440;
  return `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`;
}

/** Display form: 14:00-15:00 becomes "2:00 - 3:00 PM". */
export function fmtWindow(w) {
  const t = (mins) => {
    const m = mins === 1440 ? 1439.9 : mins;
    const h24 = Math.floor(m / 60), mm = Math.round(m % 60);
    const h12 = h24 % 12 === 0 ? 12 : h24 % 12;
    return { h12, mm, ap: h24 < 12 || h24 === 24 ? 'AM' : 'PM' };
  };
  const a = t(w.startMin), b = t(w.endMin === 1440 ? 1440 : w.endMin);
  const fa = `${a.h12}:${String(a.mm).padStart(2, '0')}`;
  const fb = `${b.h12}:${String(b.mm).padStart(2, '0')}`;
  return a.ap === b.ap ? `${fa} – ${fb} ${b.ap}` : `${fa} ${a.ap} – ${fb} ${b.ap}`;
}

/**
 * Normalise raw {start,end} strings into sorted, merged {startMin,endMin} windows.
 * Drops anything malformed rather than guessing — a bad window must not become a claim.
 */
export function normaliseWindows(raw = []) {
  const parsed = [];
  for (const w of raw) {
    const s = toMinutes(w.start), e = toMinutes(w.end);
    if (s === null || e === null) continue;
    if (e <= s) continue;                       // never wrap midnight; sources split those rows
    parsed.push({ startMin: s, endMin: e });
  }
  parsed.sort((a, b) => a.startMin - b.startMin);
  const merged = [];
  for (const w of parsed) {
    const last = merged[merged.length - 1];
    if (last && w.startMin <= last.endMin) last.endMin = Math.max(last.endMin, w.endMin);
    else merged.push({ ...w });
  }
  return merged;
}

/** Where "now" sits relative to a day's windows. */
export function evaluateDay(windows, nowMin) {
  const active = windows.find((w) => nowMin >= w.startMin && nowMin < w.endMin) || null;
  const next = windows.find((w) => w.startMin > nowMin) || null;
  const totalMin = windows.reduce((s, w) => s + (w.endMin - w.startMin), 0);
  return {
    windows,
    active,
    next,
    totalMin,
    minutesIntoActive: active ? nowMin - active.startMin : null,
    minutesLeftInActive: active ? active.endMin - nowMin : null,
    minutesUntilNext: next ? next.startMin - nowMin : null,
  };
}

/**
 * Claims that apply to a given weekday.
 *
 * Order of preference: claims stamped with today's weekday, then claims with no
 * weekday at all (they apply every day), then EVERYTHING.
 *
 * That last fallback matters. DESCO publishes one sheet per weekday and we can
 * usually only retrieve the newest one, so on a Monday the file may hold only
 * Sunday's rows. Returning nothing there left the page saying "0 of 0 feeders",
 * which reads as "no load shedding" when it actually means "wrong day". Showing
 * the rows we have, with the weekday mismatch stated loudly, is the honest
 * behaviour; the caller renders that warning.
 */
export function claimsForWeekday(claims = [], weekday) {
  const dayed = claims.filter((c) => c.weekday === weekday);
  if (dayed.length) return dayed;
  const undated = claims.filter((c) => c.weekday === null || c.weekday === undefined);
  if (undated.length) return undated;
  return claims;
}

/** A feed is stale when the last successful retrieval is older than `hours`. */
export function freshness(retrievedAt, staleAfterHours = 36) {
  if (!retrievedAt) return { state: 'unknown', ageHours: null };
  const age = (Date.now() - new Date(retrievedAt).getTime()) / 3_600_000;
  if (Number.isNaN(age)) return { state: 'unknown', ageHours: null };
  if (age > staleAfterHours * 4) return { state: 'very-stale', ageHours: age };
  if (age > staleAfterHours) return { state: 'stale', ageHours: age };
  return { state: 'fresh', ageHours: age };
}

/**
 * An RFC 5545 calendar file for the day's windows.
 * This is the free, no-server alternative to push notifications: the visitor's own
 * calendar app does the reminding, and we never learn who they are.
 */
export function buildICS({ windows, date, label, feeder, utility, sourceUrl }) {
  const [y, m, d] = date.split('-').map(Number);
  // Dhaka is UTC+6 with no DST, so the offset is a constant subtraction.
  const toUTC = (mins) => {
    const dt = new Date(Date.UTC(y, m - 1, d, 0, 0, 0) + (mins - 360) * 60000);
    return dt.toISOString().replace(/[-:]/g, '').replace(/\.\d{3}/, '');
  };
  const stamp = new Date().toISOString().replace(/[-:]/g, '').replace(/\.\d{3}/, '');
  const fold = (s) => s.replace(/(.{72})/g, '$1\r\n ');
  const lines = [
    'BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//currentKothai//Unofficial BD power schedule//EN',
    'CALSCALE:GREGORIAN', 'METHOD:PUBLISH',
  ];
  windows.forEach((w, i) => {
    lines.push(
      'BEGIN:VEVENT',
      `UID:${date}-${i}-${(feeder || 'area').replace(/\W+/g, '')}@currentkothai.local`,
      `DTSTAMP:${stamp}`,
      `DTSTART:${toUTC(w.startMin)}`,
      `DTEND:${toUTC(w.endMin)}`,
      fold(`SUMMARY:Possible load shedding — ${label || feeder || 'your area'}`),
      fold(`DESCRIPTION:Published probable load-shedding window from ${utility || 'the distributor'}.` +
        ' This is a published schedule, not a guarantee that power will be cut.' +
        (sourceUrl ? `\\n\\nOfficial source: ${sourceUrl}` : '')),
      'BEGIN:VALARM', 'TRIGGER:-PT15M', 'ACTION:DISPLAY',
      'DESCRIPTION:Possible load shedding in 15 minutes', 'END:VALARM',
      'END:VEVENT',
    );
  });
  lines.push('END:VCALENDAR');
  return lines.join('\r\n');
}
