"""Task-description ("prompt") builders for the strategist and writer agents.

Split out of `lib.crew.writer.context` purely for file-size discipline --
these two functions are long, formatted prompt strings, not data-gathering
logic. Mirrors `lib.crew.context.build_task_description`'s role for the
scout, one function per pipeline stage.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from lib.crew.writer.context import internal_link_candidates_json
from lib.crew.writer.models import ContentBrief, InternalLinkCandidate

_BLUEPRINT_SPEC = """\
## Blueprint (follow this structure exactly, as WordPress-ready HTML)
1. Byline: "By {persona} / {today}"
2. Affiliate disclosure -- immediately after the byline. Use this EXACT sentence, verbatim: \
"{disclosure}"
3. Hook -- an 80-150 word mascot-story opener (a specific, relatable scenario), matching the \
brief's mascot_angle
4. Problem statement -- why this topic matters
5. Core content -- H2/H3 sections per the brief's outline, data-heavy (at least 3 concrete \
numbers/data points per major section), engineer-metaphor framing
6. Product comparison table -- ONLY if the catalog below has products genuinely relevant to \
this topic; a markdown-in-HTML <table> with name/price-range/key-spec/pros/cons/affiliate link \
columns, using [AFFILIATE:key] placeholders for links. If nothing in the catalog fits, skip \
this section and the "Our Pick" section entirely -- do not force irrelevant products in.
7. FAQ -- one <h3> per brief faq_question, each followed by a real answer paragraph; also \
return these as faq_pairs in your structured output (used to generate schema.org FAQPage \
markup separately -- do not add the JSON-LD yourself)
8. Related reading -- a bulleted list of internal links, using ONLY the brief's real \
internal_link_candidates (never invent a URL)
9. "Our Pick" -- closing recommendation with an [AFFILIATE:key] link, ONLY if step 6 produced \
a product section

Target 2,500-3,500 words for body_html. Report the actual word_count of what you wrote -- do \
not just restate the target.
"""

_HARD_RULES = """\
## Hard rules
- Never claim implied vet/nutritionist/doctor credentials, a disease cure/treatment, dosage/\
prescription advice, or an absolute health-efficacy claim ("guaranteed", "100% safe", "cures").
- Never state a specific fact about the brand's mascot (diet, products owned, a specific \
anecdote) that isn't grounded in the mascot facts below -- if a detail isn't listed there, \
keep mascot mentions general ("in our experience", "we've noticed").
- Never invent a [AFFILIATE:key] -- only use keys listed in the product catalog below.
- Never invent an internal link URL -- only use the brief's internal_link_candidates.
- Include the current year ({year}) in the title.
"""


def _reference_category_section(categories: Sequence[str]) -> str:
    """The `reference_category` instructions -- empty string when the brand has
    no reference-photo library, so the strategist prompt stays byte-identical
    to what it was before this field existed."""
    if not categories:
        return ""
    listed = "\n".join(f"  - {label}" for label in categories)
    return f"""
