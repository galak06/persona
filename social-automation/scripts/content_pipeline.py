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
from datetime import UTC, date, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from lib.bootstrap import init_script
settings, log = init_script(__name__)

import blog_post_queue
from lib.logger import log_step
from notifier import (
    send,
    send_and_wait,
    send_video,
    skill_error,
    skill_finished,
    skill_started,
)


ENRICHMENT_CACHE = PROJECT_ROOT / ".claude/state/enrichment_cache.json"
WP_POSTS_CACHE = PROJECT_ROOT / ".claude/state/wp_posts_cache.json"
TIMELINE_FILE = PROJECT_ROOT / ".claude/state/publishing_timeline.json"
LAST_RUN_FILE = settings.paths.last_run
CAMPAIGNS_ROOT = settings.paths.campaigns_dir


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
        c
        for c in cache
        if c.get("approval_status") == "approved"
        and not c.get("content_brief", {}).get("suggested_title")
    ]
    if pending_enrichment:
        send(
            f"⏭️ content-ideator skipped — {len(pending_enrichment)} approved ideas waiting for enrichment"
        )
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


def _check_ideation_freshness(max_age_days: int = 3) -> bool:
    """Verify that Content Ideas has run recently. Returns True if fresh or user overrides."""
    hist_path = PROJECT_ROOT.parent / "dogfoodandfun/state/ideation_history.json"
    last_run_str = None
    if hist_path.exists():
        try:
            hist = json.loads(hist_path.read_text())
            last_run_str = hist.get("last_run")
        except Exception:
            pass

    if not last_run_str:
        msg = (
            "⚠️ <b>Content Ideas never run</b>\n\n"
            "The ideation history is missing or empty. This stage depends on fresh "
            "ideas to ensure we're targeting the right clusters and trends.\n\n"
            "Run <code>/content-ideator</code> first.\n\n"
            "Reply <b>force</b> to proceed anyway (not recommended)."
        )
        result = send_and_wait(msg, timeout_hours=1)
        return result["action"] == "approved" or result.get("reply_text", "").lower() == "force"

    # Clean historical timestamps like "+00:00Z"
    cleaned = last_run_str.rstrip("Z")
    try:
        last_run = datetime.fromisoformat(cleaned)
    except ValueError:
        # fallback for naive+Z
        last_run = datetime.fromisoformat(last_run_str.replace("Z", "+00:00"))

    if last_run.tzinfo is None:
        last_run = last_run.replace(tzinfo=UTC)

    age = datetime.now(UTC) - last_run
    if age.total_seconds() > (max_age_days * 86400):
        days = age.total_seconds() / 86400
        msg = (
            f"⏳ <b>Content Ideas are stale</b> ({days:.1f} days old)\n\n"
            f"The content strategy requires a fresh ideation run every {max_age_days} days "
            "to capture real-world signals (IG trends, Google Trends US/CA).\n\n"
            "Recommended: Run <code>/content-ideator</code> now.\n\n"
            "Reply <b>force</b> to continue with stale ideas."
        )
        result = send_and_wait(msg, timeout_hours=1)
        return result["action"] == "approved" or result.get("reply_text", "").lower() == "force"

    return True


