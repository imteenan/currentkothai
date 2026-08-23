# Hosting and running cost

Two questions answered here: how to put this online for free, and how to keep the
AI cost at essentially zero.

## Token cost: zero per day, by design

**Nothing on the daily path uses an LLM.** The scheduled job is plain Python:
fetch, hash, parse, validate, write JSON. It runs in GitHub Actions on a cron and
never calls a model. The website is static files plus browser JavaScript. A
visitor costs nothing. A day with a million visitors costs nothing.

The only tokens ever spent are when *you* ask an assistant to change something.
So the rule is simple:

| Situation | Token cost |
|---|---|
| Site running, data refreshing 4x a day | **0** |
| A distributor changes a URL and ingestion self-heals | **0** |
| A parse fails validation and the old data keeps serving | **0** |
| You ask for a new feature or a parser fix | Normal session cost |

### Keeping it that way

1. **Never put a model in the request path or the cron.** `AGENTS.md` already
   forbids it. If a future change needs an LLM, it belongs in an offline,
   human-reviewed workflow, not in `run_ingest.py`.
2. **Read `CONTEXT.md` first in any new session.** It is one page and replaces
   re-reading the codebase, which is where tokens actually go.
3. **Point at a file, not a symptom.** "Fix the hour parsing in
   `workers/parsers/dpdc_pdf_v1.py`" costs a fraction of "the schedule looks
   wrong", because the second one makes the assistant go looking.
4. **Let the pipeline tell you what broke.** `data/schedules/index.json` carries
   a status and message per utility, `data/schedules/_quarantine/` holds any
   rejected parse with its findings, and the workflow opens a GitHub issue on
   repeated failure. Paste that, rather than asking for an investigation.
5. **Batch requests.** One session with five changes costs far less than five
   sessions with one.

## Free hosting

### Recommended: Cloudflare Pages

Free tier, unlimited bandwidth, no card, and it honours the `_headers` file that
GitHub Pages ignores.

1. Push this repo to GitHub (public).
2. Cloudflare dashboard, Workers & Pages, Create, Pages, Connect to Git.
3. Build command: `bash tools/build-site.sh _site`
4. Output directory: `_site`
5. Deploy.

You immediately get **`currentkothai.pages.dev`**, which is free forever and
needs no domain purchase.

Ingestion still runs on GitHub Actions and commits refreshed data; Cloudflare
rebuilds on that commit.

### Alternative: GitHub Pages

Already wired in `.github/workflows/pages.yml`. Settings, Pages, Source: GitHub
Actions. Gives `<user>.github.io/<repo>`. Simpler, but no header control and a
soft bandwidth limit.

## Free domain

You do not need to buy one. In rough order of how good the name looks:

| Option | Example | Cost | Notes |
|---|---|---|---|
| **Cloudflare Pages subdomain** | `currentkothai.pages.dev` | Free, instant | Works today, no application |
| **`is-a.dev`** | `currentkothai.is-a.dev` | Free | PR a JSON file to their GitHub repo, usually merged in days |
| **`js.org`** | `currentkothai.js.org` | Free | PR to their repo; they expect a JS-related project, which this is |
| **`eu.org`** | `currentkothai.eu.org` | Free | Manual review, can take weeks, but a real second-level domain |
| **`.xyz` / `.top` promo** | `currentkothai.xyz` | ~$1-3/year | Not free, but close, and looks the most normal |

**Do not** pick a name that implies official status. `currentkothai` is fine;
anything containing `desco`, `dpdc`, `gov`, or `bd-power` is not, and would
invite a takedown that this project should not be picking a fight over.

### Pointing a free domain at Cloudflare Pages

1. In Pages, open the project, Custom domains, Set up a domain.
2. Enter the domain, Cloudflare shows a `CNAME` target.
3. In the free-domain provider's PR or panel, add a `CNAME` record pointing at
   that target.
4. Certificates are issued automatically.

## What it all costs, totalled

| Piece | Provider | Cost |
|---|---|---|
| Hosting and CDN | Cloudflare Pages | $0 |
| Scheduled ingestion | GitHub Actions | $0 (public repo) |
| Database | none, the JSON files are the database | $0 |
| Map tiles | OpenFreeMap | $0 |
| Place data | OpenStreetMap, geoBoundaries | $0 |
| OCR | Tesseract, runs in CI | $0 |
| Domain | `.pages.dev` or `is-a.dev` | $0 |
| **Total** | | **$0 per month** |
