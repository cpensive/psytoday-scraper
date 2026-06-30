"""Step 3b - Build a stratified calibration sample for manual review.

Replaces the old known-answer validator. After the full evaluation runs, this
pulls CALIBRATION_SIZE profiles spanning all verdicts and the score range so
Charles can work through them and confirm the evaluator is well-calibrated
before trusting the rest of results.csv.

Outputs: data/calibration.csv, data/calibration.md
"""

from __future__ import annotations

import csv
import sys

import common
import config
from evaluate import CSV_COLUMNS, VERDICT_RANK, row_from

logger = common.logger

BASE_TARGETS = {"CALL": 7, "READ_MORE": 7, "SKIP": 6}


def _score(record: dict) -> float:
    try:
        return float((record.get("evaluation") or {}).get("composite_score") or 0)
    except (TypeError, ValueError):
        return 0.0


def allocate(counts: dict[str, int], total: int) -> dict[str, int]:
    order = ["CALL", "READ_MORE", "SKIP"]
    alloc = {k: min(BASE_TARGETS[k], counts.get(k, 0)) for k in order}
    remaining = total - sum(alloc.values())
    while remaining > 0 and any(counts.get(k, 0) > alloc[k] for k in order):
        for k in order:
            if remaining <= 0:
                break
            if counts.get(k, 0) > alloc[k]:
                alloc[k] += 1
                remaining -= 1
    return alloc


def spread(items: list[dict], n: int) -> list[dict]:
    """Pick n items evenly across a score-sorted list (captures the range)."""
    if n >= len(items):
        return items
    if n == 1:
        return [items[0]]
    picked: list[dict] = []
    for i in range(n):
        idx = round(i * (len(items) - 1) / (n - 1))
        if items[idx] not in picked:
            picked.append(items[idx])
    i = 0
    while len(picked) < n and i < len(items):
        if items[i] not in picked:
            picked.append(items[i])
        i += 1
    return picked


def select(records: list[dict]) -> list[dict]:
    valid = [r for r in records if (r.get("evaluation") or {}).get("verdict") in VERDICT_RANK]
    if len(valid) <= config.CALIBRATION_SIZE:
        return sorted(valid, key=lambda r: (VERDICT_RANK[r["evaluation"]["verdict"]], -_score(r)))

    buckets: dict[str, list[dict]] = {"CALL": [], "READ_MORE": [], "SKIP": []}
    for r in valid:
        buckets[r["evaluation"]["verdict"]].append(r)
    for b in buckets.values():
        b.sort(key=_score, reverse=True)

    counts = {k: len(v) for k, v in buckets.items()}
    alloc = allocate(counts, config.CALIBRATION_SIZE)
    logger.info("Calibration allocation: %s (from available %s)", alloc, counts)

    sample: list[dict] = []
    for verdict, n in alloc.items():
        sample.extend(spread(buckets[verdict], n))
    return sorted(sample, key=lambda r: (VERDICT_RANK[r["evaluation"]["verdict"]], -_score(r)))


def write_md(sample: list[dict]) -> None:
    lines = [
        "# Evaluator Calibration Set",
        "",
        f"A stratified sample of {len(sample)} profiles spanning all verdicts and the "
        "score range. Work through each one and check whether the verdict and "
        "`composite_score` match your own judgment. If the model is mis-scoring, "
        "note which tier is off so the system prompt can be tuned before trusting "
        "the full `results.csv`.",
        "",
    ]
    for i, r in enumerate(sample, 1):
        ev = r["evaluation"]
        t1, t2 = ev.get("tier_1", {}), ev.get("tier_2", {})
        t3, t4, t5 = ev.get("tier_3", {}), ev.get("tier_4", {}), ev.get("tier_5", {})
        bio = (r.get("bio_narrative") or "").strip().replace("\n", " ")
        if len(bio) > 700:
            bio = bio[:700] + "..."
        fmt = ", ".join(f for f, on in (("in-person", r.get("in_person")), ("online", r.get("online"))) if on)
        lines += [
            f"## {i}. {ev.get('name') or r.get('name')} - {ev.get('verdict')} "
            f"(score {ev.get('composite_score')})",
            "",
            f"- **PT profile:** {r.get('url')}",
            f"- **Format:** {fmt or 'unknown'} | **Couples fee:** {r.get('fee_couples') or 'n/a'} "
            f"| **Languages:** {', '.join(r.get('languages') or []) or 'n/a'}",
            f"- **One-line:** {ev.get('one_line', '')}",
            f"- **Tier 1 (certification):** {t1.get('couples_certification')} "
            f"- {t1.get('modality')} - {t1.get('certification_detail')}",
            f"- **Tier 2 (composition):** couples ~{t2.get('couples_focus_pct')}%, "
            f"describes process={t2.get('describes_couples_process')}, "
            f"issues={', '.join(t2.get('specific_couples_issues_named') or []) or 'none'}",
            f"- **Tier 3 (style):** {t3.get('style')} "
            f"(challenge={t3.get('evidence_of_challenge')}, framework={t3.get('framework_plan_visible')}) "
            f"- {t3.get('style_note')}",
            f"- **Tier 4 (cultural):** asian={t4.get('asian_background')}, "
            f"intercultural={t4.get('intercultural_experience')} - {t4.get('cultural_note')}",
            f"- **Tier 5 (credentials):** {t5.get('license')} "
            f"(independent={t5.get('independent_license')}), school={t5.get('school')} "
            f"(top={t5.get('top_school')}), years={t5.get('years_est')}",
            f"- **Red flags:** {', '.join(ev.get('red_flags') or []) or 'none'}",
            f"- **Ask on call:** {ev.get('ask_on_call', '')}",
            "",
            f"> {bio}",
            "",
            "_Your call:_ agree / disagree (why?): ____",
            "",
            "---",
            "",
        ]
    config.CALIBRATION_MD_PATH.write_text("\n".join(lines), encoding="utf-8")
    logger.info("Wrote calibration review doc -> %s", config.CALIBRATION_MD_PATH)


def write_csv(sample: list[dict]) -> None:
    with config.CALIBRATION_CSV_PATH.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for rec in sample:
            writer.writerow(row_from(rec))
    logger.info("Wrote calibration CSV -> %s", config.CALIBRATION_CSV_PATH)


def calibrate() -> int:
    common.setup_logging()
    records = common.load_json(config.EVALUATED_PATH, [])
    if not records:
        logger.error("No evaluations at %s. Run evaluate.py first.", config.EVALUATED_PATH)
        return 1
    sample = select(records)
    if not sample:
        logger.error("No valid evaluations to sample (all errored?).")
        return 1
    write_csv(sample)
    write_md(sample)
    logger.info("Calibration set ready: %d profiles. Review %s", len(sample), config.CALIBRATION_MD_PATH)
    return 0


if __name__ == "__main__":
    sys.exit(calibrate())
