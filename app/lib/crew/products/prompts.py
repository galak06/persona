"""Task-description ("prompt") builder for the product-selector agent.

Mirrors `lib.crew.writer.prompts`'s role for the strategist/writer pair: one
long, formatted prompt string per stage, split out of `agent.py` for
file-size discipline. Takes the strategist's `ContentBrief` (the post plan
is judged, not the finished HTML) plus a pre-rendered candidate-catalog
text block, so this module gathers no data itself.

Embeds the `ProductSelection` JSON schema directly in the prompt via this
package's `_json_output_instructions` copy (`lib.crew.products.agent`) --
the manual-parsing contract `lib.crew.products.execute` relies on (see
`lib.crew.writer.agent`'s module docstring for why not `output_pydantic`).
"""

from __future__ import annotations

from lib.crew.products.agent import _json_output_instructions
from lib.crew.products.models import ProductSelection
from lib.crew.writer.models import ContentBrief


def build_selector_task_description(
    *, brief: ContentBrief, candidates_text: str, max_products: int
) -> str:
    """The selector agent's full prompt: one post plan + the real catalog
    -> a `ProductSelection` of at most `max_products` genuine fits.

    `candidates_text` is a pre-rendered list of candidate products, one per
    line in `key | display | category | notes` form -- the caller owns
    catalog loading/formatting; this builder only embeds it.
    """
    outline_lines = "\n".join(
        f"- [{section.level}] {section.heading} -- {section.notes}" for section in brief.outline
    )
    return f"""You are the affiliate product curator for a dog-lifestyle blog, choosing which \
products (if any) to feature in ONE planned blog post.

## The post plan
Title: {brief.suggested_title}
Primary keyword: {brief.primary_keyword}
Secondary keywords: {", ".join(brief.secondary_keywords) or "(none)"}

Section outline:
{outline_lines or "(no outline provided)"}

## Candidate products (each line is: key | display | category | notes)
{candidates_text}

## Your job
Select AT MOST {max_products} products from the candidate list above that a reader of THIS \
specific post would genuinely want -- products that directly serve the post's topic and its \
reader's intent, not merely the same broad niche. Selecting FEWER than {max_products} is \
correct whenever fewer genuinely fit, and selecting ZERO (an empty products list) is a valid, \
correct answer when nothing fits -- never pad the selection.

{max_products} is a ceiling, NOT a target. Two products that truly belong beat four that \
merely relate to dogs. Before including a pick, apply this test: would a reader who came for \
THIS post's exact topic reach for this product because of what they just read? If the honest \
answer is "not really, but it's dog gear", LEAVE IT OUT. Two near-identical products (e.g. two \
of the same kind of kit) should never both appear -- pick the better one. A product whose use \
case belongs to a different activity than the post describes is a wrong answer even when it \
suits dogs generally.

For each pick, copy its key \
EXACTLY as listed (character-for-character) -- NEVER invent a key that isn't in the list above \
-- and give one short sentence explaining why a reader of this post would want it. Never \
mention prices.

## Output format
{_json_output_instructions(ProductSelection)}
"""
