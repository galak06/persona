"""Facebook-specific helpers (session, publishers, scanners).

Sub-packages here are scoped to the FB platform — the session
abstraction lives in `lib.fb.session` and is wired into scripts via
brand-aware factories rather than module-global defaults.
"""
