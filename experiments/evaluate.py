"""Step 3 - Score each profile via the Anthropic API.

Sends a constructed text block per profile to Claude with the scoring system
prompt, parses the JSON verdict, and writes a ranked CSV. Checkpoint/resume
via data/evaluate_progress.json.

Outputs: data/evaluated.json, data/results.csv
"""

from __future__ import annotations

import concurrent.futures
import csv
import itertools
import json
import os
import re
import sys
import threading

from dotenv import load_dotenv
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

import common
import config

logger = common.logger

SYSTEM_PROMPT = """\
You are triaging couples therapist profiles for Charles and Angelina - an intercultural couple (Japanese husband, North Vietnamese wife) in NYC seeking experienced, direct, intellectually rigorous couples therapy.

For each profile, extract signals and score. Be ruthlessly honest. Most therapists are mediocre at couples work - your job is to find the exceptional ones.

SIGNAL HIERARCHY (what actually predicts extraordinary couples therapy, in order):

TIER 1 - CERTIFICATION (strongest signal):
- "Certified" in a couples modality (Certified EFT Therapist, Certified Gottman Therapist, AASECT Certified Sex Therapist) = requires 1000+ supervised clinical hours + peer review. This is the single strongest quality signal.
- "Trained in" or "Level 2/3" WITHOUT certification = they took workshops but haven't been evaluated. Much weaker.
- Supervisory role: do they train/supervise OTHER therapists? = top-tier signal. Someone clinicians pay to learn from.
- SPECIFICITY IS A SIGNAL: precise modality naming ("Certified Gottman Method 2006 + aspects of EFT", names the certificate/year) shows the therapist takes their craft seriously - upweight it. Vague "culturally sensitive / eclectic" with a 12+ modality laundry list is the opposite (generalist dilution), even if it technically lists couples modalities.

TIER 2 - PRACTICE COMPOSITION:
- What % of practice is couples? If bio only discusses individual work but checks "couples" box = individual therapist moonlighting.
- Does the bio describe HOW they work with couples (process, stages, what a session looks like)? = real couples therapist.
- Do they mention specific couples issues (resentment, infidelity, cultural dynamics, intimacy, trust repair)? = experienced.
- INDIVIDUAL-TAILORED LANGUAGE IS A DOWNGRADE even when a couples % is checked: bios written in singular "you", focused on self-esteem, "your past", "your authenticity", healing the individual = penalize. Couples-primary therapists write about the relationship, the cycle, and the two of you. A profile reading individual-focused should not clear READ_MORE on couples-% alone.

TIER 3 - STYLE MATCH:
- Active/directive vs passive/exploratory? Charles and Angelina both want active. Previous therapy failed because therapist just validated.
- Evidence of challenge ("I will push you," "expect to be uncomfortable," structured homework) = good match.
- Evidence of framework/plan (assessment phase, structured stages, exit criteria) = Charles's trust signal.

TIER 4 - CULTURAL FIT:
- Qualifying signal = Asian background OR lived intercultural experience. Charles and Angelina are themselves an intercultural couple; EITHER one passes the cultural filter. Neither alone guarantees a high score, and neither alone is disqualifying.
- Lived intercultural experience (immigrant, interracial marriage, raised across cultures - Travis Atkinson's "lived intercultural" profile is the anchor) OUTRANKS an Asian-ethnicity checkbox with no couples depth. A non-Asian Certified EFT therapist with 20 years of intercultural couples work outranks an Asian individual-anxiety therapist.
- Do NOT auto-SKIP a profile for cultural reasons alone if it shows lived intercultural experience. East Asian fluency is ideal; South Asian / other intercultural backgrounds are "directionally there".
- Speaks Mandarin/Japanese/Vietnamese = bonus signal for depth, not just checkbox.

TIER 5 - CREDENTIAL SIGNALS:
- License level: LCSW/LCSW-R/LMFT/PhD/PsyD = independent. LMSW/MHC-LP = provisional (earlier career).
- School quality: top program = grit/intellect signal, but weaker predictor than certification for therapy quality.
- Years in practice: 7+ preferred, but a 7-year Certified Gottman therapist > a 20-year generalist.

TIER 6 - SOCIAL PROOF & RELEVANCE:
- Endorsements by OTHER therapists = positive signal (peers vouching). More than ~3 is notable; weight it. Discount endorsements that read generic or solicited.
- ADHD named as a TOP specialty alongside genuine couples work = small bonus - directly topical for Charles, who has lifelong severe ADHD. This is a tie-breaker bonus, not a substitute for couples depth.
- OFF-TARGET EMPHASIS is a NEGATIVE, not neutral: bios centered on unrelated populations/issues (heavy LGBTQ-affirming focus, neurodivergence, trauma/identity laundry lists, 12+ modalities) that crowd out couples-work signal = dilution. Penalize, do not reward as "breadth".

LOCATION & FORMAT: Charles and Angelina will consider therapists who practice in NYC EITHER in person OR online (telehealth). Do NOT treat online-only practice as a disqualifier or a red flag by itself - at most note it as an "ask on the call."

Respond ONLY in this JSON format (no markdown, no backticks, no preamble):
{
  "name": "therapist name",
  "tier_1": {
    "couples_certification": "none" | "trained_not_certified" | "certified" | "supervisor_trainer",
    "modality": "specific modality name or null",
    "certification_detail": "e.g. Certified EFT Therapist since 2019, or Level 2 Gottman trained"
  },
  "tier_2": {
    "couples_focus_pct": number 0-100 (estimate from bio emphasis),
    "describes_couples_process": true/false,
    "specific_couples_issues_named": ["list of specific issues mentioned"]
  },
  "tier_3": {
    "style": "active" | "passive" | "mixed" | "unclear",
    "evidence_of_challenge": true/false,
    "framework_plan_visible": true/false,
    "style_note": "brief evidence"
  },
  "tier_4": {
    "asian_background": true/false,
    "intercultural_experience": "none" | "checkbox" | "lived" | "specialty",
    "languages": ["list"],
    "cultural_note": "brief evidence"
  },
  "tier_5": {
    "license": "LCSW/LMFT/PhD/etc",
    "independent_license": true/false,
    "school": "school name(s) or unknown",
    "top_school": true/false,
    "years_est": "number or range",
    "sees_couples_and_families": true/false
  },
  "tier_6": {
    "endorsements_count": number or null,
    "endorsements_note": "brief note; flag if generic/solicited",
    "adhd_specialty": true/false,
    "off_target_emphasis": true/false
  },
  "composite_score": number 1-10,
  "verdict": "CALL" | "READ_MORE" | "SKIP",
  "one_line": "one sentence: why call or why skip",
  "red_flags": ["list any red flags, empty array if none"],
  "ask_on_call": "one targeted question based on profile gaps"
}

SCORING GUIDE:
8-10 = CALL (book consultation immediately)
5-7 = READ_MORE (check their website/reviews for more signal)
1-4 = SKIP

A score of 8+ requires EITHER:
- Couples certification (Tier 1) + cultural fit (Tier 4: Asian OR lived intercultural)
- OR supervisor/trainer level (Tier 1) + strong style match (Tier 3) even if cultural fit is weaker

A SKIP means: no couples certification AND no evidence of couples-primary practice, OR bio is entirely individual-focused despite listing couples. Do NOT SKIP solely for lacking Asian ethnicity if lived intercultural experience is present.

CALIBRATION NOTES (from Charles's 20-profile review - apply these corrections):
- The prior prompt UNDER-scored Asian/intercultural therapists who name specific couples modalities and show couples focus (these belong at 6-7 READ_MORE, not 5). Specific modality naming + couples focus + NYC + peer endorsements should pull up.
- The prior prompt OVER-scored therapists with individual-tailored bio language despite a couples-% checkbox (these belong at ~5, not 7).
- "Directionally there" non-East-Asian intercultural profiles should not be floored to SKIP on culture alone.\
"""


