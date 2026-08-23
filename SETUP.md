# Going live

Everything is prepared. This is the whole deployment, start to finish.

---

## First: do you need a backend?

**No.** Not "you could avoid one" — there is genuinely nothing for a server to do.

A backend earns its place when a site must do something *per visitor*: check a
login, write to a database, keep a secret, or compute an answer that cannot be
computed on the client. This site does none of that.

| The thing a backend usually does | What happens here instead |
|---|---|
| Store data | The published JSON files in `data/` **are** the database |
| Run a query per visitor | The browser does it: point-in-polygon, feeder ranking, all the time maths |
| Hold an API key | There are none. Every service used is keyless |
| Authenticate someone | There are no accounts, by design |
| Fetch fresh data | GitHub Actions does it four times a day, on a schedule, and commits the result |
| Geocode an address | A 6,533-place gazetteer ships with the page and runs offline |

So the "backend" is a cron job that runs **before** anyone visits, not during.
That is the whole trick, and it is why this costs nothing and cannot fall over
under load: by the time a visitor arrives, the answer is already a static file on
a CDN.

**The one thing this shape cannot do** is push notifications, which is why the
site offers a calendar download instead and points people at their distributor's
own alerts. That was a deliberate trade, not an omission.

---

## Step 1: put it on GitHub

Already initialised and committed locally. Create an **empty public** repo on
GitHub (no README, no licence — the repo already has them), then:

```bash
git remote add origin https://github.com/YOUR-USERNAME/currentkothai.git && git branch -M main && git push -u origin main
```

It must be **public** for GitHub Actions to be free and unlimited.

## Step 2: let the data refresh itself

In the GitHub repo: **Settings → Actions → General → Workflow permissions →
Read and write permissions → Save.**

That lets the scheduled job commit refreshed schedules back. Without it the job
runs and then fails at the last step.

Then **Actions → Ingest schedules → Run workflow** once, to confirm it works
rather than waiting six hours to find out.

## Step 3: Cloudflare Pages

1. <https://dash.cloudflare.com> → **Workers & Pages** → **Create** → **Pages** →
   **Connect to Git**
2. Authorise GitHub, pick the repo
3. Settings:

   | Field | Value |
   |---|---|
   | Production branch | `main` |
   | Framework preset | **None** |
   | Build command | `bash tools/build-site.sh _site` |
   | Build output directory | `_site` |
   | Root directory | *(leave blank)* |

4. **Save and Deploy**

No environment variables. No Node version. No install command. The build is pure
shell: it copies `apps/web` and the four published `data/` folders into `_site`
and prunes old snapshots.

You get **`currentkothai.pages.dev`** in about a minute.

### Why Cloudflare over GitHub Pages

Unlimited bandwidth, and it reads the `_headers` and `_redirects` files that are
already in `apps/web/`. GitHub Pages ignores both. `.github/workflows/pages.yml`
still works if you ever want it as a fallback.

## Step 4: a free domain (optional)

`currentkothai.pages.dev` works forever and costs nothing. If you want something
shorter:

| Option | Looks like | How |
|---|---|---|
| **is-a.dev** | `currentkothai.is-a.dev` | PR one JSON file to their repo, usually merged in days |
| **js.org** | `currentkothai.js.org` | PR to their repo |
| **eu.org** | `currentkothai.eu.org` | Manual review, slow, but a real domain |

Then in Pages: **Custom domains → Set up a domain**, and point a `CNAME` at the
target Cloudflare shows you. Certificates issue automatically.

**Do not** register anything containing `desco`, `dpdc`, `gov` or `bd-power`.
This project's entire defence is that it never pretends to be official.

---

## How it runs, once live

```
     every 6 hours                     on every data commit
GitHub Actions ──────► commits ──────► Cloudflare rebuilds ──────► CDN
  fetch, parse,        to data/          (pure copy)              visitors
  validate, publish
```

Nothing runs while a visitor is on the site except their own browser.

## What to watch

| Signal | Where |
|---|---|
| Did the data refresh? | `data/schedules/index.json`, `status` and `retrieved_at` per utility |
| Did a parse go wrong? | `data/schedules/_quarantine/`, plus an auto-opened GitHub issue |
| Is the site heavy? | `bash tools/build-site.sh _site && python tools/perf_check.py _site` |
| Are sources still up? | `python -m workers.ingestion.build_registry` |

## Cost

| Piece | Provider | Cost |
|---|---|---|
| Hosting, CDN, TLS | Cloudflare Pages | $0 |
| Scheduled ingestion | GitHub Actions | $0 on a public repo |
| Database | none needed | $0 |
| Map tiles, place data, OCR | OpenFreeMap, OSM, Tesseract | $0 |
| Domain | `.pages.dev` or `is-a.dev` | $0 |
| **Total** | | **$0/month** |

Cloudflare's free tier allows 500 builds a month. Four ingest runs a day is about
120, so there is plenty of room.
