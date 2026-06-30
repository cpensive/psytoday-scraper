"""Compare Haiku vs Sonnet scoring on the Suzie boundary profile.

Runs Haiku (claude-haiku-4-5) under no_thinking / thinking_medium / thinking_high,
N times each, reporting score consistency and cents/score.

Per Charles's request we try to keep temperature LOW for the thinking configs.
NOTE: Anthropic extended thinking requires temperature=1; if the API rejects a
low temp we fall back to 1.0 and flag it (so the runs still produce data).

Pricing assumptions (EDIT if your plan differs):
  Haiku 4.5 : input $1.00 / Mtok, output $5.00 / Mtok
For reference, Sonnet 4.5 was input $3 / output $15.
"""
from __future__ import annotations

import collections
import os
import time

from dotenv import load_dotenv

import common
import config
from evaluate import SYSTEM_PROMPT, build_profile_text, parse_json

HAIKU_MODEL = "claude-haiku-4-5-20251001"
PRICE_IN = 1.0 / 1_000_000
PRICE_OUT = 5.0 / 1_000_000
N = 10
THROTTLE_S = 2.0
DESIRED_THINK_TEMP = 0.0  # Charles: keep temperature low for thinking

CONFIGS = [
    {"label": "no_thinking", "temp": 0.0, "budget": None, "max_tokens": 1200},
    {"label": "thinking_medium", "temp": DESIRED_THINK_TEMP, "budget": 4000, "max_tokens": 5500},
    {"label": "thinking_high", "temp": DESIRED_THINK_TEMP, "budget": 12000, "max_tokens": 13500},
]

load_dotenv(config.ROOT / ".env")
import anthropic  # noqa: E402

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
text = build_profile_text(
    {p["profile_id"]: p for p in common.load_json(config.PROFILES_PATH, [])}["703596"]
)


def extract_text(resp) -> str:
    for b in resp.content:
        if getattr(b, "type", None) == "text":
            return b.text
    raise ValueError("no text block")


def call(cfg: dict, temp: float):
    kw = dict(model=HAIKU_MODEL, max_tokens=cfg["max_tokens"], temperature=temp,
              system=SYSTEM_PROMPT, messages=[{"role": "user", "content": text}])
    if cfg["budget"]:
        kw["thinking"] = {"type": "enabled", "budget_tokens": cfg["budget"]}
    return client.messages.create(**kw)


def run(cfg: dict):
    rows, temp_used, fellback = [], cfg["temp"], False
    for i in range(1, N + 1):
        try:
            r = call(cfg, temp_used)
        except anthropic.BadRequestError as e:
            msg = str(e)
            if cfg["budget"] and "temperature" in msg.lower() and temp_used != 1.0:
                temp_used, fellback = 1.0, True
                print(f"  [{cfg['label']}] low temp rejected for thinking -> falling back to temp=1.0", flush=True)
                try:
                    r = call(cfg, temp_used)
                except Exception as e2:  # noqa: BLE001
                    print(f"  [{cfg['label']} {i}/{N}] ERROR {type(e2).__name__}: {str(e2)[:150]}", flush=True)
                    return rows, temp_used, fellback
            else:
                print(f"  [{cfg['label']} {i}/{N}] BadRequest: {msg[:150]}", flush=True)
                return rows, temp_used, fellback
        except Exception as e:  # noqa: BLE001
            print(f"  [{cfg['label']} {i}/{N}] ERROR {type(e).__name__}: {str(e)[:150]}", flush=True)
            return rows, temp_used, fellback
        d = parse_json(extract_text(r))
        u = r.usage
        cents = (u.input_tokens * PRICE_IN + u.output_tokens * PRICE_OUT) * 100
        rows.append((d.get("composite_score"), d.get("verdict"), cents))
        print(f"  [{cfg['label']} {i}/{N}] temp={temp_used} score={d.get('composite_score')} "
              f"verdict={d.get('verdict')} out={u.output_tokens} cost={cents:.3f}c", flush=True)
        time.sleep(THROTTLE_S)
    return rows, temp_used, fellback


print(f"HAIKU = {HAIKU_MODEL}  | profile = Suzie Wu (703596) | N={N}", flush=True)
results = []
for cfg in CONFIGS:
    print(f"=== {cfg['label']} ===", flush=True)
    rows, temp_used, fellback = run(cfg)
    results.append((cfg["label"], rows, temp_used, fellback))

print("\n==================== HAIKU SUMMARY ====================", flush=True)
for label, rows, temp_used, fellback in results:
    if not rows:
        print(f"[{label}] no data", flush=True)
        continue
    scores = collections.Counter(r[0] for r in rows)
    verdicts = collections.Counter(r[1] for r in rows)
    avg = sum(r[2] for r in rows) / len(rows)
    consistent = len(scores) == 1 and len(verdicts) == 1
    note = f" (temp fell back to 1.0)" if fellback else f" (temp={temp_used})"
    print(f"[{label}] {'CONSISTENT' if consistent else 'VARIABLE'}{note}", flush=True)
    print(f"    scores={dict(scores)} verdicts={dict(verdicts)} avg={avg:.3f}c/score "
          f"(~${avg/100*800:.2f} per 800-profile run)", flush=True)

print("\n--- SONNET 4.5 REFERENCE (same Suzie profile, prior runs) ---", flush=True)
print("[no_thinking temp0]   CONSISTENT  score=6 x all     ~1.81c/score (~$14.50/800)", flush=True)
print("[thinking_medium t1]  VARIABLE    scores 6/7 split", flush=True)
print("[thinking_high t1]    VARIABLE    scores 5,5,5,6,7 + one SKIP  ~4.21c/score (~$33.69/800)", flush=True)
