"""Tests for step-image injection into the recipe body."""
# ruff: noqa: S101

from __future__ import annotations

import markdown as md

from generators.step_images import inject_step_images

_BODY = """## Instructions

1. Preheat oven.
2. Mix wet ingredients.
3. Fold in oats.
4. Bake 25 minutes.
"""


def test_injects_under_every_nth_step() -> None:
    out = inject_step_images(_BODY, ["u1.jpg", "u2.jpg"], every_n=2)
    assert out.count("<img") == 2  # after step 2 and step 4
    assert "u1.jpg" in out
    assert "u2.jpg" in out


def test_no_images_is_noop() -> None:
    assert inject_step_images(_BODY, [], every_n=2) == _BODY


def test_keeps_ordered_list_intact() -> None:
    out = inject_step_images(_BODY, ["u1.jpg"], every_n=2)
    html = md.markdown(out, extensions=["extra"])
    assert html.count("<ol>") == 1  # single list, no restart
    assert html.count("<li>") == 4  # image nests inside the list item


def test_stops_when_images_exhausted() -> None:
    out = inject_step_images(_BODY, ["only.jpg"], every_n=2)
    assert out.count("<img") == 1
