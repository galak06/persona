"""Business-rule policies — single source of truth for decisions.

Replaces logic that drifted across:
    - scripts/comment_approver.py (Python implementation)
    - lib/comment_graph.py (drifted Python implementation)
    - .claude/skills/comment-composer/SKILL.md (markdown spec — added
      a `template_reused_recently` rule never implemented in Python)

Public surface:
    - `ApprovalContext` — input dataclass (previously-posted set, template usage)
    - `ApprovalDecision` — result dataclass (needed: bool, reason: str)
    - `requires_approval(item, ctx)` — the single authoritative check
"""

from lib.policy.approval import requires_approval
from lib.policy.approval_context import ApprovalContext
from lib.policy.approval_decision import ApprovalDecision, ApprovalReason

__all__ = [
    "ApprovalContext",
    "ApprovalDecision",
    "ApprovalReason",
    "requires_approval",
]
