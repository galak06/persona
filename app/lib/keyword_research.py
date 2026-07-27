"""
Keyword research module for DogFoodAndFun content scoring.
Pulls real data from multiple sources to validate content demand
for USA/Canada audience.

Sources:
- Google Trends (pytrends) — search interest over time by country
- Instagram Graph API — hashtag engagement data
- Facebook Graph API — page post performance by topic
- Amazon Product API — trending products + search volume proxy
- Web search — competitor analysis, People Also Ask extraction
"""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CACHE_FILE = PROJECT_ROOT / ".claude" / "state" / "keyword_research_cache.json"


def _load_cache() -> dict:
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text())
        except Exception:
            pass
    return {}


def _save_cache(cache: dict) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, indent=2))


def _cache_key(source: str, keyword: str) -> str:
    return f"{source}:{keyword.lower().strip()}"


def _is_cache_fresh(entry: dict, max_age_hours: int = 72) -> bool:
    cached_at = entry.get("cached_at", "")
    if not cached_at:
        return False
    try:
        age = (datetime.now(UTC) - datetime.fromisoformat(cached_at)).total_seconds()
        return age < max_age_hours * 3600
    except Exception:
        return False


# ── Google Trends ─────────────────────────────────────────────────────────


def get_google_trends(keyword: str, geo: str = "US", cache_only: bool = False) -> dict:
    """
    Get Google Trends interest for a keyword in a specific country.
    Returns: {"interest": 0-100, "trend": "rising"|"stable"|"declining", "related_queries": [...]}

    cache_only=True: never call Google, just read cache. Returns {"trend":"no_cached_data"}
    if not cached. Use this in fast-path skills; rely on a separate slow refresher
    (scripts/refresh_trends_only.py) to populate the cache via cron.
    """
    cache = _load_cache()
    key = _cache_key("trends", f"{keyword}_{geo}")
    if key in cache and _is_cache_fresh(cache[key]):
        return cache[key]["data"]
    if cache_only:
        return {
            "interest": 0,
            "trend": "no_cached_data",
            "geo": geo,
            "_note": "Run scripts/refresh_trends_only.py to populate.",
        }

    try:
        from pytrends.request import TrendReq

        # pytrends + urllib3 2.x: passing `retries` to TrendReq blows up because
        # pytrends still uses the removed `method_whitelist` kwarg internally.
        # Handle retries manually with a 429-aware backoff loop instead.
        pytrends = TrendReq(
            hl="en-US",
            tz=360,
            timeout=(10, 25),
            requests_args={
                "headers": {
                    "User-Agent": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
                    )
                }
            },
        )

        last_error = None
        interest = None
        for attempt in range(4):
            try:
                pytrends.build_payload([keyword], timeframe="today 3-m", geo=geo)
                time.sleep(3 + attempt * 2)
                interest = pytrends.interest_over_time()
                break
            except Exception as e:
                last_error = e
                msg = str(e)
                if "429" in msg or "TooManyRequests" in msg:
                    time.sleep(10 + attempt * 10)
                    continue
                raise
        if interest is None:
            raise last_error or RuntimeError("pytrends returned no data after retries")
        if interest.empty:
            return {"interest": 0, "trend": "unknown", "related_queries": []}

        values = interest[keyword].tolist()
        avg_interest = sum(values) / len(values) if values else 0
        recent = values[-4:] if len(values) >= 4 else values
        earlier = values[:4] if len(values) >= 4 else values

        avg_recent = sum(recent) / len(recent) if recent else 0
        avg_earlier = sum(earlier) / len(earlier) if earlier else 0

        if avg_recent > avg_earlier * 1.15:
            trend = "rising"
        elif avg_recent < avg_earlier * 0.85:
            trend = "declining"
        else:
            trend = "stable"

        # Related queries
        related = pytrends.related_queries()
        related_list = []
        if keyword in related and related[keyword].get("top") is not None:
            related_list = related[keyword]["top"]["query"].tolist()[:5]

        result = {
            "interest": round(avg_interest),
            "trend": trend,
            "related_queries": related_list,
            "geo": geo,
        }

        cache[key] = {"data": result, "cached_at": datetime.now(UTC).isoformat()}
        _save_cache(cache)
        return result

    except ImportError:
        return {"interest": 0, "trend": "unavailable", "error": "pytrends not installed"}
    except Exception as e:
        return {"interest": 0, "trend": "error", "error": str(e)[:100]}


