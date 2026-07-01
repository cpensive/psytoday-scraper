"""Step 2 - Fetch each profile page and extract structured data.

Primary source is the page's JSON-LD ``Person`` block (clean and stable);
HTML sections fill in the fields JSON-LD omits (therapy types, participants,
ethnicity, fees, license #, endorsements, format). Every field is best-effort
and defaults to ``None``/``[]`` so one bad selector never drops a profile.

Checkpoint/resume via data/extract_progress.json.
Output: data/profiles.json
"""

from __future__ import annotations

import html as _html
import json
import re
import sys

from bs4 import BeautifulSoup

import common
import config

logger = common.logger

LICENSE_TOKENS = [
    "LCSW-R", "LCSW", "LMSW", "LMFT", "LMHC", "MHC-LP", "LMHC-LP", "MHC",
    "LPC", "LCAT", "PsyD", "PhD", "EdD", "MD", "DO", "LP", "MFT", "NP", "RN",
]


# --------------------------------------------------------------------------
# Small parsing helpers
# --------------------------------------------------------------------------
def _dedupe(seq: list[str]) -> list[str]:
    out: list[str] = []
    for s in seq:
        s = s.strip().rstrip(",").strip()
        if s and s not in out:
            out.append(s)
    return out


def _jsonld(soup: BeautifulSoup) -> dict:
    for s in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = s.string or s.get_text()
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict) and data.get("@type") == "Person":
            return data
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and item.get("@type") == "Person":
                    return item
    return {}


def _items_under(soup: BeautifulSoup, heading: str) -> list[str]:
    """List item texts of the first <ul> following an h2/h3 with this text."""
    h = soup.find(
        lambda tag: tag.name in ("h2", "h3") and tag.get_text(strip=True) == heading
    )
    if not h:
        return []
    ul = h.find_next("ul")
    if not ul:
        return []
    return _dedupe([li.get_text(" ", strip=True) for li in ul.find_all("li")])


def _client_focus_group(soup: BeautifulSoup, label: str) -> list[str]:
    """Items belonging to a Client Focus subgroup (Age/Participants/etc.)."""
    for h in soup.find_all("h3", class_="client-focus-group-title"):
        if h.get_text(strip=True) == label:
            items: list[str] = []
            for sib in h.find_next_siblings():
                if sib.name == "h3":
                    break
                for sp in sib.select(".client-focus-description"):
                    items.append(sp.get_text(strip=True))
            return _dedupe(items)
    return []


def _section_text(soup: BeautifulSoup, heading: str, limit: int = 4000) -> str:
    h = soup.find(
        lambda tag: tag.name in ("h2", "h3") and tag.get_text(strip=True) == heading
    )
    if not h:
        return ""
    parts: list[str] = []
    for sib in h.find_next_siblings():
        if sib.name in ("h2",):
            break
        parts.append(sib.get_text(" ", strip=True))
    return " ".join(p for p in parts if p)[:limit]


def _as_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return _dedupe(re.split(r",\s*", value))
    if isinstance(value, list):
        out: list[str] = []
        for v in value:
            if isinstance(v, dict):
                out.append(str(v.get("name", "")).strip())
            else:
                out.append(str(v).strip())
        return _dedupe(out)
    if isinstance(value, dict):
        return _dedupe([str(value.get("name", "")).strip()])
    return []


def _license_type(credentials: str) -> str | None:
    for tok in LICENSE_TOKENS:
        if re.search(rf"\b{re.escape(tok)}\b", credentials):
            return tok
    return credentials.split(",")[0].strip() or None if credentials else None


def _fee(soup: BeautifulSoup, key: str) -> str | None:
    el = soup.select_one(f'li[data-x="{key}"]')
    if not el:
        return None
    text = el.get_text(" ", strip=True)
    m = re.search(r"\$\s?\d[\d,]*", text)
    return m.group(0).replace(" ", "") if m else (text or None)


