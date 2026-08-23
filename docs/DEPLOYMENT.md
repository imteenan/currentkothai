# Deployment

Portable by design: nothing depends on a named host. The only assumption is "something that serves
static files" plus "something that can run a Python script on a timer".

## Cloudflare Pages (recommended for traffic)

Free tier, **unlimited bandwidth**, no card, and it honours the `_headers` file in
`apps/web/` — which GitHub Pages ignores. Pick this if you expect real visitor numbers.

1. Cloudflare dashboard → Workers & Pages → Create → Pages → Connect to Git.
2. Build command: `bash tools/build-site.sh _site`
3. Build output directory: `_site`
4. Deploy. Every push to `main` rebuilds.

`_headers` sets a one-year immutable cache on `/vendor/*`, a day on the map layers, and
five minutes on `data/schedules/index.json`, so a schedule change reaches people quickly
while everything else is served from cache. The service worker (`apps/web/sw.js`) then
keeps a returning visitor working offline.

Ingestion still runs on GitHub Actions and commits data back to the repo; Cloudflare
redeploys on that commit. Nothing else changes.

## GitHub Pages (the simplest option)

1. Push the repo to GitHub. It must be **public** for unlimited free Actions minutes.
2. Settings → Pages → Source: **GitHub Actions**.
3. Push to `main`. `pages.yml` assembles `_site` and deploys.
4. Settings → Actions → General → Workflow permissions: **Read and write**, so `ingest.yml` can
   commit refreshed data and open issues on repeated parser failure.

No secrets, no API keys, no billing setup.

### What gets deployed

`pages.yml` copies `apps/web/*` to the site root and `data/{registry,geo,schedules,validation}`
into `_site/data/`. Deliberately excluded:

- `data/seed/archive/` and `data/seed/samples/` — large, never fetched by the browser
- `data/schedules/_quarantine/` — maintainer material, not visitor-facing
- dated snapshots older than the most recent seven per utility

### Custom domain

Add a `CNAME` file to `apps/web/`. Never register a domain that implies official status.

## Alternatives

| Host | How |
|---|---|
| Netlify / Vercel | Build `bash tools/build-site.sh _site`, publish `_site`; `_headers` works on Netlify |
| Any web server | Run the copy steps from `pages.yml`, serve `_site` |

The ingestion job can move to any cron runner — it is plain Python writing files into `data/`.

## Local

```bash
python tools/serve.py --port 8765
```

Serves `apps/web` and maps `/data/*` to the repo's `data/` directory, matching the deployed
layout. Set `<meta name="data-root">` in the HTML if you host `data/` elsewhere.

## Performance budget

```bash
bash tools/build-site.sh _site && python tools/perf_check.py _site
```

Fails if the critical path exceeds the gzipped budget. Currently ~375 KB, of which
MapLibre is ~206 KB. Wire this into CI before adding anything heavy.

## Operational checks after deploying

- `/data/schedules/index.json` loads and every row has a `status`
- The Sources page shows a non-zero reachable count
- A location lookup in central Dhaka returns DESCO and a timeline
- Watch the Actions tab: `ingest.yml` opens an issue labelled `ingestion` on repeated failure
