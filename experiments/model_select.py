"""One-off: pick the scoring model/config by measuring output consistency.

Runs ONE representative profile through the exact scoring prompt (evaluate.SYSTEM_PROMPT)
N times under each candidate config and reports how consistent the composite_score
and verdict are. A config is "consistent" if all N runs agree on score AND verdict.

Candidate configs (Anthropic API reality):
  - no_thinking      : temperature=0, extended thinking OFF   (current evaluate.py)
  - thinking_medium  : temperature=1, thinking budget 4000    (temp MUST be 1 when thinking)
  - thinking_high    : temperature=1, thinking budget 12000

Usage:
  uv run python model_select.py            # default N=10, all configs
  uv run python model_select.py --n 6 --name "Whitney"
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import sys
import time

from dotenv import load_dotenv

import common
import config
from evaluate import SYSTEM_PROMPT, build_profile_text, parse_json

logger = common.logger

CONFIGS = [
    {"label": "no_thinking", "temperature": 0.0, "thinking_budget": None, "max_tokens": 1200},
    {"label": "thinking_medium", "temperature": 1.0, "thinking_budget": 4000, "max_tokens": 5500},
    {"label": "thinking_high", "temperature": 1.0, "thinking_budget": 12000, "max_tokens": 13500},
]

THROTTLE_S = 4.0  # short experiment; gentler than the 12s production interval


def pick_profile(name_sub: str | None) -> dict:
    profiles = common.load_json(config.PROFILES_PATH, [])
    by_id = {p["profile_id"]: p for p in profiles}
    if name_sub:
        for p in profiles:
            if name_sub.lower() in (p.get("name") or "").lower():
                return p
        raise SystemExit(f"No profile matching {name_sub!r}")
    # Default: a boundary case (first READ_MORE scored 7) so variance is visible.
    evaluated = common.load_json(config.EVALUATED_PATH, [])
    for r in evaluated:
        ev = r.get("evaluation") or {}
        if ev.get("verdict") == "READ_MORE" and str(ev.get("composite_score")) == "7":
            pid = r.get("profile_id")
            if pid in by_id:
                return by_id[pid]
    return profiles[0]


def extract_text(resp) -> str:
    for block in resp.content:
        if getattr(block, "type", None) == "text":
            return block.text
    # Fallback: first block with a .text attribute.
    for block in resp.content:
        if hasattr(block, "text"):
            return block.text
    raise ValueError("no text block in response")


def run_config(client, cfg: dict, profile_text: str, n: int) -> dict:
    import anthropic

    scores: list = []
    verdicts: list = []
    errors = 0
    for i in range(1, n + 1):
        kwargs = dict(
            model=config.ANTHROPIC_MODEL,
            max_tokens=cfg["max_tokens"],
            temperature=cfg["temperature"],
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": profile_text}],
        )
        if cfg["thinking_budget"]:
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": cfg["thinking_budget"]}
        try:
            resp = client.messages.create(**kwargs)
            data = parse_json(extract_text(resp))
            scores.append(data.get("composite_score"))
            verdicts.append(data.get("verdict"))
            logger.info("  [%s %d/%d] score=%s verdict=%s", cfg["label"], i, n,
                        data.get("composite_score"), data.get("verdict"))
        except anthropic.BadRequestError as exc:
            logger.error("  [%s %d/%d] BadRequest (config unsupported?): %s", cfg["label"], i, n, exc)
            return {"label": cfg["label"], "unsupported": True, "error": str(exc)}
        except Exception as exc:  # noqa: BLE001
            errors += 1
            logger.error("  [%s %d/%d] error: %s", cfg["label"], i, n, exc)
        time.sleep(THROTTLE_S)

    score_counts = collections.Counter(scores)
    verdict_counts = collections.Counter(verdicts)
    consistent = len(score_counts) == 1 and len(verdict_counts) == 1 and errors == 0
    return {
        "label": cfg["label"],
        "n": n,
        "errors": errors,
        "scores": dict(score_counts),
        "verdicts": dict(verdict_counts),
        "consistent": consistent,
    }


def main() -> int:
    common.setup_logging()
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--name", type=str, default=None, help="substring of therapist name to test")
    args = ap.parse_args()

    load_dotenv(config.ROOT / ".env")
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.error("ANTHROPIC_API_KEY not set.")
        return 1

    profile = pick_profile(args.name)
    profile_text = build_profile_text(profile)
    logger.info("Test profile: %s (id=%s), %d chars of prompt input",
                profile.get("name"), profile.get("profile_id"), len(profile_text))
    logger.info("Model: %s | N=%d per config", config.ANTHROPIC_MODEL, args.n)

    import anthropic
    client = anthropic.Anthropic(api_key=api_key)

    results = [run_config(client, cfg, profile_text, args.n) for cfg in CONFIGS]

    print("\n" + "=" * 68)
    print(f"CONSISTENCY EXPERIMENT  (profile: {profile.get('name')}, N={args.n})")
    print("=" * 68)
    for r in results:
        if r.get("unsupported"):
            print(f"\n[{r['label']}] UNSUPPORTED by this model/account: {r['error'][:120]}")
            continue
        flag = "CONSISTENT" if r["consistent"] else "VARIABLE"
        print(f"\n[{r['label']}]  -> {flag}  (errors={r['errors']})")
        print(f"    scores:   {r['scores']}")
        print(f"    verdicts: {r['verdicts']}")
    print("\n" + "=" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(main())