def build_profile_text(p: dict) -> str:
    def fmt(label: str, value) -> str:
        if not value:
            return ""
        if isinstance(value, list):
            value = ", ".join(str(v) for v in value)
        return f"{label}: {value}\n"

    fmt_flag = []
    if p.get("in_person"):
        fmt_flag.append("in-person")
    if p.get("online"):
        fmt_flag.append("online")

    parts = [
        fmt("Name", p.get("name")),
        fmt("Credentials", p.get("credentials")),
        fmt("Title", p.get("job_title")),
        fmt("Location", p.get("location")),
        fmt("Format", ", ".join(fmt_flag)),
        fmt("Orientations matched in search", p.get("orientations")),
        fmt("Fee (individual)", p.get("fee_individual")),
        fmt("Fee (couples)", p.get("fee_couples")),
        fmt("Sliding scale", p.get("sliding_scale")),
        fmt("Top specialties", p.get("specialties_top")),
        fmt("Expertise", p.get("specialties_all")),
        fmt("Issues", p.get("issues")),
        fmt("Types of therapy", p.get("therapy_types")),
        fmt("Participants", p.get("client_focus_participants")),
        fmt("Age focus", p.get("client_focus_age")),
        fmt("Ethnicity", p.get("ethnicity")),
        fmt("Communities", p.get("communities")),
        fmt("Languages", p.get("languages")),
        fmt("School", p.get("school")),
        fmt("Years in practice", p.get("years_in_practice")),
        fmt("Certifications", p.get("additional_credentials")),
        fmt("Qualifications", p.get("qualifications_text")),
        fmt("Bio", p.get("bio_narrative")),
        fmt("Treatment approach", p.get("treatment_approach_text")),
        fmt("Website bio", p.get("website_bio")),
    ]
    return "".join(part for part in parts if part).strip()


def parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text).rstrip("`").strip()
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1:
        text = text[start : end + 1]
    return json.loads(text)


def _as_float(x, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def apply_guardrails(ev: dict) -> dict:
    """Deterministic safety net so gold-standard 'Travis-type' profiles ALWAYS
    surface as CALL, regardless of the LLM's holistic composite_score.

    The LLM reliably extracts the tier signals, but the 1-10 scalar is noisy in
    the middle band; these rules turn the highest-confidence pattern into a
    guarantee instead of a hope. This ONLY ever promotes a score (never demotes),
    and records why in `ev['guardrail']` for transparency.
    """
    if not isinstance(ev, dict):
        return ev
    t1 = ev.get("tier_1") or {}
    t2 = ev.get("tier_2") or {}
    t3 = ev.get("tier_3") or {}
    t4 = ev.get("tier_4") or {}
    cert = t1.get("couples_certification")
    couples_pct = _as_float(t2.get("couples_focus_pct"))
    style = t3.get("style")
    cultural_fit = bool(t4.get("asian_background")) or t4.get("intercultural_experience") in ("lived", "specialty")
    score = _as_float(ev.get("composite_score"))

    reason = None
    # Pattern A (the Travis pattern): supervisor/trainer in a couples modality
    # who actually does couples work. This is the single strongest quality signal.
    if cert == "supervisor_trainer" and couples_pct >= 40:
        reason = "supervisor/trainer in couples modality"
    # Pattern B: certified + active/directive style + cultural fit + couples-primary.
    elif cert == "certified" and style == "active" and cultural_fit and couples_pct >= 50:
        reason = "certified + active style + cultural fit + couples-primary"

    if reason and (score < 8 or ev.get("verdict") != "CALL"):
        ev["composite_score"] = max(score, 8.0)
        ev["verdict"] = "CALL"
        ev["guardrail"] = f"promoted to CALL: {reason}"
    return ev


class Evaluator:
    """Thread-safe: one instance shared across all worker threads.

    The serial _throttle() mechanism has been removed — the account is on the
    Scale tier (10K RPM / 2M OTPM for Sonnet 4.x), so the API itself is not
    the bottleneck.  RateLimitError is still retried via tenacity in case of
    transient bursts.
    """

    def __init__(self, api_key: str) -> None:
        import anthropic

        self._anthropic = anthropic
        self.client = anthropic.Anthropic(api_key=api_key)

    def score(self, profile_text: str) -> dict:
        anthropic = self._anthropic

        @retry(
            retry=retry_if_exception_type(
                (
                    anthropic.RateLimitError,
                    anthropic.APITimeoutError,
                    anthropic.APIConnectionError,
                    anthropic.InternalServerError,
                )
            ),
            wait=wait_exponential(multiplier=5, min=5, max=60),
            stop=stop_after_attempt(config.EVAL_MAX_RETRIES + 1),
            reraise=True,
        )
        def _call() -> str:
            resp = self.client.messages.create(
                model=config.ANTHROPIC_MODEL,
                max_tokens=config.ANTHROPIC_MAX_TOKENS,
                temperature=config.ANTHROPIC_TEMPERATURE,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": profile_text}],
            )
            return resp.content[0].text

        raw = _call()
        return apply_guardrails(parse_json(raw))


# --------------------------------------------------------------------------
# CSV output
# --------------------------------------------------------------------------
CSV_COLUMNS = [
    "Name", "Verdict", "Score", "Certification", "CouplesPercent", "Style",
    "Asian", "Languages", "School", "License", "Years", "FeeCouples",
    "Website", "PT_URL", "OneLine", "AskOnCall",
]
VERDICT_RANK = {"CALL": 0, "READ_MORE": 1, "SKIP": 2}


def _g(d: dict, *keys, default=""):
    for k in keys:
        if not isinstance(d, dict):
            return default
        d = d.get(k, {} if k != keys[-1] else default)
    return d if d not in ({}, None) else default


def row_from(record: dict) -> dict:
    ev = record.get("evaluation") or {}
    return {
        "Name": ev.get("name") or record.get("name", ""),
        "Verdict": ev.get("verdict", "ERROR"),
        "Score": ev.get("composite_score", ""),
        "Certification": _g(ev, "tier_1", "couples_certification"),
        "CouplesPercent": _g(ev, "tier_2", "couples_focus_pct"),
        "Style": _g(ev, "tier_3", "style"),
        "Asian": _g(ev, "tier_4", "asian_background"),
        "Languages": ", ".join(record.get("languages") or []),
        "School": _g(ev, "tier_5", "school") or (record.get("school") or ""),
        "License": _g(ev, "tier_5", "license") or (record.get("license_type") or ""),
        "Years": _g(ev, "tier_5", "years_est") or (record.get("years_in_practice") or ""),
        "FeeCouples": record.get("fee_couples") or "",
        "Website": record.get("website_url") or "",
        "PT_URL": record.get("url", ""),
        "OneLine": ev.get("one_line", ""),
        "AskOnCall": ev.get("ask_on_call", ""),
    }


def write_csv(records: list[dict]) -> None:
    def sort_key(r: dict):
        ev = r.get("evaluation") or {}
        verdict = ev.get("verdict", "ERROR")
        score = ev.get("composite_score") or 0
        try:
            score = float(score)
        except (TypeError, ValueError):
            score = 0
        return (VERDICT_RANK.get(verdict, 3), -score)

    ordered = sorted(records, key=sort_key)
    with config.RESULTS_CSV_PATH.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for rec in ordered:
            writer.writerow(row_from(rec))
    logger.info("Wrote ranked CSV -> %s", config.RESULTS_CSV_PATH)


def evaluate() -> int:
    common.setup_logging()
    load_dotenv(config.ROOT / ".env")
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.error("ANTHROPIC_API_KEY not set. Add it to .env before evaluating.")
        return 1

    profiles = common.load_json(config.PROFILES_PATH, [])
    if not profiles:
        logger.error("No profiles at %s. Run extract.py first.", config.PROFILES_PATH)
        return 1

    done = set(common.load_json(config.EVAL_PROGRESS_PATH, []))
    evaluated = {r["profile_id"]: r for r in common.load_json(config.EVALUATED_PATH, [])}

    pending = [p for p in profiles if p["profile_id"] not in done]
    if config.PROFILE_LIMIT:
        pending = pending[: config.PROFILE_LIMIT]
        logger.info("PROFILE_LIMIT=%d -> evaluating %d this run", config.PROFILE_LIMIT, len(pending))
    logger.info(
        "Evaluating %d profiles (%d already done) — %d concurrent workers",
        len(pending), len(done), config.EVAL_WORKERS,
    )

    evaluator = Evaluator(api_key)
    lock = threading.Lock()
    counter = itertools.count(1)

    def score_profile(profile: dict) -> dict:
        pid = profile["profile_id"]
        text = build_profile_text(profile)
        try:
            result = evaluator.score(text)
            record = {**profile, "evaluation": result}
        except Exception as exc:  # noqa: BLE001
            logger.exception("evaluation failed for %s: %s", pid, exc)
            record = {**profile, "evaluation": {"verdict": "ERROR", "error": str(exc)}}

        i = next(counter)
        with lock:
            evaluated[pid] = record
            done.add(pid)
            common.save_json(config.EVALUATED_PATH, list(evaluated.values()))
            common.save_json(config.EVAL_PROGRESS_PATH, sorted(done))
        ev = record["evaluation"]
        logger.info(
            "[%d/%d] %s -> %s (score=%s)",
            i, len(pending), profile.get("name"),
            ev.get("verdict"), ev.get("composite_score"),
        )
        return record

    with concurrent.futures.ThreadPoolExecutor(max_workers=config.EVAL_WORKERS) as executor:
        list(executor.map(score_profile, pending))

    write_csv(list(evaluated.values()))
    return 0


if __name__ == "__main__":
    sys.exit(evaluate())