def stage_enrich() -> bool:
    """Enrich the next approved idea."""
    log_step("Content Enricher", "starting")

    if not _check_ideation_freshness():
        return False

    cache = load_json(ENRICHMENT_CACHE, [])
    pending = [
        c
        for c in cache
        if c.get("approval_status") == "approved"
        and not c.get("content_brief", {}).get("suggested_title")
    ]

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

    # Persist the FB+IG pair to the blog-post queue BEFORE asking Telegram.
    # The web UI polls this file, so the item must be visible the moment the
    # approval gate opens. Idempotent — re-runs with identical captions reuse
    # the same item_id.
    item_id = blog_post_queue.enqueue_blog_post_pair(
        post_id=post_id,
        post_title=post_title,
        post_url=post_url,
        fb_caption=fb_caption,
        ig_caption=ig_caption,
        image_url=img_url or None,
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

    # Phase 3's notifier poll loop accepts ``item_id`` + ``queue_path`` to also
    # watch for a web-UI decision; without them only Telegram replies count.
    # If Phase 3 hasn't landed (older signature), drop back to the bare call.
    try:
        result = send_and_wait(
            preview,
            timeout_hours=12,
            item_id=item_id,
            queue_path=blog_post_queue.QUEUE_PATH,
        )
    except TypeError:
        result = send_and_wait(preview, timeout_hours=12)

    if result["action"] in ("skipped", "timeout"):
        # Skip is a terminal state — drop from queue so the web UI stops
        # showing it.
        blog_post_queue.mark_published(item_id)
        skill_finished("content-publisher", "Skipped", success=False)
        return False

    # Determine fan-out: Telegram replies use ``reply_text`` semantics; web UI
    # writes the channel directly onto the queue item. Read both and prefer
    # web UI when present (the notifier sets ``decided_by="web_ui"`` on win).
    decision = blog_post_queue.get_decision(item_id) or {}
    decided_by = decision.get("decided_by")
    channel = decision.get("channel")

    if decided_by == "web_ui":
        # Web UI may have edited captions; prefer the persisted version.
        if isinstance(decision.get("fb_caption"), str) and decision["fb_caption"]:
            fb_caption = decision["fb_caption"]
        if isinstance(decision.get("ig_caption"), str) and decision["ig_caption"]:
            ig_caption = decision["ig_caption"]
        do_fb = channel in ("both", "fb_only")
        do_ig = channel in ("both", "ig_only")
    else:
        reply = result["reply_text"].lower().strip()
        do_fb = reply in ("approve", "all", "yes", "y", "ok", "fb", "both")
        do_ig = reply in ("approve", "all", "yes", "y", "ok", "ig", "both")

    published = []

    # Publish to Facebook
    if do_fb and fb_token:
        log_step("Publishing to Facebook")
        if img_url:
            fb_resp = requests.post(
                f"https://graph.facebook.com/v23.0/{fb_page_id}/photos",
                data={"url": img_url, "message": fb_caption, "access_token": fb_token},
                timeout=30,
            )
        else:
            fb_resp = requests.post(
                f"https://graph.facebook.com/v23.0/{fb_page_id}/feed",
                data={"message": fb_caption, "link": post_url, "access_token": fb_token},
                timeout=30,
            )

        if fb_resp.status_code == 200:
            fb_id = fb_resp.json().get("id", "")
            published.append(f"FB: {fb_id}")
            timeline["last_fb_page_post"] = datetime.now(UTC).isoformat()
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
                f"https://graph.facebook.com/v23.0/{ig_account_id}/media",
                data={"image_url": img_url, "caption": ig_caption, "access_token": fb_token},
                timeout=30,
            )
            if container.status_code == 200:
                container_id = container.json()["id"]
                time.sleep(5)

                # Step 2: Publish
                pub = requests.post(
                    f"https://graph.facebook.com/v23.0/{ig_account_id}/media_publish",
                    data={"creation_id": container_id, "access_token": fb_token},
                    timeout=30,
                )
                if pub.status_code == 200:
                    ig_id = pub.json()["id"]
                    published.append(f"IG: {ig_id}")
                    timeline["last_ig_feed_post"] = datetime.now(UTC).isoformat()
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
            f.write(
                json.dumps(
                    {
                        "timestamp": datetime.now(UTC).isoformat(),
                        "platform": "facebook" if "FB" in entry else "instagram",
                        "action": "page_post" if "FB" in entry else "feed_post",
                        "source_post": post_url,
                        "result": entry,
                        "status": "SUCCESS" if "FAILED" not in entry else "FAILED",
                    }
                )
                + "\n"
            )

    summary = " | ".join(published)
    send(f"✅ <b>Social publishing done</b>\n{summary}")
    skill_finished("content-publisher", summary)
    return True


def _bootstrap_recipe_publisher() -> bool:
    """Add recipe-publisher to sys.path + load settings env. Returns True on success."""
    skill_path = PROJECT_ROOT / "recipe-publisher"
    if not skill_path.exists():
        return False
    if str(skill_path) not in sys.path:
        sys.path.insert(0, str(skill_path))
    settings = load_json(PROJECT_ROOT / ".claude/settings.local.json", {})
    for k, v in settings.get("env", {}).items():
        os.environ.setdefault(k, v)
    return True


def _load_carousel_json(seed_id: str) -> dict | None:
    """Return raw JSON of seeds/carousels/{seed_id}.json, or None if missing."""
    path = PROJECT_ROOT / "recipe-publisher" / "seeds" / "carousels" / f"{seed_id}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


