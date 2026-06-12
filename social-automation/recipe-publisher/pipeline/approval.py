# pyright: reportMissingImports=false
"""Phase 5 of the recipe-publisher pipeline: approval.

The human gate. Promotes a recipe from ``pending`` to ``approved`` (cleared to
publish) or ``rejected``. Exposed to the UI via the approval API endpoints; the
draft-before-publish rule means nothing reaches the publishing phase without an
explicit ``approve`` here.
"""

from __future__ import annotations

from recipe_db.models import ContentStatus
from recipe_db.repository import RecipeRepository

from pipeline.checkpoint import StructuredLogger, checkpoint

PHASE = "approval"


class ApprovalError(ValueError):
    """Raised when a recipe can't be approved/rejected from its current state."""


class ApprovalService:
    """Promotes pending recipes to approved/rejected (the human gate)."""

    def __init__(
        self,
        repo: RecipeRepository,
        *,
        logger: StructuredLogger | None = None,
    ) -> None:
        self._repo = repo
        self._log = logger

    def approve(self, recipe_id: str) -> None:
        """Promote a pending recipe to approved."""
        self._transition(recipe_id, ContentStatus.APPROVED)

    def reject(self, recipe_id: str) -> None:
        """Reject a pending recipe (it will not be published)."""
        self._transition(recipe_id, ContentStatus.REJECTED)

    def _transition(self, recipe_id: str, target: str) -> None:
        row = self._repo.get_recipe(recipe_id)
        if row is None:
            raise ApprovalError(f"no recipe with id '{recipe_id}'")
        if row.content_status != ContentStatus.PENDING:
            raise ApprovalError(
                f"can only approve/reject a PENDING recipe; "
                f"'{recipe_id}' is '{row.content_status}'"
            )
        self._repo.set_content_status(recipe_id, target)
        checkpoint(
            PHASE,
            ok=True,
            logger=self._log,
            recipe_id=recipe_id,
            from_status=ContentStatus.PENDING,
            to_status=target,
        )
