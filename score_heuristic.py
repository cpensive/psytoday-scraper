"""Transparent, deterministic, on-machine signal scorer (no API cost).

DESIGN (rewritten 2026-06-30 after Charles's signal review):
- Every signal is computed deterministically from the RAW scraped fields so the
  whole thing is auditable, uniform across all 900 profiles, and free to re-run.
  We do NOT reuse the LLM tier judgments (they were inconsistent, and the old
  `couples_focus_pct` was an invented number - PT publishes no such statistic).
- Signals are honest about provenance: facet-based ones are real; bio-based ones
  are labeled inferences.
- There are no fitted "target scores". The ranking index is a transparent
  weighted blend of signals, weighted by what Charles said matters most:
  couples-centrality, experience, and an active/directive approach. Every signal
  is exposed in the app as a sortable + filterable column, so sorting/filtering
  the signals directly surfaces the strongest therapists.

The profile is a SCREEN to decide who is worth a 15-min call - not a verdict on
therapist quality (which a profile cannot actually measure).

    uv run python score_heuristic.py preview   # top ~30 ranked, with signals
    uv run python score_heuristic.py            # score all -> evaluated.json + csv
"""

from __future__ import annotations

import datetime as _dt
import re
import sys

import csv

import common
import config

logger = common.logger
THIS_YEAR = _dt.date.today().year

# --------------------------------------------------------------------------
# Ranking weights (transparent; tweak freely and re-run for free).
# Primary drivers per Charles: couples-centric, experience, active approach.
# --------------------------------------------------------------------------
W_COUPLES = {"Primary": 32, "Featured": 22, "Mentioned": 10, "No": 0}
# When LLM enrichment is present, couples-centrality comes from the bio read,
# not the keyword counter. primary outranks secondary by design.
CENTRALITY_W = {"primary": 34, "secondary": 16, "individual_primary": 0}
CENTRALITY_RANK = {"primary": 0, "secondary": 1, "individual_primary": 2}
_PRELIC_CRED = re.compile(r"\b(mhc-?lp|mft-?lp|lmsw|pre-?licensed|limited permit|supervised by)\b", re.I)
# Approach is a WEAK signal: only ~2% of bios clearly read "Active" (style is
# marketing copy on PT, not reliably observable). Kept as a small bonus and
# surfaced as a call-time question rather than a primary ranking driver.
W_APPROACH = {"Active": 8, "Mixed": 5, "Unclear": 4, "Exploratory": 0}
W_METHOD_DEPTH = {"supervisor": 12, "certified": 10, "trained": 6, "listed": 3, "none": 0}
W_CULTURAL = {"asian": 7, "intercultural": 7, "ea_language": 6, "checkbox": 2, "none": 0}
W_LICENSE_INDEP = 6
P_PRELICENSED = -22
W_ADHD = 4
P_GENERALIST = -8
P_OFF_TARGET = -12
P_EARLY_CAREER = -6  # < 2 yrs and not certified (on top of low experience pts)

# Screening tiers from the 0-100 index (CALL no longer requires certification).
TIER_CALL = 62
TIER_READ_MORE = 40


def experience_points(yrs) -> int:
    if yrs is None:
        return 9  # Unknown: neutral, neither rewarded nor punished
    if yrs >= 20:
        return 25
    if yrs >= 15:
        return 22
    if yrs >= 10:
        return 18
    if yrs >= 7:
        return 13
    if yrs >= 4:
        return 8
    if yrs >= 2:
        return 4
    return 1