def _product_seed_recipe(carousel_json: dict):
    """Build a minimal Recipe stub from a product-campaign carousel seed.

    Product seeds carry their `ig_caption` + `title` inline (they don't need
    LLM voice generation). Everything else is stubbed to safe defaults so the
    downstream publishers / composers accept the object.
    """
    from generators.recipe import Recipe  # late import, recipe-publisher on path

    title = carousel_json.get("title") or carousel_json["seed_id"].replace("-", " ").title()
    caption = carousel_json["ig_caption"]
    slug = carousel_json["seed_id"]

    return Recipe(
        title=title,
        slug=slug,
        meta_description=(title + " — comparison and pick on dogfoodandfun.com."),
        body_markdown="(product campaign — body lives on the site)",
        ingredients=["n/a"],
        steps=["n/a", "n/a", "n/a"],
        prep_minutes=0,
        cook_minutes=0,
        yield_servings="n/a",
        tags=["campaign"],
        image_brief="(not used for campaigns)",
        ig_caption=caption,
        seed_id=slug,
    )


def _prepare_reel(seed_id: str, skill_label: str = "reel-publisher"):
    """Voice → slides → music → compose. Returns (recipe, video_path) or None on error.

    Two paths:
      1. Recipe seeds (seed exists in seeds.json) → generate_recipe runs the
         usual LLM voice pipeline.
      2. Product seeds (carousel JSON has top-level "ig_caption") → skip
         generate_recipe, build a minimal Recipe stub from the inline caption.
    """
    if not _bootstrap_recipe_publisher():
        skill_error(skill_label, "recipe-publisher skill not found")
        return None

    try:
        from generators.carousel import generate_carousel_slides
        from generators.recipe import generate_recipe
        from generators.reel import compose_reel
        from generators.seeds import load_seeds
    except ImportError as e:
        skill_error(skill_label, f"failed to import recipe-publisher: {e}")
        return None

    carousel = _load_carousel_json(seed_id)
    if carousel is None:
        skill_error(skill_label, f"no carousel config for seed {seed_id!r}")
        return None

    if carousel.get("ig_caption"):
        # Product seed — caption is inline, skip voice generation
        log_step(skill_label, f"product seed {seed_id} — using inline caption")
        try:
            recipe = _product_seed_recipe(carousel)
        except Exception as e:
            skill_error(skill_label, f"failed to build product recipe stub: {e}")
            return None
    else:
        # Recipe seed — needs LLM voice generation
        seed = next((s for s in load_seeds() if s.id == seed_id), None)
        if seed is None:
            skill_error(skill_label, f"seed {seed_id!r} not in seeds.json")
            return None
        log_step(skill_label, f"generating recipe voice for {seed.title!r}")
        try:
            from lib.local_env import get_brand_campaign

            hook_blocklist = (get_brand_campaign() or {}).get("hook_blocklist")
            recipe = generate_recipe(seed.title, hook_blocklist=hook_blocklist)
        except Exception as e:
            skill_error(skill_label, f"recipe generation failed: {e}")
            return None

    log_step(skill_label, "generating 4 carousel slides")
    try:
        slides = generate_carousel_slides(seed_id, recipe_title=recipe.title)
    except Exception as e:
        skill_error(skill_label, f"slide generation failed: {e}")
        return None

    video_dir = PROJECT_ROOT / ".claude/state/reels"
    video_dir.mkdir(parents=True, exist_ok=True)
    stamp = int(time.time())

    # Music intentionally disabled — Jamendo's free catalog wasn't a fit. Reels
    # publish with a silent stereo AAC track (compose_reel adds it when
    # audio_path is None) so Meta accepts the upload. Re-wire here when the
    # next music provider is picked.
    video_path = video_dir / f"{seed_id}-{stamp}.mp4"
    log_step(skill_label, f"composing reel (silent) → {video_path.name}")
    try:
        compose_reel(
            [s.bytes_ or b"" for s in slides],
            video_path,
            audio_path=None,
        )
    except Exception as e:
        skill_error(skill_label, f"reel composition failed: {e}")
        return None

    return recipe, video_path


