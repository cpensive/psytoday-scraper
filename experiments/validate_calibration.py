"""Option A: validate the tuned rubric against Charles's 20-profile calibration.

Re-scores each calibration profile under the PRODUCTION config (no thinking,
temp 0) and compares the new composite_score to Charles's stated expectation
from data/calibration.md, applying +/-1 point grace ("directionally right").

Expected bands are parsed from Charles's free-text "Your call" notes:
  - explicit number  -> (n, n)
  - a range          -> (lo, hi)
  - "agree"          -> the old model score he agreed with
A new score passes if it lands within [lo-1, hi+1].
"""
from __future__ import annotations

import os
import time

from dotenv import load_dotenv

import common
import config
from evaluate import SYSTEM_PROMPT, build_profile_text, parse_json

# (profile_id, name, old_model_score, expected_lo, expected_hi, note)
CALIB = [
    ("120401", "Travis Atkinson", 10, 10, 10, "agree CALL"),
    ("703596", "Suzie Wu", 7, 7, 7, "agree 7"),
    ("1208025", "Sahar (Common Ground)", 7, 5, 5, "disagree -> 5"),
    ("923154", "Fresh Start (Davis)", 6, 6, 6, "agree READ_MORE"),
    ("1403001", "Danielle Jediny-Racies", 6, 6, 6, "agree 6"),
    ("952611", "Nicole Elden", 5, 3, 4, "disagree -> lower"),
    ("1541831", "Whitney Sha", 5, 6, 7, "disagree -> 6-7"),
    ("923066", "Danny Gomez", 5, 4, 5, "agree 4-5"),
    ("79083", "Anita Gulati", 5, 5, 5, "agree 5"),
    ("930495", "Irene Cheng", 5, 7, 7, "disagree -> 7"),
    ("212747", "Vijayeta Sinh", 5, 6, 6, "slight disagree -> 6"),
    ("388200", "Charlene Chan", 4, 5, 6, "disagree -> 5-6"),
    ("274273", "MTZ Counseling", 4, 4, 4, "agree 4"),
    ("1019718", "Sugandha Sharma", 4, 4, 4, "agree skip"),
    ("1396700", "Poorva Parashar", 3, 3, 3, "agree skip (hard no)"),
    ("1107266", "Anushua Arif", 3, 3, 3, "agree"),
    ("395598", "Katheryn Soleil", 3, 3, 3, "agree"),
    ("1371011", "Kaylee Bayer", 3, 3, 3, "agree"),
    ("1547693", "Annabelle Ang", 2, 2, 2, "agree"),
    ("1636172", "Susan Hart (Christ)", 2, 2, 2, "agree"),
]

GRACE = 1
THROTTLE_S = 3.0

load_dotenv(config.ROOT / ".env")
import anthropic  # noqa: E402

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
profiles = {p["profile_id"]: p for p in common.load_json(config.PROFILES_PATH, [])}


def score_once(text: str) -> dict:
    r = client.messages.create(
        model=config.ANTHROPIC_MODEL, max_tokens=config.ANTHROPIC_MAX_TOKENS,
        temperature=config.ANTHROPIC_TEMPERATURE, system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": text}],
    )
    return parse_json(r.content[0].text)


print(f"{'Profile':<26}{'old':>4}{'new':>5}{'exp':>8}  grace  note", flush=True)
print("-" * 78, flush=True)
passes = 0
diffs = []
for pid, name, old, lo, hi, note in CALIB:
    p = profiles.get(pid)
    if not p:
        print(f"{name:<26}  -- profile_id {pid} not in profiles.json", flush=True)
        continue
    try:
        d = score_once(build_profile_text(p))
        new = d.get("composite_score")
    except Exception as e:  # noqa: BLE001
        print(f"{name:<26}  ERROR {type(e).__name__}: {str(e)[:120]}", flush=True)
        break
    try:
        ok = (lo - GRACE) <= float(new) <= (hi + GRACE)
    except (TypeError, ValueError):
        ok = False
    passes += ok
    # signed distance from the nearest edge of the expected band (for bias)
    nf = float(new) if new is not None else 0
    if nf < lo:
        diffs.append(nf - lo)
    elif nf > hi:
        diffs.append(nf - hi)
    else:
        diffs.append(0)
    exp = f"{lo}-{hi}" if lo != hi else f"{lo}"
    print(f"{name:<26}{old:>4}{str(new):>5}{exp:>8}  {'PASS' if ok else 'FAIL':<5}  {note}", flush=True)
    time.sleep(THROTTLE_S)

n = len(diffs)
print("-" * 78, flush=True)
if n:
    bias = sum(diffs) / n
    print(f"Within +/-{GRACE}: {passes}/{n}   mean signed deviation from expected band: {bias:+.2f}", flush=True)
    print("(negative bias = new rubric scores LOWER than you expected)", flush=True)
