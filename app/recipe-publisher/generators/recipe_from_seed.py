"""Seed-grounded recipe generation.

The LLM receives a vetted seed (ingredients + steps are FROZEN) and is only
permitted to write the voice fields around it: intro, Nalla's verdict, FAQ,
meta description, image brief, and IG caption. Ingredients, steps, prep/cook
times, yield, and tags are copied from the seed verbatim — never generated.
"""

from __future__ import annotations

import logging
from pathlib import Path

from anthropic import Anthropic

from .seeds import RecipeSeed, seed_to_body_sections
from .text_normalize import unwrap_paragraphs

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


# Narrow tool schema: LLM only writes voice fields. No ingredients/steps here.
VOICE_TOOL = {
    "name": "submit_voice",
    "description": (
        "Submit the voice fields to wrap around a frozen seed recipe. "
        "Do not attempt to rewrite the seed's ingredients or steps — those are fixed."
    ),
    "input_schema": {
        "type": "object",
        "required": [
            "intro",
            "nallas_verdict",
            "faq",
            "meta_description",
            "image_brief",
            "ig_caption",
        ],
        "properties": {
            "title": {
                "type": "string",
                "description": (
                    "Optional polished title. Keep close to the seed's title — do not "
                    "add 'Nalla's Dad' or brand marketing. If omitted, seed title is used."
                ),
            },
            "intro": {
                "type": "string",
                "description": (
                    "2-3 sentence warm intro. First-person. Nalla can appear if it lands "
                    "naturally. No H1 (the title renders as H1). No medical claims."
                ),
            },
            "nallas_verdict": {
                "type": "string",
                "description": (
                    "One short paragraph on Nalla's real reaction. Specific and honest, "
                    "even if mixed. No medical claims."
                ),
            },
            "faq": {
                "type": "array",
                "minItems": 2,
                "maxItems": 4,
                "items": {
                    "type": "object",
                    "required": ["question", "answer"],
                    "properties": {
                        "question": {"type": "string"},
                        "answer": {"type": "string"},
                    },
                },
                "description": (
                    "2-4 FAQ pairs optimized for Google 'People Also Ask' / "
                    "featured-snippet capture. Prefer full natural-language question "
                    "phrasings a dog owner would type: 'Can dogs eat sweet potato?', "
                    "'How much pumpkin is safe for a medium dog?', 'Is peanut butter "
                    "safe for puppies?'. Each answer should open with a direct "
                    "40-60 word response (the snippet-friendly lead) before adding "
                    "any nuance. Cover substitutions, storage, and portion size for "
                    "specific dog sizes. Avoid medical framing."
                ),
            },
            "meta_description": {
                "type": "string",
                "description": (
                    "SureRank page_description. 150-160 chars. Primary keyword + a "
                    "concrete reason to click. Complete sentence, not a teaser question."
                ),
            },
            "image_brief": {
                "type": "string",
                "description": (
                    "One-paragraph brief for the image generator. Candid, "
                    "everyday-life feel in a real home kitchen with natural "
                    "light; treats shown in a dog's bowl or on a board/parchment "
                    "as served to a dog. NOT staged studio photography, NOT a "
                    "human meal: no plates, no place settings, no forks/knives. "
                    "A dog casually in frame is welcome — it is Nalla, a "
                    "medium-sized fluffy shepherd mix (tan-and-black coat, alert "
                    "ears), not a golden retriever. No text."
                ),
            },
            "ig_caption": {
                "type": "string",
                "description": (
                    "IG caption with STRICT structure (in order, no reorder): "
                    "(1) Hook — first 125 chars, stands alone, no hashtag/emoji/'POV:' "
                    "at the start. "
                    "(2) Three bullet lines each starting with the '•' character — "
                    "concrete wins: time, macros, ingredient count, or specific "
                    "behavior. Not opinions. "
                    "(3) One comment-gated CTA line with the keyword in UPPERCASE, "
                    "e.g. 'Comment PUPSICLES and I'll DM you the link — hear the full song + get the printable card!' "
                    "The keyword MUST be a single specific word from the recipe name (e.g. BACON, BISCUITS, PUPSICLES, CHEWS, JERKY). "
                    "NEVER use generic words like RECIPE, FOOD, TREAT, CARD, LINK. "
                    "(4) One specific question — not 'what do you think?'. "
                    "(5) Blank line, then 8-12 hashtags mixing broad/niche/branded; "
                    "must include #nallasdad and #persona."
                ),
            },
        },
    },
}