def stage_reel(seed_id: str) -> bool:
    """Generate a 9:16 Reel from a carousel seed, approve via Telegram, publish to IG."""
    log_step("Reel Publisher", f"starting seed={seed_id}")
    skill_started("reel-publisher", f"Building Reel for {seed_id}")

    prep = _prepare_reel(seed_id, skill_label="reel-publisher")
    if prep is None:
        return False
    recipe, video_path = prep

    from publishers.instagram import publish_reel_to_instagram

    send_video(video_path, caption=f"🎬 <b>Reel preview: {recipe.title}</b>")

    caption_preview = recipe.ig_caption
    if len(caption_preview) > 400:
        caption_preview = caption_preview[:400] + "…"
    approval_msg = (
        f"📸 <b>Reel ready — {recipe.title}</b>\n\n"
        f"📝 Caption ({len(recipe.ig_caption)} chars):\n"
        f"<i>{caption_preview}</i>\n\n"
        f"Reply: <b>approve</b> · <b>skip</b>"
    )
    result = send_and_wait(approval_msg, timeout_hours=12)
    if result["action"] != "approved":
        skill_finished("reel-publisher", f"Not approved ({result['action']})", success=False)
        return False

    log_step("Reel Publisher", "publishing to Instagram Reels")
    try:
        pub = publish_reel_to_instagram(recipe, video_path=video_path)
    except Exception as e:
        skill_error("reel-publisher", f"IG publish failed: {e}")
        return False

    timeline = load_json(TIMELINE_FILE, {})
    timeline["last_ig_reel_post"] = datetime.now(UTC).isoformat()
    save_json(TIMELINE_FILE, timeline)

    summary = f"Reel: {pub.permalink or pub.media_id}"
    send(f"✅ <b>Reel published</b>\n{summary}")
    skill_finished("reel-publisher", summary)
    return True


def _campaign_dir(seed_id: str) -> Path:
    return CAMPAIGNS_ROOT / seed_id


def _save_campaign_metadata(
    seed_id: str,
    recipe,
    source_path: Path,
    wp_url: str | None = None,
    fb_caption: str | None = None,
) -> None:
    cdir = _campaign_dir(seed_id)
    cdir.mkdir(parents=True, exist_ok=True)
    meta = {
        "seed_id": seed_id,
        "title": recipe.title,
        "slug": recipe.slug,
        "ig_caption": recipe.ig_caption,
        "fb_caption": fb_caption,
        "source_video": source_path.name,
        "wp_url": wp_url,
        "prepared_at": datetime.now(UTC).isoformat(),
    }
    (cdir / "metadata.json").write_text(json.dumps(meta, indent=2))


