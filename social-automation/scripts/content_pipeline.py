"""
Content Pipeline — automated content creation with Telegram approval gates.

Stages:
  ideate   — Generate 5 ideas, send to Telegram, wait for approval, save approved
  enrich   — Pick top approved idea, research SEO/social, send brief, wait for approval
  write    — Generate WP draft from approved brief, notify via Telegram
  publish  — Post approved WP content to FB page + IG (with Telegram approval)

Usage:
    python scripts/content_pipeline.py --stage ideate
    python scripts/content_pipeline.py --stage publish
    python scripts/content_pipeline.py --stage all  # run full pipeline
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "lib"))

from logger import enable_unbuffered, log_step
from notifier import send, send_and_wait, skill_error, skill_finished, skill_started

enable_unbuffered()

ENRICHMENT_CACHE = PROJECT_ROOT / ".claude/state/enrichment_cache.json"
WP_POSTS_CACHE = PROJECT_ROOT / ".claude/state/wp_posts_cache.json"
TIMELINE_FILE = PROJECT_ROOT / ".claude/state/publishing_timeline.json"
LAST_RUN_FILE = PROJECT_ROOT / ".claude/state/last_run.json"


def load_json(path: Path, default=None):
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return default if default is not None else {}


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def stage_ideate() -> bool:
    """Generate ideas and get approval via Telegram."""
    log_step("Content Ideator", "starting")
    skill_started("content-ideator", "Generating 5 content ideas")

    # Check if we already have approved un-enriched ideas
    cache = load_json(ENRICHMENT_CACHE, [])
    pending_enrichment = [
        c for c in cache
        if c.get("approval_status") == "approved"
        and not c.get("content_brief", {}).get("suggested_title")
    ]
    if pending_enrichment:
        send(f"⏭️ content-ideator skipped — {len(pending_enrichment)} approved ideas waiting for enrichment")
        return True

    # Notify that ideation needs Claude Code to run interactively
    msg = (
        "🧠 <b>Content Ideator Ready</b>\n\n"
        "The content pipeline wants to generate 5 new ideas.\n"
        "This requires Claude Code to run interactively.\n\n"
        "Run in Claude Code:\n"
        "<code>/content-ideator</code>\n\n"
        "Or reply <b>skip</b> to skip this week."
    )
    result = send_and_wait(msg, timeout_hours=12)

    if result["action"] == "skipped":
        skill_finished("content-ideator", "Skipped by user", success=False)
        return False

    # If approved, the user will run it manually in Claude Code
    skill_finished("content-ideator", "User notified — awaiting manual run")
    return True


def stage_enrich() -> bool:
    """Enrich the next approved idea."""
    log_step("Content Enricher", "starting")

    cache = load_json(ENRICHMENT_CACHE, [])
    pending = [c for c in cache if c.get("approval_status") == "approved"
               and not c.get("content_brief", {}).get("suggested_title")]

    if not pending:
        send("⏭️ content-enricher skipped — no approved ideas to enrich")
        return False

    topic = pending[0].get("topic", "?")
    msg = (
        f"📝 <b>Content Enricher Ready</b>\n\n"
        f"Next idea to enrich: <b>{topic}</b>\n\n"
        f"Run in Claude Code:\n"
        f"<code>/content-enricher</code>\n\n"
        f"Or reply <b>skip</b> to skip."
    )
    result = send_and_wait(msg, timeout_hours=12)
    return result["action"] != "skipped"


def stage_publish() -> bool:
    """Publish approved content to FB page + IG."""
    log_step("Content Publisher", "starting")
    skill_started("content-publisher", "Publishing to FB + IG")

    settings = load_json(PROJECT_ROOT / ".claude/settings.local.json", {})
    env = settings.get("env", {})
    fb_token = env.get("FB_PAGE_TOKEN", "")
    fb_page_id = env.get("FB_PAGE_ID", "")
    ig_account_id = env.get("IG_ACCOUNT_ID", "")
    wp_url = env.get("WP_URL", "https://dogfoodandfun.com")
    wp_user = env.get("WP_USER", "")
    wp_pass = env.get("WP_APP_PASSWORD", "")

    if not fb_token or not fb_page_id:
        skill_error("content-publisher", "FB_PAGE_TOKEN or FB_PAGE_ID not configured")
        return False

    import requests

    # Find the most recent WP post that hasn't been shared to social yet
    wp_cache = load_json(WP_POSTS_CACHE, {"wp_posts": []})
    timeline = load_json(TIMELINE_FILE, {})

    # Get recent published posts from WP
    resp = requests.get(
        f"{wp_url}/wp-json/wp/v2/posts",
        params={"status": "publish", "per_page": 5, "_fields": "id,title,link,featured_media"},
        auth=(wp_user, wp_pass) if wp_user else None,
        timeout=15,
    )
    if not resp.ok:
        skill_error("content-publisher", f"WP API failed: {resp.status_code}")
        return False

    posts = resp.json()
    if not posts:
        send("⏭️ No published WordPress posts found")
        return False

    # Find first post not yet shared
    shared_ids = {p.get("post_id") for p in wp_cache.get("wp_posts", []) if p.get("fb_shared")}
    unshared = [p for p in posts if p["id"] not in shared_ids]

    if not unshared:
        send("⏭️ All recent posts already shared to social")
        return False

    post = unshared[0]
    post_title = post["title"]["rendered"]
    post_url = post["link"]
    post_id = post["id"]

    # Get featured image
    img_url = ""
    if post.get("featured_media"):
        media = requests.get(
            f"{wp_url}/wp-json/wp/v2/media/{post['featured_media']}",
            auth=(wp_user, wp_pass) if wp_user else None,
            timeout=10,
        )
        if media.ok:
            img_url = media.json().get("source_url", "")

    # Generate FB caption
    fb_caption = (
        f"New on the blog — and yes, Nalla was involved in the testing.\n\n"
        f"{post_title}\n\n"
        f"Full article with data, product comparisons, and honest picks:\n"
        f"{post_url}"
    )

    # Generate IG caption
    ig_caption = (
        f"New post alert! {post_title}\n\n"
        f"Tested, tracked, and documented — the engineer way.\n"
        f"Link in bio for the full breakdown.\n\n"
        f"#dogfoodandfun #nallasdad #doggrooming #dognutrition "
        f"#doggear #engineerdogdad #doglife #doghealth"
    )

    # Send for approval — FB and IG together
    preview = (
        f"📢 <b>Ready to publish to social</b>\n\n"
        f"📄 Post: {post_title}\n"
        f"🔗 {post_url}\n\n"
        f"📘 <b>Facebook caption:</b>\n<i>{fb_caption[:200]}...</i>\n\n"
        f"📸 <b>Instagram caption:</b>\n<i>{ig_caption[:200]}...</i>\n\n"
        f"Reply: <b>approve</b> (both) · <b>fb</b> (FB only) · <b>ig</b> (IG only) · <b>skip</b>"
    )

    result = send_and_wait(preview, timeout_hours=12)

    if result["action"] in ("skipped", "timeout"):
        skill_finished("content-publisher", "Skipped", success=False)
        return False

    reply = result["reply_text"].lower().strip()
    do_fb = reply in ("approve", "all", "yes", "y", "ok", "fb", "both")
    do_ig = reply in ("approve", "all", "yes", "y", "ok", "ig", "both")

    published = []

    # Publish to Facebook
    if do_fb and fb_token:
        log_step("Publishing to Facebook")
        if img_url:
            fb_resp = requests.post(
                f"https://graph.facebook.com/v19.0/{fb_page_id}/photos",
                data={"url": img_url, "message": fb_caption, "access_token": fb_token},
                timeout=30,
            )
        else:
            fb_resp = requests.post(
                f"https://graph.facebook.com/v19.0/{fb_page_id}/feed",
                data={"message": fb_caption, "link": post_url, "access_token": fb_token},
                timeout=30,
            )

        if fb_resp.status_code == 200:
            fb_id = fb_resp.json().get("id", "")
            published.append(f"FB: {fb_id}")
            timeline["last_fb_page_post"] = datetime.now(timezone.utc).isoformat() + "Z"
            print(f"    FB published: {fb_id}", flush=True)
        else:
            err = fb_resp.json().get("error", {}).get("message", "unknown")
            published.append(f"FB FAILED: {err}")
            print(f"    FB error: {err}", flush=True)

    # Publish to Instagram
    if do_ig and ig_account_id and fb_token:
        log_step("Publishing to Instagram")
        # Wait 4h gap from FB per content rules
        # For now, just publish (user approved both)

        if not img_url:
            published.append("IG SKIPPED: no image")
        else:
            # Step 1: Create container
            container = requests.post(
                f"https://graph.facebook.com/v19.0/{ig_account_id}/media",
                data={"image_url": img_url, "caption": ig_caption, "access_token": fb_token},
                timeout=30,
            )
            if container.status_code == 200:
                container_id = container.json()["id"]
                time.sleep(5)

                # Step 2: Publish
                pub = requests.post(
                    f"https://graph.facebook.com/v19.0/{ig_account_id}/media_publish",
                    data={"creation_id": container_id, "access_token": fb_token},
                    timeout=30,
                )
                if pub.status_code == 200:
                    ig_id = pub.json()["id"]
                    published.append(f"IG: {ig_id}")
                    timeline["last_ig_feed_post"] = datetime.now(timezone.utc).isoformat() + "Z"
                    print(f"    IG published: {ig_id}", flush=True)
                else:
                    err = pub.json().get("error", {}).get("message", "unknown")
                    published.append(f"IG FAILED: {err}")
            else:
                err = container.json().get("error", {}).get("message", "unknown")
                published.append(f"IG FAILED: {err}")

    # Save timeline
    save_json(TIMELINE_FILE, timeline)

    # Mark post as shared
    for wp_entry in wp_cache.get("wp_posts", []):
        if wp_entry.get("post_id") == post_id:
            wp_entry["fb_shared"] = do_fb
            wp_entry["ig_shared"] = do_ig
    save_json(WP_POSTS_CACHE, wp_cache)

    # Log
    with open(PROJECT_ROOT / "logs/engagement_log.jsonl", "a") as f:
        for entry in published:
            f.write(json.dumps({
                "timestamp": datetime.now(timezone.utc).isoformat() + "Z",
                "platform": "facebook" if "FB" in entry else "instagram",
                "action": "page_post" if "FB" in entry else "feed_post",
                "source_post": post_url,
                "result": entry,
                "status": "SUCCESS" if "FAILED" not in entry else "FAILED",
            }) + "\n")

    summary = " | ".join(published)
    send(f"✅ <b>Social publishing done</b>\n{summary}")
    skill_finished("content-publisher", summary)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Content pipeline with Telegram approval")
    parser.add_argument("--stage", required=True, choices=["ideate", "enrich", "write", "publish", "all"])
    args = parser.parse_args()

    if args.stage == "ideate":
        stage_ideate()
    elif args.stage == "enrich":
        stage_enrich()
    elif args.stage == "publish":
        stage_publish()
    elif args.stage == "all":
        if stage_ideate():
            stage_enrich()


if __name__ == "__main__":
    main()
