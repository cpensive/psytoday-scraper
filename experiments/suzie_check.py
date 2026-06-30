"""One-off: re-run the Suzie boundary profile under thinking_high (we got 0
clean runs before) and report cents-per-score for each config.

Pricing assumption (EDIT if your plan differs) - Claude Sonnet 4.5 standard:
  input  $3.00 / Mtok
  output $15.00 / Mtok   (extended-thinking tokens are billed as output)
"""
from __future__ import annotations

import collections
import os
import time

from dotenv import load_dotenv

import common
import config
from evaluate import SYSTEM_PROMPT, build_profile_text, parse_json

PRICE_IN = 3.0 / 1_000_000
PRICE_OUT = 15.0 / 1_000_000

load_dotenv(config.ROOT / ".env")
import anthropic  # noqa: E402

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
profiles = {p["profile_id"]: p for p in common.load_json(config.PROFILES_PATH, [])}
text = build_profile_text(profiles["703596"])  # Suzie Shihshin Wu


def extract_text(resp) -> str:
    for b in resp.content:
        if getattr(b, "type", None) == "text":
            return b.text
    raise ValueError("no text block")


def run(label: str, temp: float, budget: int | None, max_tokens: int, n: int) -> list:
    rows = []
    for i in range(1, n + 1):
        kw = dict(model=config.ANTHROPIC_MODEL, max_tokens=max_tokens, temperature=temp,
                  system=SYSTEM_PROMPT, messages=[{"role": "user", "content": text}])
        if budget:
            kw["thinking"] = {"type": "enabled", "budget_tokens": budget}
        try:
            r = client.messages.create(**kw)
        except Exception as e:  # noqa: BLE001
            print(f"{label} {i}/{n} ERROR {type(e).__name__}: {str(e)[:200]}", flush=True)
            return rows
        d = parse_json(extract_text(r))
        u = r.usage
        cents = (u.input_tokens * PRICE_IN + u.output_tokens * PRICE_OUT) * 100
        rows.append((d.get("composite_score"), d.get("verdict"), u.input_tokens, u.output_tokens, cents))
        print(f"{label} {i}/{n}: score={d.get('composite_score')} verdict={d.get('verdict')} "
              f"in={u.input_tokens} out={u.output_tokens} cost={cents:.3f}c", flush=True)
        time.sleep(3)
    return rows


def summ(label: str, rows: list) -> None:
    if not rows:
        print(f"{label}: NO DATA (likely blocked)", flush=True)
        return
    scores = collections.Counter(r[0] for r in rows)
    avg = sum(r[4] for r in rows) / len(rows)
    print(f"{label}: scores={dict(scores)}  avg={avg:.3f}c/score "
          f"(~${avg/100*800:.2f} for an 800-profile re-score)", flush=True)


print("=== no_thinking x3 (production config + cost baseline + credit check) ===", flush=True)
nt = run("no_thinking", 0.0, None, 1200, 3)
print("=== thinking_high x5 (temp 1, budget 12000) ===", flush=True)
hi = run("thinking_high", 1.0, 12000, 13500, 5)
print("\n--- SUMMARY ---", flush=True)
summ("no_thinking", nt)
summ("thinking_high", hi)
