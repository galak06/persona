"""CrewAI-based quality-editor gate.

Public surface:
    from lib.crew.editor import QualityVerdict, execute_editor_crew

Scores one already-assembled post (title + body_html) against a fixed
rubric -- coherence, on-brand voice, structural completeness against its
outline, and absence of stray LLM artifacts -- and returns a `QualityVerdict`.
`lib.crew.validate.validate_draft` is the sole consumer: it applies the real
pass/fail threshold and the stray-artifact override; this subpackage only
produces the raw assessment. See `lib.crew.editor.agent` for CrewAI
`Agent`/`Task` construction, `lib.crew.editor.prompts` for the rubric prompt,
and `lib.crew.editor.execute` for the manual-JSON-parsing `kickoff()` wrapper.
"""

from lib.crew.editor.agent import build_editor_agent, build_editor_task
from lib.crew.editor.execute import execute_editor_crew
from lib.crew.editor.models import QualityVerdict

__all__ = [
    "QualityVerdict",
    "build_editor_agent",
    "build_editor_task",
    "execute_editor_crew",
]
