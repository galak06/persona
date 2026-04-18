"""
Idea Learning Module — tracks user approval patterns to improve future scoring.

Learns from:
- Which ideas the user approves vs skips
- Which categories get approved more often
- Which keywords/angles the user prefers
- Which scores correlate with approval

Feeds learned preferences back into content-ideator scoring.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
HISTORY_FILE = PROJECT_ROOT / ".claude" / "state" / "idea_approval_history.json"


def _load_history() -> list[dict]:
    if HISTORY_FILE.exists():
        try:
            return json.loads(HISTORY_FILE.read_text())
        except Exception:
            pass
    return []


def _save_history(history: list[dict]) -> None:
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.write_text(json.dumps(history, indent=2))


def record_decision(idea: dict, decision: str, notes: str = "") -> None:
    """
    Record user's decision on an idea.
    decision: "approved" | "skipped" | "edited"
    """
    history = _load_history()
    history.append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "topic": idea.get("topic", idea.get("Topic", "")),
        "category": idea.get("category", idea.get("Category", "")),
        "keyword": idea.get("keyword", idea.get("Target_Keyword", "")),
        "score": idea.get("score", 0),
        "decision": decision,
        "notes": notes,
        "nalla_context": idea.get("Nalla_Context", "")[:100],
        "seasonal": idea.get("seasonal", False),
        "evidence": idea.get("evidence", {}),
    })
    _save_history(history)


def analyze_preferences() -> dict:
    """
    Analyze approval history to extract user preferences.
    Returns scoring adjustments for future ideation.
    """
    history = _load_history()
    if len(history) < 3:
        return {"status": "insufficient_data", "min_needed": 3, "current": len(history)}

    approved = [h for h in history if h["decision"] == "approved"]
    skipped = [h for h in history if h["decision"] == "skipped"]
    edited = [h for h in history if h["decision"] == "edited"]

    # Category preferences
    approved_categories = Counter(h["category"] for h in approved)
    skipped_categories = Counter(h["category"] for h in skipped)
    all_categories = set(approved_categories) | set(skipped_categories)

    category_preference = {}
    for cat in all_categories:
        a = approved_categories.get(cat, 0)
        s = skipped_categories.get(cat, 0)
        total = a + s
        if total > 0:
            category_preference[cat] = {
                "approval_rate": round(a / total, 2),
                "approved": a,
                "skipped": s,
                "bias": "preferred" if a / total > 0.7 else "neutral" if a / total > 0.4 else "avoided",
            }

    # Score threshold analysis
    approved_scores = [h["score"] for h in approved if h.get("score")]
    skipped_scores = [h["score"] for h in skipped if h.get("score")]

    avg_approved_score = sum(approved_scores) / len(approved_scores) if approved_scores else 0
    avg_skipped_score = sum(skipped_scores) / len(skipped_scores) if skipped_scores else 0

    # Keyword patterns — what words appear in approved vs skipped topics
    approved_words = Counter()
    skipped_words = Counter()
    for h in approved:
        words = h.get("topic", "").lower().split()
        approved_words.update(w for w in words if len(w) > 3)
    for h in skipped:
        words = h.get("topic", "").lower().split()
        skipped_words.update(w for w in words if len(w) > 3)

    # Words that appear more in approved than skipped
    preferred_words = []
    avoided_words = []
    for word in set(approved_words) | set(skipped_words):
        a_count = approved_words.get(word, 0)
        s_count = skipped_words.get(word, 0)
        if a_count > s_count and a_count >= 2:
            preferred_words.append(word)
        elif s_count > a_count and s_count >= 2:
            avoided_words.append(word)

    # Seasonal preference
    seasonal_approved = sum(1 for h in approved if h.get("seasonal"))
    seasonal_total = sum(1 for h in history if h.get("seasonal"))

    # Nalla context — does user prefer ideas with strong Nalla angles?
    has_nalla = [h for h in approved if "nalla" in h.get("nalla_context", "").lower()]

    return {
        "status": "ready",
        "total_decisions": len(history),
        "approved": len(approved),
        "skipped": len(skipped),
        "edited": len(edited),
        "approval_rate": round(len(approved) / len(history), 2) if history else 0,
        "category_preference": category_preference,
        "score_analysis": {
            "avg_approved_score": round(avg_approved_score, 1),
            "avg_skipped_score": round(avg_skipped_score, 1),
            "min_viable_score": max(1, round(avg_approved_score - 1)),
        },
        "keyword_patterns": {
            "preferred_words": preferred_words[:10],
            "avoided_words": avoided_words[:10],
        },
        "seasonal_preference": {
            "seasonal_approval_rate": round(seasonal_approved / seasonal_total, 2) if seasonal_total else 0,
        },
        "nalla_preference": {
            "nalla_in_approved_pct": round(len(has_nalla) / len(approved), 2) if approved else 0,
        },
    }


def get_scoring_adjustments() -> dict:
    """
    Returns concrete scoring adjustments based on learned preferences.
    Used by content-ideator to boost/penalize ideas.
    """
    prefs = analyze_preferences()
    if prefs["status"] != "ready":
        return {"adjustments": [], "status": "learning", "note": f"Need {prefs.get('min_needed', 3)} decisions, have {prefs.get('current', 0)}"}

    adjustments = []

    # Category boosts/penalties
    for cat, pref in prefs.get("category_preference", {}).items():
        if pref["bias"] == "preferred":
            adjustments.append({
                "type": "category_boost",
                "category": cat,
                "adjustment": +1,
                "reason": f"User approves {cat} {int(pref['approval_rate']*100)}% of the time",
            })
        elif pref["bias"] == "avoided":
            adjustments.append({
                "type": "category_penalty",
                "category": cat,
                "adjustment": -1,
                "reason": f"User skips {cat} {int((1-pref['approval_rate'])*100)}% of the time",
            })

    # Score threshold
    min_score = prefs.get("score_analysis", {}).get("min_viable_score", 5)
    if min_score > 5:
        adjustments.append({
            "type": "score_threshold",
            "min_score": min_score,
            "reason": f"User typically approves ideas scoring {min_score}+ (avg approved: {prefs['score_analysis']['avg_approved_score']})",
        })

    # Preferred words boost
    preferred = prefs.get("keyword_patterns", {}).get("preferred_words", [])
    if preferred:
        adjustments.append({
            "type": "keyword_boost",
            "words": preferred,
            "adjustment": +1,
            "reason": f"User prefers topics containing: {', '.join(preferred[:5])}",
        })

    # Avoided words penalty
    avoided = prefs.get("keyword_patterns", {}).get("avoided_words", [])
    if avoided:
        adjustments.append({
            "type": "keyword_penalty",
            "words": avoided,
            "adjustment": -1,
            "reason": f"User tends to skip topics containing: {', '.join(avoided[:5])}",
        })

    return {
        "adjustments": adjustments,
        "status": "active",
        "based_on": f"{prefs['total_decisions']} decisions ({prefs['approved']} approved, {prefs['skipped']} skipped)",
        "preferences_summary": prefs,
    }


def apply_adjustments(idea_score: int, idea: dict) -> tuple[int, list[str]]:
    """
    Apply learned adjustments to an idea's score.
    Returns (adjusted_score, list of reasons for adjustments).
    """
    adj_data = get_scoring_adjustments()
    if adj_data["status"] != "active":
        return idea_score, ["No learned preferences yet — using base score"]

    adjustments = adj_data["adjustments"]
    reasons = []
    score = idea_score

    category = idea.get("category", idea.get("Category", "")).lower()
    topic = idea.get("topic", idea.get("Topic", "")).lower()

    for adj in adjustments:
        if adj["type"] == "category_boost" and adj["category"].lower() == category:
            score += adj["adjustment"]
            reasons.append(f"+{adj['adjustment']} {adj['reason']}")

        elif adj["type"] == "category_penalty" and adj["category"].lower() == category:
            score += adj["adjustment"]
            reasons.append(f"{adj['adjustment']} {adj['reason']}")

        elif adj["type"] == "keyword_boost":
            if any(w in topic for w in adj["words"]):
                score += adj["adjustment"]
                matched = [w for w in adj["words"] if w in topic]
                reasons.append(f"+{adj['adjustment']} Preferred keywords: {matched[:3]}")

        elif adj["type"] == "keyword_penalty":
            if any(w in topic for w in adj["words"]):
                score += adj["adjustment"]
                matched = [w for w in adj["words"] if w in topic]
                reasons.append(f"{adj['adjustment']} Avoided keywords: {matched[:3]}")

        elif adj["type"] == "score_threshold":
            if score < adj["min_score"]:
                reasons.append(f"Below learned threshold ({adj['min_score']})")

    return max(0, min(10, score)), reasons


if __name__ == "__main__":
    prefs = analyze_preferences()
    print(json.dumps(prefs, indent=2))
