"""Social-post crew: one published WP post -> FB Page caption + IG caption +
art direction for the single shared hook image.

Public surface mirrors `lib.crew.reels`: builders plus the one executing
function. Orchestrated by `scripts/crewai_social_posts_pipeline.py`; published
by `recipe-publisher/workers/worker_wp_ideas_social_post.py` after human
approval on the frontend (`SocialPosts.tsx`).
"""

from lib.crew.socialpost.agent import build_social_post_agent, build_social_post_task
from lib.crew.socialpost.execute import execute_social_post_crew

__all__ = [
    "build_social_post_agent",
    "build_social_post_task",
    "execute_social_post_crew",
]
