"""Refresh keyword_research_cache.json with real data for current pending ideas.

Pulls:
- IG hashtag engagement (Graph API) — WORKING
- FB page topic performance (Graph API) — WORKING
- Google Trends US + CA (pytrends) — best-effort, frequently 429s
- Amazon SERP — SKIPPED (Google blocks unauthenticated scrape; needs Keepa)

Reads ideas from backups/ideas_2026-04-28.json and prints a per-idea summary
of real-world signal. Writes cache for downstream skill use.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from local_env import load_local_env

load_local_env()

from keyword_research import (
    get_facebook_topic_performance,
    get_google_trends_north_america,
    get_instagram_hashtag_data,
)

CATEGORY_TO_HASHTAG = {
    "Food & Diet": ["homemadedogfood", "dognutrition", "rawdogfood", "dogfoodreview"],
    "Lifestyle & Gear": ["gpsdogcollar", "dogrunning", "doggear", "canicross"],
    "Grooming": ["doggrooming", "dogshedding", "dogcoatcare"],
    "Training": ["dogtraining", "reactivedogs", "dogbehavior"],
}


def pick_hashtag(category: str, keyword: str) -> str:
    """Match the most-relevant hashtag for the keyword's category + content."""
    candidates = CATEGORY_TO_HASHTAG.get(category, ["doglife"])
    kw_low = keyword.lower()
    for h in candidates:
        if any(word in h for word in kw_low.split() if len(word) > 3):
            return h
    return candidates[0]


def keyword_to_topic_words(keyword: str) -> list[str]:
    """Extract meaningful words for FB topic-perf grep."""
    stop = {
        "for",
        "the",
        "a",
        "an",
        "of",
        "and",
        "to",
        "in",
        "on",
        "with",
        "vs",
        "best",
        "2026",
        "guide",
    }
    return [w for w in keyword.lower().split() if w not in stop and len(w) > 2]


def main() -> int:
    ideas_path = ROOT / "backups" / "ideas_2026-04-28.json"
    if not ideas_path.exists():
        print(f"ERROR: {ideas_path} not found")
        return 1

    ideas = json.loads(ideas_path.read_text())
    out = {"refreshed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "ideas": []}

    for idea in ideas:
        kw = idea["Target_Keyword"]
        cat = idea["Category"]
        idea_id = idea["id"]
        print(f"\n[{idea_id}] {idea['Topic'][:70]}")
        print(f"    keyword: {kw}")

        # IG hashtag — works
        h = pick_hashtag(cat, kw)
        ig = get_instagram_hashtag_data(h)
        print(
            f"    IG #{h}: avg_likes={ig.get('avg_likes', 'ERR')} signal={ig.get('engagement_signal', 'ERR')}"
        )

        # FB topic perf — works (sparse data)
        topic_words = keyword_to_topic_words(kw)
        fb = get_facebook_topic_performance(topic_words[:3])
        print(
            f"    FB topic ({topic_words[:3]}): matching_posts={fb.get('matching_posts', 0)} avg_eng={fb.get('avg_engagement', 0)}"
        )

        # Trends — read cache only (slow refresher populates it via cron)
        trends = get_google_trends_north_america(kw, cache_only=True)
        cache_state = trends["us"].get("trend") if trends["us"] else "no_data"
        if cache_state == "no_cached_data":
            print("    Trends NA: cache miss — run scripts/refresh_trends_only.py")
        else:
            print(
                f"    Trends NA: interest={trends['rollup_interest']} trend={trends['rollup_trend']}"
            )

        out["ideas"].append(
            {
                "id": idea_id,
                "topic": idea["Topic"],
                "keyword": kw,
                "category": cat,
                "ig_hashtag_used": h,
                "instagram": ig,
                "facebook_topic_perf": fb,
                "google_trends_na": trends,
            }
        )

        time.sleep(3)  # polite delay between idea iterations

    out_path = ROOT / ".claude" / "state" / "tier1_real_data_2026-04-28.json"
    out_path.write_text(json.dumps(out, indent=2))
    print(f"\nSaved → {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
