"""Cron drainer for prepared recipe campaigns.

Default behavior (cron call):
    Pick the next campaign in campaigns/prepared/ that is `verified` (or
    `audio_ready` if --auto-verify is set), promote its WP draft to
    publish, push the IG carousel + FB post, then move the folder to
    campaigns/published/.

Subcommands / flags:
    --seed <id> --verify   Mark a prepared campaign as verified (after you've
                           dropped audio.mp3 + reviewed everything). Sends a
                           Telegram preview before flipping state.
    --seed <id>            Force-publish a specific campaign (bypasses the
                           "first verified in queue" pick).
    --dry-run              Show what would happen, don't touch WP/IG/FB.
    --min-gap-hours N      Skip if last successful publish was <N hours ago.

State machine in each prepared/<seed>/status.json:
    awaiting_audio  → user drops audio.mp3 → audio_ready
    audio_ready     → user runs --verify (or --auto-verify) → verified
    verified        → cron picks up → publishing → published

The cron is idempotent on its own folder: if it crashes mid-publish, status
records the last completed step so a re-run picks up where it left off.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from lib.bootstrap import init_script
settings, log = init_script(__name__)
sys.path.insert(0, str(PROJECT_ROOT / "recipe-publisher"))

from lib.local_env import get_brand_campaign, load_local_env  # noqa: E402
from lib.sessions import wp_client  # noqa: E402

import notifier  # noqa: E402

logger = logging.getLogger("publish_prepared")

# Campaigns live one level up — at /Users/gilcohen/Projects/dogfoodandfun/campaigns/,
# not under social-automation/. Matches what prepare.py writes to.
CAMPAIGNS_ROOT: Final[Path] = settings.paths.campaigns_dir  # type: ignore[union-attr]
PREPARED_ROOT: Final[Path] = CAMPAIGNS_ROOT / "prepared"
PUBLISHED_ROOT: Final[Path] = CAMPAIGNS_ROOT / "published"
LAST_RUN_FILE: Final[Path] = PROJECT_ROOT / "recipe-publisher" / "state" / "last_publish_prepared.json"


@dataclass
class CampaignFolder:
    seed_id: str
    path: Path
    state: str
    metadata: dict[str, Any]


def _read_status(folder: Path) -> dict[str, Any]:
    p = folder / "status.json"
    if not p.exists():
        return {"state": "unknown", "history": []}
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return {"state": "unknown", "history": []}


def _write_status(folder: Path, state: str, by: str = "publish_prepared") -> None:
    cur = _read_status(folder)
    cur["state"] = state
    cur.setdefault("history", []).append(
        {"at": datetime.now(timezone.utc).isoformat(), "to": state, "by": by}
    )
    tmp = folder / "status.json.tmp"
    tmp.write_text(json.dumps(cur, indent=2, ensure_ascii=False))
    tmp.replace(folder / "status.json")


def _read_metadata(folder: Path) -> dict[str, Any]:
    p = folder / "metadata.json"
    return json.loads(p.read_text()) if p.exists() else {}


def _list_prepared() -> list[CampaignFolder]:
    if not PREPARED_ROOT.exists():
        return []
    out: list[CampaignFolder] = []
    for p in sorted(PREPARED_ROOT.iterdir()):
        if not p.is_dir():
            continue
        meta = _read_metadata(p)
        status = _read_status(p)
        out.append(
            CampaignFolder(
                seed_id=p.name,
                path=p,
                state=status.get("state", "unknown"),
                metadata=meta,
            )
        )
    return out


def _audio_present(folder: Path) -> bool:
    return _resolve_audio_path(folder) is not None


def _resolve_audio_path(folder: Path) -> Path | None:
    """Find the audio file dropped by the operator.

    Preference order:
      1. audio.mp3 (canonical name)
      2. any other *.mp3 (e.g. "Sun_Kissed_Swirls.mp3" — keeps user-friendly track names)

    Files under 1 KB are ignored (treated as accidental empty placeholders).
    """
    canonical = folder / "audio.mp3"
    if canonical.exists() and canonical.stat().st_size > 1024:
        return canonical
    for p in sorted(folder.glob("*.mp3")):
        if p.stat().st_size > 1024:
            return p
    return None


def _detect_audio_arrival(campaigns: list[CampaignFolder]) -> None:
    """Auto-promote awaiting_audio → audio_ready when audio.mp3 lands."""
    for c in campaigns:
        if c.state == "awaiting_audio" and _audio_present(c.path):
            _write_status(c.path, "audio_ready", by="auto_detect")
            c.state = "audio_ready"
            logger.info("audio detected for %s — state=audio_ready", c.seed_id)


def _hours_since_last_success() -> float:
    if not LAST_RUN_FILE.exists():
        return float("inf")
    try:
        data = json.loads(LAST_RUN_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return float("inf")
    if data.get("status") != "success":
        return float("inf")
    finished = data.get("finished_at")
    if not finished:
        return float("inf")
    try:
        ts = datetime.fromisoformat(finished.replace("Z", "+00:00"))
    except ValueError:
        return float("inf")
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ts).total_seconds() / 3600.0


def _record_last_run(seed_id: str, status: str, error: str = "") -> None:
    LAST_RUN_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "seed_id": seed_id,
        "status": status,
        "error": error,
    }
    tmp = LAST_RUN_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    tmp.replace(LAST_RUN_FILE)


# ---------- the publish flow ----------


def _promote_wp_draft(client, post_id: int) -> str:
    """PATCH the WP draft to status=publish. Returns the live permalink."""
    r = client.post(f"/wp-json/wp/v2/posts/{post_id}", json={"status": "publish"})
    r.raise_for_status()
    return r.json()["link"]


def verify_seed(seed_id: str) -> int:
    """Telegram-preview a prepared campaign and flip state to 'verified'."""
    folder = PREPARED_ROOT / seed_id
    if not folder.exists():
        print(f"❌ no prepared folder: {folder}")  # noqa: T201
        return 1
    meta = _read_metadata(folder)
    if not _audio_present(folder):
        print(f"❌ {seed_id}: audio.mp3 not found in {folder}")  # noqa: T201
        return 1

    msg = (
        f"📋 <b>Recipe campaign — verify</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Seed:</b> <code>{seed_id}</code>\n"
        f"<b>Title:</b> {meta.get('title', '?')}\n"
        f"<b>WP draft:</b> {meta.get('wp_admin_url', '?')}\n"
        f"<b>Audio:</b> ✅ present ({(folder / 'audio.mp3').stat().st_size // 1024} KB)\n"
        f"<b>Slides:</b> {len(meta.get('carousel_slides', []))}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Reply <b>yes</b> to mark verified (queues for next cron publish)\n"
        f"Reply <b>skip</b> to leave in audio_ready state."
    )
    notifier.send(msg, silent=False)

    cfg = notifier._load_config()
    token = cfg.get("bot_token", "")
    chat_id = cfg.get("chat_id", "")
    if not token or not chat_id:
        print("⚠️  no telegram — auto-verifying without confirmation.")  # noqa: T201
        _write_status(folder, "verified", by="cli_no_telegram")
        return 0

    offset = notifier._get_latest_offset(token)
    deadline = time.time() + 4 * 3600
    while time.time() < deadline:
        for u in notifier._get_updates(token, offset=offset, timeout=5):
            offset = u["update_id"] + 1
            text = (u.get("message", {}).get("text") or "").strip().lower()
            if text in ("yes", "y", "approve"):
                _write_status(folder, "verified", by="cli_user")
                notifier.send(f"✅ verified: <code>{seed_id}</code>", silent=True)
                print(f"✅ verified: {seed_id}")  # noqa: T201
                return 0
            if text in ("skip", "no", "n"):
                notifier.send(f"⏭ skipped verify: <code>{seed_id}</code>", silent=True)
                print(f"⏭ skip: {seed_id}")  # noqa: T201
                return 0
    print("⏰ timed out — left as audio_ready")  # noqa: T201
    return 1


_CTA_KEYWORD_RE = re.compile(r"\bComment ([A-Z]{3,})\b")
_CTA_LINE_RE = re.compile(r"^.*\bComment [A-Z]{3,}\b.*$", re.MULTILINE)
_HASHTAG_LINE_RE = re.compile(r"^[\s#].*#\w+.*$", re.MULTILINE)
_BULLET_RE = re.compile(r"^\s*•.*$", re.MULTILINE)
_BIO_FALLBACK_RE = re.compile(r"^.*link in bio.*$", re.IGNORECASE | re.MULTILINE)


def _short_fb_message_from_ig(ig_caption: str) -> str:
    """Compose a short FB-feed message when fb_caption.txt is empty.

    FB feed-card posts work best when `message` is conversational and short
    (2-4 sentences) — the link card itself carries the title/image/desc.
    Strategy:
      1. Drop the IG-native lines that don't translate to FB:
         - bullet-fact lines
         - the entire comment-CTA line
         - the bio-fallback line ("🔗 Full guide: link in bio")
         - the hashtag block
      2. Keep the hook + question + any narrative connective tissue
    """
    text = ig_caption.strip()
    text = _BULLET_RE.sub("", text)
    text = _CTA_LINE_RE.sub("", text)
    text = _BIO_FALLBACK_RE.sub("", text)
    text = _HASHTAG_LINE_RE.sub("", text)
    # Collapse blank-run noise left behind
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    return "\n\n".join(lines).strip()


def _build_first_comment(ig_caption: str) -> str:
    """Reinforce the caption's comment-CTA + nudge toward the bio link.

    Mirrors the pattern recipe_publisher uses for carousels — same shape, but
    extends with the bio-link fallback so non-commenters still see a clear
    second path. Comment is plain text (IG strips clickable URLs from
    comments anyway, so we name the destination instead of pasting it).
    """
    match = _CTA_KEYWORD_RE.search(ig_caption)
    keyword = match.group(1) if match else "RECIPE"
    return (
        f"\U0001f447 Drop a '{keyword}' and I'll DM you the printable card. "
        f"Or tap the bio link for the full guide on dogfoodandfun.com — "
        f"what's your pup's all-time favorite homemade treat?"
    )


def _mux_source_with_audio(source_mp4: Path, audio: Path, muxed: Path) -> Path:
    """Combine the silent source.mp4 with the operator's audio track.

    Reuses scripts.content_pipeline._mux_audio so we don't duplicate ffmpeg
    invocation logic. Idempotent: returns immediately if muxed.mp4 already
    exists and is newer than both inputs.
    """
    if muxed.exists():
        m_mtime = muxed.stat().st_mtime
        if m_mtime >= source_mp4.stat().st_mtime and m_mtime >= audio.stat().st_mtime:
            return muxed
    from content_pipeline import _mux_audio  # noqa: E402  (local import, optional dep)

    _mux_audio(source_mp4, audio, muxed)
    return muxed


def _load_slides_from_folder(folder: Path) -> list[Any]:
    """Reconstruct GeneratedImage instances from saved slides/*.jpg files.

    Pinterest publisher needs `bytes_`, `alt_text`, `content_type`, `url` —
    we set url empty (publisher uses slide_urls= explicitly) and reuse the
    recipe title for alt text (good enough for Pin alt; can be tuned later).
    """
    from generators.image import GeneratedImage  # noqa: E402

    slides_dir = folder / "slides"
    if not slides_dir.exists():
        return []
    out: list[Any] = []
    for p in sorted(slides_dir.glob("slide_*.jpg")):
        out.append(
            GeneratedImage(
                url="",
                alt_text=folder.name.replace("-", " ").title(),
                provider="prepared",
                bytes_=p.read_bytes(),
                content_type="image/jpeg",
            )
        )
    return out


def _build_recipe_stub(meta: dict[str, Any], ig_caption: str) -> Any:
    """Build the minimal Recipe-shaped object publish_reel_to_* require.

    The IG / FB reel publishers only read `slug` and `ig_caption` off the
    object — everything else is unused at publish time. We construct a real
    Recipe dataclass instance with sensible defaults so dataclass identity
    checks and isinstance() guards still pass.
    """
    from generators.recipe import Recipe  # noqa: E402

    return Recipe(
        title=meta.get("title", ""),
        slug=meta.get("slug", ""),
        meta_description="",
        body_markdown="",
        ingredients=[],
        steps=[],
        prep_minutes=0,
        cook_minutes=0,
        yield_servings="",
        tags=list(meta.get("tags", [])),
        image_brief="",
        ig_caption=ig_caption,
        seed_id=meta.get("seed_id", ""),
    )


def publish_one(folder: Path, *, dry_run: bool) -> bool:
    seed_id = folder.name
    meta = _read_metadata(folder)
    wp_id = meta.get("wp_draft_id")
    if not wp_id:
        logger.error("no wp_draft_id in %s/metadata.json", folder)
        return False

    source_mp4 = folder / "source.mp4"
    audio_path = _resolve_audio_path(folder)
    if not source_mp4.exists():
        logger.error("source.mp4 missing in %s", folder)
        return False
    if audio_path is None:
        logger.error("no audio file (.mp3) found in %s", folder)
        return False

    ig_caption = (folder / "ig_caption.txt").read_text(encoding="utf-8").strip()
    fb_caption = (folder / "fb_caption.txt").read_text(encoding="utf-8").strip() if (folder / "fb_caption.txt").exists() else ""

    if dry_run:
        print(  # noqa: T201
            f"[dry-run] would mux ({source_mp4.name} + {audio_path.name}) → muxed.mp4, "
            f"promote WP {wp_id} → publish, push IG reel + FB reel for {seed_id}"
        )
        return True

    notifier.send(f"🚀 publishing <code>{seed_id}</code>…", silent=True)
    _write_status(folder, "publishing")

    warnings: list[str] = []

    # Step 1: mux source + audio → muxed.mp4 (idempotent)
    muxed_path = folder / "muxed.mp4"
    try:
        _mux_source_with_audio(source_mp4, audio_path, muxed_path)
        logger.info("muxed reel ready: %s", muxed_path)
    except Exception as exc:  # noqa: BLE001
        logger.exception("mux failed")
        warnings.append(f"mux failed: {exc}")
        notifier.send(f"❌ <code>{seed_id}</code> — mux failed: {exc}", silent=False)
        _write_status(folder, "failed")
        return False

    # Step 2: promote WP draft to publish (this is the "go-live" moment)
    with wp_client() as client:
        live_url = _promote_wp_draft(client, int(wp_id))
    logger.info("WP %d promoted to publish: %s", wp_id, live_url)

    # Step 2.5: recipe card PDF — best-effort; never blocks the publish flow
    rc = settings.recipe_card
    if rc.enabled:
        try:
            from lib.recipe_card import content_parser, pdf_generator, wp_sync as rc_wp  # noqa: E402
            _post = rc_wp.fetch_post_data(int(wp_id))
            _recipe = content_parser.parse_recipe(_post["title"], _post["content"])
            if _recipe.ingredients or _recipe.instructions:
                _stamp = rc_wp.fetch_nalla_stamp(rc.stamp_media_id)
                _pdf = pdf_generator.generate_recipe_card_pdf(
                    title=_recipe.title,
                    ingredients=_recipe.ingredients,
                    instructions=_recipe.instructions,
                    nalla_stamp_bytes=_stamp,
                    cook_temp=_recipe.cook_temp,
                    cook_time=_recipe.cook_time,
                    header_title=rc.header_title,
                    footer_text=rc.footer_text,
                )
                _pdf_url = rc_wp.upload_pdf(_pdf, f"recipe-card-{wp_id}.pdf")
                rc_wp.inject_download_button(int(wp_id), _pdf_url)
                logger.info("Recipe card uploaded for WP %d: %s", wp_id, _pdf_url)
            else:
                logger.info("Post %d: no parseable recipe — skipping card.", wp_id)
        except Exception as exc:
            logger.warning("Recipe card generation failed for WP %d: %s", wp_id, exc)

    recipe = _build_recipe_stub(meta, ig_caption)

    # Step 3: push IG reel — best-effort, doesn't roll back WP if it fails
    ig_permalink = ""
    ig_media_id = ""
    try:
        from publishers.instagram import (
            post_first_comment_to_instagram,
            publish_reel_to_instagram,
        )

        ig_result = publish_reel_to_instagram(recipe, muxed_path)
        ig_permalink = ig_result.permalink or ""
        ig_media_id = ig_result.media_id or ""
        if ig_result.warnings:
            warnings.extend(f"IG: {w}" for w in ig_result.warnings)
        logger.info("IG reel published: %s (media_id=%s)", ig_permalink, ig_media_id)

        # First-comment funnel: reinforces the comment-CTA keyword from the
        # caption AND nudges non-commenters toward the bio link. Engagement
        # signal IG ranking rewards in the first hour after publish.
        try:
            comment_text = _build_first_comment(ig_caption)
            cid = post_first_comment_to_instagram(ig_result.media_id, comment_text)
            logger.info("IG first-comment posted: media=%s comment=%s", ig_result.media_id, cid)
        except Exception as exc:  # noqa: BLE001 — first-comment is cosmetic
            logger.warning("IG first-comment failed: %s", exc)
            warnings.append(f"IG first-comment failed: {exc}")
    except Exception as exc:  # noqa: BLE001
        logger.exception("IG reel push failed")
        warnings.append(f"IG reel failed: {exc}")

    # Step 4: push FB reel (description = fb_caption + WP link, fallback to ig)
    fb_permalink = ""
    fb_post_id = ""
    fb_video_id = ""
    fb_description = fb_caption or ig_caption
    fb_description = (fb_description.rstrip() + f"\n\nFull recipe: {live_url}").strip()
    try:
        from publishers.facebook import publish_reel_to_facebook

        fb_result = publish_reel_to_facebook(recipe, muxed_path, description=fb_description)
        fb_permalink = fb_result.permalink or ""
        fb_post_id = fb_result.post_id or ""
        fb_video_id = fb_result.video_id or ""
        if fb_result.warnings:
            warnings.extend(f"FB: {w}" for w in fb_result.warnings)
        logger.info(
            "FB reel published: permalink=%s post_id=%s video_id=%s",
            fb_permalink, fb_post_id, fb_video_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("FB reel push failed")
        warnings.append(f"FB reel failed: {exc}")

    # Step 4.25: post to the FB Page feed (separate audience from FB Reels).
    # If brand.campaign.link_in_first_comment is true, the body is text-only
    # and the WP URL is dropped as a follow-up first comment (dodges FB's
    # outbound-link reach penalty). Otherwise we post a standard link card.
    fb_page_post_id = ""
    fb_page_post_permalink = ""
    fb_page_comment_id = ""
    fb_message = (fb_caption or _short_fb_message_from_ig(ig_caption)).strip()
    brand_campaign = get_brand_campaign()
    link_first = bool(brand_campaign.get("link_in_first_comment"))
    try:
        from publishers.facebook import (
            post_first_comment_to_facebook,
            publish_link_post_to_facebook,
        )

        fb_page = publish_link_post_to_facebook(
            message=fb_message,
            link=live_url,
            link_in_first_comment=link_first,
        )
        fb_page_post_id = fb_page.post_id or ""
        fb_page_post_permalink = fb_page.permalink or ""
        if fb_page.warnings:
            warnings.extend(f"FB-page: {w}" for w in fb_page.warnings)
        logger.info(
            "FB Page feed post published: id=%s permalink=%s link_in_first_comment=%s",
            fb_page_post_id,
            fb_page_post_permalink,
            link_first,
        )

        if link_first and fb_page_post_id and live_url:
            comment_id, comment_warnings = post_first_comment_to_facebook(
                fb_page_post_id, live_url
            )
            fb_page.comment_id = comment_id
            fb_page.comment_warnings = comment_warnings
            fb_page_comment_id = comment_id or ""
            if comment_warnings:
                warnings.extend(f"FB-page-comment: {w}" for w in comment_warnings)
            logger.info(
                "FB Page first-comment result: post_id=%s comment_id=%s warnings=%d",
                fb_page_post_id, fb_page_comment_id, len(comment_warnings),
            )
    except Exception as exc:  # noqa: BLE001
        logger.exception("FB Page link post failed")
        warnings.append(f"FB Page post failed: {exc}")

    # Step 4.5: push Pinterest pins — best-effort, doesn't roll back WP
    pinterest_permalinks: list[str] = []
    pinterest_pin_ids: list[str] = []
    try:
        from publishers.pinterest import PinterestSkipped, publish_pins_for_recipe

        slides = _load_slides_from_folder(folder)
        if not slides:
            warnings.append("Pinterest skipped — no slides in prepared folder")
        else:
            pin_result = publish_pins_for_recipe(
                recipe, slides, wp_post_url=live_url
            )
            pinterest_pin_ids = [p.pin_id for p in pin_result.pins]
            pinterest_permalinks = [p.permalink for p in pin_result.pins if p.permalink]
            if pin_result.warnings:
                warnings.extend(f"Pinterest: {w}" for w in pin_result.warnings)
            logger.info("Pinterest published %d pins", len(pinterest_pin_ids))
    except PinterestSkipped as exc:
        logger.warning("Pinterest skipped: %s", exc)
        warnings.append(f"Pinterest skipped: {exc}")
    except Exception as exc:  # noqa: BLE001
        logger.exception("Pinterest push failed")
        warnings.append(f"Pinterest failed: {exc}")

    # Step 5: move folder to published/, mark final state
    PUBLISHED_ROOT.mkdir(parents=True, exist_ok=True)
    target = PUBLISHED_ROOT / seed_id
    if target.exists():
        shutil.rmtree(target)
    shutil.move(str(folder), str(target))
    _write_status(target, "published")

    # Persist permalinks so subsequent runs / reports can find them
    meta_after = _read_metadata(target)
    meta_after.update(
        {
            "wp_live_url": live_url,
            "ig_reel_permalink": ig_permalink,
            "ig_reel_media_id": ig_media_id,
            "fb_reel_permalink": fb_permalink,
            "fb_reel_post_id": fb_post_id,
            "fb_reel_video_id": fb_video_id,
            "pinterest_pin_ids": pinterest_pin_ids,
            "pinterest_permalinks": pinterest_permalinks,
            "fb_page_post_id": fb_page_post_id,
            "fb_page_post_permalink": fb_page_post_permalink,
            "published_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    (target / "metadata.json.tmp").write_text(
        json.dumps(meta_after, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (target / "metadata.json.tmp").replace(target / "metadata.json")

    pin_summary = (
        f"{len(pinterest_permalinks)} pin(s)" if pinterest_pin_ids else "⚠️ skipped/failed"
    )
    summary = (
        f"✅ <b>Published</b>: <code>{seed_id}</code>\n"
        f"WP:    {live_url}\n"
        f"IG:    {ig_permalink or '⚠️ failed'}\n"
        f"FB(R): {fb_permalink or '⚠️ failed'}\n"
        f"FB(P): {fb_page_post_permalink or fb_page_post_id or '⚠️ failed'}\n"
        f"Pin:   {pin_summary}"
    )
    if warnings:
        summary += "\n\n⚠️ " + " | ".join(warnings)
    notifier.send(summary, silent=False)
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Drain prepared recipe campaigns")
    parser.add_argument("--seed", help="target a specific seed id")
    parser.add_argument("--verify", action="store_true", help="(with --seed) flip audio_ready → verified via Telegram")
    parser.add_argument("--list", action="store_true", help="show all prepared campaigns + states")
    parser.add_argument("--dry-run", action="store_true", help="don't touch WP/IG/FB")
    parser.add_argument("--auto-verify", action="store_true", help="treat audio_ready as verified")
    parser.add_argument("--min-gap-hours", type=float, default=0.0)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    load_local_env()

    if args.list:
        rows = _list_prepared()
        if not rows:
            print("(no prepared campaigns)")  # noqa: T201
            return 0
        print(f"{'seed_id':<48} {'state':<18} {'audio?':<8}")  # noqa: T201
        print("-" * 80)  # noqa: T201
        for r in rows:
            audio = "✅" if _audio_present(r.path) else "—"
            print(f"{r.seed_id:<48} {r.state:<18} {audio:<8}")  # noqa: T201
        return 0

    if args.verify and args.seed:
        return verify_seed(args.seed)

    if args.min_gap_hours > 0 and _hours_since_last_success() < args.min_gap_hours:
        elapsed = _hours_since_last_success()
        print(f"skip: last publish was {elapsed:.1f}h ago, below min-gap {args.min_gap_hours:.1f}h")  # noqa: T201
        return 0

    rows = _list_prepared()
    _detect_audio_arrival(rows)
    rows = _list_prepared()  # re-read after auto-promotion

    eligible_states = {"verified"}
    if args.auto_verify:
        eligible_states.add("audio_ready")

    if args.seed:
        rows = [r for r in rows if r.seed_id == args.seed]
        if not rows:
            print(f"❌ no prepared folder for seed: {args.seed}")  # noqa: T201
            return 1
    else:
        rows = [r for r in rows if r.state in eligible_states]

    if not rows:
        print("(nothing to publish — no campaigns in eligible state)")  # noqa: T201
        return 0

    target = rows[0]
    print(f"publishing: {target.seed_id} (state={target.state})")  # noqa: T201
    ok = publish_one(target.path, dry_run=args.dry_run)
    _record_last_run(target.seed_id, "success" if ok else "failed")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
