# Can this cover all of Bangladesh?

**Short answer: not today, and the blocker is not effort — it is that five of the six
distributors do not publish a machine-readable schedule at all.**

This document records what was actually observed, so the gap is auditable rather than a
vague "coverage is limited". Every claim below was checked directly against the
distributor's own site; the probe results are in `data/registry/sources.json`.

## Verdict by entity

| Entity | Serves | Publishes | Machine-readable? | Daily? | Status here |
|---|---|---|---|---|---|
| **DESCO** | North Dhaka, Tongi | Feeder-level PDF, one per weekday | **Yes** — ruled table, parses cleanly | Republished irregularly | **Live.** 558 feeder claims, 24 zones |
| **DPDC** | South Dhaka, Narayanganj | 36 per-zone PDFs | **Partly** — see below | Revision counter increments | Linked, not parsed |
| **NESCO** | Rajshahi + Rangpur towns | Scanned images | **No** — 0 extractable characters | Irregular | Link only |
| **BPDB** | Chattogram, Sylhet, Mymensingh towns | Notices | **No** consumer schedule found | — | Link only |
| **WZPDCL** | Khulna + Barishal towns | Notices | **No** consumer schedule found | — | Link only |
| **BREB** | **Rural nationwide** | ~80 independent PBS sites | **No** national feed | — | Link only |
| **PGCB** | National grid | Generation vs demand HTML | **Yes** | Updated through the day | Registered, parser not built |
| Power Division | Ministry | Notices, 16999 call centre | n/a | — | Linked |

## Why "all of Bangladesh" is currently impossible

**The rural majority has no publisher.** BREB serves rural Bangladesh through roughly 80
Palli Bidyut Samity cooperatives. There is no national feed; each samity publishes
separately, if at all. That is not one integration — it is up to eighty, against sites
that may carry nothing but notices. This is the single largest population gap, and no
amount of engineering here closes it while the data does not exist.

**Three distributors publish nothing structured.** BPDB, WZPDCL and NESCO were each
probed directly. NESCO's schedule PDFs are photographs of paper: `pdfplumber` reports one
embedded image and zero characters. Reading them would need OCR over Bengali scans, and
`AGENTS.md` rule 1 forbids publishing OCR output as fact without validation — so the
honest output is a link, not a guess.

**DPDC is solved, and the lesson generalises.** `pdftotext` returns mojibake because
the Bangla is legacy Bijoy, which made the file look unreadable. It is not. Rendering a
page to an image shows an ordinary ruled table, and the parts that matter survive
extraction as clean ASCII: feeder codes, loads and hour headers. The shedding mark is not
text at all, it is a black filled rectangle, so the schedule is recovered from geometry.
19 of the 36 zones are digital PDFs and now parse. The other 17 are CamScanner
photographs, which is a different problem (OCR), not the same one.

**When a document resists, render it and look at it.** That single step is what unblocked
DPDC, and it is what should be tried on any source before it is written off.

**Even DESCO is weekday-shaped, not daily.** DESCO publishes one PDF per weekday. The
pipeline currently ingests the newest one it can find, so on a Wednesday the site may be
showing the Sunday edition. It says so on the page rather than pretending otherwise, but
ingesting all seven weekday PDFs is the obvious fix and is not yet done.

## What it would take, in priority order

1. **DPDC partial adapter** — feeder code + MW + windows, area text marked unavailable.
   Roughly doubles Dhaka coverage. No new research needed; the samples are already in
   `data/seed/samples/`. **Highest value per hour of work.**
2. **DESCO all seven weekdays** — `desco_listing_v1.discover()` already returns every PDF
   candidate. Publish `weekday-<0..6>.json` and select by today's Dhaka weekday. Removes
   the "this is the Sunday schedule" caveat entirely.
3. **PGCB grid parser** — national generation vs demand gives *every* visitor in the
   country something real and current ("today's shortfall is X MW"), even where no local
   feeder schedule exists. This is the only realistic route to a nationwide answer today.
4. **Bijoy → Unicode converter** — unlocks DPDC area names, and any other agency using the
   same legacy encoding. Self-contained and testable.
5. **PBS survey** — sample the ~80 Palli Bidyut Samity sites. Many `*.gov.bd` sites run the
   same national portal template; if the samity sites do too, one adapter could cover the
   rural majority. This is the highest-population prize and the least certain.
6. **NESCO OCR** — last, and only behind human review.

## The honest framing for users

The site should not imply national coverage it does not have. Today it answers well for
north Dhaka, points DPDC users at their own zone PDF, and tells everyone else plainly
that their distributor publishes nothing a computer can read — while still giving them
the official hotline and their distributor's own page. That is a smaller promise than
"all of Bangladesh", and it is one the data can actually keep.
