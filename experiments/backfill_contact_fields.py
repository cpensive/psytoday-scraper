"""One-off: backfill website_url + phone + name from already-cached HTML.

extract.py's _website() originally required an absolute http(s) href, but PT
renders the "My website" link as a same-origin redirect
(/us/profile/<id>/website), so website_url came back empty for all 900
profiles. It also never captured the phone number PT does expose (a `tel:`
link, or in the og:description meta tag), and fell back to a raw URL slug
(e.g. "jonathan-blazon-yee-new-york-ny") for the small number of profiles
whose JSON-LD Person block has no name - usually group-practice listings.
All three are now fixed in extract.py; this backfills profiles.json from
data/cache/ WITHOUT re-hitting the network.

Usage: uv run python experiments/backfill_contact_fields.py
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import common
import config
from extract import _phone, _title_name, _website

logger = common.logger


def cache_path(url: str) -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
    return config.CACHE_DIR / f"{digest}.html"


def backfill() -> int:
    common.setup_logging()
    profiles = common.load_json(config.PROFILES_PATH, [])
    if not profiles:
        logger.error("No profiles at %s", config.PROFILES_PATH)
        return 1

    n_web = n_phone = n_name = n_miss_cache = 0
    for p in profiles:
        path = cache_path(p["url"])
        if not path.exists():
            n_miss_cache += 1
            continue
        html = path.read_text(encoding="utf-8", errors="ignore")
        soup = BeautifulSoup(html, "html.parser")
        website = _website(soup)
        phone = _phone(soup, html)
        if website:
            p["website_url"] = website
            n_web += 1
        if phone:
            p["phone"] = phone
            n_phone += 1
        # Only touch names that fell back to the raw slug (e.g. contain no
        # spaces / look identical to name_slug's hyphenated form) - never
        # overwrite a real extracted name.
        if p.get("name") == p.get("name_slug"):
            better = _title_name(html)
            if better and better != p["name"]:
                p["name"] = better
                n_name += 1

    common.save_json(config.PROFILES_PATH, profiles)
    logger.info(
        "Backfilled %d profiles: website_url=%d, phone=%d, name=%d (no cache for %d)",
        len(profiles), n_web, n_phone, n_name, n_miss_cache,
    )
    return 0


if __name__ == "__main__":
    sys.exit(backfill())
