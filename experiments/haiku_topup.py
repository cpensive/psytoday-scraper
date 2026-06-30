"""Top-up Haiku thinking runs to reach 5 samples per config.
Already captured (prior run): thinking_medium = [6, 6.5, 6]; thinking_high = [].
This adds: thinking_medium x2, thinking_high x5. Thinking forces temperature=1.
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
PRICE_IN, PRICE_OUT = 1.0 / 1_000_000, 5.0 / 1_000_000

load_dotenv(config.ROOT / ".env")
import anthropic  # noqa: E402

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
text = build_profile_text(
    {p["profile_id"]: p for p in common.load_json(config.PROFILES_PATH, [])}["703596"]
)
PRIOR = {"thinking_medium": [6, 6.5, 6], "thinking_high": []}
TODO = [("thinking_medium", 4000, 5500, 2), ("thinking_high", 12000, 13500, 5)]


def text_of(resp):
    for b in resp.content:
        if getattr(b, "type", None) == "text":
            return b.text
    raise ValueError("no text")


for label, budget, max_tokens, n in TODO:
    print(f"=== {label}: {n} more (prior={PRIOR[label]}) ===", flush=True)
    new_scores, costs = list(PRIOR[label]), []
    for i in range(1, n + 1):
        r = client.messages.create(
            model=HAIKU_MODEL, max_tokens=max_tokens, temperature=1.0, system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": text}],
            thinking={"type": "enabled", "budget_tokens": budget},
        )
        d = parse_json(text_of(r))
        u = r.usage
        cents = (u.input_tokens * PRICE_IN + u.output_tokens * PRICE_OUT) * 100
        costs.append(cents)
        new_scores.append(d.get("composite_score"))
        print(f"  [{label} +{i}/{n}] score={d.get('composite_score')} verdict={d.get('verdict')} "
              f"out={u.output_tokens} cost={cents:.3f}c", flush=True)
        time.sleep(2)
    avgc = sum(costs) / len(costs) if costs else 0
    print(f"  -> {label} all 5 scores={dict(collections.Counter(new_scores))} "
          f"new-run avg={avgc:.3f}c/score", flush=True)
print("DONE", flush=True)
