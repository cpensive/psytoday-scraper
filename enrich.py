"""Step 2b (optional) - Fetch personal websites for sparse profiles.

For profiles that list a personal website AND have a very short PT bio, fetch
the site and store readable text as ``website_bio``. Updates profiles.json in
place. Safe to skip entirely.
"""

from __future__ import annotations

import sys

from bs4 import BeautifulSoup

import common
import config

logger = common.logger

STRIP_TAGS = ("script", "style", "nav", "footer", "header", "aside", "form", "noscript")


def readable_text(html: str, limit: int = 5000) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(list(STRIP_TAGS)):
        tag.decompose()
    text = soup.get_text(" ", strip=True)
    return " ".join(text.split())[:limit]


def needs_enrich(profile: dict) -> bool:
    return (
        bool(profile.get("website_url"))
        and not profile.get("website_bio")
        and len(profile.get("bio_narrative") or "") < config.SPARSE_BIO_THRESHOLD
    )


def enrich() -> int:
    common.setup_logging()
    profiles = common.load_json(config.PROFILES_PATH, [])
    if not profiles:
        logger.error("No profiles at %s. Run extract.py first.", config.PROFILES_PATH)
        return 1

    targets = [p for p in profiles if needs_enrich(p)]
    logger.info("Enriching %d sparse profiles with a website", len(targets))
    if not targets:
        return 0

    with common.new_client(config.ENRICH_DELAY) as client:
        try:
            for i, profile in enumerate(targets, 1):
                url = profile["website_url"]
                html = common.polite_get(client, url)
                if not html:
                    logger.warning("could not fetch website %s", url)
                    continue
                profile["website_bio"] = readable_text(html)
                common.save_json(config.PROFILES_PATH, profiles)
                logger.info("[%d/%d] enriched %s from %s", i, len(targets), profile.get("name"), url)
        except common.BlockedError as exc:
            # Optional step - never fail the pipeline over enrichment.
            logger.warning("stopping enrichment early: %s", exc)

    common.save_json(config.PROFILES_PATH, profiles)
    return 0


if __name__ == "__main__":
    sys.exit(enrich())
