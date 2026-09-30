"""Shared world for the spotlight compose tests.

Built the way `_socialpost_retry_fakes.py` builds the hook-image retry's world,
and for the same reason: every collaborator outside the module under test is
RECORDED rather than mocked away, so a test can assert what the run asked for
instead of only what it returned. The reference library, the brand config, the
mascot facts and the composed image are real files on `tmp_path`, so the file
lifecycle a failed composition must respect is real too.

Faked: the writer crew (`execute_social_post_crew`), the image model
(`hook_render.generate_wp_image` -- one layer below `render_from_reference`, so
the anchoring logic still runs), the overlay pass, WordPress, the product pool
and the four `derivatives_db` calls. Nothing here talks to a database, an LLM,
an image model, WordPress or a publisher.

A plain builder rather than a fixture, matching `_socialpost_retry_fakes.py`: a
pytest fixture imported into a test module shadows the parameter name that
requests it, which reads as a redefinition to any linter.
"""
# ruff: noqa: S101

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from lib.affiliate_resolver import ProductEntry
from lib.crew.socialpost import hook_render
from lib.crew.socialpost.models import SocialPostPlan
from lib.crew.spotlight import compose, inputs
from lib.crew.spotlight.result import SpotlightResult
from tests._reference_library_fakes import write_library

DERIVATIVE_ID = "deriv-1"
IDEA_ID = "idea-1"
STAMP = "20260917T090000Z"
ASIN = "B00DENTAL1"
PRODUCT_KEY = "dental-chew"
PRODUCT_DISPLAY = "Greenies Regular Dental Dog Treats"
ASSOCIATES_TAG = "dogfoodandfun-20"
POST_SLUG = "best-dental-chews"
IMAGE = f"state/social_posts_pending/spotlight-{DERIVATIVE_ID}.jpg"
MASCOT_NAME = "Nalla"

#: The live post: one Amazon link, so the spotlit product IS in the article.
POST_HTML = (
    "<p>We tested six chews over three months.</p>"
    f'<p><a href="https://www.amazon.com/dp/{ASIN}?tag=x">Greenies</a> lasted longest.</p>'
)


def plan_for(**overrides: Any) -> SocialPostPlan:
    fields: dict[str, Any] = {
        "target_question": "which dental chew actually works?",
        "comment_keyword": "DENTAL",
        "fb_caption": (
            "Plaque is what wears teeth down first. Greenies Regular Dental Dog Treats "
            "were the only chew that lasted. Comment DENTAL and I'll DM you the guide. "
            "What does your dog chew? As an Amazon Associate I earn from qualifying purchases."
        ),
        "ig_caption": (
            "Plaque is what wears teeth down first. Greenies Regular Dental Dog Treats "
            "lasted longest. Comment DENTAL and I'll DM you the guide. What works for you? "
            "As an Amazon Associate I earn from qualifying purchases.\n#dogteeth #dogcare"
        ),
        "overlay_headline": "THREE MONTHS\nOF CHEWING",
        "overlay_subcopy": "what actually held up",
        "image_brief": "a shepherd mix chewing on a kitchen floor, morning light",
        "reference_category": "kitchen-counter",
        "cta_ribbon_text": "FULL GUIDE",
        "image_alt_text": "a dog chewing on a kitchen floor",
    }
    fields.update(overrides)
    return SocialPostPlan(**fields)


def product_entry() -> ProductEntry:
    return ProductEntry(
        key=PRODUCT_KEY,
        asin=ASIN,
        display=PRODUCT_DISPLAY,
        category="dental",
        notes="Dental chew; verified against VOHC.",
    )


def composing_row(**overrides: Any) -> dict[str, Any]:
    row = {
        "id": DERIVATIVE_ID,
        "idea_id": IDEA_ID,
        "brand_id": "dogfoodandfun",
        "kind": "product_spotlight",
        "format": "feed_post",
        "status": "composing",
        "product_key": PRODUCT_KEY,
        "product_asin": ASIN,
        "product_display": PRODUCT_DISPLAY,
        "reference_category": "",
    }
    row.update(overrides)
    return row


def published_idea(**overrides: Any) -> dict[str, Any]:
    idea = {
        "id": IDEA_ID,
        "topic": "Best Dental Chews For Dogs",
        "wp_post_id": "4463",
        "wp_url": f"https://dogfoodandfun.com/{POST_SLUG}/",
        "category": "dental",
        "status": "wp_published",
    }
    idea.update(overrides)
    return idea


class _Generated:
    def __init__(self, data: bytes) -> None:
        self.bytes_ = data
        self.provider = "gemini-3-pro-image-preview"


def _write_brand(tmp_path: Path) -> None:
    """A brand with an identity, a voice guide, mascot facts and a library."""
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "site": {
                    "url": "https://dogfoodandfun.com",
                    "mascot_name": MASCOT_NAME,
                    "mascot_kind": "shepherd mix",
                    "brand_persona": "Nalla's Dad",
                }
            }
        ),
        encoding="utf-8",
    )
    config = tmp_path / "data" / "config"
    config.mkdir(parents=True, exist_ok=True)
    (config / "brand_voice_guide.md").write_text("Plain, specific, first person.", encoding="utf-8")
    (config / "nalla_facts.md").write_text(
        f"{MASCOT_NAME} is a shepherd mix. She eats a fresh-cooked diet.", encoding="utf-8"
    )
    write_library(
        tmp_path,
        {"kitchen-counter": "kitchen-bytes", "studio-mascot": "mascot-bytes"},
        mascot_categories=["studio-mascot"],
    )


