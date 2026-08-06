"""Pydantic output contract for the CrewAI post-categorizer agent.

One `Task` target (see `lib.crew.categorizer.agent`): `CategoryChoice` --
the model's pick from the brand's REAL WordPress categories, never an
invented name. Same rationale as `lib.crew.editor.models.QualityVerdict`:
CrewAI's `output_pydantic` requires a `BaseModel`, though this pipeline
parses raw JSON manually instead (see `lib.crew.categorizer.execute`).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class CategoryChoice(BaseModel):
    """The categorizer agent's pick of one real WordPress category."""

    category_name: str = Field(
        description="The single best-fitting category name, copied EXACTLY as given in "
        "the provided list (character-for-character, including any '&' or capitalization) "
        "-- or an empty string if none of the provided categories genuinely fit this post's "
        "content. Never invent a category name not in the provided list."
    )
