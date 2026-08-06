"""Tests for the validate -> draft composition used by
`scripts/crewai_content_pipeline.py`'s `_run_full_pipeline` (the exact glue:
"only call `create_wp_draft` when `validate_draft(...).passed` is True").

Exercises `lib.crew.validate.validate_draft` and `lib.crew.draft.create_wp_draft`
together with the same injected-fake / respx conventions as their own test
files -- no real CrewAI/DeepSeek/Postgres/WordPress network calls.
"""
# ruff: noqa: S101

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx
from crewai import Agent, Task

from lib.crew.draft import create_wp_draft
from lib.crew.editor.models import QualityVerdict
from lib.crew.validate import EditorExecuteFn, validate_draft

_POSTS_URL = "https://example.com/wp-json/wp/v2/posts"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def brand_dir(tmp_path: Path) -> Path:
    _write(tmp_path / "config.json", json.dumps({"site": {"name": "DogFoodAndFun"}}))
    return tmp_path


@pytest.fixture(autouse=True)
def _wp_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WP_URL", "https://example.com")
    monkeypatch.setenv("WP_USER", "test_user")
    monkeypatch.setenv("WP_APP_PASSWORD", "test_pass")


class _FakeIdeasDb:
    def __init__(self) -> None:
        self.set_wp_result_calls: list[tuple[str, str, str]] = []
        self.update_status_calls: list[tuple[str, str]] = []

    def set_wp_result(self, idea_id: str, wp_post_id: str, wp_url: str) -> bool:
        self.set_wp_result_calls.append((idea_id, wp_post_id, wp_url))
        return True

    def update_status(self, idea_id: str, status: str) -> bool:
        self.update_status_calls.append((idea_id, status))
        return True


def _run_pipeline_glue(
    brand_dir: Path,
    *,
    idea_id: str,
    title: str,
    body_html: str,
    editor_execute_fn: EditorExecuteFn,
    ideas_db: _FakeIdeasDb,
    dry_run: bool = False,
) -> tuple[bool, int | None]:
    """Mirrors `scripts/crewai_content_pipeline.py::_run_full_pipeline`'s
    "validate, then only draft on pass" control flow exactly -- including the
    validation-failure branch's `ideas_db.update_status(idea_id,
    "validation_failed")` call, guarded by `not dry_run` (a --dry-run run is
    an explicitly side-effect-free preview and must never mutate the idea's
    real lifecycle state)."""
    result = validate_draft(
        brand_dir, title=title, body_html=body_html, editor_execute_fn=editor_execute_fn
    )
    if not result.passed:
        if not dry_run:
            ideas_db.update_status(idea_id, "validation_failed")
        return False, None
    draft_result = create_wp_draft(
        idea_id=idea_id,
        title=title,
        body_html=body_html,
        set_wp_result_fn=ideas_db.set_wp_result,
        update_status_fn=ideas_db.update_status,
    )
    return True, draft_result.post_id


@respx.mock
def test_passing_validation_proceeds_to_real_draft_creation(brand_dir: Path) -> None:
    respx.post(_POSTS_URL).mock(
        return_value=httpx.Response(201, json={"id": 99, "link": "https://example.com/?p=99"})
    )
    fake_db = _FakeIdeasDb()

    def good_editor(agent: Agent, task: Task) -> QualityVerdict:
        return QualityVerdict(score=90.0, passed=True, issues=[], stray_artifacts=[])

    drafted, post_id = _run_pipeline_glue(
        brand_dir,
        idea_id="idea-1",
        title="A Great Post",
        body_html="<p>Clean content.</p>",
        editor_execute_fn=good_editor,
        ideas_db=fake_db,
    )

    assert drafted is True
    assert post_id == 99
    assert fake_db.set_wp_result_calls == [("idea-1", "99", "https://example.com/?p=99")]
    assert fake_db.update_status_calls == [("idea-1", "wp_draft")]


@respx.mock
def test_failing_validation_never_posts_to_wordpress(brand_dir: Path) -> None:
    route = respx.post(_POSTS_URL).mock(
        return_value=httpx.Response(201, json={"id": 1, "link": "https://example.com/?p=1"})
    )
    fake_db = _FakeIdeasDb()

    def bad_editor(agent: Agent, task: Task) -> QualityVerdict:
        return QualityVerdict(score=20.0, passed=False, issues=["incoherent"], stray_artifacts=[])

    drafted, post_id = _run_pipeline_glue(
        brand_dir,
        idea_id="idea-1",
        title="A Bad Post",
        body_html="<p>Incoherent content.</p>",
        editor_execute_fn=bad_editor,
        ideas_db=fake_db,
    )

    assert drafted is False
    assert post_id is None
    assert not route.called
    assert fake_db.set_wp_result_calls == []
    assert fake_db.update_status_calls == [("idea-1", "validation_failed")]


@respx.mock
def test_failing_validation_on_dry_run_never_touches_ideas_db(brand_dir: Path) -> None:
    """--dry-run is an explicitly side-effect-free preview -- a validation
    failure during a dry run must NOT mutate the idea's real status."""
    route = respx.post(_POSTS_URL).mock(
        return_value=httpx.Response(201, json={"id": 1, "link": "https://example.com/?p=1"})
    )
    fake_db = _FakeIdeasDb()

    def bad_editor(agent: Agent, task: Task) -> QualityVerdict:
        return QualityVerdict(score=20.0, passed=False, issues=["incoherent"], stray_artifacts=[])

    drafted, post_id = _run_pipeline_glue(
        brand_dir,
        idea_id="idea-1",
        title="A Bad Post",
        body_html="<p>Incoherent content.</p>",
        editor_execute_fn=bad_editor,
        ideas_db=fake_db,
        dry_run=True,
    )

    assert drafted is False
    assert post_id is None
    assert not route.called
    assert fake_db.update_status_calls == []
