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
import re
from dataclasses import dataclass, field

from .caption_validator import validate_hook
from .drafter import Drafter, get_drafter
from .recipe_from_seed import assemble_body_markdown
from .seeds import NoSeedMatchError, load_seeds, match_seed

logger = logging.getLogger(__name__)


def _seed_by_id(seed_id: str):  # noqa: ANN202 — RecipeSeed; avoid circular import
    """Return the seed with this exact id, or None. Deterministic counterpart
    to ``match_seed`` for callers that already know the precise recipe (e.g.
    batch publishing a specific DB row, not a fuzzy topic)."""
    return next((s for s in load_seeds() if s.id == seed_id), None)


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
    # PAA-style Q&A pairs. Kept as a structured field (not only inside
    # body_markdown) so the WordPress publisher can emit FAQPage JSON-LD
    # alongside the Recipe schema without re-parsing rendered HTML.
    faq: list[dict] = field(default_factory=list)


def _slugify(text: str) -> str:
    s = re.sub(r"[^\w\s-]", "", text.lower()).strip()
    return re.sub(r"[\s_-]+", "-", s)[:80]


def generate_recipe(
    topic: str,
    *,
    drafter: Drafter | None = None,
    hook_blocklist: list[str] | None = None,
    seed_id: str | None = None,
) -> Recipe:
    """Match topic to a seed and produce a voice-wrapped recipe. Raises if no seed fits.

    Args:
        topic: The recipe topic.
        drafter: Optional override for the voice drafter. None auto-selects via
            `VOICE_PROVIDER` env var (defaults: Gemini if its key is set and
            Anthropic is not, else Anthropic). Tests inject a mock here.
        hook_blocklist: Optional regex patterns the caption's first sentence
            must NOT match. None skips hook validation (back-compat for callers
            without brand context). On first failure, the drafter is re-prompted
            once with an explicit anti-pattern hint; second failure raises.
    """
    seed = _seed_by_id(seed_id) if seed_id else match_seed(topic)
    if seed is None:
        target = f"id {seed_id!r}" if seed_id else f"topic {topic!r}"
        raise NoSeedMatchError(
            f"No seed matches {target}. "
            f"Add a seed to seeds/seeds.json — we do not invent recipes."
        )

    logger.info("topic=%r seed=%s (by_id=%s)", topic, seed.id, bool(seed_id))

    voice_drafter = drafter or get_drafter()
    voice = _draft_with_hook_retry(voice_drafter, topic, seed, hook_blocklist)
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
        faq=[dict(p) for p in (voice.get("faq") or [])],
    )
    _validate(recipe)
    return recipe


def _draft_with_hook_retry(
    drafter: Drafter,
    topic: str,
    seed,  # noqa: ANN001  — RecipeSeed; avoid circular import noise
    hook_blocklist: list[str] | None,
) -> dict:
    voice = drafter.draft_voice(topic, seed)
    if not hook_blocklist:
        return voice
    try:
        validate_hook(voice.get("ig_caption", ""), hook_blocklist)
        return voice
    except Exception as first_err:
        logger.warning("hook validation failed, re-prompting drafter once: %s", first_err)
        hint = (
            f"Do NOT start the caption with any of these phrases: {hook_blocklist}. "
            "The first sentence must be a concrete moment "
            "(e.g., 'Nalla turned her nose up at her dinner.')."
        )
        voice = drafter.draft_voice(topic, seed, extra_instructions=hint)
        validate_hook(voice.get("ig_caption", ""), hook_blocklist)
        return voice


# Dog-toxic ingredients — any substring match in an ingredient line is a hard reject.
# Sources: ASPCA Toxic and Non-Toxic Food List + VCA Hospitals dog-safety guides.
_TOXIC_INGREDIENTS = (
    "xylitol",
    "chocolate",
    "cocoa",
    "cacao",
    "onion",
    "garlic",
    "chive",
    "leek",
    "shallot",
    "scallion",
    "grape",
    "raisin",
    "currant",
    "macadamia",
    "alcohol",
    "wine",
    "beer",
    "liquor",
    "rum",
    "vodka",
    "caffeine",
    "coffee",
    "espresso",
    "nutmeg",
    "raw yeast",
    "raw dough",
    "avocado pit",
    "avocado skin",
    "cherry pit",
    "apple seed",
    "raw salmon",
    "cooked bone",
)

# Vague quantities/times that make a recipe unreproducible.
_VAGUE_QUANTITY = (
    "to taste",
    "a handful",
    "a little",
    "a bit",
    "as needed",
    "enough to coat",
    "enough to cover",
    "some ",
)
_VAGUE_TIME = (
    "until done",
    "for a while",
    "some time",
    "a few minutes",
    "cook it",
    "bake it",
)

