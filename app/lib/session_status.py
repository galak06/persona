"""Browser-session (login) status for the "Human Mimic" UI panel.

Answers "is there a saved Facebook/Instagram login session, and how stale
is it" -- today that's only visible via a copy-paste CLI command shown once
at brand creation, with no way to check status afterward. A session file's
mtime is rewritten by `lib.sessions.browser.BrowserSession.__exit__` on
every successful run that uses it, so "last saved" also reflects the last
time a script actually completed against that session, not just the first
login.

Single-tenant, matching its Engagement-section siblings (`GET
/facebook/groups` et al.): resolves from the API process's own `BRAND_DIR`
via `lib.config.settings`, not an explicit `brand_id` param.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_PLATFORMS: tuple[tuple[str, str, str], ...] = (
    # (platform, session filename, login script)
    ("facebook", "facebook_session.json", "scripts/fb_login.py"),
    ("instagram", "instagram_session.json", "scripts/ig_login.py"),
)


def _login_command(brand_dir: Path, script: str) -> str:
    return f"BRAND_DIR={brand_dir} python {script}"


def session_status(brand_dir: Path) -> list[dict[str, Any]]:
    """One entry per platform: does a saved session exist, and when."""
    state_dir = brand_dir / "state"
    out: list[dict[str, Any]] = []
    for platform, filename, script in _PLATFORMS:
        path = state_dir / filename
        exists = path.exists()
        last_saved = (
            datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat() if exists else None
        )
        out.append(
            {
                "platform": platform,
                "exists": exists,
                "last_saved": last_saved,
                "login_command": _login_command(brand_dir, script),
            }
        )
    return out
