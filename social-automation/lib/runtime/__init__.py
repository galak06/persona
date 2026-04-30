"""Runtime helpers — singleton enforcement, graceful shutdown, health checks.

Used by every CLI runner to enforce the production bar:
    - Only one instance of a runner running at a time (flock)
    - SIGTERM finishes the current item, persists, exits clean
    - `--health-check` flag exits 0/1 based on platform reachability

Usage in a runner:

    from lib.runtime import (
        SingletonLock, install_shutdown_handler, run_health_checks,
    )

    with SingletonLock("comment-poster"):
        install_shutdown_handler()
        if "--health-check" in sys.argv:
            sys.exit(0 if run_health_checks(["wp", "fb"]) else 1)
        # ... actual work ...
"""

from lib.runtime.health_check import (
    HealthCheckResult,
    HealthProbe,
    run_health_checks,
)
from lib.runtime.shutdown import (
    ShutdownRequested,
    install_shutdown_handler,
    is_shutdown_requested,
)
from lib.runtime.singleton import LockAcquisitionError, SingletonLock

__all__ = [
    "HealthCheckResult",
    "HealthProbe",
    "LockAcquisitionError",
    "ShutdownRequested",
    "SingletonLock",
    "install_shutdown_handler",
    "is_shutdown_requested",
    "run_health_checks",
]
