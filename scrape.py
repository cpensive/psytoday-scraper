"""Step 1 - Discover therapist profile URLs from PT filtered listings.

Runs one search per treatment orientation (EFT, Gottman), walks all result
pages, and deduplicates by numeric profile id. No format facet is applied, so
results include both in-person and online therapists.

Output: data/profile_urls.json
"""

from __future__ import annotations

import re
import sys

import common
import config

logger = common.logger

# Matches /us/therapists/<name-slug>/<numeric-id> (the canonical profile path).
PROFILE_RE = re.compile(r"/us/therapists/([a-z0-9-]+)/(\d+)")


def build_url(search: dict[str, str], page: int) -> str:
    filters = search["filters"]
    if config.ETHNICITY_FILTER_ID:
        filters = f"{filters},{config.ETHNICITY_FILTER_ID}"
    url = f"{config.PT_LISTING_BASE}?category={search['category']}&filters={filters}"
    if page > 1:
        url += f"&page={page}"
    return url


def parse_profiles(html: str) -> list[tuple[str, str]]:
    """Return unique (name_slug, profile_id) tuples found on a listing page."""
    seen: dict[str, str] = {}
    for slug, pid in PROFILE_RE.findall(html):
        seen.setdefault(pid, slug)
    return [(slug, pid) for pid, slug in seen.items()]


def scrape() -> tuple[list[dict], bool]:
    """Return (records, blocked). `blocked` is True if the circuit breaker tripped."""
    common.setup_logging()
    # Resume-friendly: keep any URLs already discovered in a prior run.
    profiles: dict[str, dict] = {
        p["profile_id"]: p for p in common.load_json(config.PROFILE_URLS_PATH, [])
    }
    blocked = False

    with common.new_client(config.SCRAPE_DELAY) as client:
        try:
            client.warm()
            for search in config.SEARCHES:
                orientation = search["orientation"]
                logger.info("=== Search: %s (category=%s) ===", orientation, search["category"])
                consecutive_empty = 0
                for page in range(1, config.MAX_PAGES_PER_SEARCH + 1):
                    url = build_url(search, page)
                    html = common.polite_get(client, url, expect="listing", referer=config.PT_LISTING_BASE)
                    if not html:
                        logger.warning("no HTML for %s page %d; stopping this search", orientation, page)
                        break

                    found = parse_profiles(html)
                    if not found:
                        logger.info("%s: page %d empty -> reached end", orientation, page)
                        break

                    new_count = 0
                    for slug, pid in found:
                        if pid not in profiles:
                            profiles[pid] = {
                                "url": f"{config.PT_BASE}/us/therapists/{slug}/{pid}",
                                "profile_id": pid,
                                "name_slug": slug,
                                "orientations": [orientation],
                            }
                            new_count += 1
                        elif orientation not in profiles[pid]["orientations"]:
                            profiles[pid]["orientations"].append(orientation)
                    # Persist after every page so a block never loses progress.
                    common.save_json(config.PROFILE_URLS_PATH, list(profiles.values()))
                    logger.info(
                        "%s page %d: %d profiles (%d new, %d total unique)",
                        orientation, page, len(found), new_count, len(profiles),
                    )
                    if new_count == 0:
                        consecutive_empty += 1
                        if consecutive_empty >= 2:
                            logger.info("%s: 2 consecutive pages with no new profiles -> end of unique results", orientation)
                            break
                    else:
                        consecutive_empty = 0
                else:
                    logger.warning(
                        "%s hit MAX_PAGES_PER_SEARCH=%d; results may be truncated",
                        orientation, config.MAX_PAGES_PER_SEARCH,
                    )
        except common.BlockedError as exc:
            blocked = True
            logger.error("ABORTING scrape - %s", exc)

    records = list(profiles.values())
    common.save_json(config.PROFILE_URLS_PATH, records)
    logger.info("Saved %d unique profile URLs -> %s", len(records), config.PROFILE_URLS_PATH)
    return records, blocked


def main() -> int:
    records, blocked = scrape()
    if blocked:
        return 2
    if not records:
        logger.error("No profiles discovered. PT markup or filters may have changed.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
