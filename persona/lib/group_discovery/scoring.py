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

# Food keyword bonus only counts when the group is clearly dog-focused.
DOG_ANCHORS = ["dog", "canine", "puppy", "pup", "pups", "hound", "pooch"]

# Wholesale/distributor groups are vendor channels, not dog owner communities.
WHOLESALE_KW = [
    "wholesale", "distributor", "distributor", "retail", "retailer",
    "vendor", "supplier", "reseller", "bulk order", "bentahan",
]

# Cat-specific and human-food groups that match food keywords but are off-niche.
CAT_KW = ["cat food", "feline", "for cats", "cat owner", "cat lover", "cat care", "cat and dog food"]
HUMAN_FOOD_KW = [
    "soul food", "southern recipe", "southern cooking", "southern food",
    "family recipe", "home cooking", "human food", "comfort food",
]


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


# Regional keywords for USA/Canada targeting
GEO_KEYWORDS = [
    "usa", "u.s.a", "united states", "american", "canada", "canadian",
    "america", "north america", "us based", "ca based",
    "new york", "california", "texas", "florida", "toronto", "vancouver",
]

# Non-US/CA signals — any match DISQUALIFIES the group (score forced to 0)
NON_US_SIGNALS = [
    # English-speaking non-NA
    "uk", "united kingdom", "britain", "british", "england", "scotland", "wales",
    "australia", "australian", "new zealand", "nz",
    # South/Southeast Asia
    "india", "indian", "pakistan", "bangladesh", "sri lanka",
    "philippines", "filipino", "pilipinas", "pilipino", "bentahan",
    "indonesia", "malaysia", "singapore", "vietnam", "thailand",
    # Middle East / Africa
    "nigeria", "kenya", "south africa", "ghana", "egypt", "israel",
    "saudi", "dubai", "uae",
    # Europe
    "europe", "european", "germany", "german", "france", "french",
    "spain", "spanish", "italy", "italian", "netherlands", "poland",
    "london", "sydney", "melbourne", "delhi", "mumbai", "lahore",
]

def score_group(g: dict, competitor_mentions: int = 0) -> int:
    score = 0
    name_desc = (g["name"] + " " + g["description"]).lower()
    
    # Niche keyword match (additive, max 30)
    # Food bonus only when group is clearly dog-focused (not cat food, soul food, etc.)
    if any(kw in name_desc for kw in FOOD_KW) and any(a in name_desc for a in DOG_ANCHORS):
        score += 15
    if any(kw in name_desc for kw in GPS_KW):
        score += 10
    if any(kw in name_desc for kw in LIFESTYLE_KW):
        score += 5

    # Hard disqualify: any non-US/CA geo signal → score 0
    if any(sig in name_desc for sig in NON_US_SIGNALS):
        return 0

    # Hard disqualify: group must mention dogs in some form
    if not any(a in name_desc for a in DOG_ANCHORS):
        return 0

    # Geo bonus for explicit USA/Canada signal
    if any(kw in name_desc for kw in GEO_KEYWORDS):
        score += 15

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
    if any(brand in name_desc for brand in PRODUCT_BRANDS):
        score -= 40

    # Wholesale/distributor penalty — vendor channels, not dog owner communities
    if any(kw in name_desc for kw in WHOLESALE_KW):
        score -= 35

    # Cat-focused groups penalty
    if any(kw in name_desc for kw in CAT_KW):
        score -= 30

    # Human food / off-niche food penalty
    if any(kw in name_desc for kw in HUMAN_FOOD_KW):
        score -= 40

    return max(0, score)
