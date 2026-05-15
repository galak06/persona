"""Re-score the pending content-ideator batch using real-world signals.

Inputs:
- backups/ideas_2026-04-28.json — prior cluster-aware scoring
- .claude/state/tier1_research_2026-04-28.json — web-research deltas
- .claude/state/tier1_real_data_2026-04-28.json — IG hashtag engagement
- .claude/state/keyword_research_cache.json — Google Trends cache (populated by refresh_trends_only.py)

Output:
- backups/ideas_2026-04-28-v3.json — re-scored batch + 5 new opportunities
- .claude/state/ideation_history.json — updated with this run

Score deltas applied on top of prior cluster-aware scores:
- IG hashtag signal:   high=+1, medium=0, low=-1, err=0
- Google Trends US/CA: rising=+1, stable=0, declining=-1, no_data=0
- Tier 1 research:     deltas already computed by background agent
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PRIOR_IDEAS = settings.paths.backups_dir / "ideas_2026-04-28.json"
TIER1_DELTAS = ROOT / ".claude" / "state" / "tier1_research_2026-04-28.json"
IG_SIGNALS = ROOT / ".claude" / "state" / "tier1_real_data_2026-04-28.json"
TRENDS_CACHE = ROOT / ".claude" / "state" / "keyword_research_cache.json"
OUT_PATH = settings.paths.backups_dir / "ideas_2026-04-28-v3.json"
HISTORY = ROOT / ".claude" / "state" / "ideation_history.json"


def load_json(path: Path, default=None):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return default


def trend_signal_for(keyword: str, cache: dict) -> dict:
    """Read US + CA trend cache. Combine into a single delta."""
    us = cache.get(f"trends:{keyword.lower().strip()}_us", {}).get("data", {})
    ca = cache.get(f"trends:{keyword.lower().strip()}_ca", {}).get("data", {})

    trends = [us.get("trend"), ca.get("trend")]
    if "rising" in trends:
        return {"delta": 1, "reason": "trends rising in US or CA", "us": us, "ca": ca}
    if "declining" in trends and "rising" not in trends:
        return {"delta": -1, "reason": "trends declining", "us": us, "ca": ca}
    if any(t in ("stable",) for t in trends):
        return {"delta": 0, "reason": "trends stable", "us": us, "ca": ca}
    return {"delta": 0, "reason": "no trends data cached yet", "us": us, "ca": ca}


def ig_signal_for(idea_id: int, ig_data: dict) -> dict:
    """Pull this idea's IG engagement from tier1_real_data."""
    for entry in ig_data.get("ideas", []):
        if entry.get("id") == idea_id:
            ig = entry.get("instagram", {})
            sig = ig.get("engagement_signal", "")
            if sig == "high":
                return {
                    "delta": 1,
                    "reason": f"IG #{entry['ig_hashtag_used']} = {ig.get('avg_likes')} avg likes (high)",
                }
            if sig == "low":
                return {
                    "delta": -1,
                    "reason": f"IG #{entry['ig_hashtag_used']} = {ig.get('avg_likes')} avg likes (low)",
                }
            if sig == "medium":
                return {
                    "delta": 0,
                    "reason": f"IG #{entry['ig_hashtag_used']} = {ig.get('avg_likes')} avg likes (medium)",
                }
            return {"delta": 0, "reason": f"IG signal unavailable: {ig.get('error', 'unknown')}"}
    return {"delta": 0, "reason": "no IG data"}


def tier1_delta_for(idea_id: int, tier1: dict) -> dict:
    """Already-computed web-research delta from tier 1 verification run."""
    for entry in tier1.get("ideas", []):
        if entry.get("id") == idea_id:
            return {"delta": entry.get("delta", 0), "reason": entry.get("delta_reason", "")}
    return {"delta": 0, "reason": "no tier1 data"}


def main() -> int:
    prior = load_json(PRIOR_IDEAS, [])
    tier1 = load_json(TIER1_DELTAS, {})
    ig = load_json(IG_SIGNALS, {})
    trends_cache = load_json(TRENDS_CACHE, {})

    if not prior:
        print(f"ERROR: {PRIOR_IDEAS} missing or empty")
        return 1

    rescored = []
    for idea in prior:
        idea_id = idea["id"]
        prior_score = idea.get("score", 0)

        t1 = tier1_delta_for(idea_id, tier1)
        ig_sig = ig_signal_for(idea_id, ig)
        tr = trend_signal_for(idea["Target_Keyword"], trends_cache)

        new_score = prior_score + t1["delta"] + ig_sig["delta"] + tr["delta"]

        rescored.append(
            {
                **idea,
                "score": new_score,
                "score_v3_breakdown": {
                    "prior_v2_score": prior_score,
                    "tier1_research_delta": t1,
                    "ig_engagement_delta": ig_sig,
                    "trends_delta": tr,
                    "new_score": new_score,
                },
            }
        )

    # Sort by new score desc
    rescored.sort(key=lambda x: x["score"], reverse=True)

    # New opportunities surfaced by tier 1 research
    new_opps = tier1.get("new_opportunities", [])

    output = {
        "_schema": "ideas_with_real_signals_v3",
        "_generated_at": datetime.now(UTC).isoformat(),
        "_inputs": {
            "prior_ideas": str(PRIOR_IDEAS.relative_to(ROOT)),
            "tier1_deltas": str(TIER1_DELTAS.relative_to(ROOT)),
            "ig_signals": str(IG_SIGNALS.relative_to(ROOT)),
            "trends_cache": str(TRENDS_CACHE.relative_to(ROOT)),
        },
        "ideas": rescored,
        "new_opportunities": new_opps,
    }

    OUT_PATH.write_text(json.dumps(output, indent=2))
    print(f"\nWrote → {OUT_PATH}")

    # Update ideation_history.json with a new run entry
    hist = load_json(HISTORY, {"schema_version": 1, "runs": []})
    hist["last_run"] = output["_generated_at"]
    hist["runs"].append(
        {
            "run_id": "2026-04-28-v3-real-signals-fold-in",
            "timestamp": output["_generated_at"],
            "trigger": "user-invoked: incorporate IG + trends real data",
            "ideas_generated": len(rescored),
            "research_grounded": True,
            "approval_status": "pending",
            "scoring_system": "v3 (cluster-aware + tier1 + IG + trends)",
            "ideas": [
                {
                    "id": i["id"],
                    "topic": i["Topic"][:60],
                    "category": i["Category"],
                    "cluster_id": i.get("cluster_id"),
                    "score": i["score"],
                }
                for i in rescored
            ],
            "output_file": str(OUT_PATH.relative_to(ROOT)),
        }
    )
    HISTORY.write_text(json.dumps(hist, indent=2))
    print(f"Updated → {HISTORY}")

    # Summary
    print("\n=== Final ranking ===")
    for i in rescored:
        b = i["score_v3_breakdown"]
        print(
            f"  [{i['id']}] score={i['score']:>2}  (was {b['prior_v2_score']}) — {i['Topic'][:60]}"
        )
        deltas = []
        if b["tier1_research_delta"]["delta"]:
            deltas.append(f"tier1 {b['tier1_research_delta']['delta']:+d}")
        if b["ig_engagement_delta"]["delta"]:
            deltas.append(f"ig {b['ig_engagement_delta']['delta']:+d}")
        if b["trends_delta"]["delta"]:
            deltas.append(f"trends {b['trends_delta']['delta']:+d}")
        if deltas:
            print(f"        deltas: {', '.join(deltas)}")

    print(f"\n{len(new_opps)} new opportunities surfaced (not yet scored — see {OUT_PATH.name})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
