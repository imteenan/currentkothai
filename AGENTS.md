# AGENTS.md — mandatory rules

These apply to every contributor, human or agent. They exist because the failure mode of this
kind of project is not a crash; it is confidently telling someone their power is fine when it
is not.

## 1. Provenance or it does not ship

No schedule, feeder mapping, service boundary, or outage claim may enter `data/` without a
recorded source URL, retrieval timestamp, content hash, and the parser adapter and version that
produced it. `validate.py` rejects documents with missing provenance fields — do not weaken it.

## 2. Uncertainty is never converted into certainty for UX convenience

- A text match like "Road 1, Section 12" is **evidence, not proof**.
- Show the top three candidate feeders. Never silently pick one.
- Do not print a percentage for feeder matching until `data/validation/calibration.json` exists
  and reports measured Top-1/Top-3 accuracy on a holdout set. `confidence.js` caps displayed
  confidence at Medium until then, and the cap lifts on its own once the file appears.
- If evidence is weak, say "feeder not confidently identified" and fall back to area level.

## 3. New parsers need tests and a rollback path

One adapter per utility per document family. Never one universal government-PDF parser. Treat a
layout change as a versioned migration, and add a fixture for the old layout **and** the new one.

Concretely: DESCO's PDF gained a `Load (MW)` column between 2022 and 2026. An adapter with a
hardcoded metadata-column count moved every window an hour late and invented a midnight window on
all 558 feeders. `tests/test_desco_parser.py::test_2026_layout_is_not_shifted` exists so that
cannot recur. Do not delete it.

## 4. A source disappearing must not erase validated data

A failed fetch flips a feed's `status` and keeps serving the last validated version with a
staleness warning. It never blanks `latest.json`. The same rule applies to generated GeoJSON —
`territories.py` carries forward the previous geometry when a rebuild fails.

## 5. Never retain a user's bill, account, meter or consumer number

Not by default, not "temporarily", not to enable a feature. Link people to their distributor's own
flow and tell them what they will need there. There is no account system, no analytics, and no
server-side logging of location.

## 6. Never present this project as official

Not in copy, not in styling, not in a domain name, not in a social preview. The ribbon at the top
of every page is not decorative. Every schedule links back to the publisher, and where the two
disagree, the publisher is right.

## Working agreement

Agents communicate through repository files, not through a proprietary harness. Any capable
contributor should be able to pick up a role and continue.

- `docs/CONTRACTS.md` is frozen. Changing a shape there means updating every producer and consumer
  in the same change.
- Prefer deterministic code on the request path. No LLM is required to answer a visitor.
- When you cannot verify something, record what you actually observed — including the failure.
  A verified "this utility publishes nothing machine-readable" is a valuable result, not a gap to
  paper over.
