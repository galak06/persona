"""Tests for lib.skill_loader — SKILL.md '## LLM Prompt' extraction/rendering.

Pure, tmp_path-driven (style of test_brand_templates.py): every case builds a
throwaway skills dir and passes ``skills_dir``/``brand`` explicitly, so the
lru_caches never see colliding keys and no test depends on $BRAND_DIR.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.skill_loader import (
    BrandVars,
    SkillPromptError,
    load_brand_vars,
    load_skill_prompt,
)

BRAND = BrandVars(
    name="Acme Dogs",
    name_lower="acme dogs",
    domain="acmedogs.example",
    mascot="Rex",
    persona="Rex's Human",
)

_FRONTMATTER = "---\nname: test-skill\ndescription: fixture skill\n---\n"

_CI_BRAND = Path(__file__).parent / "fixtures" / "ci_brand"


def _write_skill(tmp_path: Path, body: str, *, name: str = "test-skill") -> Path:
    """Write ``<tmp>/<name>/SKILL.md`` and return the skills dir."""
    skill_dir = tmp_path / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(body, encoding="utf-8")
    return tmp_path


# ------------------------------------------------------------- load_skill_prompt


def test_happy_path_extracts_renders_and_strips_everything_else(tmp_path: Path) -> None:
    body = _FRONTMATTER + (
        "# Test Skill\n\nchoreography text (never sent)\n\n"
        "## LLM Prompt\n\n"
        "<!-- MACHINE-READ SECTION. -->\n\n"
        "You are {{brand.persona}} of {{brand.name}} ({{brand.domain}}).\n"
        "Dog: {{brand.mascot}}. Slug: {{brand.name_lower}}.\n\n"
        "## Something After\n\nnot sent either\n"
    )
    out = load_skill_prompt("test-skill", skills_dir=_write_skill(tmp_path, body), brand=BRAND)
    assert out == (
        "You are Rex's Human of Acme Dogs (acmedogs.example).\nDog: Rex. Slug: acme dogs."
    )


def test_frontmatter_and_choreography_never_leak_into_prompt(tmp_path: Path) -> None:
    body = _FRONTMATTER + "intro choreography\n\n## LLM Prompt\n\nprompt body\n"
    out = load_skill_prompt("test-skill", skills_dir=_write_skill(tmp_path, body), brand=BRAND)
    assert out == "prompt body"
    assert "fixture skill" not in out
    assert "choreography" not in out


def test_named_section_selected_case_insensitively(tmp_path: Path) -> None:
    body = "## LLM Prompt\n\ndefault prompt\n\n## LLM Prompt: Short\n\nshort prompt\n"
    skills = _write_skill(tmp_path, body)
    assert load_skill_prompt("test-skill", skills_dir=skills, brand=BRAND) == "default prompt"
    assert (
        load_skill_prompt("test-skill", section="short", skills_dir=skills, brand=BRAND)
        == "short prompt"
    )


def test_tolerant_heading_spacing_and_trailing_colon(tmp_path: Path) -> None:
    body = "##  llm prompt :\n\ntolerant body\n"
    out = load_skill_prompt("test-skill", skills_dir=_write_skill(tmp_path, body), brand=BRAND)
    assert out == "tolerant body"


def test_h3_subheading_stays_inside_region(tmp_path: Path) -> None:
    body = "## LLM Prompt\n\nintro\n\n### Details\n\nmore\n\n## Next Section\n\noutside\n"
    out = load_skill_prompt("test-skill", skills_dir=_write_skill(tmp_path, body), brand=BRAND)
    assert out == "intro\n\n### Details\n\nmore"
    assert "outside" not in out


def test_multi_line_marker_comment_is_stripped(tmp_path: Path) -> None:
    body = "## LLM Prompt\n\n<!-- MACHINE-READ SECTION.\n     spans lines. -->\n\nreal text\n"
    out = load_skill_prompt("test-skill", skills_dir=_write_skill(tmp_path, body), brand=BRAND)
    assert out == "real text"


def test_missing_file_error_names_path(tmp_path: Path) -> None:
    with pytest.raises(SkillPromptError) as exc:
        load_skill_prompt("nope", skills_dir=tmp_path, brand=BRAND)
    assert str(tmp_path / "nope" / "SKILL.md") in str(exc.value)


def test_missing_section_error_names_path_and_section(tmp_path: Path) -> None:
    skills = _write_skill(tmp_path, "# Just Choreography\n\nno prompt here\n")
    with pytest.raises(SkillPromptError) as exc:
        load_skill_prompt("test-skill", skills_dir=skills, brand=BRAND)
    assert "SKILL.md" in str(exc.value)
    assert "## LLM Prompt" in str(exc.value)


def test_missing_named_section_error_names_it(tmp_path: Path) -> None:
    skills = _write_skill(tmp_path, "## LLM Prompt\n\nonly the default\n")
    with pytest.raises(SkillPromptError) as exc:
        load_skill_prompt("test-skill", section="variant-b", skills_dir=skills, brand=BRAND)
    assert "## LLM Prompt: variant-b" in str(exc.value)


def test_empty_section_is_an_error(tmp_path: Path) -> None:
    skills = _write_skill(tmp_path, "## LLM Prompt\n\n## Next\n\nbody\n")
    with pytest.raises(SkillPromptError, match="empty"):
        load_skill_prompt("test-skill", skills_dir=skills, brand=BRAND)


def test_marker_comment_only_section_is_an_error(tmp_path: Path) -> None:
    skills = _write_skill(tmp_path, "## LLM Prompt\n\n<!-- marker only -->\n")
    with pytest.raises(SkillPromptError, match="empty"):
        load_skill_prompt("test-skill", skills_dir=skills, brand=BRAND)


def test_unknown_placeholder_is_an_error_naming_it(tmp_path: Path) -> None:
    skills = _write_skill(tmp_path, "## LLM Prompt\n\nUse {{brand.tagline}} here\n")
    with pytest.raises(SkillPromptError, match="tagline"):
        load_skill_prompt("test-skill", skills_dir=skills, brand=BRAND)


def test_malformed_unclosed_placeholder_is_an_error(tmp_path: Path) -> None:
    skills = _write_skill(tmp_path, "## LLM Prompt\n\nBroken {{brand.name fragment\n")
    with pytest.raises(SkillPromptError, match="unrendered"):
        load_skill_prompt("test-skill", skills_dir=skills, brand=BRAND)


def test_empty_brand_value_for_referenced_placeholder_is_an_error(tmp_path: Path) -> None:
    """Fail-fast: a skill that references {{brand.mascot}} on a brand with no
    mascot must abort loudly, not render a hole into the system prompt."""
    no_mascot = BrandVars(
        name="Acme Dogs",
        name_lower="acme dogs",
        domain="acmedogs.example",
        mascot="",
        persona="Rex's Human",
    )
    skills = _write_skill(tmp_path, "## LLM Prompt\n\nDog: {{brand.mascot}}\n")
    with pytest.raises(SkillPromptError, match="mascot"):
        load_skill_prompt("test-skill", skills_dir=skills, brand=no_mascot)


def test_whitespace_in_placeholder_braces_is_tolerated(tmp_path: Path) -> None:
    skills = _write_skill(tmp_path, "## LLM Prompt\n\nHi from {{ brand.name }}!\n")
    out = load_skill_prompt("test-skill", skills_dir=skills, brand=BRAND)
    assert out == "Hi from Acme Dogs!"


# --------------------------------------------------------------- load_brand_vars


def test_load_brand_vars_maps_site_keys_and_strips_www(tmp_path: Path) -> None:
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "site": {
                    "name": "Dog Food & Fun",
                    "url": "https://www.dogfoodandfun.com",
                    "brand_persona": "Nalla's Dad",
                    "mascot_name": "Nalla",
                }
            }
        ),
        encoding="utf-8",
    )
    assert load_brand_vars(str(tmp_path)) == BrandVars(
        name="Dog Food & Fun",
        name_lower="dog food & fun",
        domain="dogfoodandfun.com",
        mascot="Nalla",
        persona="Nalla's Dad",
    )


def test_load_brand_vars_missing_config_error_names_path(tmp_path: Path) -> None:
    with pytest.raises(SkillPromptError) as exc:
        load_brand_vars(str(tmp_path / "no-such-brand"))
    assert "config.json" in str(exc.value)


def test_load_brand_vars_from_real_ci_brand_fixture(tmp_path: Path) -> None:
    brand = load_brand_vars(str(_CI_BRAND))
    assert brand.name == "Your Brand Name"
    assert brand.name_lower == "your brand name"
    assert brand.domain == "yourbrand.com"
    assert brand.mascot == "Your Mascot"
    assert brand.persona == "Your Persona Name"
    # And rendering works against the fixture-derived vars end-to-end.
    skills = _write_skill(
        tmp_path, "## LLM Prompt\n\n{{brand.persona}} / {{brand.mascot}} / {{brand.domain}}\n"
    )
    out = load_skill_prompt("test-skill", skills_dir=skills, brand=brand)
    assert out == "Your Persona Name / Your Mascot / yourbrand.com"


# --------------------------------------------- the real ig-comment skill file


def test_real_ig_comment_skill_renders_against_ci_brand() -> None:
    """Integration guard: the shipped ig-comment SKILL.md loads from the real
    engine skills dir and renders cleanly — no leftover placeholders, no
    marker comment, and no JSON envelope (that stays Python-side)."""
    out = load_skill_prompt("ig-comment", brand=load_brand_vars(str(_CI_BRAND)))
    assert "Your Persona Name" in out
    assert "yourbrand.com" in out
    assert "BRAND VOICE" in out
    assert "Decline (engage=false) if:" in out
    assert "{{brand." not in out
    assert "MACHINE-READ" not in out
    assert "Respond with ONLY a JSON object" not in out
