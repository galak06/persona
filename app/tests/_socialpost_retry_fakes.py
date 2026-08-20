"""Shared world for the two hook-image retry test modules.

`test_socialpost_retry.py` covers WHICH photo a retry anchors on;
`test_socialpost_retry_safety.py` covers what it is allowed to touch. Both need
the same brand dir, the same faked crew/image-model/DB, and the same real files
on disk, and the two must not drift -- a retry that resolves the right photo and
then eats the operator's captions is not two separate stories.

A plain builder rather than a fixture, matching `_reference_library_fakes.py`:
a pytest fixture imported into a test module shadows the parameter name that
requests it, which reads as a redefinition to any linter.
"""
# ruff: noqa: S101

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from lib.crew.socialpost import hook_render, retry
from lib.crew.socialpost.models import SocialPostPlan

IDEA_ID = "idea-1"
STAMP = "20260820T060000Z"
OLD_IMAGE = "state/social_posts_pending/idea-1.jpg"
NEW_IMAGE = f"state/social_posts_pending/{IDEA_ID}-{STAMP}.jpg"
OLD_IMAGE_BYTES = b"the-hero-fallback-image"


def plan_for(reference_category: str = "") -> SocialPostPlan:
    return SocialPostPlan(
        fb_caption="a freshly drafted fb caption",
        ig_caption="a freshly drafted ig caption",
        overlay_headline="headline",
        overlay_subcopy="subcopy",
        image_brief="the mascot at breakfast",
        reference_category=reference_category,
        cta_ribbon_text="FULL GUIDE",
        image_alt_text="a new alt text",
        target_question="what should my pet eat?",
        comment_keyword="RECIPE",
    )


def queued_idea(**overrides: Any) -> dict[str, Any]:
    idea = {
        "id": IDEA_ID,
        "wp_post_id": "4463",
        "target_keyword": "fresh food",
        "social_post_status": "queued",
        "social_post_fb_caption": "the caption the operator already read",
        "social_post_ig_caption": "the ig caption the operator already read",
        "social_post_image_path": OLD_IMAGE,
        "social_post_image_alt": "the old alt",
        "social_post_source": "fallback",
        "social_post_validation_flags": ["a-flag"],
    }
    idea.update(overrides)
    return idea


class _Generated:
    def __init__(self, data: bytes) -> None:
        self.bytes_ = data
        self.provider = "nano_pro"


def build_world(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    """Every collaborator outside this module, recorded rather than mocked away.

    `state["plan"]` is what the writer crew comes back with; `state["image"]`
    is what the image model returns (`None` to fail the generation). Everything
    the run wrote is in `state` afterwards.
    """
    state: dict[str, Any] = {
        "plan": plan_for(),
        "image": b"a-generated-image",
        "idea": queued_idea(),
        "claimed": [],
        "restored": [],
        "written": [],
        "generate_calls": [],
        "resolve_seeds": [],
        "brand_dir": tmp_path,
    }

    # The post already has an image on disk -- that is the whole reason a retry
    # has to be careful about when it writes and when it deletes.
    old = tmp_path / OLD_IMAGE
    old.parent.mkdir(parents=True, exist_ok=True)
    old.write_bytes(OLD_IMAGE_BYTES)
    (tmp_path / "config.json").write_text('{"site": {"url": "https://example.com"}}')

    monkeypatch.setattr(retry.ideas_db, "get_idea", lambda _id: state["idea"])
    monkeypatch.setattr(retry, "_stamp", lambda: STAMP)
    monkeypatch.setattr(
        retry.wp_source,
        "fetch_post",
        lambda _id: {"title": {"rendered": "T"}, "content": {"rendered": "<p>body</p>"}},
    )

    def _claim(idea_id: str) -> bool:
        state["claimed"].append(idea_id)
        return str(state["idea"].get("social_post_status")) == "queued"

    monkeypatch.setattr(retry.social_post_retry_db, "claim_for_recompose", _claim)
    monkeypatch.setattr(
        retry.social_post_retry_db,
        "restore_queued",
        lambda idea_id: bool(state["restored"].append(idea_id)) or True,
    )

    def _set_pending_review(idea_id: str, **kwargs: Any) -> bool:
        state["written"].append({"idea_id": idea_id, **kwargs})
        return bool(state.get("write_ok", True))

    monkeypatch.setattr(retry.social_post_db, "set_pending_review", _set_pending_review)

    # The crew: real prompt building, faked kickoff.
    monkeypatch.setattr(retry, "build_social_post_agent", lambda: object())
    monkeypatch.setattr(retry, "build_social_post_task", lambda _a, _d: object())
    monkeypatch.setattr(retry, "execute_social_post_crew", lambda *_a, **_kw: state["plan"])

    # The image model, at the same seam `test_socialpost_compose.py` uses.
    def _generate(brief: str, **kwargs: Any) -> _Generated:
        state["generate_calls"].append({"brief": brief, **kwargs})
        if state["image"] is None:
            raise RuntimeError("gemini is down")
        return _Generated(state["image"])

    monkeypatch.setattr(hook_render, "generate_wp_image", _generate)

    # The overlay pass writes a real file, so the file lifecycle is real.
    def _compose_image(_plan_arg: Any, **kwargs: Any) -> str | None:
        if state.get("overlay_fails"):
            return None
        relative = f"state/social_posts_pending/{kwargs['filename_stem']}.jpg"
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"composed:" + kwargs["image_bytes"])
        return relative

    monkeypatch.setattr(retry, "compose_image", _compose_image)

    real_resolve = retry.resolve_reference

    def _resolve(brand_dir: Path, category: str | None, *, seed: str = "") -> Any:
        state["resolve_seeds"].append((category, seed))
        return real_resolve(brand_dir, category, seed=seed)

    monkeypatch.setattr(retry, "resolve_reference", _resolve)
    return state


def run_retry(world: dict[str, Any], **kwargs: Any) -> retry.RetryResult:
    """Invoke the retry against the world's brand dir."""
    return retry.retry_hook_image(IDEA_ID, brand_dir=world["brand_dir"], **kwargs)