def build_world(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, with_library: bool = True
) -> dict[str, Any]:
    """Every collaborator outside `compose`/`inputs`, recorded.

    `state["plan"]` is what the writer crew answers (`None` to fail it),
    `state["image"]` what the image model returns (`None` to raise inside it),
    `state["post"]` what WordPress answers (`None` for "unreachable"), and
    `state["write_ok"]` whether the row still accepts the review payload.
    Everything the run did is in `state` afterwards.
    """
    state: dict[str, Any] = {
        "brand_dir": tmp_path,
        "row": composing_row(),
        "idea": published_idea(),
        "post": {"title": {"rendered": "Best Dental Chews"}, "content": {"rendered": POST_HTML}},
        "wp_error": None,
        "pool": {PRODUCT_KEY: product_entry()},
        "plan": plan_for(),
        "image": b"a-generated-image",
        "write_ok": True,
        "overlay_fails": False,
        "written": [],
        "failed": [],
        "crew_calls": [],
        "generate_calls": [],
        "resolve_calls": [],
    }

    if with_library:
        _write_brand(tmp_path)
    else:
        # A brand that has uploaded no photo at all: the tier-3 mascot anchor
        # has nothing to offer either, which is the only way a spotlight ends
        # up with no reference.
        (tmp_path / "config.json").write_text('{"site": {"url": "https://example.com"}}')

    monkeypatch.setenv("AMAZON_ASSOCIATES_TAG", ASSOCIATES_TAG)
    monkeypatch.setenv("IG_USERNAME", "dogfoodandfun")

    # ── the row and the idea ────────────────────────────────────────────────
    monkeypatch.setattr(compose.derivatives_db, "get", lambda _id: state["row"])
    monkeypatch.setattr(compose.ideas_db, "get_idea", lambda _id: state["idea"])

    def _set_pending_review(derivative_id: str, **kwargs: Any) -> bool:
        state["written"].append({"id": derivative_id, **kwargs})
        return bool(state["write_ok"])

    def _mark_failed(derivative_id: str, *, error: str) -> bool:
        state["failed"].append({"id": derivative_id, "error": error})
        return True

    monkeypatch.setattr(compose.derivatives_db, "set_pending_review", _set_pending_review)
    monkeypatch.setattr(compose.derivatives_db, "mark_failed", _mark_failed)

    # ── WordPress ───────────────────────────────────────────────────────────
    def _fetch_post(_wp_post_id: str) -> dict[str, Any] | None:
        if state["wp_error"] is not None:
            raise state["wp_error"]
        post: dict[str, Any] | None = state["post"]
        return post

    monkeypatch.setattr(inputs.wp_source, "fetch_post", _fetch_post)
    monkeypatch.setattr(compose, "load_candidate_pool", lambda _dir: state["pool"])

    # ── the writer crew: real prompt building, faked kickoff ─────────────────
    monkeypatch.setattr(inputs, "build_social_post_agent", lambda: object())
    monkeypatch.setattr(inputs, "build_social_post_task", lambda _a, description: description)

    def _execute(_agent: Any, description: Any, **kwargs: Any) -> SocialPostPlan | None:
        state["crew_calls"].append({"description": description, **kwargs})
        plan: SocialPostPlan | None = state["plan"]
        return plan

    monkeypatch.setattr(inputs, "execute_social_post_crew", _execute)

    # ── the image model, at the seam `test_socialpost_compose.py` uses ───────
    def _generate(brief: str, **kwargs: Any) -> _Generated:
        state["generate_calls"].append({"brief": brief, **kwargs})
        if state["image"] is None:
            raise RuntimeError("gemini is down")
        return _Generated(state["image"])

    monkeypatch.setattr(hook_render, "generate_wp_image", _generate)

    # ── the overlay pass writes a real file, so the lifecycle is real ───────
    def _compose_image(_plan: Any, **kwargs: Any) -> str | None:
        if state["overlay_fails"]:
            return None
        relative = f"state/social_posts_pending/{kwargs['filename_stem']}.jpg"
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"composed:" + kwargs["image_bytes"])
        return relative

    monkeypatch.setattr(compose, "compose_image", _compose_image)
    monkeypatch.setattr(compose, "_stamp", lambda: STAMP)

    real_resolve = compose.resolve_retry_reference

    def _resolve(brand_dir: Path, **kwargs: Any) -> Any:
        state["resolve_calls"].append(kwargs)
        return real_resolve(brand_dir, **kwargs)

    monkeypatch.setattr(compose, "resolve_retry_reference", _resolve)
    return state


def run_compose(world: dict[str, Any], **kwargs: Any) -> SpotlightResult:
    """Compose the world's derivative against its brand dir."""
    return compose.compose_spotlight(DERIVATIVE_ID, brand_dir=world["brand_dir"], **kwargs)
