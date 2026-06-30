"""Selective LLM enrichment (Haiku) for the signals the regex can't judge.

Why: the deterministic scorer keyword-counts "couples" mentions, which badly
under/over-rates how couples-CENTRAL a practice really is, and can't read
session STYLE (directive vs. exploratory) from prose. We only enrich profiles
that survive the deterministic hard-flags, so we never spend a token on a
profile that's already disqualified.

Enriched fields (per profile):
  couples_centrality : primary | secondary | individual_primary
  couples_evidence   : short quote justifying the call
  style              : directive | balanced | exploratory | unclear
  style_evidence     : short quote justifying the call
  faith_framed       : bool   (confirm the religious hard-flag)
  individual_only    : bool   (couples is just a checkbox)
  one_line           : <=18-word honest summary

Usage:
  uv run python enrich_llm.py test [N]   # sample N (default 12), print + cost, write nothing
  uv run python enrich_llm.py run        # enrich all survivors -> data/enriched.json
"""

from __future__ import annotations

import json
import os
import re
import sys

from dotenv import load_dotenv

import common
import config
import score_heuristic as sh

logger = common.logger

MODEL = "claude-haiku-4-5-20251001"
# Haiku 4.5 list price (per million tokens). Used only for the cost estimate.
PRICE_IN, PRICE_OUT = 1.0, 5.0
MAX_TOKENS = 400

_PRELIC = re.compile(r"\b(mhc-?lp|mft-?lp|lmsw|pre-?licensed|limited permit|supervised by)\b", re.I)

SYSTEM = """You screen Psychology Today therapist profiles for a couple seeking COUPLES therapy. \
You read the profile text and return ONLY honest judgments you can support with a quote. Never invent facts.

Return a single JSON object, no prose, with exactly these keys:
- couples_centrality: "primary" if couples/relationship work is the CORE of the practice (the bio is written to couples, "I help couples...", couples named first); "secondary" if they clearly do couples but it's one of several focuses; "individual_primary" if it reads as an individual therapist who merely lists couples.
- couples_evidence: a short verbatim quote (<=15 words) from the text supporting the call, or "" if none.
- style: "directive" (active: assigns homework/exercises, challenges patterns, structured, coaches), "exploratory" (holds space, client-led, insight/process-oriented, gentle), "balanced" (clearly both), or "unclear" (text doesn't say).
- style_evidence: a short verbatim quote (<=15 words), or "".
- faith_framed: true if the practice is framed around Christian/biblical/faith/spiritual values, else false.
- individual_only: true if couples appears to be only a checkbox and the practice is really individual work, else false.
- one_line: an honest <=18-word summary for the couple.

Output valid JSON only. Inside string values, never use the double-quote character; if you quote the bio, use single quotes."""


def _profile_text(p: dict) -> str:
    parts = [
        f"Name: {p.get('name','')}",
        f"Credentials: {p.get('credentials','')} {p.get('license_type','')} {p.get('job_title','')}".strip(),
        f"Years in practice: {p.get('years_in_practice') or 'unknown'}",
        f"Top specialties: {', '.join(p.get('specialties_top') or [])}",
        f"Client focus: {', '.join(p.get('client_focus_participants') or [])}",
        f"Therapy types: {', '.join((p.get('therapy_types') or [])[:12])}",
        "",
        "Bio:",
        (p.get("bio_narrative") or "").strip(),
        "",
        "Treatment approach:",
        (p.get("treatment_approach_text") or "").strip(),
    ]
    return "\n".join(parts)[:6000]


def hard_excluded(p: dict) -> str | None:
    """Clear, deterministic disqualifiers (cheap). Primary-vs-Featured couples
    is intentionally NOT decided here - the LLM does that on survivors."""
    s = sh.extract_signals(p)
    creds = f"{p.get('credentials') or ''} {p.get('license_type') or ''} {p.get('job_title') or ''}"
    if s["couples"] == "No":
        return "no_couples"
    if _PRELIC.search(creds) or s["license"] == "pre_licensed":
        return "pre_licensed"
    if s["experience_years"] is not None and s["experience_years"] < 3 and s["method_depth"] not in ("certified", "supervisor"):
        return "early_career"
    if s["off_target"]:
        return "religious/off_target"
    return None


def survivors(profiles: list[dict]) -> list[dict]:
    return [p for p in profiles if hard_excluded(p) is None]


