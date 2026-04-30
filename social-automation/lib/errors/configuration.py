"""Configuration error — missing or malformed settings detected at startup."""

from __future__ import annotations

from lib.errors.base import PermanentError


class ConfigurationError(PermanentError):
    """A required setting is missing or malformed.

    Raise at process startup (in the runner's preflight) when:
        - a required env var is unset
        - a config file is missing or unparseable
        - a credential file's schema doesn't match expectations

    A ConfigurationError must never be retried — fix the config, restart.
    """
