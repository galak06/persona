"""FastAPI approval sidecar package.

Exposes the Telegram approval queues (comment_queue, blog_post_queue) over a
localhost HTTP API so the web UI can decide in parallel with Telegram. The
queue-state helpers in ``state.py`` are flock-protected so concurrent web /
Telegram decisions cannot race.
"""
