"""Tests for generators.drafter — Protocol + factory + provider implementations.

The drafters delegate the actual API calls to existing functions
(`generate_from_seed` / `generate_from_seed_gemini`); these tests verify
the wiring (factory selection, env-var precedence, mockable client
injection), not the upstream API behavior — those are exercised by the
upstream functions' own tests.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from generators.anthropic_drafter import AnthropicDrafter
from generators.drafter import Drafter, get_drafter
from generators.gemini_drafter import GeminiDrafter
from generators.seeds import RecipeSeed


@pytest.fixture
def seed() -> RecipeSeed:
    return RecipeSeed(
        id="test-seed",
        title="Test Recipe",
        topic_keywords=["test", "recipe"],
        category="treat",
        prep_minutes=10,
        cook_minutes=20,
        yield_servings="serves 1 dog",
        tags=["test"],
        ingredients=["1 cup flour"],
        steps=["Mix.", "Bake at 350F for 20 min.", "Cool."],
        dog_safety_notes="Safe.",
        storage="fridge",
        portion_guide={"small": "1 piece"},
        source_attribution="test fixture",
    )


# ──────────────────────────────────────────────────────────────────────────
# Factory: get_drafter
# ──────────────────────────────────────────────────────────────────────────


class TestFactoryExplicitProvider:
    def test_anthropic(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "x")  # constructor needs it
        with patch("anthropic.Anthropic"):
            assert isinstance(get_drafter("anthropic"), AnthropicDrafter)

    def test_gemini(self) -> None:
        assert isinstance(get_drafter("gemini"), GeminiDrafter)

    def test_unknown_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown VOICE_PROVIDER"):
            get_drafter("openai")


class TestFactoryEnvSelection:
    def test_voice_provider_env_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VOICE_PROVIDER", "gemini")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "x")  # would auto-pick anthropic
        assert isinstance(get_drafter(), GeminiDrafter)

    def test_voice_provider_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VOICE_PROVIDER", "GEMINI")
        assert isinstance(get_drafter(), GeminiDrafter)


class TestFactoryAutoDetect:
    def test_anthropic_when_only_anthropic_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("VOICE_PROVIDER", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
        with patch("anthropic.Anthropic"):
            assert isinstance(get_drafter(), AnthropicDrafter)

    def test_gemini_when_only_gemini_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("VOICE_PROVIDER", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("GEMINI_API_KEY", "x")
        assert isinstance(get_drafter(), GeminiDrafter)

    def test_anthropic_when_both_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Anthropic preferred (matches prior behavior of _auto_voice_provider).
        monkeypatch.delenv("VOICE_PROVIDER", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
        monkeypatch.setenv("GEMINI_API_KEY", "y")
        with patch("anthropic.Anthropic"):
            assert isinstance(get_drafter(), AnthropicDrafter)

    def test_gemini_when_neither_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("VOICE_PROVIDER", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        assert isinstance(get_drafter(), GeminiDrafter)


# ──────────────────────────────────────────────────────────────────────────
# AnthropicDrafter
# ──────────────────────────────────────────────────────────────────────────


class TestAnthropicDrafter:
    def test_accepts_injected_client(self) -> None:
        client = MagicMock()
        drafter = AnthropicDrafter(client=client, model="test-model")
        assert drafter._client is client
        assert drafter._model == "test-model"

    def test_default_model_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RECIPE_MODEL", "claude-sonnet-4-9")
        drafter = AnthropicDrafter(client=MagicMock())
        assert drafter._model == "claude-sonnet-4-9"

    def test_default_model_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("RECIPE_MODEL", raising=False)
        drafter = AnthropicDrafter(client=MagicMock())
        assert drafter._model == "claude-sonnet-4-6"

    def test_draft_voice_delegates(self, seed: RecipeSeed, monkeypatch: pytest.MonkeyPatch) -> None:
        client = MagicMock()
        captured: dict[str, Any] = {}

        def fake_generate(
            topic: str, the_seed: RecipeSeed, *, client: object, model: str
        ) -> dict[str, Any]:
            captured["topic"] = topic
            captured["seed_id"] = the_seed.id
            captured["client"] = client
            captured["model"] = model
            return {"title": "Generated"}

        monkeypatch.setattr("generators.anthropic_drafter.generate_from_seed", fake_generate)
        drafter = AnthropicDrafter(client=client, model="claude-sonnet-4-6")
        result = drafter.draft_voice("test topic", seed)
        assert result == {"title": "Generated"}
        assert captured == {
            "topic": "test topic",
            "seed_id": "test-seed",
            "client": client,
            "model": "claude-sonnet-4-6",
        }


# ──────────────────────────────────────────────────────────────────────────
# GeminiDrafter
# ──────────────────────────────────────────────────────────────────────────


class TestGeminiDrafter:
    def test_draft_voice_delegates(self, seed: RecipeSeed, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}

        def fake_generate(topic: str, the_seed: RecipeSeed) -> dict[str, Any]:
            captured["topic"] = topic
            captured["seed_id"] = the_seed.id
            return {"title": "Gemini Generated"}

        monkeypatch.setattr("generators.gemini_drafter.generate_from_seed_gemini", fake_generate)
        drafter = GeminiDrafter()
        result = drafter.draft_voice("test topic", seed)
        assert result == {"title": "Gemini Generated"}
        assert captured == {"topic": "test topic", "seed_id": "test-seed"}


# ──────────────────────────────────────────────────────────────────────────
# Protocol conformance — both implementations satisfy `Drafter`
# ──────────────────────────────────────────────────────────────────────────


class TestProtocolConformance:
    def test_anthropic_drafter_is_a_drafter(self) -> None:
        drafter: Drafter = AnthropicDrafter(client=MagicMock())
        assert hasattr(drafter, "draft_voice")
        assert callable(drafter.draft_voice)

    def test_gemini_drafter_is_a_drafter(self) -> None:
        drafter: Drafter = GeminiDrafter()
        assert hasattr(drafter, "draft_voice")
        assert callable(drafter.draft_voice)