# --------------------------------------------------------------------------
# Regexes for bio-derived (inferred) signals
# --------------------------------------------------------------------------
RE_ACTIVE = re.compile(r"\b(directive|active|i will (challenge|push)|homework|structured|accountab|tough question|i'?ll be honest|practical (skill|tool)|action[- ]oriented|exercise|assign|coach you|tell you)\b", re.I)
RE_EXPLORATORY = re.compile(r"\b(safe space|hold space|non-?judgment|at your (own )?pace|client-?centered|explore|reflect|curiosity|gentle|warmth|witness|sit with|process your)\b", re.I)
RE_SUPERVISOR = re.compile(r"\b(approved supervisor|certified.{0,30}supervisor|master trainer|certified trainer|consultant|faculty|teaches? other|train(s|ing) (other )?(therapist|clinician))\b", re.I)
RE_CERTIFIED = re.compile(r"\bcertified\b.{0,40}\b(gottman|emotionally focused|eft|imago|pact|discernment|sex therap|aasect)\b|\baasect certified\b|\bcertified (gottman|imago|pact|emotionally focused|eft)\b", re.I)
RE_PRELICENSED = re.compile(r"\b(pre-?licensed|limited permit|mhc-?lp|mft-?lp|lmsw|supervised by|under (clinical )?supervision|permit holder)\b", re.I)
RE_INDEPENDENT = re.compile(r"\b(lcsw|licsw|lcsw-?r|lmft|lcat|lmhc|ph\.?d|psy\.?d|licensed psychoanalyst|\blp\b|\bmd\b)\b", re.I)
RE_INTERCULTURAL = re.compile(r"\b(immigrant|intercultural|bicultural|cross-?cultural|interracial|interethnic|1\.5 gen|first-?gen|multiracial|raised (across|between)|third culture|mixed race|biracial)\b", re.I)
RE_OFF_TARGET = re.compile(r"\b(kink|polyamor|bdsm|ethical non-?monogamy|\benm\b|christ|scripture|faith-?based|biblical|gospel|spiritually grounded)\b", re.I)
RE_COUPLE = re.compile(r"\b(couple|partner|relationship|marital|marriage|spouse|the two of you|your relationship)\b", re.I)
RE_SINCE = re.compile(r"\b(?:since|practicing since|in practice since)\s+((?:19|20)\d{2})\b", re.I)
RE_NYEARS = re.compile(r"\b(\d{1,2})\+?\s*years?\b", re.I)

EAST_ASIAN_LANGS = {"mandarin", "cantonese", "chinese", "japanese", "korean", "vietnamese", "taiwanese"}
METHODS = [
    ("Gottman", re.compile(r"gottman", re.I)),
    ("EFT", re.compile(r"emotionally focused|\beft\b|efct", re.I)),
    ("PACT", re.compile(r"\bpact\b|psychobiological", re.I)),
    ("Imago", re.compile(r"imago", re.I)),
    ("Discernment", re.compile(r"discernment", re.I)),
]
STRONG_COUPLE_SPECS = ("marital", "premarital", "couples", "marriage")
LGBTQ_TERMS = re.compile(r"\b(lgbtq|queer|transgender|gender identity|nonbinary|non-?binary|gay|lesbian|bisexual)\b", re.I)


def _text(record: dict) -> str:
    return "\n".join([
        record.get("bio_narrative") or "",
        record.get("treatment_approach_text") or "",
        record.get("qualifications_text") or "",
        " ".join(record.get("additional_credentials") or []),
        record.get("website_bio") or "",
    ])


def _years(record: dict, text: str):
    y = record.get("years_in_practice")
    try:
        return int(str(y).strip())
    except (TypeError, ValueError):
        pass
    m = RE_SINCE.search(text)
    if m:
        yr = int(m.group(1))
        if 1960 <= yr <= THIS_YEAR:
            return THIS_YEAR - yr
    m = re.search(r"\b(?:over|more than)?\s*(\d{1,2})\+?\s*years?\s+(?:of\s+)?(?:experience|practice|practicing)", text, re.I)
    if m:
        n = int(m.group(1))
        if 1 <= n <= 60:
            return n
    # Fallback: earliest year mentioned in credentials/qualifications (e.g. a
    # certificate or license year) is a conservative lower bound on tenure.
    cred_text = " ".join(record.get("additional_credentials") or []) + " " + (record.get("qualifications_text") or "")
    yrs = [int(x) for x in re.findall(r"\b(19[6-9]\d|20[0-2]\d)\b", cred_text)]
    yrs = [yr for yr in yrs if 1960 <= yr <= THIS_YEAR]
    if yrs:
        est = THIS_YEAR - min(yrs)
        if 2 <= est <= 50:
            return est
    return None


