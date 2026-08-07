"""CrewAI-based market-trend scout.

Public surface:
    from lib.crew.trends import (
        TrendSignal, TrendsOutput,
        build_trends_agent, build_trends_task, execute_trends_crew,
    )

Surfaces a deduped, keyword-level list of brand-relevant trend signals by
merging real GSC ranking data (carried forward as-is), live web search
(`SerperDevTool`), and -- when available -- the public Instagram trends
feed (`lib.crew.trends.instagram_scraper.fetch_instagram_trends_text`).
`lib.crew.scout.run_crew_scout` is the sole orchestrator: it builds the
prompt via `lib.crew.trends.prompts.build_trends_task_description`, then
calls `build_trends_agent`/`build_trends_task`/`execute_trends_crew` and
hands the resulting `TrendsOutput.signals` to the downstream Idea stage
(`lib.crew.idea`) for synthesis into publishable post ideas -- this
subpackage never synthesizes titles or ideas itself.

See `lib.crew.trends.agent` for CrewAI `Agent`/`Task` construction,
`lib.crew.trends.prompts` for the prompt builder, `lib.crew.trends.execute`
for the manual-JSON-parsing `kickoff()` wrapper, and
`lib.crew.trends.instagram_scraper` for the Instagram trend-signal source.
"""

from lib.crew.trends.agent import build_trends_agent, build_trends_task
from lib.crew.trends.execute import execute_trends_crew
from lib.crew.trends.models import TrendSignal, TrendsOutput

__all__ = [
    "TrendSignal",
    "TrendsOutput",
    "build_trends_agent",
    "build_trends_task",
    "execute_trends_crew",
]
