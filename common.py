"""Shared utilities: logging, a hardened polite fetcher, and JSON I/O.

The fetcher is built specifically to avoid getting a home IP blacklisted by
Psychology Today's aggressive anti-bot layer (Cloudflare/DataDome-class):

- curl_cffi browser TLS impersonation so the JA3/JA4 fingerprint matches the
  User-Agent (a fixed, consistent browser identity - NOT rotated, since a
  rotating UA over a fixed TLS stack is itself a detectable inconsistency).
- A single warmed session that carries cookies, like a real browser.
- Very slow, randomized, jittered delays plus occasional longer "human" pauses.
- On-disk HTML caching so resuming/re-running never re-hits the site.
- Block detection (HTTP 403/429/503, Cloudflare/DataDome challenge markers,
  suspiciously small bodies) feeding a CIRCUIT BREAKER that aborts the whole
  run after a couple of block signals rather than hammering into a long ban.
- Honors Retry-After once before counting a block.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import time
from pathlib import Path
from typing import Any

from curl_cffi import requests as cffi

import config

logger = logging.getLogger("psytoday")


def setup_logging(verbose: bool = True) -> logging.Logger:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO if verbose else logging.WARNING)
    fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s")
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logger.addHandler(console)
    file_handler = logging.FileHandler(config.SCRAPE_LOG_PATH, encoding="utf-8")
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)
    return logger


class BlockedError(Exception):
    """Raised when the circuit breaker trips - stop the run, do not hammer."""


class Fetcher:
    """A polite, cache-backed, block-aware HTTP client for one run."""

    def __init__(self, delay_range: tuple[float, float] = config.SCRAPE_DELAY) -> None:
        self.delay_range = delay_range
        self.session = cffi.Session(impersonate=config.CURL_IMPERSONATE)
        self.request_count = 0
        self.warmed = False

    # -- lifecycle -------------------------------------------------------
    def __enter__(self) -> "Fetcher":
        return self

    def __exit__(self, *exc) -> None:
        try:
            self.session.close()
        except Exception:  # noqa: BLE001
            pass

    # -- timing ----------------------------------------------------------
    def _sleep_delay(self) -> None:
        self.request_count += 1
        if config.LONG_PAUSE_EVERY and self.request_count % config.LONG_PAUSE_EVERY == 0:
            pause = random.uniform(*config.LONG_PAUSE_RANGE)
            logger.info("long human pause: %.0fs", pause)
            time.sleep(pause)
        delay = random.uniform(*self.delay_range)
        logger.info("waiting %.1fs before next request", delay)
        time.sleep(delay)

    # -- low-level request ----------------------------------------------
    def _request(self, url: str, referer: str | None):
        headers = {"Referer": referer} if referer else None
        try:
            r = self.session.get(
                url, headers=headers, timeout=config.REQUEST_TIMEOUT, allow_redirects=True
            )
        except Exception as exc:  # noqa: BLE001 - curl_cffi raises various errors
            logger.warning("request error for %s: %s", url, exc)
            return None, 0, None
        ra = r.headers.get("Retry-After")
        try:
            ra = int(ra) if ra else None
        except (TypeError, ValueError):
            ra = None
        return r.text, r.status_code, ra

    @staticmethod
    def _classify(status: int, text: str | None, expect: str | None):
        """Return (is_blocked, reason)."""
        if status == 0:
            return True, "connection error"
        if status in (403, 429, 503):
            return True, f"HTTP {status}"
        body = text or ""
        small = len(body) < config.MIN_OK_BODY_BYTES
        if small:
            for marker in config.BLOCK_MARKERS:
                if marker in body:
                    return True, f"challenge marker '{marker}'"
        if status == 200 and expect and small:
            if expect == "listing" and "/us/therapists/" not in body:
                return True, "listing page missing profile links"
            if expect == "profile" and "application/ld+json" not in body:
                return True, "profile page missing JSON-LD"
        return False, ""

    # -- cache -----------------------------------------------------------
    @staticmethod
    def _cache_path(url: str) -> Path:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
        return config.CACHE_DIR / f"{digest}.html"

    def _read_cache(self, url: str) -> str | None:
        path = self._cache_path(url)
        if path.exists():
            logger.info("cache hit: %s", url)
            return path.read_text(encoding="utf-8")
        return None

    def _write_cache(self, url: str, text: str) -> None:
        config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self._cache_path(url).write_text(text, encoding="utf-8")

    # -- public ----------------------------------------------------------
    def warm(self) -> None:
        """Acquire cookies like a real browser before filtered requests.

        Routes through the same resilient fetch path, so a transient throttle
        during warm-up is waited out rather than aborting the whole run.
        """
        if self.warmed:
            return
        logger.info("warming session via %s", config.WARMUP_URL)
        # _resilient_request raises BlockedError only after all cooldowns fail.
        self._resilient_request(config.WARMUP_URL, expect="listing", referer=None, do_delay=False)
        self.warmed = True

    def _resilient_request(self, url: str, expect: str | None, referer: str | None, do_delay: bool):
        """Make a request, waiting out throttles via the cooldown schedule.

        Returns (text, status). Raises BlockedError only if every cooldown in
        config.COOLDOWN_SCHEDULE still returns a block signal.
        """
        if do_delay:
            self._sleep_delay()
        text, status, retry_after = self._request(url, referer)
        blocked, reason = self._classify(status, text, expect)
        if not blocked:
            return text, status

        for attempt, cooldown in enumerate(config.COOLDOWN_SCHEDULE, 1):
            # Prefer the server's own Retry-After hint when it's reasonable.
            wait = cooldown
            if retry_after and retry_after <= config.RETRY_AFTER_CAP:
                wait = max(cooldown, retry_after)
            logger.warning(
                "BLOCK SIGNAL (%s) for %s -> cooldown %d/%d: sleeping %.0fs (%.0f min) then retrying",
                reason, url, attempt, len(config.COOLDOWN_SCHEDULE), wait, wait / 60,
            )
            time.sleep(wait)
            text, status, retry_after = self._request(url, referer)
            blocked, reason = self._classify(status, text, expect)
            if not blocked:
                logger.info("recovered after cooldown %d for %s", attempt, url)
                return text, status

        raise BlockedError(
            f"{reason} - still blocked after {len(config.COOLDOWN_SCHEDULE)} cooldowns "
            f"(~{sum(config.COOLDOWN_SCHEDULE) // 60} min). Progress is checkpointed; "
            "wait longer or switch network, then re-run to resume."
        )

    def get(self, url: str, expect: str | None = None, referer: str | None = None) -> str | None:
        """Fetch a URL politely. Returns HTML, or None for a skippable miss.

        Waits out transient throttles automatically; raises BlockedError only
        when the site stays blocked through the entire cooldown schedule.
        """
        cached = self._read_cache(url)
        if cached is not None:
            return cached

        text, status = self._resilient_request(url, expect, referer, do_delay=True)
        if status == 200 and text:
            self._write_cache(url, text)
            return text
        logger.info("non-200 (%s) for %s; skipping", status, url)
        return None


def new_client(delay_range: tuple[float, float] = config.SCRAPE_DELAY) -> Fetcher:
    return Fetcher(delay_range)


def polite_get(
    client: Fetcher,
    url: str,
    expect: str | None = None,
    referer: str | None = None,
) -> str | None:
    return client.get(url, expect=expect, referer=referer)


# --------------------------------------------------------------------------
# JSON I/O
# --------------------------------------------------------------------------
def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("could not read %s (%s); using default", path, exc)
        return default


def save_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