def extract_signals(record: dict) -> dict:
    text = _text(record)
    therapy_types = record.get("therapy_types") or []
    tt_join = " ".join(therapy_types).lower()
    specialties = [s.lower() for s in (record.get("specialties_all") or [])]
    top_specs = [s.lower() for s in (record.get("specialties_top") or [])]
    participants = [p.lower() for p in (record.get("client_focus_participants") or [])]
    ethnicity = [e.lower() for e in (record.get("ethnicity") or [])]
    langs = [l.lower() for l in (record.get("languages") or [])]
    creds = " ".join([
        record.get("credentials") or "", record.get("license_type") or "", record.get("job_title") or "",
    ])

    couple_hits = len(RE_COUPLE.findall(text))

    # --- Couples-centric (real top-specialties + inferred bio emphasis) ---
    strong_spec = any(any(k in t for k in STRONG_COUPLE_SPECS) for t in top_specs)
    rel_spec_top = any("relationship" in t for t in top_specs)
    couples_participant = "couples" in participants
    if strong_spec and couple_hits >= 3:
        couples = "Primary"
    elif strong_spec or (rel_spec_top and couple_hits >= 3):
        couples = "Featured"
    elif rel_spec_top or couple_hits >= 2 or couples_participant:
        couples = "Mentioned"
    else:
        couples = "No"

    # --- Method named (real: therapy-types list + bio) ---
    methods = [name for name, rx in METHODS if rx.search(tt_join) or rx.search(text)]
    if len(methods) >= 2:
        method = "Multiple"
    elif methods:
        method = methods[0]
    else:
        method = "None"

    # --- Method depth (text-derived) ---
    if RE_SUPERVISOR.search(text):
        method_depth = "supervisor"
    elif RE_CERTIFIED.search(text):
        method_depth = "certified"
    elif methods and re.search(r"\b(level [123]|trained|externship|certificate|advanced training)\b", text, re.I):
        method_depth = "trained"
    elif methods:
        method_depth = "listed"
    else:
        method_depth = "none"

    # --- Experience (real where present, else parsed from bio, else Unknown) ---
    years = _years(record, text)

    # --- Approach (inferred from bio language; weak signal - see note) ---
    a, e = len(RE_ACTIVE.findall(text)), len(RE_EXPLORATORY.findall(text))
    if a and e:
        approach = "Active" if a > e + 1 else ("Exploratory" if e > a + 1 else "Mixed")
    elif a:
        approach = "Active"
    elif e:
        approach = "Exploratory"
    else:
        approach = "Unclear"

    # --- Cultural fit (ethnicity facet + languages + bio) ---
    asian = any("asian" in x for x in ethnicity)
    ea_lang = any(l in EAST_ASIAN_LANGS for l in langs)
    intercultural = bool(RE_INTERCULTURAL.search(text))
    if asian:
        cultural = "asian"
    elif intercultural:
        cultural = "intercultural"
    elif ea_lang:
        cultural = "ea_language"
    elif any("multicultural" in s or "culturally" in s for s in specialties):
        cultural = "checkbox"
    else:
        cultural = "none"

    # --- License (real) ---
    prelicensed = bool(RE_PRELICENSED.search(creds)) or method_depth == "none" and bool(RE_PRELICENSED.search(text))
    independent = bool(RE_INDEPENDENT.search(creds)) and not prelicensed
    license_level = "pre_licensed" if prelicensed else ("independent" if independent else "unknown")

    n_modalities = len(therapy_types)
    n_specialties = len(specialties)
    generalist = n_modalities > 12 or n_specialties > 20
    # off-target: explicit religious/kink/poly, OR a bio heavily centered on LGBTQ identity
    off_target = bool(RE_OFF_TARGET.search(text)) or len(LGBTQ_TERMS.findall(text)) >= 3
    adhd = any("adhd" in s for s in top_specs) or any("adhd" in s for s in specialties[:6])

    return {
        "couples": couples,
        "method": method,
        "method_depth": method_depth,
        "experience_years": years,
        "approach": approach,
        "cultural": cultural,
        "license": license_level,
        "adhd": adhd,
        "n_modalities": n_modalities,
        "n_specialties": n_specialties,
        "generalist": generalist,
        "off_target": off_target,
        "nyc_in_person": bool(record.get("in_person")),
        "online": bool(record.get("online")),
    }


def deterministic_exclude(record: dict) -> str | None:
    """Clear, free disqualifiers (Charles's confirmed major flags). The
    Primary-vs-secondary couples cut is NOT made here - that comes from the LLM
    couples-centrality read in score_record."""
    s = extract_signals(record)
    creds = f"{record.get('credentials') or ''} {record.get('license_type') or ''} {record.get('job_title') or ''}"
    if s["couples"] == "No":
        return "no_couples"
    if _PRELIC_CRED.search(creds) or s["license"] == "pre_licensed":
        return "pre_licensed"
    if (s["experience_years"] is not None and s["experience_years"] < 3
            and s["method_depth"] not in ("certified", "supervisor")):
        return "early_career"
    if s["off_target"]:
        return "religious/off_target"
    return None


