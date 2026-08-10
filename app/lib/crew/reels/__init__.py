"""WP-post -> IG/FB Reels crew.

One agent, one shared plan (`agent`, `prompts`, `execute` -> `ReelPlan`): a
fixed 5-beat on-screen script, each beat carrying overlay text plus an
image-generation prompt. Two possible image sources for those same beats,
both feeding the identical overlay+slideshow pipeline (see
`scripts/crewai_reels_pipeline.py` and
`recipe-publisher/workers/worker_wp_ideas_reel.py`):

    Primary  -- OpenArt (`openart_client`): 5 distinct AI-generated images,
                one per beat's own `image_prompt`, via OpenArt's MCP server
                (`mcp.openart.ai`).
    Fallback -- reached only when OpenArt fails for any reason (including
                running out of credits): the WP post's existing hero image,
                reused for all 5 beats.

Public surface:
    from lib.crew.reels import build_reels_agent, build_reels_task, execute_reels_crew
"""

from lib.crew.reels.agent import build_reels_agent, build_reels_task
from lib.crew.reels.execute import execute_reels_crew

__all__ = [
    "build_reels_agent",
    "build_reels_task",
    "execute_reels_crew",
]
