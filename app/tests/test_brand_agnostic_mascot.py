"""No brand may be assumed to have a dog.

This is a multi-brand engine -- `CLAUDE.md`: "All brand-specific values (site
URL, persona name, mascot, niche, target audience) live [in
`$BRAND_DIR/config.json`] -- never hardcoded here." The reference-image
feature broke that in the worst possible place: not in a label an operator
could ignore, but in the PROMPTS sent to the image and vision models, which
said "person and dog", "any other animal", "a specific dog named X". A brand
whose mascot is a cat, a cartoon character or a delivery van was being told
by the engine that it owned a dog.

Two things are pinned here, and nowhere else in one place:

* `lib.crew.mascot.read_mascot` -- the ONE reader of `site.mascot_name` and
  the new, optional `site.mascot_kind`, degrading to the empty mascot rather
  than raising, because a cosmetic identity field may never fail a cron flow.
* the vision prompt naming a species only when the brand named it first.

The clause half lives in `tests/test_reference_clauses.py`. "Rusty, the
delivery van" is not a joke: it is the cheapest proof that nothing in the
prompt path is reaching for an animal noun on its own.
"""
# ruff: noqa: S101

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from lib.crew.mascot import NO_MASCOT, Mascot, read_mascot
from lib.crew.reference_vision import analyze_for_brand, analyze_image, vision_model

_CATEGORIES = ["Eating", "Walking", "Sleeping"]


def _url() -> str:
    return (
        f"https://generativelanguage.googleapis.com/v1beta/models/{vision_model()}:generateContent"
    )


def _answer(category: str = "Walking") -> httpx.Response:
    payload = {"description": "a photo", "category": category, "shows_mascot": False}
    return httpx.Response(
        200, json={"candidates": [{"content": {"parts": [{"text": json.dumps(payload)}]}}]}
    )


def _prompt_sent(route: respx.Route) -> str:
    return str(json.loads(route.calls[0].request.content)["contents"][0]["parts"][1]["text"])


@pytest.fixture(autouse=True)
def _gemini_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.delenv("GEMINI_VISION_MODEL", raising=False)


# ── the config reader ────────────────────────────────────────────────────────


def test_both_fields_are_read_and_trimmed(tmp_path: Path) -> None:
    (tmp_path / "config.json").write_text(
        json.dumps({"site": {"mascot_name": "  Rusty ", "mascot_kind": " delivery van "}})
    )

    assert read_mascot(tmp_path) == Mascot(name="Rusty", kind="delivery van")


def test_a_kind_is_optional(tmp_path: Path) -> None:
    """The field is NEW, so every existing brand config lacks it -- and must
    keep working, described only as "the brand's mascot"."""
    (tmp_path / "config.json").write_text(json.dumps({"site": {"mascot_name": "Nalla"}}))

    mascot = read_mascot(tmp_path)

    assert mascot.name == "Nalla"
    assert mascot.kind == ""


@pytest.mark.parametrize(
    "config",
    [
        "not json at all",
        json.dumps({"site": {}}),
        json.dumps({"site": "wrong shape"}),
        json.dumps({"site": {"mascot_name": None, "mascot_kind": None}}),
        "{}",
        "[]",
    ],
)
def test_every_malformed_config_yields_the_empty_mascot(tmp_path: Path, config: str) -> None:
    (tmp_path / "config.json").write_text(config)

    assert read_mascot(tmp_path) == NO_MASCOT


def test_no_config_at_all_yields_the_empty_mascot(tmp_path: Path) -> None:
    assert read_mascot(tmp_path) == NO_MASCOT


# ── the vision prompt may not invent a species ───────────────────────────────


@respx.mock
def test_the_prompt_never_guesses_what_the_mascot_is() -> None:
    """It used to ask about "a specific dog named X" and rule out "any other
    animal" -- a lie told to every brand whose mascot is not one."""
    route = respx.post(_url()).mock(return_value=_answer())

    assert (
        analyze_image(b"png", "image/png", existing_categories=_CATEGORIES, mascot_name="Rusty")
        is not None
    )

    prompt = _prompt_sent(route)
    assert "dog" not in prompt.lower()
    assert "animal" not in prompt.lower()
    assert '"Rusty"' in prompt


@respx.mock
def test_a_configured_kind_reaches_the_prompt_in_the_brands_own_words() -> None:
    """`site.mascot_kind` is free text and the only route a species has into
    this prompt -- here a mascot that is not alive at all."""
    route = respx.post(_url()).mock(return_value=_answer())

    analysis = analyze_image(
        b"png",
        "image/png",
        existing_categories=_CATEGORIES,
        mascot_name="Rusty",
        mascot_kind="delivery van",
    )

    assert analysis is not None
    prompt = _prompt_sent(route)
    assert 'the specific delivery van named "Rusty"' in prompt
    # And the stand-in it must rule out is described in that same word.
    assert "generic or stock delivery van" in prompt
    assert "dog" not in prompt.lower()


@respx.mock
def test_an_unconfigured_kind_asks_in_general_terms() -> None:
    route = respx.post(_url()).mock(return_value=_answer())

    assert (
        analyze_image(b"png", "image/png", existing_categories=_CATEGORIES, mascot_name="")
        is not None
    )

    prompt = _prompt_sent(route)
    assert "the specific one this brand uses" in prompt
    assert "generic or stock look-alike" in prompt


@respx.mock
def test_analyze_for_brand_passes_the_configured_kind_through(tmp_path: Path) -> None:
    """The brand's own word has to survive the seam every upload route calls --
    it is the only thing that lets the prompt be specific without guessing."""
    (tmp_path / "config.json").write_text(
        json.dumps({"site": {"mascot_name": "Nalla", "mascot_kind": "dog"}})
    )
    route = respx.post(_url()).mock(return_value=_answer("Eating"))

    assert analyze_for_brand(tmp_path, b"png", "image/png") is not None

    assert 'the specific dog named "Nalla"' in _prompt_sent(route)