def generate_from_seed(
    topic: str,
    seed: RecipeSeed,
    *,
    client: Anthropic,
    model: str,
    extra_instructions: str | None = None,
) -> dict:
    """Call Claude for voice fields only. Returns the raw tool input dict.

    Ingredients, steps, prep/cook times, yield, and tags are the caller's
    responsibility to copy from the seed — this function does not touch them.
    """
    sections = seed_to_body_sections(seed)
    system_prompt = (_PROMPTS_DIR / "recipe_system.md").read_text()

    user_msg = _build_user_message(topic, seed, sections)
    if extra_instructions:
        user_msg = f"{user_msg}\n\nADDITIONAL CONSTRAINTS:\n{extra_instructions}"

    logger.info(
        "generating voice fields for topic=%r seed=%s model=%s",
        topic, seed.id, model,
    )
    from anthropic.types import ToolUseBlock

    response = client.messages.create(
        model=model,
        max_tokens=2048,
        system=system_prompt,
        tools=[VOICE_TOOL],  # type: ignore[arg-type]
        tool_choice={"type": "tool", "name": "submit_voice"},
        messages=[{"role": "user", "content": user_msg}],
    )

    tool_block = next(
        (b for b in response.content if isinstance(b, ToolUseBlock)), None
    )
    if tool_block is None:
        raise RuntimeError(
            f"model did not call submit_voice tool; content={response.content!r}"
        )
    return tool_block.input


def _build_user_message(topic: str, seed: RecipeSeed, sections: dict[str, str]) -> str:
    return (
        f"Topic: {topic}\n\n"
        f"Use this vetted seed recipe as the factual basis. Ingredients and steps "
        f"are FROZEN — do not rewrite them, do not add ingredients, do not change "
        f"quantities or times. Your job is to write the voice fields (intro, "
        f"Nalla's verdict, FAQ, meta description, image brief, IG caption) around "
        f"this seed.\n\n"
        f"Seed ID: {seed.id}\n"
        f"Seed title: {seed.title}\n"
        f"Category: {seed.category}\n"
        f"Yield: {seed.yield_servings}\n"
        f"Prep: {seed.prep_minutes} min • Cook: {seed.cook_minutes} min\n\n"
        f"Dog-safety notes (mention in FAQ if natural, do not repeat verbatim in intro):\n"
        f"{seed.dog_safety_notes}\n\n"
        f"Ingredients (FROZEN):\n{sections['ingredients']}\n\n"
        f"Instructions (FROZEN):\n{sections['instructions']}\n\n"
        f"Submit the voice fields via the submit_voice tool. Do not respond with prose."
    )


def assemble_body_markdown(
    voice: dict,
    seed: RecipeSeed,
) -> str:
    """Stitch seed sections + voice fields into the final body_markdown."""
    s = seed_to_body_sections(seed)
    # H3 headers (not bold text) give Google a clear question anchor to pull into
    # 'People Also Ask' and featured-snippet results. Pairs with the FAQPage
    # JSON-LD emitted by the WordPress publisher.
    faq_md = "\n\n".join(
        f"### {p['question']}\n\n{p['answer']}" for p in voice["faq"]
    )
    assembled = (
        f"{voice['intro']}\n\n"
        f"## Ingredients\n\n{s['ingredients']}\n\n"
        f"## Instructions\n\n{s['instructions']}\n\n"
        f"## Nalla's verdict\n\n{voice['nallas_verdict']}\n\n"
        f"## FAQ\n\n{faq_md}\n\n"
        f"## Portion guide\n\n{s['portion_guide']}\n\n"
        f"## Storage\n\n{s['storage']}\n\n"
        f"## Safety note\n\n{s['safety_note']}\n"
    )
    return unwrap_paragraphs(assembled)
