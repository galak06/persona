"""Health-check probes for the `--health-check` flag on every runner.

Each probe verifies one external dependency (WP REST, FB/IG Graph,
Pinterest, Telegram, Playwright session file) is reachable and the
credentials are valid. Probes have NO side effects — strictly
read-only verification.

Usage in a runner:

    if "--health-check" in sys.argv:
        ok = run_health_checks(["wp", "fb", "ig", "telegram"])
        sys.exit(0 if ok else 1)

Probes are pluggable — register a new platform via the `HealthProbe`
protocol. The default registry covers the platforms this codebase
actually uses; new ones get added as we migrate publishers.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class HealthCheckResult:
    """Outcome of a single probe.

    Attributes:
        platform: The platform name as supplied to `run_health_checks`.
        ok: True iff the probe succeeded.
        detail: Human-readable diagnostic ("token valid", "WP 200",
            "missing FB_PAGE_TOKEN", etc.). Never includes secrets.
        latency_ms: Round-trip time for diagnostic — None if probe
            short-circuited before any I/O.
    """

    platform: str
    ok: bool
    detail: str
    latency_ms: int | None = None


class HealthProbe(Protocol):
    """A single-platform probe.

    Implementations must:
        - perform exactly one read-only request
        - never raise — convert all errors into `HealthCheckResult(ok=False, ...)`
        - return within ~10s (use a hard timeout on the underlying request)
        - never log secrets in `detail`
    """

    def __call__(self) -> HealthCheckResult: ...


# Registry of platform name → probe callable. Populated by callers
# (the runner imports their probes and passes them in via `register`),
# OR — for the common case — uses the defaults below.
_registry: dict[str, HealthProbe] = {}


def register(platform: str, probe: HealthProbe) -> None:
    """Register a probe under a platform name.

    Idempotent — re-registering replaces the prior probe (useful for tests).
    """
    _registry[platform] = probe


def unregister(platform: str) -> None:
    """Remove a probe. No-op if not registered."""
    _registry.pop(platform, None)


def get_registered() -> dict[str, HealthProbe]:
    """Return a copy of the current registry. Useful for diagnostics."""
    return dict(_registry)


def run_health_checks(
    platforms: list[str],
    *,
    on_result: Callable[[HealthCheckResult], None] | None = None,
) -> bool:
    """Run probes for the named platforms in order. Return True iff all pass.

    Args:
        platforms: Names of platforms to probe. Each must be registered.
            An unregistered platform produces an `ok=False` result.
        on_result: Optional callback invoked with each result as it
            completes. Useful to stream progress to stdout/logger
            without buffering the whole list.

    Returns:
        True if every probe returned `ok=True`. False on any failure
        OR if any platform was unregistered.
    """
    all_ok = True
    for platform in platforms:
        probe = _registry.get(platform)
        if probe is None:
            result = HealthCheckResult(
                platform=platform, ok=False, detail=f"no probe registered for {platform!r}"
            )
        else:
            result = _safe_run_probe(platform, probe)
        if on_result is not None:
            on_result(result)
        if not result.ok:
            all_ok = False
    return all_ok


def _safe_run_probe(platform: str, probe: HealthProbe) -> HealthCheckResult:
    """Wrap a probe in a try/except so a misbehaving probe can't kill the runner.

    Probes are SUPPOSED to never raise — but defensive wrapping lets
    `--health-check` always exit cleanly.
    """
    try:
        return probe()
    except Exception as exc:
        return HealthCheckResult(
            platform=platform,
            ok=False,
            detail=f"probe raised: {type(exc).__name__}: {exc!s}",
        )
