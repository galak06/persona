"""
Comment generator for DogFoodAndFun social media automation.
Drafts Nalla's Dad-voice comments using templates + Claude API.
Validates voice before returning.
"""

from __future__ import annotations

import json
from lib.config import settings
import re
from pathlib import Path

from draft_history import (
    filter_unused,
    record_draft,
    was_post_commented,
    was_text_recently_used,
)

DATA_DIR = settings.paths.data_dir
TEMPLATES_FILE = DATA_DIR / "post_templates.json"
BRAND_VOICE_FILE = DATA_DIR / "brand_voice_guide.md"

# Keywords that signal voice failure — never allowed in final comment
MEDICAL_JARGON = [
    "clinical",
    "veterinary-grade",
    "clinically proven",
    "studies show",
    "research indicates",
    "scientifically",
    "diagnosis",
    "symptoms",
    "treatment",
    "prescribe",
    "consult your vet before",
]
SALESY_PHRASES = [
    "check out our",
    "visit our site",
    "click here",
    "buy now",
    "our product",
    "shop now",
    "affiliate",
    "promo code",
    "dogfoodandfun.com",  # never include the URL unless user explicitly approves
]


def load_templates() -> dict:
    if TEMPLATES_FILE.exists():
        with TEMPLATES_FILE.open() as f:
            return json.load(f)
    return {}


def score_relevance(
    post_text: str,
    post_meta: dict | None = None,
    group_category: str = "",
) -> float:
    """
    Score a post for relevance to dogfoodandfun.com content.
    Returns float 0.0 – 1.0+. Threshold to queue: 0.70.
    group_category: "food", "gps", "health", "training", "general"
    """
    text = post_text.lower()
    score = 0.0

    # Food / nutrition signals (broadened — these groups ARE about dog food)
    food_keywords = [
        "dog food",
        "homemade",
        "recipe",
        "nutrition",
        "ingredients",
        "raw",
        "kibble",
        "diet",
        "feeding",
        "meal",
        "protein",
        "grain",
        "food",
        "treat",
        "snack",
        "chew",
        "supplement",
        "vitamin",
        "probiotic",
        "omega",
        "calcium",
        "freeze dried",
        "dehydrated",
        "batch cook",
        "prep",
        "topper",
        "fresh pet",
        "freshpet",
        "transition",
        "switching",
        "picky eater",
        "allergy",
        "sensitive",
        "stomach",
        "digestive",
        "gut",
        "dental",
        "teeth",
        "yogurt",
        "pumpkin",
        "sardine",
        "chicken",
        "beef",
        "turkey",
        "salmon",
        "sweet potato",
        "broth",
        "bone broth",
        "coconut oil",
    ]
    if any(kw in text for kw in food_keywords):
        score += 0.40

    # GPS / running / active dog signals
    active_keywords = [
        "gps",
        "tracker",
        "running",
        "canicross",
        "trail",
        "hike",
        "gear",
        "collar",
        "leash",
        "activity",
        "exercise",
        "sport",
        "fi ",
        "tractive",
        "walk",
        "adventure",
    ]
    if any(kw in text for kw in active_keywords):
        score += 0.30

    # Question format
    if "?" in post_text:
        score += 0.20

    # Specific brands reviewed on site
    reviewed_brands = [
        "fi collar",
        "tractive",
        "whistle",
        "link akc",
        "ollie",
        "nom nom",
        "the farmer's dog",
        "open farm",
    ]
    if any(brand in text for brand in reviewed_brands):
        score += 0.20

    # Meta signals from post
    if post_meta:
        comment_count = post_meta.get("comment_count", 0)
        hours_old = post_meta.get("hours_old", 999)

        if 5 <= comment_count <= 50:
            score += 0.10  # engaged but not viral
        elif comment_count > 100:
            score -= 0.30  # too crowded

        if hours_old <= 24:
            score += 0.10  # fresh post

        if post_meta.get("is_competitor", False):
            score -= 0.50

    # Group context bonus: posts in food groups about food-adjacent topics
    # get a boost since the group context confirms relevance
    if (group_category == "food" and score >= 0.30) or (group_category == "gps" and score >= 0.20):
        score += 0.15
    elif group_category == "health" and score >= 0.30:
        score += 0.10

    return round(score, 2)


