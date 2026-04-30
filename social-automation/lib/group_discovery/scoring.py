"""Candidate scoring for fb-group-scout."""

from __future__ import annotations

import re

FOOD_KW = [
    "food",
    "nutrition",
    "recipe",
    "diet",
    "raw",
    "kibble",
    "homemade",
    "feeding",
    "meal",
    "ingredient",
    "protein",
    "grain free",
]
GPS_KW = [
    "gps",
    "tracker",
    "tracking",
    "running",
    "canicross",
    "hike",
    "hiking",
    "trail",
    "sport",
    "active",
    "agility",
]
LIFESTYLE_KW = [
    "dog owner",
    "dog lifestyle",
    "dog product",
    "dog health",
    "dog care",
    "dog community",
    "dog lover",
]

# Brands we review on the site — groups dominated by these are a conflict-of-interest,
# so they get a negative score. Distinct from content-competitors in competitors.json.
PRODUCT_BRANDS = {
    "tractive",
    "fi collar",
    "ficollar",
    "whistle",
    "link akc",
    "ollie",
    "nom nom",
    "farmer's dog",
    "open farm",
}


def parse_member_count(text: str) -> int:
    if not text:
        return 0
    text = text.lower().replace(",", "")
    m = re.search(r"([\d.]+)\s*k", text)
    if m:
        return int(float(m.group(1)) * 1000)
    m = re.search(r"([\d.]+)\s*m", text)
    if m:
        return int(float(m.group(1)) * 1_000_000)
    m = re.search(r"(\d+)", text)
    return int(m.group(1)) if m else 0


def competitor_signal_boost(mentions: int) -> int:
    """+15 per distinct competitor name in group text, capped at +45."""
    return min(mentions * 15, 45)


def score_group(g: dict, competitor_mentions: int = 0) -> int:
    score = 0
    text = (g["name"] + " " + g["description"]).lower()

    # Niche keyword match (additive, max 30)
    if any(kw in text for kw in FOOD_KW):
        score += 15
    if any(kw in text for kw in GPS_KW):
        score += 10
    if any(kw in text for kw in LIFESTYLE_KW):
        score += 5

    # Member count (max 20)
    mc = g["member_count"]
    if 1_000 <= mc <= 10_000:
        score += 20
    elif 10_000 < mc <= 50_000:
        score += 15
    elif 50_000 < mc <= 150_000:
        score += 10

    # Activity level (max 20)
    freq = g["post_frequency"].lower()
    if "day" in freq:
        score += 20
    elif "week" in freq:
        score += 10

    # Private group bonus
    if g["privacy"] == "private":
        score += 10

    # Competitor signal — groups where content competitors are active are prime targets
    score += competitor_signal_boost(competitor_mentions)

    # Product-brand penalty (conflict of interest for review site)
    if any(brand in text for brand in PRODUCT_BRANDS):
        score -= 40

    return max(0, score)
