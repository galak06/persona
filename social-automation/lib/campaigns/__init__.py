"""Campaign execution primitives.

Extracted from ``scripts/campaign_worker.py`` so the per-campaign
"run one stage" logic is library code (testable, importable from other
runners) rather than CLI-internal.

Public surface:
    - :func:`run_campaign` — execute one stage of one campaign.
    - :class:`CampaignRunResult` — structured return value.
    - :class:`LockHeldError` — raised when ``worker.lock`` is held.

The outer cron-evaluation loop, telegram-notifier, and "which campaign
do I run next" logic remains in ``scripts/campaign_worker.py``.
"""

from lib.campaigns.runner import CampaignRunResult, LockHeldError, run_campaign

__all__ = ["CampaignRunResult", "LockHeldError", "run_campaign"]
