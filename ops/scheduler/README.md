# The scheduler: making the daily update actually happen

The site kept showing yesterday's sheet. The ingest was never broken — it was
barely being *run*.

GitHub runs scheduled workflows on a best-effort basis. Measured on this repo:

| scheduled | actually ran | late by |
|---|---|---|
| 08-28 17:40Z | 21:30Z | 3h 50m |
| 08-29 07:40Z | 11:20Z | 3h 40m |
| 08-29 11:40Z | 15:44Z | 4h 04m |
| 08-30 04:40Z | never fired | — |

Roughly three of four runs a day, hours late, with whole days missed. Two
things that look like fixes are not:

- **Aiming the cron at DESCO's publishing window.** Pointless when delivery
  drifts by four hours.
- **Adding more cron lines.** Actively harmful. GitHub throttles repos that ask
  for a lot: twelve crons a day delivered *fewer* runs than four did.

So the schedule moves to Cloudflare, which fires on time, and GitHub keeps
doing the work — dispatched on demand. A `workflow_dispatch` is queued
immediately instead of waiting behind the shared scheduler.

This is a **separate Worker** from the site. The site is assets-only; nothing
here can break it.

---

## Setup (about five minutes, once)

### 1. Create a GitHub token

<https://github.com/settings/personal-access-tokens/new>

- **Resource owner**: your account
- **Repository access** → Only select repositories → `currentkothai`
- **Permissions** → Repository permissions → **Actions: Read and write**
- Expiration: whatever you are willing to renew. A year is reasonable; put a
  reminder in your calendar, because when it expires the updates stop silently.

Copy the token. GitHub shows it once.

### 2. Deploy the Worker

From this directory:

```bash
npx wrangler deploy
```

It will ask you to log in to Cloudflare the first time.

### 3. Give it the token

```bash
npx wrangler secret put GITHUB_TOKEN
```

Paste the token when prompted. Then a key for the manual trigger — any long
random string you invent:

```bash
npx wrangler secret put TRIGGER_KEY
```

### 4. Check it works

```bash
npx wrangler tail
```

Leave that running and trigger a dispatch by hand:

```bash
curl -X POST -H "x-trigger-key: YOUR_TRIGGER_KEY" \
  https://currentkothai-scheduler.YOUR-SUBDOMAIN.workers.dev/run?only=DESCO
```

You should see `dispatched` in the tail, and a new run appear at
<https://github.com/imteenan/currentkothai/actions> within seconds.

---

## What it does

| cron (UTC) | Dhaka | what runs |
|---|---|---|
| `0 1,3,5,7,9,11,13,15 * * *` | 07:00–21:00, every 2h | DESCO only (~1 min) |
| `30 2,14 * * *` | 08:30 and 20:30 | everything, incl. DPDC OCR (~10 min) |

DESCO issues a new sheet every weekday morning and is cheap to check, so it is
checked often. DPDC publishes a standing schedule that rarely changes and costs
ten minutes of OCR, so twice a day is right for it.

A sweep that finds nothing changed does not commit, so it costs no rebuild.

## Cost

Zero. Cloudflare's free plan includes cron triggers and 100,000 requests a day;
this uses about ten. GitHub Actions is free on public repositories.

## When it stops working

The token expiring is the likeliest cause, and it fails silently from the
outside. Two places to look:

- `npx wrangler tail` — a 401 from GitHub means the token is dead
- The repo's Actions tab — if dispatches stopped arriving, the Worker is not
  reaching GitHub

The ingest workflow also warns in its own job summary when more than 14 hours
have passed since the last data commit.

## The manual lever

Whenever the site looks stale and you do not want to debug anything:

**Repo → Actions → "Ingest schedules" → Run workflow.**

That is a dispatch too, and it runs immediately.
