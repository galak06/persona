"""Load secrets from .claude/settings.local.json into os.environ.

Why this exists: Claude Code injects the settings file's `env` dict into the
process when running interactively, but launchd cron jobs run plain python and
inherit nothing. Per project rule (memory: feedback_secrets_read_from_config),
secrets live only in settings.local.json — not inline in scripts, not in
launchd plists. Scripts call `load_local_env()` at startup to bridge the gap.

Existing values in os.environ are NOT overwritten, so a manual `FB_PAGE_TOKEN=...
python ...` invocation still wins.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

_SETTINGS_FILE = Path(__file__).resolve().parent.parent.parent / ".claude" / "settings.local.json"


def load_local_env(*, settings_file: Path | None = None) -> int:
    """Merge settings.local.json `env` into os.environ. Returns count loaded."""
    path = settings_file or _SETTINGS_FILE
    if not path.exists():
        return 0
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return 0
    env = data.get("env") or {}
    loaded = 0
    for k, v in env.items():
        if k in os.environ or v is None:
            continue
        os.environ[k] = str(v)
        loaded += 1
    return loaded
