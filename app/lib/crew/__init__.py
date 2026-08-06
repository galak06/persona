"""CrewAI-based content-opportunity scout.

Public surface:
    from lib.crew import run_crew_scout, IdeaCandidate, ScoutOutput

`run_crew_scout` combines `lib.gsc_scout_scoring`'s real GSC-grounded
opportunities with live web-search discovery (Serper, via a CrewAI agent
pointed at DeepSeek) into one deduped, ranked list, then writes winners to
the same `content_ideas` table `lib.gsc_scout.run_scout` already writes to
(tagged `"source": "crewai_scout"` so the two are distinguishable).

See `lib.crew.scout` for the orchestration, `lib.crew.agent` for the CrewAI
`Agent`/`Task` construction, `lib.crew.context` for prompt/context building,
and `lib.crew.models` for the structured output contract.
"""

from lib.crew.models import IdeaCandidate, ScoutOutput
from lib.crew.scout import run_crew_scout

__all__ = ["IdeaCandidate", "ScoutOutput", "run_crew_scout"]
