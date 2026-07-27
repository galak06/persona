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
from collections import Counter
from datetime import UTC, datetime
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


def _extract_content_signals(idea: dict) -> dict:
    """
    Extract content signals that indicate WHY a user approves/skips.
    These matter more than category alone.
    """
    topic = (idea.get("topic", idea.get("Topic", "")) or "").lower()
    keyword = (idea.get("keyword", idea.get("Target_Keyword", "")) or "").lower()
    nalla = (idea.get("Nalla_Context", idea.get("nalla_context", "")) or "").lower()
    combined = f"{topic} {keyword} {nalla}"

    return {
        # Content format signals
        "has_comparison": any(
            w in combined for w in ["vs", "vs.", "comparison", "compare", "versus", "showdown"]
        ),
        "has_testing": any(
            w in combined for w in ["tested", "test", "30-day", "week", "trial", "experiment"]
        ),
        "has_product_review": any(
            w in combined for w in ["review", "best", "top", "rated", "picks"]
        ),
        "has_how_to": any(w in combined for w in ["how to", "guide", "step", "method", "tutorial"]),
        "has_data_angle": any(
            w in combined
            for w in ["data", "numbers", "cost", "price", "spreadsheet", "tracked", "measured"]
        ),
        "has_list_format": any(w in combined for w in ["best", "top", "ranked", "list"]),
        # Tone signals
        "tone_practical": any(
            w in combined for w in ["tested", "tried", "used", "bought", "switched", "survival"]
        ),
        "tone_academic": any(
            w in combined for w in ["protocol", "decoder", "analysis", "study", "research", "AAFCO"]
        ),
        "tone_urgent": any(
            w in combined for w in ["season", "spring", "summer", "now", "this week", "today"]
        ),
        "tone_fun": any(
            w in combined for w in ["survival", "showdown", "battle", "vs", "explosion", "crazy"]
        ),
        # Revenue signals
        "has_affiliate_potential": any(
            w in combined
            for w in [
                "product",
                "buy",
                "collar",
                "tracker",
                "food",
                "tool",
                "brush",
                "shampoo",
                "supplement",
                "harness",
                "bed",
                "camera",
                "treat",
            ]
        ),
        # Story signals
        "has_personal_story": any(
            w in combined
            for w in ["lost", "refused", "broke", "surprised", "discovered", "noticed"]
        ),
        "has_specific_nalla": "nalla" in combined and len(nalla) > 20,
    }


def record_decision(idea: dict, decision: str, notes: str = "") -> None:
    """
    Record user's decision on an idea with content signals.
    decision: "approved" | "skipped" | "edited"
    """
    signals = _extract_content_signals(idea)

    history = _load_history()
    history.append(
        {
            "timestamp": datetime.now(UTC).isoformat(),
            "topic": idea.get("topic", idea.get("Topic", "")),
            "category": idea.get("category", idea.get("Category", "")),
            "keyword": idea.get("keyword", idea.get("Target_Keyword", "")),
            "score": idea.get("score", 0),
            "decision": decision,
            "notes": notes,
            "nalla_context": idea.get("Nalla_Context", "")[:100],
            "seasonal": idea.get("seasonal", False),
            "evidence": idea.get("evidence", {}),
            "content_signals": signals,
        }
    )
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
                "bias": "preferred"
                if a / total > 0.7
                else "neutral"
                if a / total > 0.4
                else "avoided",
            }

    # Score threshold analysis
    approved_scores = [h["score"] for h in approved if h.get("score")]
    skipped_scores = [h["score"] for h in skipped if h.get("score")]

    avg_approved_score = sum(approved_scores) / len(approved_scores) if approved_scores else 0
    avg_skipped_score = sum(skipped_scores) / len(skipped_scores) if skipped_scores else 0

    # ── Content signal analysis (most important) ──
    # Track which signals correlate with approval vs skip
    all_signals = set()
    signal_approved = Counter()
    signal_skipped = Counter()

    for h in approved:
        signals = h.get("content_signals", {})
        for sig, val in signals.items():
            all_signals.add(sig)
            if val:
                signal_approved[sig] += 1

    for h in skipped:
        signals = h.get("content_signals", {})
        for sig, val in signals.items():
            all_signals.add(sig)
            if val:
                signal_skipped[sig] += 1

    # Signals that predict approval
    preferred_signals = []
    avoided_signals = []
    for sig in all_signals:
        a = signal_approved.get(sig, 0)
        s = signal_skipped.get(sig, 0)
        total = a + s
        if total > 0:
            rate = a / total
            if rate > 0.65 and a >= 1:
                preferred_signals.append(
                    {"signal": sig, "approval_rate": round(rate, 2), "count": total}
                )
            elif rate < 0.35 and s >= 1:
                avoided_signals.append(
                    {"signal": sig, "approval_rate": round(rate, 2), "count": total}
                )

    preferred_signals.sort(key=lambda x: x["approval_rate"], reverse=True)
    avoided_signals.sort(key=lambda x: x["approval_rate"])

    # Keyword patterns
    approved_words = Counter()
    skipped_words = Counter()
    for h in approved:
        words = h.get("topic", "").lower().split()
        approved_words.update(w for w in words if len(w) > 3)
    for h in skipped:
        words = h.get("topic", "").lower().split()
        skipped_words.update(w for w in words if len(w) > 3)

    preferred_words = []
    avoided_words = []
    for word in set(approved_words) | set(skipped_words):
        a_count = approved_words.get(word, 0)
        s_count = skipped_words.get(word, 0)
        if a_count > s_count and a_count >= 1:
            preferred_words.append(word)
        elif s_count > a_count and s_count >= 1:
            avoided_words.append(word)

    # Seasonal preference
    seasonal_approved = sum(1 for h in approved if h.get("seasonal"))
    seasonal_total = sum(1 for h in history if h.get("seasonal"))

    return {
        "status": "ready",
        "total_decisions": len(history),
        "approved": len(approved),
        "skipped": len(skipped),
        "edited": len(edited),
        "approval_rate": round(len(approved) / len(history), 2) if history else 0,
        "category_preference": category_preference,
        "content_signals": {
            "preferred": preferred_signals,
            "avoided": avoided_signals,
            "summary": {
                "user_prefers": [
                    s["signal"].replace("has_", "").replace("tone_", "") for s in preferred_signals
                ],
                "user_avoids": [
                    s["signal"].replace("has_", "").replace("tone_", "") for s in avoided_signals
                ],
            },
        },
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
            "seasonal_approval_rate": round(seasonal_approved / seasonal_total, 2)
            if seasonal_total
            else 0,
        },
    }


