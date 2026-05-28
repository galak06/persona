"""Brand-scoped Facebook session abstraction for Playwright flows."""
from lib.fb.session.factory import build_fb_session
from lib.fb.session.playwright_session import PlaywrightFbSession
from lib.fb.session.protocol import FbSession

__all__ = ["FbSession", "PlaywrightFbSession", "build_fb_session"]