def get_google_trends_north_america(keyword: str, cache_only: bool = False) -> dict:
    """
    Pull Google Trends for both US and CA — site's target market.
    Combines into a single rollup with per-geo breakdown.
    Best-effort: pytrends frequently 429s on unauthenticated calls.

    cache_only=True: read both geos from cache, never call Google.
    """
    us = get_google_trends(keyword, geo="US", cache_only=cache_only)
    if not cache_only:
        time.sleep(60)  # diagnostic-confirmed: ~3-5 calls/min triggers Google's anti-abuse layer
    ca = get_google_trends(keyword, geo="CA", cache_only=cache_only)

    interests = [
        v for v in (us.get("interest", 0), ca.get("interest", 0)) if isinstance(v, (int, float))
    ]
    avg = round(sum(interests) / len(interests)) if interests else 0

    trends = [us.get("trend"), ca.get("trend")]
    if "rising" in trends:
        rollup = "rising"
    elif "stable" in trends:
        rollup = "stable"
    elif "declining" in trends:
        rollup = "declining"
    else:
        rollup = "unavailable"

    related = list({*us.get("related_queries", []), *ca.get("related_queries", [])})

    return {
        "rollup_interest": avg,
        "rollup_trend": rollup,
        "us": us,
        "ca": ca,
        "related_queries_combined": related[:10],
    }


# ── Instagram Hashtag Research ────────────────────────────────────────────


def get_instagram_hashtag_data(hashtag: str) -> dict:
    """
    Get Instagram hashtag engagement data using Graph API.
    Returns: {"post_count": int, "top_posts_avg_likes": int, "engagement_signal": str}
    """
    cache = _load_cache()
    key = _cache_key("ig_hashtag", hashtag)
    if key in cache and _is_cache_fresh(cache[key]):
        return cache[key]["data"]

    ig_account_id = os.environ.get("IG_ACCOUNT_ID", "")
    fb_token = os.environ.get("FB_PAGE_TOKEN", "")

    if not ig_account_id or not fb_token:
        return {"error": "IG_ACCOUNT_ID or FB_PAGE_TOKEN not set"}

    try:
        # Search for hashtag ID
        resp = requests.get(
            "https://graph.facebook.com/v23.0/ig_hashtag_search",
            params={"user_id": ig_account_id, "q": hashtag, "access_token": fb_token},
            timeout=30,
        )
        if not resp.ok:
            return {"error": f"Hashtag search failed: {resp.status_code}"}

        data = resp.json().get("data", [])
        if not data:
            return {"error": "Hashtag not found"}

        hashtag_id = data[0]["id"]

        # Get top media for the hashtag — `limit` is required, otherwise Graph returns
        # error code 1 ("reduce the amount of data you're asking for")
        media_resp = requests.get(
            f"https://graph.facebook.com/v23.0/{hashtag_id}/top_media",
            params={
                "user_id": ig_account_id,
                "fields": "like_count,comments_count",
                "limit": 25,
                "access_token": fb_token,
            },
            timeout=30,
        )

        if media_resp.ok:
            posts = media_resp.json().get("data", [])
            likes = [p.get("like_count", 0) for p in posts if "like_count" in p]
            comments = [p.get("comments_count", 0) for p in posts if "comments_count" in p]

            avg_likes = sum(likes) / len(likes) if likes else 0
            avg_comments = sum(comments) / len(comments) if comments else 0

            if avg_likes > 500:
                signal = "high"
            elif avg_likes > 100:
                signal = "medium"
            else:
                signal = "low"

            result = {
                "top_posts_count": len(posts),
                "avg_likes": round(avg_likes),
                "avg_comments": round(avg_comments),
                "engagement_signal": signal,
            }
        else:
            result = {"error": f"Media fetch failed: {media_resp.status_code}"}

        cache[key] = {"data": result, "cached_at": datetime.now(UTC).isoformat()}
        _save_cache(cache)
        return result

    except Exception as e:
        return {"error": str(e)[:100]}


# ── Facebook Page Insights ────────────────────────────────────────────────


