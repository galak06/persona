"""Unit tests for campaign_assembly.append_teaser_and_cta. No network."""
# ruff: noqa: S101

from __future__ import annotations

import json
from pathlib import Path

from campaign_assembly import append_teaser_and_cta


def _teasers() -> list[str]:
    return ["T1", "T2", "T3"]


def _ctas() -> list[str]:
    return ["C1", "C2", "C3"]


def test_first_call_appends_first_teaser_and_cta(tmp_path: Path) -> None:
    rot = tmp_path / "state" / "campaign_rotation.json"
    out = append_teaser_and_cta("HOOK", _teasers(), _ctas(), rot)
    assert out == "HOOK\n\nT1\n\nC1"
    state = json.loads(rot.read_text(encoding="utf-8"))
    assert state == {"teaser_next_idx": 1, "cta_next_idx": 1}


def test_second_call_rotates_to_next(tmp_path: Path) -> None:
    rot = tmp_path / "state" / "campaign_rotation.json"
    append_teaser_and_cta("HOOK", _teasers(), _ctas(), rot)
    out2 = append_teaser_and_cta("HOOK2", _teasers(), _ctas(), rot)
    assert out2 == "HOOK2\n\nT2\n\nC2"
    state = json.loads(rot.read_text(encoding="utf-8"))
    assert state == {"teaser_next_idx": 2, "cta_next_idx": 2}


def test_wraps_around_at_pool_end(tmp_path: Path) -> None:
    rot = tmp_path / "state" / "campaign_rotation.json"
    rot.parent.mkdir(parents=True)
    rot.write_text(json.dumps({"teaser_next_idx": 2, "cta_next_idx": 2}), encoding="utf-8")
    out = append_teaser_and_cta("HOOK", _teasers(), _ctas(), rot)
    assert out == "HOOK\n\nT3\n\nC3"
    state = json.loads(rot.read_text(encoding="utf-8"))
    assert state == {"teaser_next_idx": 0, "cta_next_idx": 0}


def test_empty_teasers_returns_caption_unchanged(tmp_path: Path) -> None:
    rot = tmp_path / "state" / "campaign_rotation.json"
    out = append_teaser_and_cta("HOOK", [], _ctas(), rot)
    assert out == "HOOK"
    assert not rot.exists()


def test_empty_ctas_returns_caption_unchanged(tmp_path: Path) -> None:
    rot = tmp_path / "state" / "campaign_rotation.json"
    out = append_teaser_and_cta("HOOK", _teasers(), [], rot)
    assert out == "HOOK"
    assert not rot.exists()


def test_corrupt_state_falls_back_to_index_zero(tmp_path: Path) -> None:
    rot = tmp_path / "state" / "campaign_rotation.json"
    rot.parent.mkdir(parents=True)
    rot.write_text("not-json{{", encoding="utf-8")
    out = append_teaser_and_cta("HOOK", _teasers(), _ctas(), rot)
    assert out == "HOOK\n\nT1\n\nC1"


def test_out_of_range_state_wraps_via_modulo(tmp_path: Path) -> None:
    rot = tmp_path / "state" / "campaign_rotation.json"
    rot.parent.mkdir(parents=True)
    rot.write_text(json.dumps({"teaser_next_idx": 99, "cta_next_idx": 50}), encoding="utf-8")
    out = append_teaser_and_cta("HOOK", _teasers(), _ctas(), rot)
    assert out == "HOOK\n\nT1\n\nC3"
