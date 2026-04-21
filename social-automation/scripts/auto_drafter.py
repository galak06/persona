"""Fill in `draft_comment` for queued items the template generator can handle.

Closes the gap between scanners (which queue pending items) and comment-poster
(which only acts on items with `draft_comment` populated). Runs after the
evening IG scan and before the 22:00 poster so drafted items reach Telegram
for approval with enough lead time.

For each queue item with status=pending AND no draft_comment:
  - Call comment_generator.generate_comment()
  - If a template matches: voice-validate, set draft_comment
  - If it needs LLM generation: leave it alone (handled by a manual
    `/comment-composer` session — LLM drafting still requires human eye)

Nothing else is touched — status stays "pending", so comment-poster picks the
drafted items up and runs them through the normal Telegram-approval flow.

Usage:
    python scripts/auto_drafter.py              # fill in what we can
    python scripts/auto_drafter.py --dry-run    # show what would draft, save nothing
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "lib"))

from comment_generator import generate_comment, validate_voice
from logger import enable_unbuffered, log_step
from notifier import skill_error, skill_finished, skill_started

enable_unbuffered()

QUEUE_FILE = PROJECT_ROOT / ".claude/state/comment_queue.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="Auto-draft pending queue items via templates")
    parser.add_argument("--dry-run", action="store_true", help="print what would be drafted, save nothing")
    args = parser.parse_args()

    skill_started("auto-drafter", "drafting template-matchable queue items")

    if not QUEUE_FILE.exists():
        skill_error("auto-drafter", "comment_queue.json missing")
        return
    queue = json.loads(QUEUE_FILE.read_text())
    needs_draft = [
        i
        for i in queue
        if i.get("status") == "pending" and not i.get("draft_comment")
    ]
    if not needs_draft:
        print("no pending items without drafts — nothing to do", flush=True)
        skill_finished("auto-drafter", "queue already drafted")
        return

    print(f"{len(needs_draft)} pending items without drafts", flush=True)
    drafted = skipped_llm = skipped_voice = 0
    for item in needs_draft:
        group = item.get("group_name") or item.get("hashtag") or "?"
        category = item.get("category", "general")
        post_text = item.get("post_text") or item.get("post") or ""
        log_step(f"  → [{item.get('platform', '?')}] {group[:40]}  cat={category}")

        result = generate_comment(
            post_text=post_text,
            category=category,
            group_name=item.get("group_name") or item.get("hashtag", ""),
        )
        if result.get("method") == "needs_generation":
            print("    needs LLM drafting — skipping (manual /comment-composer)", flush=True)
            skipped_llm += 1
            continue

        draft = result.get("comment", "")
        if not draft:
            print("    empty template result — skipping", flush=True)
            skipped_llm += 1
            continue

        valid, violations = validate_voice(draft)
        if not valid:
            print(f"    voice fail: {violations} — skipping", flush=True)
            skipped_voice += 1
            continue

        if args.dry_run:
            print(f"    DRY-RUN: would set draft_comment ({len(draft)} chars)", flush=True)
            drafted += 1
            continue

        item["draft_comment"] = draft
        drafted += 1
        print(f"    ✅ drafted ({len(draft)} chars)", flush=True)

    if not args.dry_run and drafted:
        QUEUE_FILE.write_text(json.dumps(queue, indent=2))
        print(f"\nqueue updated: {drafted} drafted", flush=True)

    summary = f"drafted={drafted} needs_llm={skipped_llm} voice_fail={skipped_voice}"
    print(f"\n=== Done === {summary}", flush=True)
    skill_finished("auto-drafter", summary)


if __name__ == "__main__":
    main()