def validate_voice(
    comment: str,
    *,
    allow_own_url: bool = False,
) -> tuple[bool, list[str]]:
    """
    Validates that a comment follows Nalla's Dad voice rules.
    Returns (is_valid, list_of_violations).

    allow_own_url: when True, the "dogfoodandfun.com" phrase is excluded from
    the SALESY check. Only brand publishers that bake the URL into post
    bodies (FB group posts, FB page link cards, IG carousel captions) may
    pass True. Engagement-comment paths (fb_scanner, ig_scanner,
    wp_comment_handler, comment_composer) MUST use the default (False) so
    third-party replies never carry our URL.
    """
    violations = []
    comment_lower = comment.lower()

    # Block list checks
    for phrase in MEDICAL_JARGON:
        if phrase in comment_lower:
            violations.append(f"Medical jargon detected: '{phrase}'")

    salesy_phrases = SALESY_PHRASES
    if allow_own_url:
        salesy_phrases = [p for p in SALESY_PHRASES if p != "dogfoodandfun.com"]
    for phrase in salesy_phrases:
        if phrase in comment_lower:
            violations.append(f"Salesy language detected: '{phrase}'")

    # Must end with a question
    if not re.search(r"\?", comment):
        violations.append("Comment must end with a question (no '?' found)")

    # Length
    if len(comment) < 40:
        violations.append(f"Comment too short ({len(comment)} chars) — needs substance")
    if len(comment) > 500:
        violations.append(f"Comment too long ({len(comment)} chars) — trim to under 500")

    # Generic openers are forbidden
    generic_openers = ["great post!", "love this!", "awesome!", "nice post", "amazing!"]
    if any(comment_lower.startswith(g) for g in generic_openers):
        violations.append("Generic opener detected — start with something specific")

    # Specificity check — must have Nalla OR a specific detail (number, brand, timeframe)
    has_nalla = "nalla" in comment_lower
    has_number = bool(re.search(r"\d+", comment))  # any digit = specific detail
    has_brand = any(
        b in comment_lower
        for b in ["fi", "tractive", "whistle", "ollie", "nom nom", "farmer's dog", "open farm"]
    )
    has_timeframe = any(
        t in comment_lower
        for t in ["day", "week", "month", "year", "hours", "minute", "last winter", "last year"]
    )

    if not any([has_nalla, has_number, has_brand, has_timeframe]):
        violations.append(
            "Comment lacks specificity — mention Nalla, a number, a brand, or a timeframe"
        )

    # Personal experience check — must claim first-person experience
    has_personal = any(
        p in comment_lower
        for p in [
            "we ",
            "we've",
            "nalla",
            "our ",
            "i ",
            "i've",
            "found",
            "noticed",
            "tried",
            "tested",
        ]
    )
    if not has_personal:
        violations.append(
            "Comment must claim personal experience (we, Nalla, our, found, tested...)"
        )

    return len(violations) == 0, violations


def draft_comment_from_template(
    category: str,
    post_text: str,
    post_author: str = "",
) -> str | None:
    """
    Draft a comment using a category template. Falls back to None if no template
    is available OR every template was already used in the last 30 days
    (forces caller to use Claude generation for variation).

    category: "gps" | "food" | "health" | "training"
    """
    templates = load_templates()
    category_templates = templates.get(category, [])
    if not category_templates:
        return None

    # Drop templates whose normalized text has been posted in the last 30 days.
    # This is the primary defense against the recurring duplicate-text bug.
    fresh = filter_unused(category_templates)
    if not fresh:
        return None  # all templates used recently — caller falls through to Claude

    import random
    template = random.choice(fresh)

    # Record the selection up-front. Whether the caller posts the draft or
    # rejects it on voice-validation, the same template should not come up
    # again in the next 30 days — otherwise repeated calls in one run keep
    # picking the same template until it happens to validate.
    record_draft(template, platform="facebook", post_id="", target="template_selection")

    comment = template
    comment = comment.replace("{author}", post_author.split()[0] if post_author else "you")
    return comment