def get_facebook_topic_performance(topic_keywords: list[str]) -> dict:
    """
    Check how posts with these keywords performed on your FB page.
    Returns: {"avg_reach": int, "avg_engagement": int, "best_post": str}
    """
    fb_page_id = os.environ.get("FB_PAGE_ID", "")
    fb_token = os.environ.get("FB_PAGE_TOKEN", "")

    if not fb_page_id or not fb_token:
        return {"error": "FB_PAGE_ID or FB_PAGE_TOKEN not set"}

    try:
        resp = requests.get(
            f"https://graph.facebook.com/v23.0/{fb_page_id}/posts",
            params={
                "fields": "message,shares,reactions.summary(true),comments.summary(true),created_time",
                "limit": 25,
                "access_token": fb_token,
            },
            timeout=30,
        )
        if not resp.ok:
            return {"error": f"FB API failed: {resp.status_code}"}

        posts = resp.json().get("data", [])
        matching = []
        for post in posts:
            msg = (post.get("message") or "").lower()
            if any(kw.lower() in msg for kw in topic_keywords):
                reactions = post.get("reactions", {}).get("summary", {}).get("total_count", 0)
                comments = post.get("comments", {}).get("summary", {}).get("total_count", 0)
                shares = post.get("shares", {}).get("count", 0)
                matching.append(
                    {
                        "message": msg[:80],
                        "reactions": reactions,
                        "comments": comments,
                        "shares": shares,
                        "engagement": reactions + comments + shares,
                    }
                )

        if not matching:
            return {"matching_posts": 0, "note": "No posts match these keywords yet"}

        avg_engagement = sum(p["engagement"] for p in matching) / len(matching)
        best = max(matching, key=lambda p: p["engagement"])

        return {
            "matching_posts": len(matching),
            "avg_engagement": round(avg_engagement),
            "best_post": best["message"],
            "best_engagement": best["engagement"],
        }

    except Exception as e:
        return {"error": str(e)[:100]}


# ── Amazon Trending Products ─────────────────────────────────────────────


def get_amazon_product_demand(keyword: str) -> dict:
    """
    Check Amazon for product demand signal using search results count.
    Returns: {"result_count": int, "top_products": [...], "demand_signal": str}

    DEPRECATED: Google blocks unauthenticated SERP scraping — this returns
    "low" / 1 mention regardless of true demand. Replace with Keepa API or
    PA-API for real Amazon demand signal. Flagged unreliable in output.
    """
    cache = _load_cache()
    key = _cache_key("amazon", keyword)
    if key in cache and _is_cache_fresh(cache[key]):
        return cache[key]["data"]

    # We use web search as a proxy since Amazon API requires approval
    try:
        resp = requests.get(
            "https://www.google.com/search",
            params={"q": f"amazon.com {keyword} dog", "num": 5},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=30,
        )
        # Count Amazon results as demand proxy
        amazon_mentions = resp.text.lower().count("amazon.com")

        if amazon_mentions > 10:
            signal = "high"
        elif amazon_mentions > 5:
            signal = "medium"
        else:
            signal = "low"

        result = {
            "amazon_mentions_in_serp": amazon_mentions,
            "demand_signal": signal,
            "keyword": keyword,
            "_unreliable": "Google SERP scrape is blocked; signal is not trustworthy. Replace with Keepa API.",
        }

        cache[key] = {"data": result, "cached_at": datetime.now(UTC).isoformat()}
        _save_cache(cache)
        return result

    except Exception as e:
        return {"error": str(e)[:100]}


# ── Comprehensive Keyword Score ───────────────────────────────────────────


def _load_site_config() -> dict:
    """Load site categories, keywords, voice rules, and audience config."""
    config = {}
    config_file = PROJECT_ROOT / "config.json"
    rules_file = PROJECT_ROOT / "data" / "content_rules.json"
    cache_file = PROJECT_ROOT / "data" / "site_content_cache.json"

    if config_file.exists():
        config["site"] = json.loads(config_file.read_text())
    if rules_file.exists():
        config["rules"] = json.loads(rules_file.read_text())
    if cache_file.exists():
        config["cache"] = json.loads(cache_file.read_text())

    return config


