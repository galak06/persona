"""Tests for `api/social_posts_retry_api.py` -- the Retry image button.

Same convention as `test_social_posts_compose_api.py`: the two collaborators
that cross the API/worker boundary (the Redis `flow-run` queue and the shared
`worker_runs` row) plus the idea lookup are faked. No Postgres, no Redis, no
run.

The load-bearing assertion is the negative one -- this route dispatches a
script with no publisher in it, and there is no argument shape that changes
that. The compose button has to strip `--release-only` from its row's args to
be safe; this one has nothing to strip.
"""
# ruff: noqa: S101, SLF001

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest
from api import social_posts_retry_api as retry_api
from fastapi import HTTPException

from lib import flow_queue
from tests._reference_library_fakes import write_library as _write_library

_BRAND = "b1"
_IDEA_ID = "idea-1"


@pytest.fixture()
def world(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    state: dict[str, Any] = {
        "row": None,
        "pushed": [],
        "idea": {
            "id": _IDEA_ID,
            "social_post_status": "queued",
            "social_post_source": "fallback",
            "social_post_image_path": "state/social_posts_pending/idea-1.jpg",
        },
        "queued": [],
    }
    _write_library(
        tmp_path,
        {"home-exterior": "porch-bytes", "studio-mascot": "mascot-bytes"},
        mascot_categories=("studio-mascot",),
    )

    monkeypatch.setattr("api.brand_context.brands_db.get", lambda _b: {"brand_dir": str(tmp_path)})
    monkeypatch.setattr("api.brand_context.current_brand_id", lambda: _BRAND)
    monkeypatch.setattr(retry_api.ideas_db, "get_idea", lambda _id: state["idea"])
    monkeypatch.setattr(retry_api.worker_db, "get_one", lambda _d, _l, _b: state["row"])
    monkeypatch.setattr(
        retry_api.worker_db,
        "record_queued",
        lambda _d, label, brand: state["queued"].append((label, brand)),
    )

    class _FakeQueue:
        def __init__(self, worker: str, brand: str) -> None:
            state["queue"] = (worker, brand)

        def push(self, payload: dict[str, Any]) -> str:
            state["pushed"].append(payload)
            return "1"

    monkeypatch.setattr(flow_queue, "TaskQueue", _FakeQueue)
    state["brand_dir"] = tmp_path
    return state


# ── dispatch ────────────────────────────────────────────────────────────────


def test_the_retry_is_dispatched_to_the_worker(world: dict[str, Any]) -> None:
    """The API image has no image-model credentials and no fonts, so the run
    goes on the shared queue rather than executing here."""
    assert retry_api.retry_social_post_image(_IDEA_ID) == {
        "status": "started",
        "id": _IDEA_ID,
    }

    assert world["queue"] == ("flow-run", _BRAND)
    payload = world["pushed"][0]
    assert payload["script"] == "scripts/social_post_retry_image.py"
    assert payload["args"] == ["--idea-id", _IDEA_ID]
    assert payload["brand"] == _BRAND
    assert payload["timeout_seconds"] == 900
    assert world["queued"] == [(f"{_BRAND}-social-post-retry-image", _BRAND)]


def test_the_chosen_collection_reaches_the_run(world: dict[str, Any]) -> None:
    retry_api.retry_social_post_image(
        _IDEA_ID, retry_api.RetryImageRequest(reference_category="Studio Mascot")
    )

    # Slugified on the way through, so the picker may send either form.
    assert world["pushed"][0]["args"] == [
        "--idea-id",
        _IDEA_ID,
        "--reference-category",
        "studio-mascot",
    ]


def test_this_route_cannot_publish(world: dict[str, Any]) -> None:
    """THE SAFETY PROPERTY. Compose dispatches the pipeline that publishes and
    has to filter its args to stay safe; this dispatches a script that contains
    no publisher at all, so no argument can reach a platform."""
    retry_api.retry_social_post_image(
        _IDEA_ID, retry_api.RetryImageRequest(reference_category="home-exterior")
    )

    payload = world["pushed"][0]
    assert payload["script"] != "scripts/crewai_social_posts_pipeline.py"
    assert not any(a.startswith("--release") for a in payload["args"])

    # And the script it DOES name imports nothing that can post.
    script = Path(__file__).resolve().parents[1] / payload["script"]
    tree = ast.parse(script.read_text(encoding="utf-8"))
    imported = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert not any("worker_wp_ideas" in name or "publish" in name for name in imported)
    assert not any("pipeline" in name for name in imported)


# ── guards ──────────────────────────────────────────────────────────────────


def test_an_unknown_idea_is_a_404(world: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(retry_api.ideas_db, "get_idea", lambda _id: None)

    with pytest.raises(HTTPException) as exc:
        retry_api.retry_social_post_image(_IDEA_ID)

    assert exc.value.status_code == 404
    assert world["pushed"] == []


@pytest.mark.parametrize("status", ["scheduled", "published", "rejected"])
def test_a_post_that_left_the_review_queue_is_refused(world: dict[str, Any], status: str) -> None:
    """An approved post's image is already claimed by a slot; a rejected one is
    terminal. Neither may be regenerated behind the operator's back."""
    world["idea"]["social_post_status"] = status

    with pytest.raises(HTTPException) as exc:
        retry_api.retry_social_post_image(_IDEA_ID)

    assert exc.value.status_code == 409
    assert world["pushed"] == []


def test_a_post_already_regenerating_says_so(world: dict[str, Any]) -> None:
    world["idea"]["social_post_status"] = "composing"

    with pytest.raises(HTTPException) as exc:
        retry_api.retry_social_post_image(_IDEA_ID)

    assert exc.value.status_code == 409
    assert "already regenerating" in str(exc.value.detail)


def test_a_second_retry_while_one_runs_is_refused(world: dict[str, Any]) -> None:
    world["row"] = {"status": "running", "last_run": "2026-08-20T06:00:00Z", "message": ""}

    with pytest.raises(HTTPException) as exc:
        retry_api.retry_social_post_image(_IDEA_ID)

    assert exc.value.status_code == 409
    assert world["pushed"] == []


def test_a_collection_with_no_photos_is_refused_rather_than_ignored(
    world: dict[str, Any],
) -> None:
    """Silently ignoring it would resolve to no image and reproduce the very
    fallback the button was clicked to escape."""
    with pytest.raises(HTTPException) as exc:
        retry_api.retry_social_post_image(
            _IDEA_ID, retry_api.RetryImageRequest(reference_category="products")
        )

    assert exc.value.status_code == 422
    assert world["pushed"] == []


# ── status ──────────────────────────────────────────────────────────────────


def test_a_claimed_row_reports_running(world: dict[str, Any]) -> None:
    """`'composing'` IS the in-flight signal, so a reload or another tab sees
    the same truth as the tab that clicked."""
    world["idea"]["social_post_status"] = "composing"

    status = retry_api.retry_image_status(_IDEA_ID)

    assert status.running is True
    assert status.status == "composing"


def test_a_finished_retry_reports_the_new_source(world: dict[str, Any]) -> None:
    world["idea"]["social_post_source"] = "gemini"
    world["row"] = {
        "status": "success",
        "last_run": "2026-08-20T06:01:00Z",
        "message": 'summary: {"ok": true, "reason": "regenerated"}',
    }

    status = retry_api.retry_image_status(_IDEA_ID)

    assert (status.running, status.ok, status.source) == (False, True, "gemini")
    assert "regenerated" in (status.detail or "")


def test_a_failed_retry_leaves_the_post_where_it_was(world: dict[str, Any]) -> None:
    world["row"] = {
        "status": "error",
        "last_run": "2026-08-20T06:01:00Z",
        "message": 'summary: {"ok": false, "reason": "generation_failed"}',
    }

    status = retry_api.retry_image_status(_IDEA_ID)

    assert (status.running, status.ok) == (False, False)
    assert status.status == "queued"  # still reviewable
    assert status.source == "fallback"  # still the hero