_NUMBER_RE = re.compile(r"\d")

_WORD_NUMBERS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}

# A number (digit or word) within two words of "ingredient(s)", e.g.
# "4 simple ingredients", "three ingredients", "5 main ingredients".
_INGREDIENT_COUNT_RE = re.compile(
    r"\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\b"
    r"(?:\s+\w+){0,2}\s+ingredients?\b",
    re.IGNORECASE,
)


def _claimed_ingredient_count(caption: str) -> int | None:
    """Extract an explicit ingredient count asserted in the caption, or None.

    Catches both digit ("4 simple ingredients") and spelled ("three
    ingredients") forms so a caption can't under/over-count vs the recipe.
    """
    m = _INGREDIENT_COUNT_RE.search(caption)
    if m is None:
        return None
    token = m.group(1).lower()
    return int(token) if token.isdigit() else _WORD_NUMBERS[token]


def _validate(recipe: Recipe) -> None:
    if not 120 <= len(recipe.meta_description) <= 165:
        raise ValueError(f"meta_description length={len(recipe.meta_description)} outside 120-165")
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
    claimed = _claimed_ingredient_count(recipe.ig_caption)
    if claimed is not None and claimed != len(recipe.ingredients):
        raise ValueError(
            f"ig_caption claims {claimed} ingredients but recipe has "
            f"{len(recipe.ingredients)} — counts must match the recipe card"
        )
    if len(recipe.steps) < 3:
        raise ValueError(f"too few steps: {len(recipe.steps)} (min 3)")

    body_lower = recipe.body_markdown.lower()
    banned_medical = {"cures", "treats disease", "prescribed", "medical-grade"}
    hits = [w for w in banned_medical if w in body_lower]
    if hits:
        raise ValueError(f"medical-claim language detected in body: {hits}")

    # Dog-safety: hard reject any toxic ingredient in the structured list.
    # Safety qualifiers must NOT trip the check — for each occurrence of a
    # toxic token, look back within the same clause (delimited by . or |) for
    # a "no" / "without" / "{toxic}-free" prefix. If one exists, the token is
    # being EXCLUDED, not used.
    # Handles:
    #   "xylitol-free peanut butter"
    #   "no garlic" / "without garlic"
    #   "no onion or garlic" / "no garlic, onion, salt"
    ingredients_text = " | ".join(recipe.ingredients).lower()
    _toxic_alts = "|".join(re.escape(w) for w in _TOXIC_INGREDIENTS)
    # First pass: strip "{toxic}-free" forms wholesale ("xylitol-free", "fat-free")
    ingredients_text = re.sub(
        rf"\b(?:{_toxic_alts})[- ]free\b", " ", ingredients_text
    )
    _toxic_token_re = re.compile(rf"\b({_toxic_alts})\b")
    _negation_re = re.compile(r"\b(?:no|without)\b")
    toxic_hits: list[str] = []
    for m in _toxic_token_re.finditer(ingredients_text):
        # Find the start of this clause (last . or | before the token)
        before = ingredients_text[: m.start()]
        clause_start = max(before.rfind("."), before.rfind("|")) + 1
        clause_before = ingredients_text[clause_start : m.start()]
        if _negation_re.search(clause_before):
            continue  # negated — exclusion, not inclusion
        toxic_hits.append(m.group(1))
    toxic_hits = sorted(set(toxic_hits))
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
                "until",
                "fork-tender",
                "golden",
                "smooth",
                "cool",
                "combined",
                "firm",
                "clean",
                "springs back",
                "fragrant",
                "heat",
                "bubble",
                "reduce",
                "simmer",
                "no pink",
                "toothpick",
                "browned",
                "tender",
            )
        )
        # Word-boundary match so "cookie", "cooker", "uncooked" don't trigger,
        # AND only look near the start of the step so narrative references to
        # cooking ("during the long simmer") don't count as imperative actions.
        first_clause = low[:60]
        # NB: kneading/rolling/shaping are prep verbs with no doneness state —
        # only true cooking verbs require a time/temp/visual cue here.
        is_action = bool(
            re.search(
                r"\b(bake|simmer|boil|roast|cook|fry|saut[eé]|whisking until|dehydrate)\b",
                first_clause,
            )
        )
        if is_action and not (has_time or has_temp or has_visual_cue):
            raise ValueError(f"action step {i} missing time/temp/doneness cue: {step!r}")