def _effective_centrality(s: dict, enr: dict | None) -> str:
    """LLM read if available, else map the regex couples bucket onto the same
    three-level scale so everything sorts/filters consistently."""
    if enr and enr.get("couples_centrality") in CENTRALITY_RANK:
        return enr["couples_centrality"]
    return {"Primary": "primary", "Featured": "secondary",
            "Mentioned": "secondary", "No": "individual_primary"}[s["couples"]]


def score_record(record: dict, enr: dict | None = None) -> dict:
    s = extract_signals(record)
    centrality = _effective_centrality(s, enr)
    s["couples_centrality"] = centrality
    if enr:
        s["style_llm"] = enr.get("style")
        s["faith_framed"] = bool(enr.get("faith_framed"))
        s["individual_only"] = bool(enr.get("individual_only"))
        if enr.get("one_line"):
            s["llm_one_line"] = enr["one_line"]

    idx = 0
    idx += CENTRALITY_W.get(centrality, W_COUPLES.get(s["couples"], 0))
    idx += experience_points(s["experience_years"])
    idx += W_APPROACH.get(s["approach"], 0)
    idx += W_METHOD_DEPTH.get(s["method_depth"], 0)
    idx += W_CULTURAL.get(s["cultural"], 0)
    if s["license"] == "independent":
        idx += W_LICENSE_INDEP
    elif s["license"] == "pre_licensed":
        idx += P_PRELICENSED
    if s["adhd"]:
        idx += W_ADHD
    if s["generalist"]:
        idx += P_GENERALIST
    if s["off_target"]:
        idx += P_OFF_TARGET
    if (s["experience_years"] is not None and s["experience_years"] < 2
            and s["method_depth"] not in ("certified", "supervisor")):
        idx += P_EARLY_CAREER

    index = max(0, min(100, idx))

    reason = deterministic_exclude(record)
    if not reason and enr:
        if s.get("faith_framed"):
            reason = "religious"
        elif s.get("individual_only") or centrality == "individual_primary":
            reason = "individual_only"
    elif not reason and centrality == "individual_primary":
        reason = "individual_only"

    if reason:
        tier = "EXCLUDED"
    elif centrality == "primary":
        tier = "CALL" if index >= TIER_CALL else "READ_MORE"
    elif index >= TIER_CALL:
        tier = "READ_MORE"  # strong secondary: worth a look, never a top CALL
    elif index >= TIER_READ_MORE:
        tier = "READ_MORE"
    else:
        tier = "SKIP"

    return {"index": index, "tier": tier, "signals": s, "exclude_reason": reason}


def _summary(s: dict) -> str:
    yrs = f"{s['experience_years']} yrs" if s["experience_years"] is not None else "yrs unknown"
    bits = [f"Couples: {s['couples']}", yrs, s["approach"]]
    if s["method"] != "None":
        depth = "" if s["method_depth"] in ("none", "listed") else f" ({s['method_depth']})"
        bits.append(f"{s['method']}{depth}")
    if s["cultural"] not in ("none", "checkbox"):
        bits.append(s["cultural"].replace("_", " "))
    if s["adhd"]:
        bits.append("ADHD")
    flags = []
    if s["generalist"]:
        flags.append("generalist")
    if s["off_target"]:
        flags.append("off-target")
    if s["license"] == "pre_licensed":
        flags.append("pre-licensed")
    out = " \u00b7 ".join(bits)
    if flags:
        out += "  [\u26a0 " + ", ".join(flags) + "]"
    return out


# --------------------------------------------------------------------------
# CSV output (matches the signals schema above - no stale LLM tier fields)
# --------------------------------------------------------------------------
CSV_COLUMNS = [
    "Name", "Verdict", "Score", "ExcludeReason", "CouplesCentrality", "Method",
    "MethodDepth", "Years", "Style", "Cultural", "License", "ADHD",
    "Generalist", "Languages", "School", "FeeCouples", "Phone", "Website",
    "PT_URL", "OneLine", "AskOnCall",
]
VERDICT_RANK = {"CALL": 0, "READ_MORE": 1, "SKIP": 2, "EXCLUDED": 3, "ERROR": 4}


