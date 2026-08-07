"""CrewAI-based content-idea synthesis stage.

Public surface:
    from lib.crew.idea import build_idea_agent, build_idea_task, execute_idea_crew

Second hop of the split scout pipeline: takes the trend signals
`lib.crew.trends` already gathered (plus brand voice + seed keywords) and
synthesizes them into a final, deduped, ranked list of concrete publishable
content ideas (`lib.crew.models.ScoutOutput`). `lib.crew.scout.run_crew_scout`
is the sole consumer; orchestration is inlined there rather than in a
separate `orchestrator.py`, matching `lib.crew.editor`'s shape. See
`lib.crew.idea.agent` for CrewAI `Agent`/`Task` construction,
`lib.crew.idea.prompts` for the synthesis prompt, and `lib.crew.idea.execute`
for the manual-JSON-parsing `kickoff()` wrapper.
"""

from lib.crew.idea.agent import build_idea_agent, build_idea_task
from lib.crew.idea.execute import execute_idea_crew

__all__ = [
    "build_idea_agent",
    "build_idea_task",
    "execute_idea_crew",
]
