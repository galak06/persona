# pyright: reportMissingImports=false
"""Tests for `api/session_status_api.py` (`GET /sessions`).

Handler-level unit test, following `test_api_brand_flows.py`'s pattern:
call the route function directly, monkeypatch `settings.paths` to a
`tmp_path`-backed brand dir instead of touching the real one.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from api import session_status_api


def test_get_session_status_no_sessions_saved(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(session_status_api.settings, "paths", SimpleNamespace(brand_dir=tmp_path))

    resp = session_status_api.get_session_status()

    assert {s.platform for s in resp.sessions} == {"facebook", "instagram"}
    assert all(s.exists is False for s in resp.sessions)
    assert all(s.last_saved is None for s in resp.sessions)


def test_get_session_status_reflects_existing_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "instagram_session.json").write_text("{}")
    monkeypatch.setattr(session_status_api.settings, "paths", SimpleNamespace(brand_dir=tmp_path))

    resp = session_status_api.get_session_status()

    ig = next(s for s in resp.sessions if s.platform == "instagram")
    fb = next(s for s in resp.sessions if s.platform == "facebook")
    assert ig.exists is True
    assert ig.last_saved is not None
    assert fb.exists is False
