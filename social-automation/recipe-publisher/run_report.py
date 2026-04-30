"""Markdown run-report writer for recipe-publisher.

Extracted out of recipe_publisher.py to keep that module under the 300-line
ceiling. Imports nothing from the orchestrator — it takes plain values.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from generators.image import GeneratedImage
    from generators.recipe import Recipe
    from recipe_publisher import RunResult

REPORT_DIR = Path(os.getenv("RECIPE_REPORT_DIR", "/mnt/dogfoodandfun"))


def write_report(
    result: "RunResult",
    recipe: "Recipe | None",
    image: "GeneratedImage | None",
) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = REPORT_DIR / f"recipe-publisher-report-{today}.md"
    lines = [
        f"# recipe-publisher run — {today}",
        "",
        f"- **status:** `{result.status}`",
        f"- **dry_run:** `{result.dry_run}`",
        f"- **topic:** {result.topic or '_(not selected)_'}",
        f"- **started_at:** {result.started_at}",
        f"- **finished_at:** {result.finished_at or '_(unfinished)_'}",
    ]
    if result.error:
        lines += ["", "## ❌ Error", "", f"```\n{result.error}\n```"]
    if result.warnings:
        lines += ["", "## ⚠️ Warnings", *(f"- {w}" for w in result.warnings)]
    if recipe:
        lines += [
            "",
            "## Recipe",
            "",
            f"**Title:** {recipe.title}",
            f"**Slug:** `{recipe.slug}`",
            f"**Meta description:** {recipe.meta_description}",
            "",
            "### Body (markdown)",
            "",
            recipe.body_markdown,
            "",
            "### IG caption",
            "",
            f"```\n{recipe.ig_caption}\n```",
        ]
    if image:
        lines += [
            "",
            "## Image",
            "",
            f"- **URL:** {image.url}",
            f"- **Alt:** {image.alt_text}",
        ]
    if result.wp_post_url:
        lines += ["", f"## WP post: {result.wp_post_url}"]
    if result.ig_permalink:
        lines += [f"## IG post: {result.ig_permalink}"]
    if result.pinterest_permalinks:
        lines += ["", "## Pinterest pins", ""]
        lines += [f"- {p}" for p in result.pinterest_permalinks]
    path.write_text("\n".join(lines) + "\n")
    return path