def _website(soup: BeautifulSoup) -> str | None:
    """Best-effort personal-website link.

    PT renders the "My website" link as a same-origin redirect
    (``/us/profile/<id>/website``), not a direct external href - so this must
    be checked BEFORE the http(s)-only external-link fallback below.
    """
    a = soup.select_one('a[data-x="website-link"][href]')
    if a and a["href"]:
        href = a["href"]
        return href if href.startswith("http") else f"{config.PT_BASE}{href}"

    blocked = ("psychologytoday.com", "sussexdirectories.com", "workable.com")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href.startswith("http"):
            continue
        if any(b in href for b in blocked):
            continue
        label = (a.get_text(strip=True) or a.get("aria-label", "") or "").lower()
        if "website" in label or a.get("rel") and "nofollow" in (a.get("rel") or []):
            return href
    return None


def _phone(soup: BeautifulSoup, html: str) -> str | None:
    """Direct phone number, if PT displays one (``tel:`` link, else the
    og:description meta tag which lists it right after city/state/zip)."""
    a = soup.select_one('a[href^="tel:"]')
    if a:
        return a.get_text(strip=True) or a["href"].removeprefix("tel:")
    m = re.search(r'og:description"\s+content="[^"]*?,\s*(\(\d{3}\)\s?\d{3}-\d{4})', html)
    return m.group(1) if m else None


def _title_name(page_html: str) -> str | None:
    """Fallback name parse from <title> ('Name, Credential | Role, City, ST,
    Zip | Psychology Today'). Used when the JSON-LD Person block has no name -
    happens for some group-practice listings whose @type isn't Person."""
    m = re.search(r"<title>([^<]+)</title>", page_html)
    if not m:
        return None
    title = _html.unescape(m.group(1)).split(" | ")[0]
    name = title.split(",")[0].strip()
    return name or None


def _years_in_practice(html: str) -> str | None:
    m = re.search(r"(\d{1,2})\+?\s*Years?\s+(?:in|of)\s+Practice", html, re.I)
    if m:
        return m.group(1)
    m = re.search(r"in\s+Practice\s+for\s+(\d{1,2})\+?\s*Years?", html, re.I)
    return m.group(1) if m else None


def _certifications(text: str) -> list[str]:
    """Pull 'Certified ...' / 'Certificate: ...' phrases as credential signals."""
    out: list[str] = []
    for m in re.finditer(r"Certif(?:ied|icate)[^.,;:\n]{0,70}", text):
        out.append(re.sub(r"\s+", " ", m.group(0)).strip())
    return _dedupe(out)[:8]


