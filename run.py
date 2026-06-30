"""Orchestrator - run the full pipeline in sequence.

Steps: scrape -> extract -> enrich (sparse bios) -> enrich_llm (Haiku signals)
       -> score_heuristic (rank) -> build_review (app)

enrich_llm.py calls the Anthropic API (Haiku) and costs a small amount of
money (~$0.001/profile on survivors of the deterministic flags - see
enrich_llm.py docstring). Run `uv run python enrich_llm.py test` first if
you've changed the prompt, to sanity check before spending on the full set.

Stops and reports if any step fails. Each step checkpoints its own progress,
so re-running resumes rather than restarting.
"""

from __future__ import annotations

import subprocess
import sys

STEPS = [
    ("Scraping listing pages...", "scrape.py"),
    ("Extracting profiles...", "extract.py"),
    ("Enriching sparse profiles (website bios)...", "enrich.py"),
    ("Enriching couples-centrality + style via Haiku...", "enrich_llm.py", ["run"]),
    ("Scoring + ranking...", "score_heuristic.py"),
    ("Building review app...", "build_review.py"),
]


def main() -> int:
    for step in STEPS:
        label, script = step[0], step[1]
        extra_args = step[2] if len(step) > 2 else []
        print(f"\n{'=' * 60}\n{label}\n{'=' * 60}")
        result = subprocess.run([sys.executable, script, *extra_args])
        if result.returncode == 2:
            print(
                f"\nBLOCKED at {script}. Psychology Today's anti-bot tripped the "
                "circuit breaker. Progress is checkpointed - wait a while (or switch "
                "network), then re-run to resume. See data/scrape.log."
            )
            return 2
        if result.returncode != 0:
            print(f"\nFAILED at {script}. Check logs in data/scrape.log")
            return 1

    print("\nDone. Open data/review.html, or see the full ranking in data/results.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
