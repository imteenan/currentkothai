/**
 * A cron that actually fires.
 *
 * GitHub runs scheduled workflows on a best-effort basis, and for this repo
 * "best effort" measured out as roughly three runs a day, two to four hours
 * late, with whole days missed:
 *
 *   scheduled 08-28 17:40Z -> ran 21:30Z   (3h50m late)
 *   scheduled 08-29 07:40Z -> ran 11:20Z   (3h40m late)
 *   scheduled 08-29 11:40Z -> ran 15:44Z   (4h04m late)
 *   scheduled 08-30 04:40Z -> never fired
 *
 * That is why the site kept showing yesterday's sheet. Aiming the cron at
 * DESCO's publishing window does not help when delivery drifts by four hours,
 * and adding cron lines makes it worse: asking for twelve runs a day got us
 * fewer than asking for four, because GitHub throttles repos that ask for a lot.
 *
 * Cloudflare Cron Triggers fire on time. So the schedule lives here and GitHub
 * only does the work, dispatched on demand. A workflow_dispatch is queued
 * immediately rather than behind the shared scheduler.
 *
 * This is a SEPARATE Worker from the site. The site is assets-only and serves
 * Dhaka's load-shedding schedule; nothing here should be able to break that.
 */

/** Which cron asks for what. Keys must match the crons in wrangler.toml. */
const PLAN = {
  // Every two hours across the Dhaka working day. DESCO issues a new sheet
  // each weekday morning and costs about a minute to fetch and parse, so it
  // can be checked often. The ingest only commits when the sheet actually
  // changed, so a no-op sweep costs nothing downstream.
  '0 1,3,5,7,9,11,13,15 * * *': { only: 'DESCO' },
  // Twice a day for everything, including DPDC's 17 scanned sheets. That is
  // ~10 minutes of OCR and a standing schedule that rarely changes.
  '30 2,14 * * *': {},
};

const REPO = 'imteenan/currentkothai';
const WORKFLOW = 'ingest.yml';

async function dispatch(env, inputs) {
  const res = await fetch(
    `https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches`,
    {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${env.GITHUB_TOKEN}`,
        Accept: 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        // GitHub rejects API calls with no User-Agent.
        'User-Agent': 'currentkothai-scheduler',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ ref: 'main', inputs }),
    },
  );
  // 204 No Content is success here. Anything else is worth seeing in the log,
  // because a silent failure would put us back where we started: no runs, no
  // errors, and a site quietly going stale.
  if (res.status !== 204) {
    throw new Error(`dispatch failed: ${res.status} ${await res.text()}`);
  }
}

export default {
  async scheduled(event, env, ctx) {
    const inputs = PLAN[event.cron] ?? {};
    ctx.waitUntil(
      dispatch(env, inputs).then(
        () => console.log(`dispatched ${event.cron} ${JSON.stringify(inputs)}`),
        (err) => { console.error(String(err)); throw err; },
      ),
    );
  },

  /**
   * Manual trigger, so a stale site can be fixed from a browser without
   * waiting for the next cron. Requires the same token as a header, since an
   * open endpoint would let anyone spend the repo's Actions minutes.
   */
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname !== '/run') {
      return new Response('currentkothai scheduler. POST /run to dispatch.', {
        status: 404,
      });
    }
    if (request.headers.get('x-trigger-key') !== env.TRIGGER_KEY) {
      return new Response('forbidden', { status: 403 });
    }
    const only = url.searchParams.get('only');
    try {
      await dispatch(env, only ? { only } : {});
      return new Response(`dispatched${only ? ` (${only})` : ''}\n`);
    } catch (err) {
      return new Response(`${err}\n`, { status: 502 });
    }
  },
};
