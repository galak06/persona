"""IG follow-scout: discover competitor-adjacent NA users and follow them.

Consumed by `scripts/ig_follow.py`. Internal cross-references use
relative imports (`from .X import Y`) so the package works whether
imported as `lib.ig_follow` (mypy / tests / project-root scripts) or
as bare `ig_follow` (runtime scripts using `sys.path.insert(lib/)`).

Submodules:
    candidate         — Candidate dataclass + SourceSignal Literal
    constants         — DAILY_FOLLOW_CEILING, FOLLOW_JITTER_SECONDS
    state             — history I/O, daily-cap accounting (.claude/state/)
    targets           — load competitors with non-null ig_handle
    scout_followers   — scrape the followers tab of a source account
    scout_engagers    — scrape likers/commenters on recent posts
    geo_filter        — bool|None heuristic for North-America-likely
    follower          — perform the follow click via Playwright
    exceptions        — IGActionBlockedError, IGUserNotFoundError
"""

from __future__ import annotations

from .candidate import Candidate as Candidate
from .candidate import SourceSignal as SourceSignal
from .constants import DAILY_FOLLOW_CEILING as DAILY_FOLLOW_CEILING
from .constants import FOLLOW_JITTER_SECONDS as FOLLOW_JITTER_SECONDS
from .exceptions import IGActionBlockedError as IGActionBlockedError
from .exceptions import IGUserNotFoundError as IGUserNotFoundError
from .follower import FollowOutcome as FollowOutcome
from .follower import FollowResult as FollowResult
from .follower import follow_user as follow_user
from .geo_filter import is_north_america_likely as is_north_america_likely
from .scout_engagers import scout_engagers as scout_engagers
from .scout_followers import scout_followers as scout_followers
from .state import FollowRecord as FollowRecord
from .state import follows_in_window as follows_in_window
from .state import follows_today as follows_today
from .state import is_already_followed as is_already_followed
from .state import load_history as load_history
from .state import record_follow as record_follow
from .targets import IGSource as IGSource
from .targets import ig_sources as ig_sources
from .targets import round_robin_sources as round_robin_sources

__all__ = [
    "DAILY_FOLLOW_CEILING",
    "FOLLOW_JITTER_SECONDS",
    "Candidate",
    "FollowOutcome",
    "FollowRecord",
    "FollowResult",
    "IGActionBlockedError",
    "IGSource",
    "IGUserNotFoundError",
    "SourceSignal",
    "follow_user",
    "follows_in_window",
    "follows_today",
    "ig_sources",
    "is_already_followed",
    "is_north_america_likely",
    "load_history",
    "record_follow",
    "round_robin_sources",
    "scout_engagers",
    "scout_followers",
]