def _mux_audio(video_path: Path, audio_path: Path, out_path: Path) -> None:
    """Replace the video's audio track with `audio_path`. Trims audio to video
    length and fades out the last 1.5s so longer tracks don't end abruptly."""
    import subprocess

    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nokey=1:noprint_wrappers=1",
            str(video_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    duration = float(probe.stdout.strip())
    fade_dur = 1.5
    fade_start = max(0.0, duration - fade_dur)

    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(video_path),
        "-i",
        str(audio_path),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-af",
        f"afade=t=out:st={fade_start:.2f}:d={fade_dur}",
        "-shortest",
        "-movflags",
        "+faststart",
        str(out_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def _build_recipe_from_metadata(meta: dict):
    """Rehydrate a Recipe stub from a saved metadata.json so publish doesn't re-run prepare."""
    from generators.recipe import Recipe  # late import; recipe-publisher on path

    return Recipe(
        title=meta["title"],
        slug=meta["slug"],
        meta_description=meta["title"],
        body_markdown="(campaign reel)",
        ingredients=["n/a"],
        steps=["n/a", "n/a", "n/a"],
        prep_minutes=0,
        cook_minutes=0,
        yield_servings="n/a",
        tags=["campaign", "reel"],
        image_brief="(not used)",
        ig_caption=meta["ig_caption"],
        seed_id=meta["seed_id"],
    )


def stage_reel_prepare(seed_id: str) -> bool:
    """Build a silent reel into campaigns/{seed_id}/source.mp4 and stop.

    User can then drop audio.mp3 (mux on publish) or final.mp4 (used as-is)
    before running --stage reel-publish.
    """
    log_step("Reel Prepare", f"starting seed={seed_id}")
    skill_started("reel-prepare", f"Building silent reel for {seed_id}")

    prep = _prepare_reel(seed_id, skill_label="reel-prepare")
    if prep is None:
        return False
    recipe, video_path = prep

    cdir = _campaign_dir(seed_id)
    cdir.mkdir(parents=True, exist_ok=True)
    source_path = cdir / "source.mp4"
    source_path.write_bytes(video_path.read_bytes())
    carousel = _load_carousel_json(seed_id) or {}
    _save_campaign_metadata(
        seed_id,
        recipe,
        source_path,
        wp_url=carousel.get("wp_url"),
        fb_caption=carousel.get("fb_caption"),
    )

    msg = (
        f"✅ Silent reel ready: {source_path}\n\n"
        f"Edit options before publish:\n"
        f"  • Drop audio at:  {cdir}/audio.mp3   (will mux on publish)\n"
        f"  • OR full edit:   {cdir}/final.mp4   (used as-is, wins over audio.mp3)\n\n"
        f"Then run:\n"
        f"  python scripts/content_pipeline.py --stage reel-publish --seed {seed_id}"
    )
    print(msg, flush=True)
    skill_finished(
        "reel-prepare",
        f"Silent reel at {cdir.name}/source.mp4 — drop audio.mp3 or final.mp4",
    )
    return True


def stage_reel_publish(seed_id: str, platforms: str = "both", wp_url: str | None = None) -> bool:
    """Pick best video from campaigns/{seed_id}/ and publish to IG and/or FB
    with Telegram approval.

    `platforms`: "both" (default) | "ig" | "fb".
    `wp_url`: optional — appended to the FB description as "Full guide: <url>"
        so FB viewers (no link-in-bio) have a path to the WP page. Ignored for IG
        (IG suppresses caption URLs).
    Resolution order for video: final.mp4 > mux(source.mp4, audio.mp3) > source.mp4.
    """
    do_ig = platforms in ("both", "ig")
    do_fb = platforms in ("both", "fb")
    if not (do_ig or do_fb):
        skill_error("reel-publish", f"invalid --platforms: {platforms!r}")
        return False
    log_step("Reel Publish", f"starting seed={seed_id}")
    skill_started("reel-publish", f"Publishing reel for {seed_id}")

    cdir = _campaign_dir(seed_id)
    meta_path = cdir / "metadata.json"
    if not meta_path.exists():
        skill_error(
            "reel-publish",
            f"no metadata.json in {cdir} — run --stage reel-prepare first",
        )
        return False

    meta = json.loads(meta_path.read_text())
    source_path = cdir / "source.mp4"
    audio_path = cdir / "audio.mp3"
    final_path = cdir / "final.mp4"

    if final_path.exists():
        upload_path = final_path
        log_step("reel-publish", f"using final.mp4 ({final_path.stat().st_size // 1024} KB)")
    elif audio_path.exists() and source_path.exists():
        upload_path = cdir / "muxed.mp4"
        log_step("reel-publish", "muxing source.mp4 + audio.mp3")
        try:
            _mux_audio(source_path, audio_path, upload_path)
        except Exception as e:
            skill_error("reel-publish", f"audio mux failed: {e}")
            return False
    elif source_path.exists():
        upload_path = source_path
        log_step("reel-publish", "no audio.mp3 or final.mp4 — uploading silent source.mp4")
    else:
        skill_error("reel-publish", f"no video file in {cdir}")
        return False

    if not _bootstrap_recipe_publisher():
        skill_error("reel-publish", "recipe-publisher skill not found")
        return False

    recipe = _build_recipe_from_metadata(meta)

    send_video(upload_path, caption=f"🎬 <b>Reel preview: {recipe.title}</b>")
    cap = recipe.ig_caption[:400] + ("…" if len(recipe.ig_caption) > 400 else "")
    plat_label = {"both": "IG + FB", "ig": "IG", "fb": "FB"}[platforms]
    approval_msg = (
        f"📸 <b>Reel ready — {recipe.title}</b>\n\n"
        f"🎯 Targets: {plat_label}\n"
        f"📝 Caption ({len(recipe.ig_caption)} chars):\n"
        f"<i>{cap}</i>\n\n"
        f"Reply: <b>approve</b> · <b>skip</b>"
    )
    result = send_and_wait(approval_msg, timeout_hours=12)
    if result["action"] != "approved":
        skill_finished("reel-publish", f"Not approved ({result['action']})", success=False)
        return False

    timeline = load_json(TIMELINE_FILE, {})
    summary_parts: list[str] = []
    ig_pub = fb_pub = None

    if do_ig:
        from publishers.instagram import publish_reel_to_instagram

        log_step("Reel Publish", "publishing to Instagram Reels")
        try:
            ig_pub = publish_reel_to_instagram(recipe, video_path=upload_path)
            timeline["last_ig_reel_post"] = datetime.now(UTC).isoformat()
            summary_parts.append(f"IG: {ig_pub.permalink or ig_pub.media_id}")
        except Exception as e:
            skill_error("reel-publish", f"IG publish failed: {e}")
            if not do_fb:
                return False
            send(f"⚠️ IG publish failed: {e} — continuing to FB")

    if do_fb:
        from publishers.facebook import publish_reel_to_facebook

        log_step("Reel Publish", "publishing to Facebook Reels")
        effective_wp_url = wp_url or meta.get("wp_url")
        # Prefer explicit fb_caption (FB-tailored copy with URL inline);
        # otherwise fall back to ig_caption + auto-appended Full-guide URL.
        if meta.get("fb_caption"):
            fb_description = meta["fb_caption"]
        else:
            fb_description = recipe.ig_caption.rstrip()
            if effective_wp_url:
                fb_description += f"\n\nFull guide: {effective_wp_url}"
        try:
            fb_pub = publish_reel_to_facebook(
                recipe, video_path=upload_path, description=fb_description
            )
            timeline["last_fb_reel_post"] = datetime.now(UTC).isoformat()
            summary_parts.append(f"FB: {fb_pub.permalink or fb_pub.post_id or fb_pub.video_id}")
        except Exception as e:
            if ig_pub:
                send(f"⚠️ Reel: IG published, FB failed: {e}")
            else:
                skill_error("reel-publish", f"FB publish failed: {e}")
                return False

    save_json(TIMELINE_FILE, timeline)
    summary = " | ".join(summary_parts) if summary_parts else "(nothing published)"
    send(f"✅ <b>Reel published</b>\n{summary}")
    skill_finished("reel-publish", summary)
    return True


def _compose_fb_description(
    ig_caption: str,
    product_display: str,
    affiliate_url: str,
    wp_url: str | None,
) -> str:
    """Build an FB Reel description in Nalla's Dad voice.

    Rules we honor:
      - Start from the already-validated IG caption so the hook + Nalla
        mention + question pattern carry over verbatim
      - Frame each URL in a conversational "we did X" sentence — no clinical
        "Product:" or "Link:" prefixes (those read as salesy and break the
        tone our site holds)
      - WP link is optional; skip the phrase entirely if no URL given
    """
    parts = [ig_caption.rstrip()]
    if wp_url:
        parts.append(
            f"The full write-up with exact amounts and what actually "
            f"worked for Nalla is here: {wp_url}"
        )
    parts.append(f"The {product_display} we used for this one: {affiliate_url}")
    return "\n\n".join(parts)


def _hours_since_last_reel() -> float | None:
    """Return hours since the most recent IG-or-FB Reel publish, or None if never."""
    timeline = load_json(TIMELINE_FILE, {})
    stamps = [
        timeline.get("last_ig_reel_post"),
        timeline.get("last_fb_reel_post"),
    ]
    stamps = [s for s in stamps if s]
    if not stamps:
        return None
    parsed: list[datetime] = []
    for s in stamps:
        # Historical writes produced "+00:00Z" (redundant Z on top of offset).
        # Strip trailing Z unconditionally, then parse.
        cleaned = s.rstrip("Z")
        try:
            parsed.append(datetime.fromisoformat(cleaned))
        except ValueError:
            # Fall back: maybe the Z WAS the tz (naive + Z shape)
            try:
                parsed.append(datetime.fromisoformat(s.replace("Z", "+00:00")))
            except ValueError:
                continue
    if not parsed:
        return None
    newest = max(parsed)
    if newest.tzinfo is None:
        newest = newest.replace(tzinfo=UTC)
    delta = datetime.now(UTC) - newest
    return delta.total_seconds() / 3600.0


def stage_campaign(
    product_key: str,
    reel_seed: str,
    wp_url: str | None = None,
    campaign_id: str | None = None,
    min_gap_hours: float = 72.0,
    force: bool = False,
) -> bool:
    """Campaign = one Reel published to BOTH IG and FB with affiliate-aware description.

    Product is looked up in data/affiliate_products.json. Affiliate URL is
    built with AMAZON_ASSOCIATES_TAG + ascsubtag=campaign_id. IG caption stays
    brand-safe (no raw URL — drives via bio). FB description includes the URL
    directly (FB allows clickable links in Reel descriptions).

    `min_gap_hours` enforces spacing between promotional Reels — running two
    back-to-back teaches IG+FB's algorithms your account is "promotional" and
    hurts reach on both. Default 72h (3 days). Pass `force=True` to bypass.
    """
    log_step("Campaign", f"product={product_key} seed={reel_seed}")
    skill_started("campaign", f"Campaign: {product_key}")

    if min_gap_hours > 0 and not force:
        elapsed = _hours_since_last_reel()
        if elapsed is not None and elapsed < min_gap_hours:
            remaining = min_gap_hours - elapsed
            msg = (
                f"last Reel was {elapsed:.1f}h ago — below the "
                f"{min_gap_hours:.0f}h minimum gap. Wait {remaining:.1f}h "
                f"or pass --force to override."
            )
            skill_error("campaign", msg)
            return False

    sys.path.insert(0, str(PROJECT_ROOT / "lib"))
    import affiliate_resolver as ar

    tag = os.environ.get("AMAZON_ASSOCIATES_TAG", "").strip()
    if not tag:
        settings = load_json(PROJECT_ROOT / ".claude/settings.local.json", {})
        tag = settings.get("env", {}).get("AMAZON_ASSOCIATES_TAG", "").strip()
        if tag:
            os.environ["AMAZON_ASSOCIATES_TAG"] = tag
    if not tag:
        skill_error("campaign", "AMAZON_ASSOCIATES_TAG not set")
        return False

    try:
        product = ar.lookup(product_key)
    except ar.AffiliateResolverError as e:
        skill_error("campaign", str(e))
        return False

    if not campaign_id:
        campaign_id = f"{date.today().isoformat()[:7]}-{product_key}"
    affiliate_url = ar.build_affiliate_url(product.asin, tag, campaign_id=campaign_id)

    preview = (
        f"🎬 <b>Campaign: {product.display}</b>\n\n"
        f"Product: {product.display} ({product.asin})\n"
        f"Reel seed: <code>{reel_seed}</code>\n"
        f"WP post: {wp_url or '(none provided)'}\n"
        f"Affiliate URL: {affiliate_url}\n"
        f"Campaign ID: {campaign_id}\n\n"
        f"Will compose a Reel and publish to both IG and FB.\n\n"
        f"Reply: <b>approve</b> · <b>skip</b>"
    )
    kickoff = send_and_wait(preview, timeout_hours=12)
    if kickoff["action"] != "approved":
        skill_finished("campaign", f"Kickoff {kickoff['action']}", success=False)
        return False

    prep = _prepare_reel(reel_seed, skill_label="campaign")
    if prep is None:
        return False
    recipe, video_path = prep

    # FB description stays in Nalla's Dad voice — URLs framed as "we used X"
    # not clinical "Product: URL". IG caption is already brand-validated; we
    # only add the product + site links in conversational phrasing.
    fb_description = _compose_fb_description(
        recipe.ig_caption, product.display, affiliate_url, wp_url
    )

    send_video(
        video_path,
        caption=f"🎬 <b>Campaign Reel preview: {recipe.title}</b>",
    )
    reel_approval = send_and_wait(
        f"📸 <b>Campaign Reel ready — {product.display}</b>\n\n"
        f"IG caption: <i>{recipe.ig_caption[:250]}…</i>\n\n"
        f"FB description adds affiliate URL.\n\n"
        f"Reply: <b>approve</b> · <b>skip</b>",
        timeout_hours=12,
    )
    if reel_approval["action"] != "approved":
        skill_finished("campaign", f"Reel {reel_approval['action']}", success=False)
        return False

    # Publish to IG
    from publishers.instagram import publish_reel_to_instagram

    log_step("Campaign", "publishing to Instagram Reels")
    try:
        ig_pub = publish_reel_to_instagram(recipe, video_path=video_path)
    except Exception as e:
        skill_error("campaign", f"IG publish failed: {e}")
        return False

    # Publish to FB with affiliate-aware description
    from publishers.facebook import publish_reel_to_facebook

    log_step("Campaign", "publishing to Facebook Reels")
    try:
        fb_pub = publish_reel_to_facebook(recipe, video_path=video_path, description=fb_description)
    except Exception as e:
        # IG already succeeded — don't treat FB failure as full campaign failure.
        send(f"⚠️ Campaign: IG published, FB failed: {e}")
        fb_pub = None

    # Persist campaign state
    campaigns_file = PROJECT_ROOT / "data" / "campaigns.json"
    campaigns = load_json(campaigns_file, [])
    if not isinstance(campaigns, list):
        campaigns = []
    campaigns.append(
        {
            "campaign_id": campaign_id,
            "product": {"key": product.key, "asin": product.asin, "display": product.display},
            "affiliate_url": affiliate_url,
            "reel_seed": reel_seed,
            "wp_url": wp_url,
            "ig_permalink": ig_pub.permalink,
            "ig_media_id": ig_pub.media_id,
            "fb_permalink": fb_pub.permalink if fb_pub else None,
            "fb_post_id": fb_pub.post_id if fb_pub else None,
            "started_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }
    )
    save_json(campaigns_file, campaigns)

    timeline = load_json(TIMELINE_FILE, {})
    now = datetime.now(UTC).isoformat()
    timeline["last_ig_reel_post"] = now
    if fb_pub:
        timeline["last_fb_reel_post"] = now
    save_json(TIMELINE_FILE, timeline)

    summary_parts = [f"IG: {ig_pub.permalink or ig_pub.media_id}"]
    if fb_pub:
        summary_parts.append(f"FB: {fb_pub.permalink or fb_pub.post_id or fb_pub.video_id}")
    summary = " | ".join(summary_parts)
    send(f"✅ <b>Campaign live — {product.display}</b>\n{summary}")
    skill_finished("campaign", summary)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Content pipeline with Telegram approval")
    parser.add_argument(
        "--stage",
        required=True,
        choices=[
            "ideate",
            "enrich",
            "write",
            "publish",
            "reel",
            "reel-prepare",
            "reel-publish",
            "campaign",
            "all",
        ],
    )
    parser.add_argument(
        "--seed",
        default=None,
        help="Carousel seed id for --stage reel or --reel-seed for --stage campaign",
    )
    parser.add_argument(
        "--product",
        default=None,
        help="Affiliate product key from data/affiliate_products.json (for --stage campaign)",
    )
    parser.add_argument(
        "--reel-seed",
        default=None,
        help="Carousel seed id for --stage campaign (defaults to --seed if given)",
    )
    parser.add_argument(
        "--wp-url",
        default=None,
        help="WP post URL to include in FB description (for --stage campaign or --stage reel-publish; overrides seed.wp_url)",
    )
    parser.add_argument(
        "--platforms",
        default="both",
        choices=["both", "ig", "fb"],
        help="For --stage reel-publish: target platforms (default: both)",
    )
    parser.add_argument(
        "--campaign-id",
        default=None,
        help="Override campaign id (default: YYYY-MM-{product_key})",
    )
    parser.add_argument(
        "--min-gap-hours",
        type=float,
        default=72.0,
        help="Refuse to launch if a Reel was published more recently than this (default 72h = 3 days). Set 0 to disable.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Bypass --min-gap-hours guard (use sparingly — back-to-back promotional Reels hurt reach on both platforms).",
    )
    args = parser.parse_args()

    if args.stage == "ideate":
        stage_ideate()
    elif args.stage == "enrich":
        stage_enrich()
    elif args.stage == "publish":
        stage_publish()
    elif args.stage == "reel":
        if not args.seed:
            print("--stage reel requires --seed <id>", flush=True)
            sys.exit(2)
        ok = stage_reel(args.seed)
        sys.exit(0 if ok else 1)
    elif args.stage == "reel-prepare":
        if not args.seed:
            print("--stage reel-prepare requires --seed <id>", flush=True)
            sys.exit(2)
        sys.exit(0 if stage_reel_prepare(args.seed) else 1)
    elif args.stage == "reel-publish":
        if not args.seed:
            print("--stage reel-publish requires --seed <id>", flush=True)
            sys.exit(2)
        sys.exit(
            0 if stage_reel_publish(args.seed, platforms=args.platforms, wp_url=args.wp_url) else 1
        )
    elif args.stage == "campaign":
        if not args.product:
            print("--stage campaign requires --product <key>", flush=True)
            sys.exit(2)
        reel_seed = args.reel_seed or args.seed
        if not reel_seed:
            print(
                "--stage campaign requires --reel-seed <id> (or --seed as shorthand)",
                flush=True,
            )
            sys.exit(2)
        ok = stage_campaign(
            product_key=args.product,
            reel_seed=reel_seed,
            wp_url=args.wp_url,
            campaign_id=args.campaign_id,
            min_gap_hours=args.min_gap_hours,
            force=args.force,
        )
        sys.exit(0 if ok else 1)
    elif args.stage == "all":
        if stage_ideate():
            stage_enrich()


if __name__ == "__main__":
    main()
