"""Seed-grounded recipe generation.

Flow:
  1. Match the topic to a vetted seed in seeds/seeds.json.
  2. Call Claude only for the voice fields (intro, verdict, FAQ, captions, meta).
  3. Assemble the final Recipe from the seed (frozen factual content) + voice.
  4. Validate (dog-safety, reproducibility, voice rules).

If no seed matches, raise NoSeedMatchError — we do not invent recipes.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass

from .recipe_from_seed import assemble_body_markdown, generate_from_seed
from .recipe_from_seed_gemini import generate_from_seed_gemini
from .seeds import NoSeedMatchError, match_seed

logger = logging.getLogger(__name__)

_MODEL = os.getenv("RECIPE_MODEL", "claude-sonnet-4-6")


@dataclass
class Recipe:
    title: str
    slug: str
    meta_description: str
    body_markdown: str
    ingredients: list[str]
    steps: list[str]
    prep_minutes: int
    cook_minutes: int
    yield_servings: str
    tags: list[str]
    image_brief: str
    ig_caption: str
    seed_id: str = ""  # set by generate_recipe() — used to look up carousel config


def _slugify(text: str) -> str:
    s = re.sub(r"[^\w\s-]", "", text.lower()).strip()
    return re.sub(r"[\s_-]+", "-", s)[:80]


def _auto_voice_provider() -> str:
    """Default provider: Gemini if its key is set and Anthropic is not."""
    if os.getenv("GEMINI_API_KEY") and not os.getenv("ANTHROPIC_API_KEY"):
        return "gemini"
    if os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic"
    return "gemini"


def generate_recipe(topic: str, *, client: object | None = None) -> Recipe:
    """Match topic to a seed and produce a voice-wrapped recipe. Raises if no seed fits.

    Voice provider is selected by `VOICE_PROVIDER` env: `gemini` (default when
    GEMINI_API_KEY is set and Anthropic is not) or `anthropic`.
    """
    seed = match_seed(topic)
    if seed is None:
        raise NoSeedMatchError(
            f"No seed matches topic {topic!r}. "
            f"Add a seed to seeds/seeds.json — we do not invent recipes."
        )

    logger.info("topic=%r matched seed=%s", topic, seed.id)

    provider = (os.getenv("VOICE_PROVIDER") or _auto_voice_provider()).lower()
    if provider == "gemini":
        voice = generate_from_seed_gemini(topic, seed)
    elif provider == "anthropic":
        from anthropic import Anthropic
        anthropic_client = client or Anthropic()
        voice = generate_from_seed(topic, seed, client=anthropic_client, model=_MODEL)
    else:
        raise ValueError(f"unknown VOICE_PROVIDER={provider!r}")
    body = assemble_body_markdown(voice, seed)
    title = (voice.get("title") or seed.title).strip()

    recipe = Recipe(
        title=title,
        slug=_slugify(title),
        meta_description=voice["meta_description"],
        body_markdown=body,
        ingredients=list(seed.ingredients),
        steps=list(seed.steps),
        prep_minutes=seed.prep_minutes,
        cook_minutes=seed.cook_minutes,
        yield_servings=seed.yield_servings,
        tags=list(seed.tags),
        image_brief=voice["image_brief"],
        ig_caption=voice["ig_caption"],
        seed_id=seed.id,
    )
    _validate(recipe)
    return recipe


# Dog-toxic ingredients — any substring match in an ingredient line is a hard reject.
# Sources: ASPCA Toxic and Non-Toxic Food List + VCA Hospitals dog-safety guides.
_TOXIC_INGREDIENTS = (
    "xylitol", "chocolate", "cocoa", "cacao",
    "onion", "garlic", "chive", "leek", "shallot", "scallion",
    "grape", "raisin", "currant",
    "macadamia",
    "alcohol", "wine", "beer", "liquor", "rum", "vodka",
    "caffeine", "coffee", "espresso",
    "nutmeg",
    "raw yeast", "raw dough",
    "avocado pit", "avocado skin",
    "cherry pit", "apple seed",
    "raw salmon",
    "cooked bone",
)

# Vague quantities/times that make a recipe unreproducible.
_VAGUE_QUANTITY = (
    "to taste", "a handful", "a little", "a bit", "as needed",
    "enough to coat", "enough to cover", "some ",
)
_VAGUE_TIME = (
    "until done", "for a while", "some time", "a few minutes",
    "cook it", "bake it",
)

_NUMBER_RE = re.compile(r"\d")


def _validate(recipe: Recipe) -> None:
    if not 120 <= len(recipe.meta_description) <= 165:
        raise ValueError(
            f"meta_description length={len(recipe.meta_description)} outside 120-165"
        )
    if len(recipe.ig_caption) < 80:
        raise ValueError(f"ig_caption too short: {len(recipe.ig_caption)} chars")
    hook = recipe.ig_caption[:125]
    if not hook.strip():
        raise ValueError("ig_caption hook (first 125 chars) is empty")
    if recipe.ig_caption.count("\u2022") < 3:
        raise ValueError(
            "ig_caption missing 3 bullet-fact lines (need at least three '\u2022' markers)"
        )
    if not re.search(r"\bComment [A-Z]{3,}\b", recipe.ig_caption):
        raise ValueError(
            "ig_caption missing comment-gated CTA like 'Comment RECIPE' "
            "(uppercase keyword after 'Comment ')"
        )
    for tag in ("#nallasdad", "#dogfoodandfun"):
        if tag not in recipe.ig_caption:
            raise ValueError(f"ig_caption missing required branded hashtag {tag!r}")
    if len(recipe.ingredients) < 1:
        raise ValueError("recipe must have at least one ingredient")
    if len(recipe.steps) < 3:
        raise ValueError(f"too few steps: {len(recipe.steps)} (min 3)")

    body_lower = recipe.body_markdown.lower()
    banned_medical = {"cures", "treats disease", "prescribed", "medical-grade"}
    hits = [w for w in banned_medical if w in body_lower]
    if hits:
        raise ValueError(f"medical-claim language detected in body: {hits}")

    # Dog-safety: hard reject any toxic ingredient in the structured list.
    # Safety qualifiers like "xylitol-free peanut butter" must NOT trip the check —
    # strip negation phrases before scanning.
    ingredients_text = " | ".join(recipe.ingredients).lower()
    for w in _TOXIC_INGREDIENTS:
        for pat in (
            rf"\b{re.escape(w)}[- ]free\b",
            rf"\bno {re.escape(w)}\b",
            rf"\bwithout {re.escape(w)}\b",
        ):
            ingredients_text = re.sub(pat, " ", ingredients_text)
    toxic_hits = [t for t in _TOXIC_INGREDIENTS if t in ingredients_text]
    if toxic_hits:
        raise ValueError(
            f"dog-toxic ingredient(s) in recipe: {toxic_hits}. "
            "Seed library is corrupted — reject before publishing."
        )

    # Reproducibility: every ingredient line needs a measurable quantity.
    for ing in recipe.ingredients:
        low = ing.lower()
        if any(v in low for v in _VAGUE_QUANTITY):
            raise ValueError(f"vague quantity in ingredient: {ing!r}")
        if not _NUMBER_RE.search(ing):
            raise ValueError(
                f"ingredient has no numeric quantity: {ing!r}. "
                "Use 'X cups', 'X tbsp', 'X g', 'X oz', or an explicit count."
            )

    # Reproducibility: every action step should have a time, a temperature, or a
    # visual doneness cue. Short prep steps ("line a sheet with parchment") are
    # exempt — we only check steps that contain a cooking verb.
    for i, step in enumerate(recipe.steps, 1):
        low = step.lower()
        if any(v in low for v in _VAGUE_TIME):
            raise ValueError(f"vague timing in step {i}: {step!r}")
        has_time = bool(re.search(r"\d+\s*(min|minute|hour|second|hr|s\b)", low))
        has_temp = bool(re.search(r"\d+\s*°|\b\d{2,3}\s*(f|c)\b", low))
        has_visual_cue = any(
            kw in low
            for kw in (
                "until", "fork-tender", "golden", "smooth", "cool",
                "combined", "firm", "clean", "springs back", "fragrant",
                "heat", "bubble", "reduce", "simmer", "no pink",
                "toothpick", "browned", "tender",
            )
        )
        # Word-boundary match so "cookie", "cooker", "uncooked" don't trigger,
        # AND only look near the start of the step so narrative references to
        # cooking ("during the long simmer") don't count as imperative actions.
        first_clause = low[:60]
        is_action = bool(
            re.search(
                r"\b(bake|simmer|boil|roast|cook|fry|saut[eé]|whisking until|knead|dehydrate)\b",
                first_clause,
            )
        )
        if is_action and not (has_time or has_temp or has_visual_cue):
            raise ValueError(
                f"action step {i} missing time/temp/doneness cue: {step!r}"
            )
