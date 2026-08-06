"""CrewAI-based post-categorizer stage.

Public surface:
    from lib.crew.categorizer import CategoryChoice, execute_categorizer_crew

Given a post's actual content (title + primary keyword + mascot angle) and
the brand's REAL WordPress category names, picks the single best-fitting one
-- or none, rather than guessing wrong. `lib.crew.draft._resolve_category_id`
is the sole consumer: it tries an exact-string match first (free, no LLM
call) and only falls back to this stage when that misses. See
`lib.crew.categorizer.agent` for CrewAI `Agent`/`Task` construction and
`lib.crew.categorizer.execute` for the manual-JSON-parsing `kickoff()` wrapper.
"""

from lib.crew.categorizer.agent import (
    build_categorizer_agent,
    build_categorizer_task,
    build_categorizer_task_description,
)
from lib.crew.categorizer.execute import execute_categorizer_crew
from lib.crew.categorizer.models import CategoryChoice

__all__ = [
    "CategoryChoice",
    "build_categorizer_agent",
    "build_categorizer_task",
    "build_categorizer_task_description",
    "execute_categorizer_crew",
]
