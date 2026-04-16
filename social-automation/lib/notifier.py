"""
Telegram notification helper for DogFoodAndFun social automation.
Sends push messages to Gil when skills start, finish, or hit errors.
Also handles interactive comment approvals via Telegram reply.
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from pathlib import Path

import requests

# ── Credentials ────────────────────────────────────────────────────────────────
# Store in .claude/state/telegram_config.json to keep out of source code
_CONFIG_FILE = Path(__file__).resolve().parent.parent / ".claude" / "state" / "telegram_config.json"


def _load_config() -> dict:
    if _CONFIG_FILE.exists():
        import json

        return json.loads(_CONFIG_FILE.read_text())
    # Fallback to env vars
    return {
        "bot_token": os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        "chat_id": os.environ.get("TELEGRAM_CHAT_ID", ""),
    }


def send(message: str, silent: bool = False) -> bool:
    """
    Send a Telegram message. Returns True on success, False on failure.
    silent=True sends without phone notification (useful for non-urgent updates).
    """
    cfg = _load_config()
    token = cfg.get("bot_token", "")
    chat_id = cfg.get("chat_id", "")

    if not token or not chat_id:
        print("[notifier] WARNING: Telegram credentials not configured — skipping notification.")
        return False

    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "HTML",
                "disable_notification": silent,
            },
            timeout=8,
        )
        if not resp.ok:
            print(f"[notifier] Telegram API error: {resp.status_code} — {resp.text[:200]}")
            return False
        return True
    except Exception as e:
        print(f"[notifier] Failed to send notification: {e}")
        return False


def skill_started(skill_name: str, detail: str = "") -> None:
    """Notify that a skill just started."""
    ts = datetime.now(UTC).strftime("%H:%M UTC")
    msg = f"🐾 <b>{skill_name}</b> started\n⏰ {ts}"
    if detail:
        msg += f"\n{detail}"
    send(msg, silent=True)  # start = silent, don't wake up phone


def skill_finished(skill_name: str, summary: str = "", success: bool = True) -> None:
    """Notify that a skill finished."""
    ts = datetime.now(UTC).strftime("%H:%M UTC")
    icon = "✅" if success else "❌"
    msg = f"{icon} <b>{skill_name}</b> finished\n⏰ {ts}"
    if summary:
        msg += f"\n{summary}"
    send(msg, silent=False)  # finish = audible notification


def skill_error(skill_name: str, error: str) -> None:
    """Notify on error — always audible."""
    ts = datetime.now(UTC).strftime("%H:%M UTC")
    msg = f"🚨 <b>{skill_name}</b> ERROR\n⏰ {ts}\n<code>{error[:300]}</code>"
    send(msg, silent=False)


def skill_skipped(skill_name: str, reason: str = "") -> None:
    """Notify that a skill was skipped (re-run guard, rate limit, etc.)."""
    ts = datetime.now(UTC).strftime("%H:%M UTC")
    msg = f"⏭️ <b>{skill_name}</b> skipped\n⏰ {ts}"
    if reason:
        msg += f"\n{reason}"
    send(msg, silent=True)


def _get_updates(token: str, offset: int = 0, timeout: int = 30) -> list[dict]:
    """Long-poll Telegram for new messages."""
    try:
        resp = requests.get(
            f"https://api.telegram.org/bot{token}/getUpdates",
            params={"offset": offset, "timeout": timeout, "allowed_updates": ["message"]},
            timeout=timeout + 5,
        )
        if resp.ok:
            return resp.json().get("result", [])
    except Exception:
        pass
    return []


def _get_latest_offset(token: str) -> int:
    """
    Get the current update offset so we only listen for NEW replies
    sent AFTER the approval request — ignores any old messages.
    """
    updates = _get_updates(token, timeout=1)
    if updates:
        return updates[-1]["update_id"] + 1
    return 0


def request_approval(
    platform: str,
    group_or_hashtag: str,
    post_preview: str,
    draft_comment: str,
    relevance_score: float,
    timeout_hours: int = 12,
) -> dict:
    """
    Send a comment draft to Telegram for approval and wait for a reply.

    Returns:
        {
            "action": "approved" | "skipped" | "edited" | "timeout" | "pending",
            "comment": str,   # final comment text (edited if user sent new text)
        }

    action="pending" means Telegram was unreachable — caller should leave the
    item in the queue and retry on next run.

    User replies:
        yes / y / approve  → approved, use draft as-is
        skip / s / no      → skipped, don't post
        edit: <new text>   → approved with new text

    Times out after timeout_hours and returns action="timeout" (treated as skip).
    """
    cfg = _load_config()
    token = cfg.get("bot_token", "")
    chat_id = cfg.get("chat_id", "")

    if not token or not chat_id:
        print("[notifier] No Telegram credentials — leaving item pending for next run.")
        return {"action": "pending", "comment": draft_comment}

    # Snapshot offset BEFORE sending — ignore anything older
    try:
        offset = _get_latest_offset(token)
    except Exception:
        print("[notifier] Telegram unreachable (getUpdates failed) — leaving item pending.")
        return {"action": "pending", "comment": draft_comment}

    # Send the approval request
    score_pct = int(relevance_score * 100)
    icon = "📘" if platform == "facebook" else "📸"
    msg = (
        f"{icon} <b>Comment approval needed</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Platform:</b> {platform.capitalize()}\n"
        f"<b>Group/Tag:</b> {group_or_hashtag}\n"
        f"<b>Score:</b> {score_pct}%\n"
        f"<b>Post:</b> <i>{post_preview[:200]}...</i>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Proposed comment:</b>\n"
        f"<blockquote>{draft_comment}</blockquote>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Reply: <b>yes</b> · <b>skip</b> · <b>edit: your new text</b>\n"
        f"⏳ Waiting up to {timeout_hours}h"
    )
    sent = send(msg, silent=False)
    if not sent:
        print("[notifier] Telegram send failed — leaving item pending for next run.")
        return {"action": "pending", "comment": draft_comment}
    print(
        f"[notifier] Approval request sent to Telegram. Waiting for reply (timeout: {timeout_hours}h)..."
    )

    # Poll for reply
    deadline = time.time() + (timeout_hours * 3600)
    poll_interval = 5  # seconds between polls

    while time.time() < deadline:
        updates = _get_updates(token, offset=offset, timeout=poll_interval)
        for update in updates:
            offset = update["update_id"] + 1
            msg_data = update.get("message", {})
            # Only accept replies from our chat
            if str(msg_data.get("chat", {}).get("id", "")) != str(chat_id):
                continue
            text = msg_data.get("text", "").strip()
            if not text:
                continue

            result = _parse_reply(text, draft_comment)
            # Acknowledge
            ack = {
                "approved": "✅ Comment approved — posting now.",
                "skipped": "⏭️ Comment skipped.",
                "edited": "✅ Comment edited — posting with your version.",
                "timeout": "⏰ Timed out.",
            }.get(result["action"], "Got it.")
            send(ack, silent=True)
            return result

    # Timeout
    send(f"⏰ No reply after {timeout_hours}h — comment skipped.", silent=True)
    return {"action": "timeout", "comment": draft_comment}


def _parse_reply(text: str, draft: str) -> dict:
    """Parse user's Telegram reply into an action dict."""
    lower = text.lower().strip()
    if lower in ("yes", "y", "approve", "ok", "post it", "post"):
        return {"action": "approved", "comment": draft}
    if lower in ("skip", "s", "no", "n", "nope"):
        return {"action": "skipped", "comment": draft}
    if lower.startswith("edit:"):
        new_text = text[5:].strip()
        if new_text:
            return {"action": "edited", "comment": new_text}
    # If they just typed new text directly (no keyword), treat as edit
    if len(text) > 20 and "?" in text:
        return {"action": "edited", "comment": text}
    # Unknown — treat as skip
    return {"action": "skipped", "comment": draft}


if __name__ == "__main__":
    # Quick test
    print("Sending test notification...")
    ok = send("🐾 <b>DogFoodAndFun Bot</b> is connected!\nNotifications are working ✅")
    print("✅ Sent!" if ok else "❌ Failed — check credentials")
