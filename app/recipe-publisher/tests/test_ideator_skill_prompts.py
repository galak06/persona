"""The recipe ideator's two system prompts live in the content-ideator SKILL.md.

Since Slice 8 neither ``ideator/research.py`` nor ``ideator/enricher.py``
carries a ``_SYSTEM_PROMPT`` constant: both load their SYSTEM prompt from the
content-ideator skill's ``## LLM Prompt: research`` / ``## LLM Prompt: enrich``
sections via ``lib.skill_loader``. That makes SKILL.md the single source of
truth for the ideation rules — and an edit there can now silently change what
the model is asked for.

These tests load the REAL skill file, render it against the ci_brand fixture
config, and assert:

  * both sections render with no ``{{`` residue and no hardcoded brand words
  * each still carries the ideation/safety/output rules it owned as a Python
    constant (research: trend signals, no-URL evidence rule, category list;
    enrich: dog-safety rules + the strict seed field list)
  * the two sections are genuinely different prompts
  * the allowed-category set is NOT duplicated in the enrich markdown — it
    stays the ``schema.ALLOWED_CATEGORIES`` constant the validator enforces

plus a payload-capture test per module (monkeypatched httpx) proving the
rendered skill text is what actually reaches Gemini's ``systemInstruction``,
and that the enricher appends its ``ALLOWED CATEGORIES:`` suffix there.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from ideator import enricher, research
from ideator.research import Candidate
from ideator.schema import ALLOWED_CATEGORIES

import skill_loader

# <engine>/tests/fixtures/ci_brand — the generic placeholder brand CI uses.
_CI_BRAND = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "ci_brand"

_SECTIONS = ("research", "enrich")

# Rules each section owned as a Python constant before the move.
_RESEARCH_RULES = (
    "trending RIGHT NOW",
    "NEVER suggest recipes containing xylitol",
    "treats-baked, treats-frozen, treats-no-bake, treats-dehydrated",
    "Must be DIFFERENT from any title in the EXCLUDED list",
    "DO NOT paste raw",
    "seasonal_relevance: 1 (off-season) to 10 (perfect for current month).",
    "Mix categories. Don't return 5 baked treats — diversify.",
)
_ENRICH_RULES = (
    "factual backbone of a recipe",
    'NEVER include xylitol (always note "xylitol-free" for peanut butter).',
    "Garlic and onion: avoid entirely OR very small cooked amounts ONLY if",
    "id: lowercase-kebab-case, ≤40 chars",
    "ingredients: 4-10 items. Each item with measurement AND grams in parens",
    "portion_guide: object with small / medium / large keys",
    "source_attribution: 1-2 sentences citing GENERIC sources",
)

# The literals that were baked into the Python prompts and are now placeholders.
_DEPARAMETRIZED = ("your-brand.com", "Nalla")


def _clear_caches() -> None:
    """Both loaders and both call-site wrappers are ``lru_cache``d."""
    skill_loader.load_brand_vars.cache_clear()
    skill_loader.load_skill_prompt.cache_clear()
    research._system_prompt.cache_clear()
    enricher._system_prompt.cache_clear()


@pytest.fixture
def ci_brand(monkeypatch: pytest.MonkeyPatch) -> Iterator[skill_loader.BrandVars]:
    """Point ``$BRAND_DIR`` at the fixture brand, with the caches cleared."""
    monkeypatch.setenv("BRAND_DIR", str(_CI_BRAND))
    _clear_caches()
    yield skill_loader.load_brand_vars(str(_CI_BRAND))
    _clear_caches()


def _rendered(section: str) -> str:
    return skill_loader.load_skill_prompt(
        "content-ideator",
        section=section,
        skills_dir=skill_loader.default_skills_dir(),
        brand=skill_loader.load_brand_vars(str(_CI_BRAND)),
    )


@pytest.mark.parametrize("section", _SECTIONS)
def test_section_renders_without_placeholder_residue(section: str) -> None:
    """A surviving ``{{`` would be sent to the model verbatim."""
    text = _rendered(section)
    assert "{{" not in text
    assert "}}" not in text
    assert len(text) > 500


@pytest.mark.parametrize("section", _SECTIONS)
def test_section_has_no_hardcoded_brand_words(
    section: str, ci_brand: skill_loader.BrandVars
) -> None:
    """The site and persona/mascot names must come from the brand config."""
    text = _rendered(section)
    for literal in _DEPARAMETRIZED:
        assert literal not in text, (
            f"{section}: {literal!r} should be a {{{{brand.*}}}} placeholder"
        )
    assert ci_brand.domain in text


def test_research_section_keeps_its_ideation_rules(ci_brand: skill_loader.BrandVars) -> None:
    text = _rendered("research")
    assert text.startswith(f"You research recipe ideas for {ci_brand.domain}")
    assert ci_brand.persona in text
    for rule in _RESEARCH_RULES:
        assert rule in text, f"research prompt lost: {rule!r}"


def test_enrich_section_keeps_its_seed_rules(ci_brand: skill_loader.BrandVars) -> None:
    text = _rendered("enrich")
    assert text.startswith(f"You generate structured recipe seeds for {ci_brand.domain}")
    assert ci_brand.mascot in text
    for rule in _ENRICH_RULES:
        assert rule in text, f"enrich prompt lost: {rule!r}"


def test_the_two_sections_are_different_prompts() -> None:
    assert _rendered("research") != _rendered("enrich")


def test_enrich_section_does_not_duplicate_the_category_constant() -> None:
    """``ALLOWED_CATEGORIES`` stays single-sourced in ``ideator/schema.py``."""
    text = _rendered("enrich")
    for category in ALLOWED_CATEGORIES:
        assert category not in text, f"enrich markdown hardcodes category {category!r}"
    assert "ALLOWED CATEGORIES listed at the end of this prompt" in text


@dataclass
class _FakeResponse:
    payload: dict[str, Any]
    status_code: int = 200
    text: str = ""

    def json(self) -> dict[str, Any]:
        return self.payload


@dataclass
class _Capture:
    calls: list[dict[str, Any]] = field(default_factory=list)

    def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append({"url": url, **kwargs})
        return self.response

    response: _FakeResponse = field(
        default_factory=lambda: _FakeResponse({"candidates": []}),
    )

    @property
    def system_text(self) -> str:
        body = self.calls[0]["json"]
        parts = body["systemInstruction"]["parts"]
        return "".join(p["text"] for p in parts)


def _gemini_text_response(text: str) -> _FakeResponse:
    return _FakeResponse({"candidates": [{"content": {"parts": [{"text": text}]}}]})


def _install_capture(
    monkeypatch: pytest.MonkeyPatch, module: Any, response: _FakeResponse
) -> _Capture:
    capture = _Capture(response=response)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(module.httpx, "post", capture.post)
    return capture


def test_research_call_sends_the_skill_section_as_system_prompt(
    monkeypatch: pytest.MonkeyPatch, ci_brand: skill_loader.BrandVars
) -> None:
    body = json.dumps(
        [
            {
                "title": "Frozen Watermelon Cubes",
                "category": "treats-frozen",
                "why_now": "heat wave searches spiking",
                "evidence": "AKC summer-safety guide",
                "seasonal_relevance": 9,
                "search_demand_estimate": "high",
            }
        ]
    )
    capture = _install_capture(monkeypatch, research, _gemini_text_response(body))

    candidates = research.research_candidates(["Pumpkin Oat Biscuits"], n=1)

    assert [c.title for c in candidates] == ["Frozen Watermelon Cubes"]
    assert capture.system_text == _rendered("research")
    assert ci_brand.domain in capture.system_text
    # The per-run context stays Python-side, in the USER turn only.
    user_text = capture.calls[0]["json"]["contents"][0]["parts"][0]["text"]
    assert "Pumpkin Oat Biscuits" in user_text
    assert "Pumpkin Oat Biscuits" not in capture.system_text


def test_enrich_call_appends_allowed_categories_to_the_skill_section(
    monkeypatch: pytest.MonkeyPatch, ci_brand: skill_loader.BrandVars
) -> None:
    capture = _install_capture(
        monkeypatch, enricher, _gemini_text_response('{"id": "frozen-watermelon-cubes"}')
    )
    candidate = Candidate(
        title="Frozen Watermelon Cubes",
        category="treats-frozen",
        why_now="heat wave searches spiking",
        evidence="AKC summer-safety guide",
        seasonal_relevance=9,
        search_demand_estimate="high",
    )

    seed = enricher.enrich_to_seed(candidate)

    assert seed == {"id": "frozen-watermelon-cubes"}
    system = capture.system_text
    section = _rendered("enrich")
    assert system.startswith(section)
    assert system == f"{section}\n\nALLOWED CATEGORIES: {sorted(ALLOWED_CATEGORIES)}"
    suffix = system.rsplit("ALLOWED CATEGORIES: ", 1)[1]
    for category in ALLOWED_CATEGORIES:
        assert f"'{category}'" in suffix
    assert ci_brand.domain in system
