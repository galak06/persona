"""Orchestrator for the recipe ideator.

Flow:
    1. Load existing context (seeds, published, history) for dedup
    2. research_candidates() via Gemini + google_search
    3. Filter out duplicates against existing context
    4. For each survivor:
        a. approve_idea() — Telegram gate #1
        b. enrich_to_seed() — Gemini structured output
        c. validate_seed() — schema + dog-safety
        d. approve_seed() — Telegram gate #2
        e. append_seed() — atomic write to seeds.json
    5. record_run() — append history entry

Usage:
    python -m ideator                       # full live run
    python -m ideator --dry-run             # research only, no Telegram, no writes
    python -m ideator --auto-approve        # skip Telegram gates (DANGER)
    python -m ideator --max-candidates 4
    python -m ideator --candidate-timeout-h 6
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "lib"))

from local_env import load_local_env  # noqa: E402

from .approval import approve_idea, approve_seed
from .enricher import enrich_to_seed
from .research import Candidate, research_candidates
from .schema import validate_seed
from .state import (
    append_seed,
    is_duplicate_title,
    load_existing_context,
    record_run,
)

logger = logging.getLogger("ideator")


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )


def _summarize_candidates(candidates: list[Candidate]) -> str:
    lines = []
    for i, c in enumerate(candidates, 1):
        lines.append(
            f"  {i}. [{c.category}] {c.title}\n"
            f"     why_now: {c.why_now}\n"
            f"     evidence: {c.evidence[:120]}\n"
            f"     demand={c.search_demand_estimate} seasonal={c.seasonal_relevance}/10"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Research-driven recipe ideator")
    parser.add_argument("--dry-run", action="store_true", help="research only, no enrichment, no writes")
    parser.add_argument("--auto-approve", action="store_true", help="skip Telegram gates (DANGER)")
    parser.add_argument("--max-candidates", type=int, default=6, help="how many to research (default 6)")
    parser.add_argument("--max-approved", type=int, default=None, help="stop after N seeds added to queue (default: process all)")
    parser.add_argument("--candidate-timeout-h", type=int, default=6)
    parser.add_argument("--seed-timeout-h", type=int, default=6)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    _setup_logging(args.verbose)
    load_local_env()

    print("=== Recipe Ideator ===")
    ctx = load_existing_context()
    print(
        f"Existing context: {len(ctx.seed_titles)} queued + "
        f"{len(ctx.published_titles)} published + "
        f"{len(ctx.history_titles)} previously proposed"
    )

    print(f"\nResearching {args.max_candidates} candidates via Gemini + google_search...")
    candidates = research_candidates(list(ctx.all_titles), n=args.max_candidates)
    print(f"\nReceived {len(candidates)} candidates from Gemini:")
    print(_summarize_candidates(candidates))

    fresh = [c for c in candidates if not is_duplicate_title(c.title, ctx)]
    dropped = len(candidates) - len(fresh)
    if dropped:
        print(f"\nDropped {dropped} duplicate(s) against existing context.")
    if not fresh:
        print("\nNo unique candidates — nothing to do.")
        record_run(
            candidates=[c.to_dict() for c in candidates],
            approved_seed_ids=[],
            notes="all duplicates",
        )
        return 0

    print(f"\n{len(fresh)} unique candidate(s) to process.")

    if args.dry_run:
        print("\n[DRY RUN — stopping here, no enrichment / Telegram / writes]")
        return 0

    approved_ids: list[str] = []
    for cand in fresh:
        # Gate 1: idea approval
        if args.auto_approve:
            print(f"\n[--auto-approve] idea OK: {cand.title}")
            final_title = cand.title
        else:
            result = approve_idea(cand, timeout_hours=args.candidate_timeout_h)
            print(f"  → idea approval: {result.action}")
            if result.action == "skipped" or result.action == "timeout":
                continue
            if result.action == "pending":
                print("  → telegram unavailable — abort run, retry next time")
                break
            final_title = (
                result.edited_text if result.action == "edited" and result.edited_text else cand.title
            )

        cand_for_enrich = Candidate(
            title=final_title,
            category=cand.category,
            why_now=cand.why_now,
            evidence=cand.evidence,
            seasonal_relevance=cand.seasonal_relevance,
            search_demand_estimate=cand.search_demand_estimate,
        )

        # Enrich
        try:
            seed = enrich_to_seed(cand_for_enrich)
        except Exception as exc:
            logger.error("enrich failed for %r: %s", final_title, exc)
            continue

        # Validate
        errors = validate_seed(seed)
        if errors:
            print(f"  ❌ enriched seed failed validation: {errors}")
            continue

        # Gate 2: seed approval
        if args.auto_approve:
            print(f"  [--auto-approve] seed OK: {seed['id']}")
            decision = "approved"
        else:
            result2 = approve_seed(seed, timeout_hours=args.seed_timeout_h)
            decision = result2.action

        if decision != "approved":
            print(f"  → seed dropped ({decision})")
            continue

        # Persist
        append_seed(seed)
        approved_ids.append(seed["id"])
        print(f"  ✅ added to queue: {seed['id']}")

        if args.max_approved is not None and len(approved_ids) >= args.max_approved:
            print(f"\nReached max-approved={args.max_approved} — stopping early.")
            break

    record_run(
        candidates=[c.to_dict() for c in candidates],
        approved_seed_ids=approved_ids,
    )

    print(f"\n=== Run complete: {len(approved_ids)}/{len(fresh)} added to queue ===")
    if approved_ids:
        print(f"New seed ids: {approved_ids}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
