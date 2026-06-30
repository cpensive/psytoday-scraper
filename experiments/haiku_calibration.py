"""Haiku no_thinking across the 20 calibration profiles, head-to-head vs Sonnet.

Scores each profile with Haiku 4.5 (no thinking, temp 0) and compares to:
  - Charles's expected band (+/-1 grace)
  - Sonnet 4.5's new scores (from validate_calibration.py this session)
"""
from __future__ import annotations

import os
import time

from dotenv import load_dotenv

import common
import config
from evaluate import SYSTEM_PROMPT, build_profile_text, parse_json

HAIKU_MODEL = "claude-haiku-4-5-20251001"
PRICE_IN, PRICE_OUT = 1.0 / 1_000_000, 5.0 / 1_000_000
GRACE = 1

# (pid, name, expected_lo, expected_hi, sonnet_new_score)
CALIB = [
    ("120401", "Travis Atkinson", 10, 10, 10),
    ("703596", "Suzie Wu", 7, 7, 6),
    ("1208025", "Sahar (Common Ground)", 5, 5, 5),
    ("923154", "Fresh Start (Davis)", 6, 6, 5),
    ("1403001", "Danielle Jediny-Racies", 6, 6, 4),
    ("952611", "Nicole Elden", 3, 4, 5),
    ("1541831", "Whitney Sha", 6, 7, 5),
    ("923066", "Danny Gomez", 4, 5, 5),
    ("79083", "Anita Gulati", 5, 5, 6),
    ("930495", "Irene Cheng", 7, 7, 5),
    ("212747", "Vijayeta Sinh", 6, 6, 5),
    ("388200", "Charlene Chan", 5, 6, 5),
    ("274273", "MTZ Counseling", 4, 4, 4),
    ("1019718", "Sugandha Sharma", 4, 4, 4),
    ("1396700", "Poorva Parashar", 3, 3, 3),
    ("1107266", "Anushua Arif", 3, 3, 4),
    ("395598", "Katheryn Soleil", 3, 3, 3),
    ("1371011", "Kaylee Bayer", 3, 3, 3),
    ("1547693", "Annabelle Ang", 2, 2, 3),
    ("1636172", "Susan Hart (Christ)", 2, 2, 2),
]

load_dotenv(config.ROOT / ".env")
import anthropic  # noqa: E402

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
profiles = {p["profile_id"]: p for p in common.load_json(config.PROFILES_PATH, [])}


def score(text: str):
    r = client.messages.create(
        model=HAIKU_MODEL, max_tokens=1200, temperature=0.0, system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": text}],
    )
    u = r.usage
    cents = (u.input_tokens * PRICE_IN + u.output_tokens * PRICE_OUT) * 100
    return parse_json(r.content[0].text), cents


print(f"{'Profile':<26}{'exp':>6}{'son':>5}{'hai':>5}  grace  son-hai", flush=True)
print("-" * 70, flush=True)
hpass = agree = 0
deltas, total_cents = [], 0.0
n = 0
for pid, name, lo, hi, son in CALIB:
    p = profiles.get(pid)
    if not p:
        print(f"{name:<26}  (missing {pid})", flush=True)
        continue
    try:
        d, cents = score(build_profile_text(p))
        hai = d.get("composite_score")
    except Exception as e:  # noqa: BLE001
        print(f"{name:<26}  ERROR {type(e).__name__}: {str(e)[:110]}", flush=True)
        break
    total_cents += cents
    n += 1
    try:
        hf = float(hai)
        ok = (lo - GRACE) <= hf <= (hi + GRACE)
    except (TypeError, ValueError):
        hf, ok = None, False
    hpass += ok
    delta = (hf - son) if hf is not None else None
    if delta is not None:
        deltas.append(delta)
        if abs(delta) < 1e-9:
            agree += 1
    exp = f"{lo}-{hi}" if lo != hi else f"{lo}"
    ds = f"{delta:+.1f}" if delta is not None else "?"
    print(f"{name:<26}{exp:>6}{son:>5}{str(hai):>5}  {'PASS' if ok else 'FAIL':<5}  {ds}", flush=True)
    time.sleep(2)

print("-" * 70, flush=True)
if n:
    mad = sum(abs(x) for x in deltas) / len(deltas) if deltas else 0
    mean_d = sum(deltas) / len(deltas) if deltas else 0
    print(f"Haiku within +/-{GRACE} of your scores: {hpass}/{n}", flush=True)
    print(f"Haiku == Sonnet (exact): {agree}/{len(deltas)}   "
          f"mean(haiku-sonnet)={mean_d:+.2f}   mean|diff|={mad:.2f}", flush=True)
    print(f"Haiku total cost for 20: {total_cents:.1f}c  (~{total_cents/20:.3f}c/score)", flush=True)
print("DONE", flush=True)