# --------------------------------------------------------------------------
# Main per-profile extraction
# --------------------------------------------------------------------------
def extract_profile(html: str, base: dict) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    ld = _jsonld(soup)

    credentials = str(ld.get("honorificSuffix") or "").strip()

    # Location from workLocation list (US offices => has an in-person address).
    locations: list[str] = []
    has_office = False
    work = ld.get("workLocation") or []
    if isinstance(work, dict):
        work = [work]
    for place in work:
        addr = (place or {}).get("address") or {}
        loc = addr.get("addressLocality")
        region = addr.get("addressRegion")
        if loc:
            has_office = True
            locations.append(", ".join(x for x in (loc, region) if x))

    page_text = soup.get_text(" ", strip=True)
    online = bool(re.search(r"Online Therapy|Teletherapy|Telehealth", page_text, re.I))
    in_person = has_office or bool(re.search(r"\bIn Person\b", page_text))

    bio = str(ld.get("description") or "").strip()
    if not bio:
        card = soup.find(class_=re.compile("personal-statement"))
        if card:
            bio = re.sub(r"^\s*English\s*\|\s*\S+\s*", "", card.get_text(" ", strip=True))

    languages = _as_list(ld.get("knowsLanguage"))
    languages += _client_focus_group(soup, "I also speak")

    qual_text = _section_text(soup, "Qualifications")
    edu_text = _section_text(soup, "Education and Years In Practice")
    treat_text = _section_text(soup, "Treatment Approach")

    record = {
        **base,
        "name": str(ld.get("name") or "").strip() or _title_name(html) or base.get("name_slug", ""),
        "credentials": credentials,
        "job_title": str(ld.get("jobTitle") or "").strip(),
        "bio_narrative": bio,
        "specialties_top": _items_under(soup, "Top Specialties"),
        "specialties_all": _items_under(soup, "Expertise"),
        "therapy_types": _items_under(soup, "Types of Therapy"),
        "issues": _as_list(ld.get("knowsAbout")),
        "client_focus_age": _client_focus_group(soup, "Age"),
        "client_focus_participants": _client_focus_group(soup, "Participants"),
        "ethnicity": _client_focus_group(soup, "Ethnicity"),
        "communities": _client_focus_group(soup, "Communities"),
        "languages": _dedupe(languages),
        "location": "; ".join(_dedupe(locations)) or None,
        "in_person": in_person,
        "online": online,
        "fee_individual": _fee(soup, "fees-individual"),
        "fee_couples": _fee(soup, "fees-couples"),
        "sliding_scale": bool(re.search(r"Sliding scale", page_text, re.I)),
        "insurance": _items_under(soup, "Insurance"),
        "years_in_practice": _years_in_practice(html),
        "school": "; ".join(_as_list(ld.get("alumniOf"))) or None,
        "additional_credentials": _certifications(f"{bio} {qual_text} {edu_text}"),
        "license_type": _license_type(credentials),
        "license_number": None,
        "website_url": _website(soup),
        "phone": _phone(soup, html),
        "endorsements": _section_text(soup, "Endorsement", 2000) or None,
        "qualifications_text": f"{qual_text} {edu_text}".strip(),
        "treatment_approach_text": treat_text,
    }

    m = re.search(r"License\s*(?:No\.?|#|Number)?\s*:?\s*([A-Z]?\d{4,})", html)
    if m:
        record["license_number"] = m.group(1)
    return record


def extract() -> list[dict]:
    common.setup_logging()
    urls = common.load_json(config.PROFILE_URLS_PATH, [])
    if not urls:
        logger.error("No profile URLs found at %s. Run scrape.py first.", config.PROFILE_URLS_PATH)
        return []

    done_ids = set(common.load_json(config.EXTRACT_PROGRESS_PATH, []))
    profiles = {p["profile_id"]: p for p in common.load_json(config.PROFILES_PATH, [])}

    pending = [r for r in urls if r["profile_id"] not in done_ids]
    if config.PROFILE_LIMIT:
        pending = pending[: config.PROFILE_LIMIT]
        logger.info("PROFILE_LIMIT=%d -> extracting %d this run", config.PROFILE_LIMIT, len(pending))
    logger.info("Extracting %d profiles (%d already done)", len(pending), len(done_ids))

    blocked = False
    with common.new_client(config.SCRAPE_DELAY) as client:
        try:
            client.warm()
            for i, rec in enumerate(pending, 1):
                pid = rec["profile_id"]
                html = common.polite_get(
                    client, rec["url"], expect="profile", referer=config.PT_LISTING_BASE
                )
                if not html:
                    logger.warning("skip %s (%s): no HTML", pid, rec["url"])
                    continue
                try:
                    profiles[pid] = extract_profile(html, rec)
                except Exception as exc:  # noqa: BLE001 - keep going on any parse error
                    logger.exception("parse failed for %s: %s", pid, exc)
                    continue
                done_ids.add(pid)
                common.save_json(config.PROFILES_PATH, list(profiles.values()))
                common.save_json(config.EXTRACT_PROGRESS_PATH, sorted(done_ids))
                logger.info("[%d/%d] extracted %s (%s)", i, len(pending), profiles[pid]["name"], pid)
        except common.BlockedError as exc:
            blocked = True
            logger.error("ABORTING extract - %s", exc)

    records = list(profiles.values())
    common.save_json(config.PROFILES_PATH, records)
    logger.info("Saved %d profiles -> %s", len(records), config.PROFILES_PATH)
    return records, blocked


def main() -> int:
    records, blocked = extract()
    if blocked:
        return 2
    return 0 if records else 1


if __name__ == "__main__":
    sys.exit(main())
