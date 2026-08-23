# Confidence model

Implemented in `apps/web/src/confidence.js`. The governing rule is AGENTS.md #2: uncertainty is
never converted into certainty for UX convenience.

## Three separate signals, never collapsed

| Signal | Question | How it is shown |
|---|---|---|
| Feeder match | How strongly does this point match a candidate feeder? | High / Medium / Low, capped at Medium |
| Source status | Did this come from the distributor, or from us? | `OFFICIAL` / `DERIVED` / `ESTIMATED` / `UNKNOWN` badge |
| Schedule reliability | How often do published windows match real outages? | **Not shown.** No ground truth exists |

The third is the one most products would fake. We have no verified outage observations, so there
is nothing to compute it from and we say so.

## Scoring

For each schedule claim:

```
score = 0.5 · divisionScore + 0.5 · areaScore
```

**divisionScore** comes from `rankDivisions()`: 1.0 for containment in the estimated division
polygon, otherwise an exponential decay on distance to each division reference point
(`exp(-km / 5.5) · 0.95`). Rank 1 → 1.0, rank 2 → 0.62, rank 3 → 0.36, unranked → 0.10. With no
geometry at all it stays neutral at 0.5 rather than pretending to know.

**areaScore** is weighted token coverage between the feeder's own "area under the feeder" text and
the evidence pool — the reverse-geocoded address plus every named OSM place within 2.5 km.
Structured fragments (`sector:7`, `road:12`, `block:c`) weigh double, because they locate far more
precisely than a neighbourhood name. Transliteration variants are folded through an explicit alias
table (`kallyanpur` → `kalyanpur`, `uttora` → `uttara`, …); bare numbers and stopwords are dropped.

## Thresholds

- `MIN_EVIDENCE = 0.28`. Below it, `identified: false` and the UI says *"feeder not confidently
  identified"* and falls back to area-level information.
- `low` — any candidate above the floor.
- `medium` — strong division match (≥0.95) **and** real text match (≥0.45) **and** margin over the
  runner-up ≥0.10.
- `high` — computed, but **capped to Medium** while `calibrated === false`.

## The calibration gate

`UNCALIBRATED_CEILING = 'medium'` applies until `data/validation/calibration.json` exists and
reports `top1_accuracy`. That file should be produced from a dataset of verified address→feeder
pairs, split into development and holdout sets, measuring Top-1 / Top-2 / Top-3 accuracy and
recording error types (boundary ambiguity, incomplete address, overlapping descriptions, stale
feeder text, geocoding failure).

Until that exists, **no percentage is rendered anywhere in the UI.** The cap lifts automatically
when the file appears — no code change needed, which is deliberate: the honest behaviour is the
default, and relaxing it requires producing evidence.

## "Probability of load shedding right now"

Users ask for one number. The honest version we show instead is **agreement between candidates**:

> *2 of 3 candidate feeders are in a published window*

This is a count, never a percentage, and it is labelled as candidate agreement rather than a
forecast. It answers the real question — "how much does the answer depend on which feeder is
actually mine?" — without implying we can predict the grid. When all candidates agree, the answer
is robust to feeder uncertainty; when they disagree, the interface says so in the headline.
