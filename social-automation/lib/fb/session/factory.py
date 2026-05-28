"""Build a brand-scoped `FbSession`.

Centralises the resolution of the two inputs every FB Playwright flow
needs — the brand's cookie-jar path and the runtime headless flag —
so call sites stop hard-coding `.claude/state/facebook_session.json`
or rediscovering `get_runtime_headless()`. Returns the protocol type
so callers depend on the abstraction, not the Playwright impl.
"""

from __future__ import annotations

from lib.fb.session.playwright_session import PlaywrightFbSession
from lib.fb.session.protocol import FbSession


def build_fb_session(*, headless: bool | None = None) -> FbSession:
    """Construct an `FbSession` from brand config + runtime overlay.

    Args:
        headless: Override the brand overlay's `runtime.headless`
            value. Default None — defer to
            `lib.local_env.get_runtime_headless`.

    Returns:
        A ready `FbSession` (currently always a `PlaywrightFbSession`).

    Raises:
        ValueError: Propagated from `lib.config` when `BRAND_DIR` is
            unset — there is intentionally no silent fallback to a
            project-root default, because brand misconfiguration
            should fail loudly at the first FB flow that runs.
    """
    from lib.config import settings
    from lib.local_env import get_runtime_headless

    if settings.paths is None:
        raise RuntimeError("settings.paths is unset; lib.config failed to resolve BRAND_DIR")
    storage_path = settings.paths.facebook_session
    resolved_headless = get_runtime_headless() if headless is None else headless
    return PlaywrightFbSession(
        storage_path=storage_path,
        headless=resolved_headless,
    )
