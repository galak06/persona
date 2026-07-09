# pyright: reportMissingImports=false
"""FastAPI router for recipe card webhook.

POST /webhooks/recipe-card — triggered by WordPress on post publish.
Runs the full pipeline in the background:
  fetch post → parse recipe → generate PDF → upload → inject download button.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel

from lib.recipe_card import content_parser, pdf_generator, wp_sync

router = APIRouter(tags=["recipe-card"])
_log = logging.getLogger(__name__)


class RecipeCardWebhookPayload(BaseModel):
    post_id: int
    post_status: str
    post_type: str = "post"


@router.post("/webhooks/recipe-card", status_code=202)
async def recipe_card_webhook(
    payload: RecipeCardWebhookPayload,
    background_tasks: BackgroundTasks,
) -> dict:
    """Accept a WordPress publish event and queue PDF generation."""
    if payload.post_type != "post":
        return {"status": "skipped", "reason": f"post_type={payload.post_type!r} is not 'post'"}
    if payload.post_status != "publish":
        return {"status": "skipped", "reason": f"post_status={payload.post_status!r} is not 'publish'"}

    background_tasks.add_task(_run_pipeline, payload.post_id)
    return {"status": "accepted", "post_id": payload.post_id}


def _run_pipeline(post_id: int) -> None:
    """Full recipe-card pipeline; runs in a background task."""
    try:
        _log.info("recipe_card_pipeline start post_id=%d", post_id)

        # 1. Fetch post data from WordPress
        post_data = wp_sync.fetch_post_data(post_id)
        title: str = post_data["title"]
        content: str = post_data["content"]

        # 2. Parse recipe structure from HTML content
        recipe: content_parser.RecipeData = content_parser.parse_recipe(title, content)

        # 3. Guard: skip if nothing parseable
        if not recipe.ingredients and not recipe.instructions:
            _log.warning(
                "recipe_card_pipeline skipped post_id=%d: no ingredients or instructions found",
                post_id,
            )
            return

        # 4. Fetch Nalla stamp image
        nalla_stamp_bytes: bytes = wp_sync.fetch_nalla_stamp()

        # 5. Generate PDF
        pdf_bytes: bytes = pdf_generator.generate_recipe_card_pdf(
            title=recipe.title,
            ingredients=recipe.ingredients,
            instructions=recipe.instructions,
            nalla_stamp_bytes=nalla_stamp_bytes,
            cook_temp=recipe.cook_temp,
            cook_time=recipe.cook_time,
        )

        # 6. Upload PDF to WordPress media library
        filename = f"recipe-card-{post_id}.pdf"
        pdf_url: str = wp_sync.upload_pdf(pdf_bytes, filename)

        # 7. Inject download button into the post
        wp_sync.inject_download_button(post_id, pdf_url)

        _log.info(
            "recipe_card_pipeline success post_id=%d pdf_url=%s",
            post_id,
            pdf_url,
        )

    except Exception as exc:
        _log.error(
            "recipe_card_pipeline failed post_id=%d error=%s",
            post_id,
            exc,
            exc_info=True,
        )
