"""Shared configuration for the Psychology Today therapist scraper.

All tunable constants live here so the brittle bits (PT filter IDs, model
name, rate limits) can be changed in one place.
"""

from __future__ import annotations

import os
from pathlib import Path


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"

PROFILE_URLS_PATH = DATA_DIR / "profile_urls.json"
PROFILES_PATH = DATA_DIR / "profiles.json"
EXTRACT_PROGRESS_PATH = DATA_DIR / "extract_progress.json"
EVALUATED_PATH = DATA_DIR / "evaluated.json"
EVAL_PROGRESS_PATH = DATA_DIR / "evaluate_progress.json"
RESULTS_CSV_PATH = DATA_DIR / "results.csv"
CALIBRATION_CSV_PATH = DATA_DIR / "calibration.csv"
CALIBRATION_MD_PATH = DATA_DIR / "calibration.md"
SCRAPE_LOG_PATH = DATA_DIR / "scrape.log"

# --------------------------------------------------------------------------
# Psychology Today scraping
# --------------------------------------------------------------------------
PT_BASE = "https://www.psychologytoday.com"
PT_LISTING_BASE = f"{PT_BASE}/us/therapists/ny/new-york"

# PT faceted search uses one readable `category=<slug>` anchor plus a set of
# opaque numeric `filters=<ids>` for additional facets. These were verified
# live (2026-06): anchoring on the treatment-orientation slug and applying the
# couples/marriage filter bundle narrows the NYC pool appropriately. We
# intentionally DO NOT include a format facet, so results include BOTH
# in-person and online therapists (per project requirement).
#
# If PT changes its parameters, re-derive these by opening the filtered search
# in a browser and copying the resulting `category`/`filters` values.
COUPLES_FILTER_IDS = "2437,2681,5942"  # couples/marriage facet bundle

# One search per treatment orientation; deduplicated by profile id afterwards.
# The ADHD search anchors on category=adhd and applies the Couples Therapy (5945)
# + Gottman Method (3810) facet bundle, per the broader non-Asian pull Charles
# requested (couples + ADHD + Gottman). Verified live 2026-06.
SEARCHES: list[dict[str, str]] = [
    {"orientation": "EFT", "category": "emotionally-focused", "filters": COUPLES_FILTER_IDS},
    {"orientation": "Gottman", "category": "gottman-method", "filters": COUPLES_FILTER_IDS},
    {"orientation": "ADHD+Gottman", "category": "adhd", "filters": "5945,3810"},
]

# Optional: set to a numeric PT ethnicity filter id (e.g. "Asian") to narrow at
# scrape time. Left empty by default; the evaluator judges Asian background from
# the bio (more reliable than PT's ambiguous ethnicity checkbox).
ETHNICITY_FILTER_ID: str | None = None

# Hard stop so a runaway pagination loop can never hammer PT forever. The
# scraper also breaks early after 2 consecutive pages with no NEW profiles, so
# this is just a safety ceiling (EFT ~19 real pages, Gottman ~9).
MAX_PAGES_PER_SEARCH = 30

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
]

# Polite per-request delay ranges (seconds). Default is deliberately VERY SLOW
# because Psychology Today rate-limits aggressively by IP and blacklists noisy
# clients. Overridable via env (PSY_DELAY_MIN / PSY_DELAY_MAX):
#   - local home IP (safest):     15-40  (default)
#   - moderate (e.g. behind VPN): 8-20   PSY_DELAY_MIN=8 PSY_DELAY_MAX=20
SCRAPE_DELAY = (_env_float("PSY_DELAY_MIN", 15.0), _env_float("PSY_DELAY_MAX", 40.0))
ENRICH_DELAY = (10.0, 20.0)

# Optional cap on how many profiles extract.py / evaluate.py process this run
# (0 = no cap). Enables small nightly batches; checkpoints make the rest resume.
PROFILE_LIMIT = _env_int("PSY_LIMIT", 0)

# --------------------------------------------------------------------------
# Anti-blacklist hardening
# --------------------------------------------------------------------------
# curl_cffi TLS/JA3-JA4 impersonation target so our handshake matches the UA.
CURL_IMPERSONATE = "chrome131"

