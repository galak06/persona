"""Per-platform comment-queue routing.

Instagram and Facebook engagement run as independent loops, each owning its own
comment queue. WordPress (and any unspecified platform) stays on the legacy
shared queue, which its own producers (``wp_scan``, ``reply_follower``) still
append to.

Consumers (``comment_approver``, ``comment_poster``) accept ``--platform`` and
resolve their queue + re-run-guard key through this module so the two loops
never read or rewrite each other's state.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from lib.config import settings

_PLATFORM_FLAG = "--platform"
VALID_PLATFORMS: tuple[str, ...] = ("instagram", "facebook", "wordpress")


def parse_platform_arg(argv: list[str]) -> Optional[str]:
    """Return the ``--platform`` value from ``argv`` (or ``None`` if absent).

    Accepts both ``--platform instagram`` and ``--platform=instagram``. Raises
    ``SystemExit`` on an unknown platform so a misconfigured launchd job fails
    loudly instead of silently draining the wrong queue.
    """
    for i, tok in enumerate(argv):
        value: Optional[str] = None
        if tok == _PLATFORM_FLAG and i + 1 < len(argv):
            value = argv[i + 1]
        elif tok.startswith(_PLATFORM_FLAG + "="):
            value = tok.split("=", 1)[1]
        if value is None:
            continue
        normalized = value.strip().lower()
        if normalized not in VALID_PLATFORMS:
            raise SystemExit(
                f"--platform must be one of {VALID_PLATFORMS}, got {value!r}"
            )
        return normalized
    return None


def queue_path_for(platform: Optional[str]) -> Path:
    """Resolve the comment-queue file for ``platform``.

    ``instagram``/``facebook`` map to their own queue; ``wordpress`` or ``None``
    fall back to the legacy shared queue.
    """
    paths = settings.paths
    if paths is None:
        raise RuntimeError("settings.paths not resolved (brand_dir missing)")
    if platform == "instagram":
        return paths.instagram_comment_queue
    if platform == "facebook":
        return paths.facebook_comment_queue
    return paths.comment_queue


def guard_key_for(platform: Optional[str], base: str = "comment_composer") -> str:
    """Per-platform re-run-guard key, so one loop's run never skips another's.

    Returns ``base`` unchanged when no platform is set (legacy single-loop
    behaviour), otherwise ``<base>_<platform>``.
    """
    return f"{base}_{platform}" if platform else base