def _parse(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.I | re.M).strip()
    m = re.search(r"\{.*\}", raw, re.S)
    return json.loads(m.group(0) if m else raw)


def enrich_one(client, p: dict) -> tuple[dict, int, int]:
    resp = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        temperature=0,
        system=SYSTEM,
        messages=[{"role": "user", "content": _profile_text(p)}],
    )
    data = _parse(resp.content[0].text)
    return data, resp.usage.input_tokens, resp.usage.output_tokens


def _client():
    import anthropic
    load_dotenv(config.ROOT / ".env")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        logger.error("ANTHROPIC_API_KEY not set")
        sys.exit(1)
    return anthropic.Anthropic()


def test(n: int = 12) -> int:
    common.setup_logging()
    profiles = common.load_json(config.PROFILES_PATH, [])
    surv = survivors(profiles)
    # Spread the sample across the regex couples buckets so we can see whether
    # Haiku promotes/demotes the keyword counter sensibly.
    buckets: dict[str, list] = {"Primary": [], "Featured": [], "Mentioned": []}
    for p in surv:
        c = sh.extract_signals(p)["couples"]
        if c in buckets:
            buckets[c].append(p)
    want = {"Primary": 3, "Featured": 4, "Mentioned": 5}
    sample = []
    for k, q in want.items():
        sample += buckets[k][:q]
    sample = sample[:n]

    client = _client()
    tin = tout = 0
    print(f"\nHaiku enrichment test on {len(sample)} survivors (model={MODEL})\n" + "=" * 100)
    for p in sample:
        regex_c = sh.extract_signals(p)["couples"]
        try:
            data, i, o = enrich_one(client, p)
            tin += i; tout += o
        except Exception as exc:  # noqa: BLE001
            print(f"  ERROR {p.get('name')}: {exc}")
            continue
        print(f"\n{p.get('name')}  ({p.get('credentials','')})   regex_couples={regex_c}")
        print(f"  couples_centrality: {data.get('couples_centrality'):18}  style: {data.get('style')}")
        print(f"    couples_evidence: \"{data.get('couples_evidence','')}\"")
        print(f"    style_evidence:   \"{data.get('style_evidence','')}\"")
        print(f"    faith_framed={data.get('faith_framed')}  individual_only={data.get('individual_only')}")
        print(f"    one_line: {data.get('one_line','')}")

    cost = tin / 1e6 * PRICE_IN + tout / 1e6 * PRICE_OUT
    n_done = max(1, len(sample))
    full = len(survivors(profiles))
    proj = cost / n_done * full
    print("\n" + "=" * 100)
    print(f"tokens: in={tin} out={tout}   sample cost=${cost:.4f}")
    print(f"projected full run on {full} survivors: ~${proj:.2f}")
    return 0


def run() -> int:
    import concurrent.futures
    import itertools
    import threading

    common.setup_logging()
    profiles = common.load_json(config.PROFILES_PATH, [])
    surv = survivors(profiles)
    done = common.load_json(config.DATA_DIR / "enriched.json", {})
    pending = [p for p in surv if str(p["profile_id"]) not in done]
    client = _client()
    logger.info("Enriching %d survivors (%d already done) - 8 concurrent", len(pending), len(done))

    lock = threading.Lock()
    counter = itertools.count(1)
    totals = {"in": 0, "out": 0}

    def work(p: dict) -> None:
        pid = str(p["profile_id"])
        try:
            data, i, o = enrich_one(client, p)
        except Exception as exc:  # noqa: BLE001
            logger.exception("enrich failed for %s: %s", pid, exc)
            data, i, o = {"error": str(exc)}, 0, 0
        n = next(counter)
        with lock:
            done[pid] = data
            totals["in"] += i; totals["out"] += o
            if n % 25 == 0:
                common.save_json(config.DATA_DIR / "enriched.json", done)
                logger.info("[%d/%d] cost ~$%.2f", n, len(pending),
                            totals["in"] / 1e6 * PRICE_IN + totals["out"] / 1e6 * PRICE_OUT)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(work, pending))
    common.save_json(config.DATA_DIR / "enriched.json", done)
    logger.info("Done. tokens in=%d out=%d  cost ~$%.2f", totals["in"], totals["out"],
                totals["in"] / 1e6 * PRICE_IN + totals["out"] / 1e6 * PRICE_OUT)
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "test"
    if cmd == "test":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 12
        sys.exit(test(n))
    sys.exit(run())