def get_scoring_adjustments() -> dict:
    """
    Returns concrete scoring adjustments based on learned preferences.
    Used by content-ideator to boost/penalize ideas.
    """
    prefs = analyze_preferences()
    if prefs["status"] != "ready":
        return {
            "adjustments": [],
            "status": "learning",
            "note": f"Need {prefs.get('min_needed', 3)} decisions, have {prefs.get('current', 0)}",
        }

    adjustments = []

    # ── Content signal adjustments (primary — what actually matters) ──
    content_signals = prefs.get("content_signals", {})

    for sig in content_signals.get("preferred", []):
        adjustments.append(
            {
                "type": "signal_boost",
                "signal": sig["signal"],
                "adjustment": +1,
                "reason": f"User prefers {sig['signal'].replace('has_', '').replace('tone_', '')} ({int(sig['approval_rate'] * 100)}% approval rate)",
            }
        )

    for sig in content_signals.get("avoided", []):
        adjustments.append(
            {
                "type": "signal_penalty",
                "signal": sig["signal"],
                "adjustment": -1,
                "reason": f"User avoids {sig['signal'].replace('has_', '').replace('tone_', '')} ({int(sig['approval_rate'] * 100)}% approval rate)",
            }
        )

    # ── Category adjustments (secondary — less weight than signals) ──
    for cat, pref in prefs.get("category_preference", {}).items():
        if pref["bias"] == "preferred" and pref["approved"] >= 2:
            adjustments.append(
                {
                    "type": "category_boost",
                    "category": cat,
                    "adjustment": +1,
                    "reason": f"User approves {cat} {int(pref['approval_rate'] * 100)}% of the time",
                }
            )
        elif pref["bias"] == "avoided" and pref["skipped"] >= 2:
            adjustments.append(
                {
                    "type": "category_penalty",
                    "category": cat,
                    "adjustment": -1,
                    "reason": f"User skips {cat} {int((1 - pref['approval_rate']) * 100)}% of the time",
                }
            )

    # ── Score threshold ──
    min_score = prefs.get("score_analysis", {}).get("min_viable_score", 5)
    if min_score > 5:
        adjustments.append(
            {
                "type": "score_threshold",
                "min_score": min_score,
                "reason": f"User typically approves ideas scoring {min_score}+",
            }
        )

    # ── Keyword patterns ──
    preferred = prefs.get("keyword_patterns", {}).get("preferred_words", [])
    if preferred:
        adjustments.append(
            {
                "type": "keyword_boost",
                "words": preferred,
                "adjustment": +1,
                "reason": f"User prefers topics with: {', '.join(preferred[:5])}",
            }
        )

    avoided = prefs.get("keyword_patterns", {}).get("avoided_words", [])
    if avoided:
        adjustments.append(
            {
                "type": "keyword_penalty",
                "words": avoided,
                "adjustment": -1,
                "reason": f"User tends to skip topics with: {', '.join(avoided[:5])}",
            }
        )

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

    # Extract signals for this idea
    idea_signals = _extract_content_signals(idea)

    for adj in adjustments:
        if adj["type"] == "signal_boost":
            if idea_signals.get(adj["signal"], False):
                score += adj["adjustment"]
                reasons.append(f"+{adj['adjustment']} {adj['reason']}")

        elif adj["type"] == "signal_penalty":
            if idea_signals.get(adj["signal"], False):
                score += adj["adjustment"]
                reasons.append(f"{adj['adjustment']} {adj['reason']}")

        elif adj["type"] == "category_boost" and adj["category"].lower() == category:
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
