"""
Telegram notification helper for DogFoodAndFun social automation.
Sends push messages to Gil when skills start, finish, or hit errors.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
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
    ts = datetime.now(timezone.utc).strftime("%H:%M UTC")
    msg = f"🐾 <b>{skill_name}</b> started\n⏰ {ts}"
    if detail:
        msg += f"\n{detail}"
    send(msg, silent=True)  # start = silent, don't wake up phone


def skill_finished(skill_name: str, summary: str = "", success: bool = True) -> None:
    """Notify that a skill finished."""
    ts = datetime.now(timezone.utc).strftime("%H:%M UTC")
    icon = "✅" if success else "❌"
    msg = f"{icon} <b>{skill_name}</b> finished\n⏰ {ts}"
    if summary:
        msg += f"\n{summary}"
    send(msg, silent=False)  # finish = audible notification


def skill_error(skill_name: str, error: str) -> None:
    """Notify on error — always audible."""
    ts = datetime.now(timezone.utc).strftime("%H:%M UTC")
    msg = f"🚨 <b>{skill_name}</b> ERROR\n⏰ {ts}\n<code>{error[:300]}</code>"
    send(msg, silent=False)


def skill_skipped(skill_name: str, reason: str = "") -> None:
    """Notify that a skill was skipped (re-run guard, rate limit, etc.)."""
    ts = datetime.now(timezone.utc).strftime("%H:%M UTC")
    msg = f"⏭️ <b>{skill_name}</b> skipped\n⏰ {ts}"
    if reason:
        msg += f"\n{reason}"
    send(msg, silent=True)


if __name__ == "__main__":
    # Quick test
    print("Sending test notification...")
    ok = send("🐾 <b>DogFoodAndFun Bot</b> is connected!\nNotifications are working ✅")
    print("✅ Sent!" if ok else "❌ Failed — check credentials")