def _matches_site_category(keyword: str, category: str, config: dict) -> dict:
    """
    Check if keyword fits one of the site's 4 categories.
    Returns category match details.
    """
    categories = config.get("rules", {}).get("categories", {})
    keyword_lower = keyword.lower()

    # Extended keywords beyond what's in content_rules.json
    # These cover common search terms that map to each category
    extended_keywords = {
        "grooming": [
            "bath",
            "nails",
            "ears",
            "coat",
            "brush",
            "groom",
            "clean",
            "shedding",
            "shed",
            "fur",
            "deshedding",
            "shampoo",
            "wash",
            "matting",
            "trim",
            "haircut",
            "pedicure",
        ],
        "food_and_diet": [
            "kibble",
            "homemade",
            "recipe",
            "nutrition",
            "ingredient",
            "raw",
            "diet",
            "protein",
            "grain",
            "pumpkin",
            "food",
            "feeding",
            "meal",
            "AAFCO",
            "label",
            "allergy",
            "allergies",
            "supplement",
            "vitamin",
            "omega",
            "probiotic",
            "treat",
            "freeze dried",
            "fresh",
            "cost",
            "itching",
            "itch",
            "skin",
            "digestive",
            "gut",
        ],
        "lifestyle_and_gear": [
            "leash",
            "collar",
            "toy",
            "bed",
            "GPS",
            "tracker",
            "vest",
            "gear",
            "harness",
            "crate",
            "travel",
            "car",
            "camera",
            "monitor",
            "cooling",
            "jacket",
            "backpack",
            "bowl",
            "feeder",
        ],
        "training": [
            "recall",
            "command",
            "trick",
            "behavior",
            "reactivity",
            "threshold",
            "marker",
            "obedience",
            "puppy",
            "socialization",
            "anxiety",
            "separation",
            "counter surfing",
            "leash pulling",
            "barking",
            "aggression",
            "fear",
            "desensitization",
            "protocol",
        ],
    }

    # Direct category match with extended keywords
    if category in extended_keywords:
        cat_keywords = extended_keywords[category]
        matched = [kw for kw in cat_keywords if kw.lower() in keyword_lower]
        if matched:
            return {"matches": True, "category": category, "matched_keywords": matched}

    # Check all categories for best fit
    for cat_name, cat_keywords in extended_keywords.items():
        matched = [kw for kw in cat_keywords if kw.lower() in keyword_lower]
        if matched:
            return {"matches": True, "category": cat_name, "matched_keywords": matched}

    return {"matches": False, "category": category, "matched_keywords": []}


def _matches_site_voice(keyword: str, config: dict) -> dict:
    """
    Check if keyword aligns with the engineer/Nalla's Dad voice.
    Any dog-related topic CAN be made data-driven — the question is
    how naturally it fits the engineer framing.
    """
    keyword_lower = keyword.lower()

    # Keywords that EXPLICITLY signal data/engineer framing
    explicit_angles = [
        "comparison",
        "vs",
        "review",
        "best",
        "guide",
        "cost",
        "analysis",
        "test",
        "data",
        "how to",
        "protocol",
        "method",
        "budget",
        "worth it",
        "pros cons",
        "breakdown",
        "tracker",
    ]
    explicit_matches = [a for a in explicit_angles if a in keyword_lower]

    # Keywords that can be FRAMED as data-driven with the right title
    # e.g., "dog shedding" → "Engineer's Shedding Data: What 30 Days of Brushing Revealed"
    frameable_topics = [
        "dog",
        "pet",
        "food",
        "training",
        "gear",
        "health",
        "allergy",
        "shedding",
        "grooming",
        "diet",
        "raw",
        "kibble",
        "collar",
        "leash",
        "reactivity",
        "recall",
        "supplement",
        "recipe",
        "ingredient",
        "label",
    ]
    topic_matches = [t for t in frameable_topics if t in keyword_lower]

    # Engineer framing suggestions for non-obvious topics
    framing_suggestions = []
    if not explicit_matches and topic_matches:
        framing_suggestions = [
            f"Track {topic_matches[0]} metrics over N weeks",
            "Compare products with data table",
            "Cost-per-day analysis",
            "Before/after measurements",
            "Systematic protocol with measurable results",
        ]

    # Any dog topic is frameable — explicit angles just make it easier
    fits_voice = len(topic_matches) > 0 or len(explicit_matches) > 0
    can_be_data_driven = len(explicit_matches) > 0 or len(topic_matches) > 0

    return {
        "fits_voice": fits_voice,
        "engineer_angles_found": explicit_matches,
        "can_be_data_driven": can_be_data_driven,
        "topic_matches": topic_matches,
        "framing_suggestions": framing_suggestions[:2] if framing_suggestions else [],
    }


