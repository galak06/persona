"""Tests for `lib/session_status.py` (browser-session/login status).

Pure filesystem reads -- no DB/Redis needed, uses `tmp_path`.
"""

from __future__ import annotations

from pathlib import Path

from lib.session_status import session_status


def test_no_sessions_saved_yet(tmp_path: Path) -> None:
    result = session_status(tmp_path)

    assert {s["platform"] for s in result} == {"facebook", "instagram"}
    assert all(s["exists"] is False for s in result)
    assert all(s["last_saved"] is None for s in result)


def test_login_command_uses_brand_dir(tmp_path: Path) -> None:
    result = session_status(tmp_path)

    fb = next(s for s in result if s["platform"] == "facebook")
    ig = next(s for s in result if s["platform"] == "instagram")
    assert fb["login_command"] == f"BRAND_DIR={tmp_path} python scripts/fb_login.py"
    assert ig["login_command"] == f"BRAND_DIR={tmp_path} python scripts/ig_login.py"


def test_existing_session_file_reports_exists_and_timestamp(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "facebook_session.json").write_text("{}")

    result = session_status(tmp_path)

    fb = next(s for s in result if s["platform"] == "facebook")
    ig = next(s for s in result if s["platform"] == "instagram")
    assert fb["exists"] is True
    assert fb["last_saved"] is not None
    assert ig["exists"] is False
    assert ig["last_saved"] is None