def row_from(record: dict) -> dict:
    ev = record.get("evaluation") or {}
    s = ev.get("signals") or {}
    return {
        "Name": ev.get("name") or record.get("name", ""),
        "Verdict": ev.get("verdict", "ERROR"),
        "Score": ev.get("composite_score", ""),
        "ExcludeReason": ev.get("exclude_reason") or "",
        "CouplesCentrality": s.get("couples_centrality", ""),
        "Method": s.get("method", ""),
        "MethodDepth": s.get("method_depth", ""),
        "Years": s.get("experience_years") if s.get("experience_years") is not None else "",
        "Style": s.get("style_llm") or s.get("approach", ""),
        "Cultural": s.get("cultural", ""),
        "License": s.get("license", ""),
        "ADHD": "yes" if s.get("adhd") else "",
        "Generalist": "yes" if s.get("generalist") else "",
        "Languages": ", ".join(record.get("languages") or []),
        "School": record.get("school") or "",
        "FeeCouples": record.get("fee_couples") or "",
        "Phone": record.get("phone") or "",
        "Website": record.get("website_url") or "",
        "PT_URL": record.get("url", ""),
        "OneLine": ev.get("one_line", ""),
        "AskOnCall": ev.get("ask_on_call", ""),
    }


def write_csv(records: list[dict]) -> None:
    def sort_key(r: dict):
        ev = r.get("evaluation") or {}
        score = ev.get("composite_score") or 0
        try:
            score = float(score)
        except (TypeError, ValueError):
            score = 0
        return (VERDICT_RANK.get(ev.get("verdict", "ERROR"), 5), -score)

    ordered = sorted(records, key=sort_key)
    with config.RESULTS_CSV_PATH.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for rec in ordered:
            writer.writerow(row_from(rec))
    logger.info("Wrote ranked CSV -> %s", config.RESULTS_CSV_PATH)


def run() -> int:
    common.setup_logging()
    profiles = common.load_json(config.PROFILES_PATH, [])
    if not profiles:
        logger.error("No profiles at %s", config.PROFILES_PATH)
        return 1

    enriched = common.load_json(config.DATA_DIR / "enriched.json", {})
    if enriched:
        logger.info("Merging LLM enrichment for %d profiles", len(enriched))

    out_records = []
    counts: dict[str, int] = {}
    for p in profiles:
        enr = enriched.get(str(p.get("profile_id")))
        if enr and "error" in enr:
            enr = None
        out = score_record(p, enr)
        s = out["signals"]
        rec = dict(p)
        rec["evaluation"] = {
            "name": p.get("name"),
            "composite_score": out["index"],   # 0-100 screening index (within centrality band)
            "verdict": out["tier"],
            "exclude_reason": out["exclude_reason"],
            "scorer": "signals_v3" if enr else "signals_v3_regex",
            "one_line": s.get("llm_one_line") or _summary(s),
            "ask_on_call": (
                "Roughly what share of your current caseload is couples (vs individuals), "
                "and how directive are you in session - do you assign exercises/homework and "
                "actively challenge patterns, or mostly hold space? (Style isn't knowable from a profile.)"
            ),
            "signals": s,
        }
        out_records.append(rec)
        counts[out["tier"]] = counts.get(out["tier"], 0) + 1

    # Sort so all 'primary' rank above all 'secondary' (per Charles), then by index.
    out_records.sort(key=lambda r: (
        CENTRALITY_RANK.get(r["evaluation"]["signals"].get("couples_centrality"), 3),
        -r["evaluation"]["composite_score"],
    ))
    common.save_json(config.EVALUATED_PATH, out_records)
    write_csv([r for r in out_records if r["evaluation"]["verdict"] != "EXCLUDED"])
    logger.info("Signal-scored %d profiles. Tiers: %s", len(out_records), counts)
    return 0


def preview(n: int = 30) -> int:
    common.setup_logging()
    profiles = common.load_json(config.PROFILES_PATH, [])
    scored = [(score_record(p), p) for p in profiles]
    scored.sort(key=lambda x: -x[0]["index"])
    print(f"{'idx':>3} {'tier':9} {'couples':9} {'yrs':>4} {'approach':11} {'method':9} {'depth':10} {'cult':12} name")
    print("-" * 110)
    for out, p in scored[:n]:
        s = out["signals"]
        print(f"{out['index']:>3} {out['tier']:9} {s['couples']:9} {str(s['experience_years'] or '?'):>4} "
              f"{s['approach']:11} {s['method']:9} {s['method_depth']:10} {s['cultural']:12} {p.get('name')}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "preview":
        sys.exit(preview())
    sys.exit(run())