def _check_usa_canada_relevance(keyword: str, config: dict) -> dict:
    """
    Verify keyword targets USA/Canada audience.
    Check that products/topics are available in North America.
    """
    target = config.get("site", {}).get("target_countries", ["USA", "Canada"])

    # Keywords that signal non-US content
    non_us_signals = ["uk", "australia", "europe", "india", "nhs", "£", "€"]
    keyword_lower = keyword.lower()

    is_us_relevant = not any(sig in keyword_lower for sig in non_us_signals)

    return {
        "usa_canada_relevant": is_us_relevant,
        "target_countries": target,
        "currency": config.get("site", {}).get("currency", "USD"),
    }


def _check_existing_coverage(keyword: str, config: dict) -> dict:
    """
    Check if site already covers this topic.
    Returns overlap analysis.
    """
    cache = config.get("cache", {})
    posts = cache.get("recent_posts", [])
    gaps = cache.get("content_summary", {}).get("site", {}).get("content_gaps", [])

    keyword_lower = keyword.lower()
    keyword_words = set(keyword_lower.split())

    overlapping = []
    for post in posts:
        title_words = set(post.get("title", "").lower().split())
        overlap = keyword_words & title_words
        if len(overlap) >= 2:  # at least 2 words in common
            overlapping.append(post["title"])

    # Check if keyword falls in a content gap category
    in_gap = any(gap.lower() in keyword_lower for gap in gaps)

    return {
        "has_existing_content": len(overlapping) > 0,
        "overlapping_posts": overlapping,
        "in_content_gap": in_gap,
        "content_gaps": gaps,
    }