# Persisted HTML cache so resuming or re-running never re-hits PT.
CACHE_DIR = DATA_DIR / "cache"

# On a block signal (503/429/challenge), wait out the throttle with these
# escalating cooldowns (seconds) and retry the SAME request after each. PT's
# 503s are temporary rate-limits that clear within minutes, so riding them out
# lets an overnight run self-heal instead of aborting. Only if every cooldown
# still returns a block do we trip the circuit breaker and stop.
COOLDOWN_SCHEDULE = (300, 900, 1800, 1800)  # 5m, 15m, 30m, 30m

# Cap on honoring a server Retry-After (seconds) before falling back to the
# cooldown schedule above.
RETRY_AFTER_CAP = 600

# Take an occasional longer "human" break to avoid metronomic request timing.
LONG_PAUSE_EVERY = 20
LONG_PAUSE_RANGE = (45.0, 120.0)

# Warm a session (cookies) by hitting this first before any filtered requests.
WARMUP_URL = PT_LISTING_BASE

# Substrings that indicate a Cloudflare/DataDome challenge or block interstitial
# (checked only on small bodies; real profile pages legitimately contain the
# word "captcha" inside embedded scripts, so size-gating avoids false positives).
BLOCK_MARKERS = (
    "Just a moment...",
    "Attention Required!",
    "cf-browser-verification",
    "cf-challenge",
    "_cf_chl_opt",
    "Access denied",
    "You have been blocked",
    "DataDome",
    "captcha-delivery.com",
    "geo.captcha-delivery",
    "Enable JavaScript and cookies to continue",
)
# Bodies smaller than this that lack expected content are treated as a block.
MIN_OK_BODY_BYTES = 60000

# Backoff schedule (seconds) used on 429/403/5xx, max len = max retries.
BACKOFF_SCHEDULE = (30, 60, 120)

REQUEST_TIMEOUT = 30.0

# --------------------------------------------------------------------------
# Anthropic evaluation
# --------------------------------------------------------------------------
# Verified available against the account on build date (2026-06). Pinned
# snapshot for reproducibility; latest alias alternative is "claude-sonnet-4-6".
#
# MODEL/CONFIG LOCKED via model_select.py consistency experiment (2026-06-30):
# scoring one boundary profile 10x per config gave:
#   no_thinking (temp 0)     -> CONSISTENT  (score 6 x10)
#   thinking_medium (temp 1) -> VARIABLE    (score 6 x5, 7 x5)
#   thinking_high (temp 1)   -> would also vary (temp must be 1 when thinking)
# Conclusion: NO extended thinking, temperature 0. It is the most consistent
# AND cheapest for this fixed-rubric extraction task. Do NOT enable thinking
# here (it forces temperature=1 and reintroduces score variance).
ANTHROPIC_MODEL = "claude-sonnet-4-5-20250929"
ANTHROPIC_MAX_TOKENS = 1200
ANTHROPIC_TEMPERATURE = 0.0

# Serial throttle kept for reference / single-worker fallback. Not used when
# EVAL_WORKERS > 1 because the account is on Scale tier (10K RPM / 2M OTPM).
EVAL_MIN_INTERVAL = _env_float("PSY_EVAL_INTERVAL", 0.0)
EVAL_MAX_RETRIES = 3

# Number of concurrent Anthropic API calls during evaluation.
# Scale-tier limits (Sonnet 4.x): 10K RPM, 2M OTPM.  At max_tokens=1200 the
# OTPM ceiling is ~1667 req/min; 12 workers at ~4s/call ≈ 180 req/min —
# well within limits.  Override via PSY_EVAL_WORKERS.
EVAL_WORKERS = _env_int("PSY_EVAL_WORKERS", 12)

# Profiles with a personal website and a bio shorter than this many characters
# are candidates for enrich.py.
SPARSE_BIO_THRESHOLD = 300

# Size of the human-review calibration sample.
CALIBRATION_SIZE = 20
