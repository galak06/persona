"""CrewAI-based content-opportunity scout.

Public surface:
    from lib.crew import run_crew_scout, IdeaCandidate, ScoutOutput

`run_crew_scout` runs a two-hop pipeline: the Trends stage
(`lib.crew.trends`) combines `lib.gsc_scout_scoring`'s real GSC-grounded
opportunities with live web-search discovery (Serper) and the Instagram
trends feed into a deduped list of trend signals, then the Idea stage
(`lib.crew.idea`) synthesizes those signals (both via CrewAI agents pointed
at DeepSeek) into one deduped, ranked list of content ideas, and writes
winners to the same `content_ideas` table `lib.gsc_scout.run_scout` already
writes to (tagged `"source": "crewai_scout"` so the two are distinguishable).

See `lib.crew.scout` for the orchestration, `lib.crew.trends` and
`lib.crew.idea` for the CrewAI `Agent`/`Task` construction of each stage,
`lib.crew.context` for shared prompt/context building, and
`lib.crew.models` for the structured output contract.
"""

from lib.crew.models import IdeaCandidate, ScoutOutput
from lib.crew.scout import run_crew_scout

__all__ = ["IdeaCandidate", "ScoutOutput", "run_crew_scout"]