def score_keyword(
    keyword: str,
    category: str,
    site_has_content: bool = False,
    nalla_experience: bool = True,
    seasonal: bool = False,
) -> dict:
    """
    Score a keyword idea using real data sources, filtered through
    DogFoodAndFun's categories, voice, and USA/Canada audience.

    Score breakdown (max 10):
    - Content gap: +3 (no existing coverage on site)
    - Trending in USA: +2 (Google Trends rising in US + social engagement)
    - Demand signal: +2 (IG engagement + Amazon product demand)
    - Seasonal: +1 (timely for current month)
    - Competitor gap: +1 (engineer angle not covered by competitors)
    - Nalla experience: +1 (can speak authentically)

    Also validates:
    - Category fit (must match one of 4 site categories)
    - Voice fit (must work with engineer/data-driven tone)
    - USA/Canada relevance (products available, USD pricing)
    """
    config = _load_site_config()

    results: dict[str, Any] = {
        "keyword": keyword,
        "category": category,
        "score": 0,
        "max_score": 10,
        "evidence": {},
        "validation": {},
    }

    # ── Pre-validation: does this fit the site? ──
    cat_match = _matches_site_category(keyword, category, config)
    voice_match = _matches_site_voice(keyword, config)
    usa_match = _check_usa_canada_relevance(keyword, config)
    coverage = _check_existing_coverage(keyword, config)

    results["validation"]["category_fit"] = cat_match
    results["validation"]["voice_fit"] = voice_match
    results["validation"]["usa_canada"] = usa_match
    results["validation"]["existing_coverage"] = coverage

    # If it doesn't fit category or audience, flag it
    if not cat_match["matches"]:
        results["validation"]["warning"] = "Keyword doesn't match any site category keywords"
    if not usa_match["usa_canada_relevant"]:
        results["validation"]["warning"] = "Keyword may not target USA/Canada audience"
    if not voice_match["fits_voice"]:
        results["validation"]["note"] = "May need creative framing for engineer voice"

    # Override site_has_content with actual check
    site_has_content = coverage["has_existing_content"]

    # ── Scoring ──
    score = 0

    # 1. Content gap (+3)
    if not site_has_content:
        score += 3
        gap_reason = "No existing coverage on site"
        if coverage["in_content_gap"]:
            gap_reason += f" — in identified gap: {coverage['content_gaps']}"
        results["evidence"]["content_gap"] = {"points": 3, "reason": gap_reason}
    else:
        results["evidence"]["content_gap"] = {
            "points": 0,
            "reason": f"Overlaps with: {coverage['overlapping_posts']}",
        }

    # 2. Trending in USA (+2) — Google Trends filtered to US
    trends = get_google_trends(keyword, geo="US")
    results["evidence"]["google_trends_usa"] = trends
    if trends.get("trend") == "rising":
        score += 2
        results["evidence"]["trending_points"] = 2
    elif trends.get("trend") == "stable" and trends.get("interest", 0) > 50:
        score += 1
        results["evidence"]["trending_points"] = 1
    else:
        results["evidence"]["trending_points"] = 0

    # 3. Demand (+2) — IG engagement + Amazon product signals
    # Use category-relevant hashtag, not just keyword mashed together
    # Map categories to the most relevant IG hashtags for research
    category_hashtags = {
        "food_and_diet": ["dognutrition", "rawdogfood", "homemadedogfood", "dogfoodreview"],
        "lifestyle_and_gear": ["gpsdogcollar", "dogrunning", "doggear", "canicross"],
        "grooming": ["doggrooming", "dogshedding", "dogcoatcare", "dogbath"],
        "training": ["dogtraining", "positivereinforcement", "reactivedogs", "dogbehavior"],
    }

    # Pick hashtag based on category, then keyword specifics
    hashtag_candidates = category_hashtags.get(category, ["doglife"])
    keyword_lower = keyword.lower()

    # Try to find a more specific match
    relevant_hashtag = hashtag_candidates[0]  # default to first in category
    for h in hashtag_candidates:
        if any(word in h for word in keyword_lower.split() if len(word) > 3):
            relevant_hashtag = h
            break

    ig_data = get_instagram_hashtag_data(relevant_hashtag)
    results["evidence"]["instagram"] = {**ig_data, "hashtag_searched": relevant_hashtag}

    amazon_data = get_amazon_product_demand(keyword)
    results["evidence"]["amazon"] = amazon_data

    demand_points = 0
    if ig_data.get("engagement_signal") in ("high", "medium"):
        demand_points += 1
    if amazon_data.get("demand_signal") in ("high", "medium"):
        demand_points += 1
    score += demand_points
    results["evidence"]["demand_points"] = demand_points

    # 4. Seasonal (+1)
    if seasonal:
        score += 1
        results["evidence"]["seasonal"] = {"points": 1, "reason": "Timely for current season"}
    else:
        results["evidence"]["seasonal"] = {"points": 0}

    # 5. Competitor gap (+1) — our engineer angle is always a differentiator
    if voice_match["can_be_data_driven"]:
        score += 1
        if voice_match["engineer_angles_found"]:
            reason = f"Explicit data angle: {voice_match['engineer_angles_found']}"
        elif voice_match.get("framing_suggestions"):
            reason = f"Can be framed as: {voice_match['framing_suggestions'][0]}"
        else:
            reason = (
                f"Topic matches: {voice_match.get('topic_matches', [])[:3]} — engineer voice fits"
            )
        results["evidence"]["competitor_gap"] = {"points": 1, "reason": reason}
    else:
        results["evidence"]["competitor_gap"] = {
            "points": 0,
            "reason": "Topic doesn't align with dog niche",
        }

    # 6. Nalla experience (+1)
    if nalla_experience:
        score += 1
        results["evidence"]["nalla_experience"] = {"points": 1}
    else:
        results["evidence"]["nalla_experience"] = {"points": 0}

    results["score"] = min(score, 10)
    results["scored_at"] = datetime.now(UTC).isoformat()

    return results


def score_keyword_batch(keywords: list[dict]) -> list[dict]:
    """
    Score multiple keywords. Each item needs: keyword, category, site_has_content, nalla_experience, seasonal.
    Returns sorted list by score descending.
    """
    results = []
    for kw in keywords:
        result = score_keyword(
            keyword=kw["keyword"],
            category=kw["category"],
            site_has_content=kw.get("site_has_content", False),
            nalla_experience=kw.get("nalla_experience", True),
            seasonal=kw.get("seasonal", False),
        )
        results.append(result)
        time.sleep(1)  # rate limit between API calls

    results.sort(key=lambda x: x["score"], reverse=True)
    return results


if __name__ == "__main__":
    # Quick test
    result = score_keyword(
        keyword="GPS dog tracker",
        category="lifestyle_and_gear",
        site_has_content=False,
        nalla_experience=True,
        seasonal=False,
    )
    print(json.dumps(result, indent=2))
