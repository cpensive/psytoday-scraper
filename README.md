# psytoday-scraper

Scrapes Psychology Today's NYC couples-therapist directory, extracts structured
profile data, screens out clear non-fits, and ranks the rest on transparent,
auditable signals — producing a single-file HTML app for reviewing candidates
and picking who to call.

This is a personal screening tool, not a quality judgment on any therapist. A
profile can only tell you so much; the ranking exists to get a couple from
~900 directory listings down to a handful worth a 15-minute intro call.

## Pipeline

```
scrape.py        listing pages -> data/profile_urls.json
extract.py        profile pages -> data/profiles.json
enrich.py          (optional) fetch personal websites for sparse bios
enrich_llm.py      Haiku reads bios to judge couples-centrality + style
                   on profiles that survive the deterministic hard-flags
score_heuristic.py merges enrichment + free deterministic signals into
                   data/evaluated.json and data/results.csv
build_review.py   data/evaluated.json -> data/review.html (the app)
```

Run the whole thing with `uv run python run.py`, or run any step standalone —
every step checkpoints its own progress, so re-running resumes rather than
restarting (and never re-fetches a page already in `data/cache/`).

## Setup

```bash
uv sync
cp .env.example .env   # add ANTHROPIC_API_KEY (only needed for enrich_llm.py)
```

Requires Python 3.12 (see `.python-version`). Dependencies are managed with
[uv](https://docs.astral.sh/uv/); see `pyproject.toml`.

## Scoring philosophy

`score_heuristic.py` is a free, deterministic, on-machine scorer — no LLM
required for the bulk of it. Every signal is derived straight from the raw
scraped fields (facets, credentials, bio text) so the ranking is auditable: no
fabricated statistics (e.g. there's no "% of caseload that's couples" anywhere
in Psychology Today's data — we don't claim to know it), no fitted target
scores, no opaque black-box judgment.

**Hard excludes** (profile is screened out entirely, never scored or shown):
doesn't do couples at all, pre-licensed/in training, early-career and not
certified, or religious/off-target framing (faith-centered, kink/poly-centric,
etc. as the core of the practice).

**Selective LLM enrichment** (`enrich_llm.py`, Claude Haiku): only the two
judgments a regex genuinely can't make reliably — how central couples work
really is to a practice (vs. a checkbox), and confirming faith-framing /
individual-only practices the keyword scan missed. Every enrichment is
verified against a small sample first (`uv run python enrich_llm.py test`)
before spending on the full set. Full-run cost is roughly $0.001/profile.

**Ranking**: profiles where couples work is the bio's primary focus rank
above profiles where it's secondary, then by a transparent weighted index
(experience, method depth/certification, cultural fit, license independence,
ADHD specialty). Session style (directive vs. exploratory) turned out to be
unreliable to infer from profile text even for the LLM, so it's surfaced as a
question to ask on the call rather than used to rank.

## The review app

`data/review.html` is a single self-contained file — open it directly in any
browser, no server needed. It embeds the full ranked dataset plus all the
UI/JS, so it's safe to email to someone else to review on their own machine.

- Mark each therapist **Shortlist / Intro / Session / Pass**, and add notes.
  Stored in the browser's `localStorage`, keyed by profile ID.
- **Filters** (collapsed by default) let you slice by couples-centrality,
  method depth, cultural fit, ADHD, license, experience, and status.
- **Export / Import** (in the Filters panel) lets two people reviewing on
  separate machines merge their marks — export a JSON file of your decisions,
  send it over, the other person imports and it merges (without clobbering
  existing marks).

Re-run `build_review.py` any time after a re-score to refresh the data; your
saved decisions persist since they're keyed by profile ID, not file content.

## Project layout

- `common.py`, `config.py` — shared fetch/IO utilities and all tunable
  constants (rate limits, model names, weights, paths).
- `context/therapist-scraper-spec.md` — the original project spec and scoring
  rubric history.
- `data/` — all scraped/generated data (gitignored). `data/calibration.md` is
  a hand-annotated calibration set used while tuning the scorer.
- `experiments/` — one-off scripts used during model selection and rubric
  calibration (kept for reference; not part of the active pipeline).

## Anti-blocking notes

Psychology Today rate-limits aggressively. `scrape.py`/`common.py` use
TLS/JA3 browser impersonation (`curl_cffi`), a warmed cookie session, slow
randomized delays, on-disk caching, and a circuit breaker that backs off and
eventually stops cleanly on repeated blocks rather than hammering the site.
Tune request pacing via `PSY_DELAY_MIN`/`PSY_DELAY_MAX` env vars — see
`config.py` for safe ranges.