def build_claude_prompt(post_text: str, category: str, group_name: str = "") -> str:
    """Builds the prompt to send to Claude for generating a comment."""
    brand_voice = ""
    if BRAND_VOICE_FILE.exists():
        brand_voice = BRAND_VOICE_FILE.read_text()[:2000]  # first 2000 chars

    return f"""You are Nalla's Dad — a passionate dog owner who runs dogfoodandfun.com.
You are writing a helpful comment on a Facebook dog group post.

{brand_voice}

## Post Context
Group: {group_name}
Category: {category}
Post text:
---
{post_text[:1000]}
---

## Task
Write a genuine, helpful comment as Nalla's Dad. Requirements:
- Reference something specific from the post (show you read it)
- Share a brief personal experience ("We tried this with Nalla...")
- Add one concrete tip or observation
- End with a genuine question to continue the conversation
- NO medical jargon, NO salesy language, NO generic openers like "Great post!"
- Length: 80–250 characters
- Tone: warm, peer-to-peer, like texting a fellow dog owner

Output ONLY the comment text. No quotes, no preamble."""


def generate_comment(
    post_text: str,
    category: str,
    group_name: str = "",
    post_author: str = "",
    *,
    platform: str = "facebook",
    post_id: str = "",
) -> dict:
    """
    Main entry point. Returns:
    {
        "comment": str | None,
        "valid": bool,
        "violations": list[str],
        "method": "template" | "generated" | "skipped",
        "score_check": bool,
        "skip_reason": str (only when method == "skipped"),
    }

    When platform+post_id are provided, refuses to draft if the bot has already
    engaged with this post (returns method="skipped").
    """
    # Pre-flight: refuse to draft a duplicate engagement on the same post.
    if post_id and platform in ("facebook", "instagram", "wordpress"):
        if was_post_commented(platform, post_id):
            return {
                "comment": None,
                "valid": False,
                "violations": [],
                "method": "skipped",
                "score_check": False,
                "skip_reason": f"post_id {post_id} on {platform} already engaged",
            }

    # Try template first
    draft = draft_comment_from_template(category, post_text, post_author)
    method = "template"

    if not draft:
        # Fall back to instructing the calling agent to use Claude
        prompt = build_claude_prompt(post_text, category, group_name)
        # The agent will use this prompt to generate via Claude directly
        # We return the prompt so the skill can invoke it
        return {
            "comment": None,
            "valid": False,
            "violations": ["No template found — use build_claude_prompt() output"],
            "method": "needs_generation",
            "prompt": prompt,
        }

    is_valid, violations = validate_voice(draft)
    # Note: draft_comment_from_template already recorded this template as
    # used. We don't double-record here. If is_valid is True, the post-side
    # mark_engaged() in comment_poster.py provides per-post dedupe.
    return {
        "comment": draft,
        "valid": is_valid,
        "violations": violations,
        "method": method,
        "prompt": None,
    }


if __name__ == "__main__":
    # Quick test
    test_post = """
    Hey everyone! My dog Bella has been having some digestive issues lately.
    I've been thinking about switching her to a homemade diet but I'm not sure
    where to start. What did you all feed your dogs when transitioning from kibble?
    """
    score = score_relevance(test_post)
    print(f"Relevance score: {score}")

    result = generate_comment(test_post, "food", "Homemade Dog Food Community")
    print(f"\nResult: {json.dumps(result, indent=2)}")