## Reference-photo collection (`reference_category`)
The post's hero image is generated with a real photo of the brand's actual dog as its \
visual reference (the result is still a generated image, not a photograph of a real \
moment -- the title rule in step 1 still holds). The brand keeps several collections of \
those photos, one collection per kind of scene:
{listed}
Set `reference_category` to the ONE collection whose scenes best match the hero image \
this post calls for, copied verbatim from the list above. Pick it from the topic and \
your mascot_angle: a feeding post wants a feeding reference, a trail post wants an \
outdoor one. If none of them fits, use "general".
"""


def build_strategist_task_description(
    *,
    idea: dict[str, Any],
    identity: str,
    voice: str,
    mascot_facts: str,
    link_candidates: list[InternalLinkCandidate],
    year: int,
    reference_categories: Sequence[str] = (),
) -> str:
    """The strategist agent's full prompt: one content idea -> a `ContentBrief`."""
    return f"""You are turning ONE approved content idea into a structured content brief.

## Brand context
{identity}

## Brand voice (write the brief's angle in this voice; do not draft the post itself)
{voice or "(no voice guide available)"}

## Mascot facts (ONLY true things the mascot_angle may reference as fact)
{mascot_facts or "(no mascot facts file available -- keep mascot_angle general)"}

## The approved idea
Topic: {idea.get("topic", "")}
Target keyword: {idea.get("target_keyword", "")}
Category: {idea.get("category", "")}
Reasoning (why this idea was surfaced): {idea.get("nalla_context", "")}

## Real internal-link candidates (choose from these ONLY -- never invent a URL)
{internal_link_candidates_json(link_candidates)}

## Your job
1. Propose a suggested_title including the year {year}. The hero image is AI-generated from a \
short text brief -- it is NOT a real photo of the brand's mascot or persona, and nothing \
verifies it actually depicts them. Never phrase the title as if it documents a specific \
personal/photographed moment (e.g. "Before Nalla and I Ran Properly", "How We Learned X \
Together") -- that claims the accompanying photo shows something it doesn't. Keep the title \
generic and topic/benefit-driven (e.g. "Canicross for Beginners: 5 Gear Mistakes to Avoid"); \
first-person mascot narrative belongs in the body's hook (a text claim, not a visual one), never \
in the title. Keep the title to 60 characters or fewer, INCLUDING the year -- Google truncates \
longer search-result titles (~555-600px, roughly 60 characters), so anything past that limit \
never displays in search results at all.
2. Propose an outline: 5-9 H2/H3 sections with a 1-line note on what each covers, following the \
brand's blueprint (hook -> problem statement -> core content -> [product comparison if the \
topic warrants it] -> FAQ -> related reading -> our pick).
3. Set primary_keyword to the idea's target keyword (or a close, better-targeted variant) and \
propose 3-6 secondary_keywords.
4. Choose 3-6 internal_link_candidates from the real list above whose topic is genuinely \
relevant to this idea -- if fewer than 3 are relevant, return only the relevant ones.
5. Propose 3-6 faq_questions this post should answer -- real questions a reader would search \
for, not generic filler.
6. Write mascot_angle: 2-4 sentences on how the brand's real voice/mascot fits THIS specific \
topic, grounded in the idea's own reasoning above and the mascot facts above -- never a generic \
statement that could apply to any topic.
{_reference_category_section(reference_categories)}"""


def build_writer_task_description(
    *,
    brief: ContentBrief,
    identity: str,
    voice: str,
    mascot_facts: str,
    catalog_text: str,
    disclosure_text: str,
    persona: str,
    today: str,
    year: int,
) -> str:
    """The writer agent's full prompt: a `ContentBrief` -> a `WrittenPost`."""
    outline_lines = "\n".join(
        f"- [{section.level}] {section.heading} -- {section.notes}" for section in brief.outline
    )
    link_lines = "\n".join(f"- {c.title}: {c.url}" for c in brief.internal_link_candidates)
    faq_lines = "\n".join(f"- {q}" for q in brief.faq_questions)

    return f"""You are writing ONE full blog post from the brief below.

## Brand context
{identity}

## Brand voice (write in this voice throughout)
{voice or "(no voice guide available)"}

## Mascot facts (ONLY true things you may state as fact about the mascot)
{mascot_facts or "(no mascot facts file available -- keep mascot mentions general)"}

## Brief
Title: {brief.suggested_title}
Primary keyword: {brief.primary_keyword}
Secondary keywords: {", ".join(brief.secondary_keywords) or "(none)"}
Mascot angle: {brief.mascot_angle}

Outline:
{outline_lines or "(no outline provided -- use the blueprint below directly)"}

Internal link candidates (use ONLY these, never invent a URL):
{link_lines or "(none available -- omit the related-reading section)"}

FAQ questions to answer:
{faq_lines or "(none provided -- propose 3-6 yourself, grounded in the topic)"}

## Product catalog (use ONLY these [AFFILIATE:key] keys, or none)
{catalog_text}

{_BLUEPRINT_SPEC.format(persona=persona, today=today, disclosure=disclosure_text)}
{_HARD_RULES.format(year=year)}
"""
