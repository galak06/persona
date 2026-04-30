"""Load env from social-automation/.claude/settings.local.json into os.environ.

Per project convention: every script reads API keys from settings.local.json's
env dict — never inline literals. Idempotent, safe to import multiple times.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

_SETTINGS = (
    Path(__file__).resolve().parents[2]
    / "social-automation"
    / ".claude"
    / "settings.local.json"
)


def load() -> dict[str, str]:
    """Hydrate os.environ from settings.local.json. Returns the env dict."""
    data = json.loads(_SETTINGS.read_text())
    env = data.get("env") or {}
    for k, v in env.items():
        os.environ.setdefault(k, str(v))
    return env


if __name__ == "__main__":
    load()
    need = [
        "WP_URL", "WP_USER", "WP_APP_PASSWORD",
        "FB_PAGE_ID", "FB_PAGE_TOKEN", "FB_USER_TOKEN",
        "IG_ACCOUNT_ID", "AMAZON_ASSOCIATES_TAG",
    ]
    for v in need:
        val = os.environ.get(v)
        if val is None:
            print(f"{v}=<MISSING>")
        elif v in {"WP_URL", "WP_USER", "FB_PAGE_ID", "IG_ACCOUNT_ID", "AMAZON_ASSOCIATES_TAG"}:
            print(f"{v}={val}")
        else:
            print(f"{v}=<SET len={len(val)}>")
