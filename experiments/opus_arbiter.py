"""Opus 4.8 no_thinking as an arbiter on the contested calibration profiles.

For each contested profile, score 3x with Opus (no thinking, temp 0) and show it
next to your expected band, Sonnet's score, and Haiku's score.

Pricing assumption (EDIT if different) - Opus 4.8: input $15 / Mtok, output $75 / Mtok.
"""
from __future__ import annotations

import collections
import os
import time

from dotenv import load_dotenv

import common
import config
from evaluate import SYSTEM_PROMPT, build_profile_text, parse_json

OPUS_MODEL = "claude-opus-4-8"
PRICE_IN, PRICE_OUT = 15.0 / 1_000_000, 75.0 / 1_000_000
N = 3

# (pid, name, exp_lo, exp_hi, sonnet, haiku, why_contested)
CONTESTED = [
    ("1403001", "Danielle Jediny-Racies", 6, 6, 4, 5, "you=6, Sonnet under (4=SKIP)"),
    ("930495", "Irene Cheng", 7, 7, 5, 5, "you=7, both under (specificity tweak didn't lift)"),
    ("212747", "Vijayeta Sinh", 6, 6, 5, 4, "you=6, Haiku under (4=SKIP)"),
    ("923066", "Danny Gomez", 4, 5, 5, 4, "Sonnet/Haiku straddle SKIP/READ_MORE"),
    ("1107266", "Anushua Arif", 3, 3, 4, 5, "Sonnet/Haiku straddle; Haiku over-surfaces"),
]

load_dotenv(config.ROOT / ".env")
import anthropic  # noqa: E402

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
profiles = {p["profile_id"]: p for p in common.load_json(config.PROFILES_PATH, [])}


def score(text: str):
    # Opus 4.8 deprecated the `temperature` param; omit it (model is deterministic by default).
    r = client.messages.create(
        model=OPUS_MODEL, max_tokens=1200, system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": text}],
    )
    u = r.usage
    cents = (u.input_tokens * PRICE_IN + u.output_tokens * PRICE_OUT) * 100
    d = parse_json(r.content[0].text)
    return d.get("composite_score"), d.get("verdict"), cents


rows = []
total_cents = 0.0
for pid, name, lo, hi, son, hai, why in CONTESTED:
    p = profiles.get(pid)
    if not p:
        print(f"{name}: missing {pid}", flush=True)
        continue
    text = build_profile_text(p)
    scores, verdicts = [], []
    for i in range(N):
        try:
            s, v, c = score(text)
        except Exception as e:  # noqa: BLE001
            print(f"{name} run{i+1} ERROR {type(e).__name__}: {str(e)[:120]}", flush=True)
            break
        scores.append(s)
        verdicts.append(v)
        total_cents += c
        time.sleep(2)
    opus_mode = collections.Counter(scores).most_common(1)[0][0] if scores else "?"
    consistent = len(set(scores)) <= 1
    rows.append((name, f"{lo}-{hi}" if lo != hi else str(lo), son, hai, opus_mode, scores, consistent, why))
    print(f"{name}: opus={scores} verdicts={verdicts}", flush=True)

print("\n" + "=" * 92, flush=True)
print(f"{'Profile':<24}{'you':>6}{'son':>5}{'hai':>5}{'opus':>6}  consist  note", flush=True)
print("-" * 92, flush=True)
for name, exp, son, hai, opus_mode, scores, consistent, why in rows:
    print(f"{name:<24}{exp:>6}{son:>5}{hai:>5}{str(opus_mode):>6}  "
          f"{'yes' if consistent else 'NO ':>7}  {why}", flush=True)
print("-" * 92, flush=True)
print(f"Opus cost for this arbiter run: {total_cents:.1f}c", flush=True)
print("DONE", flush=True)
