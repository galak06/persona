"""The `.env` skeleton every new brand folder is scaffolded with.

Brand-owned credentials live in `$BRAND_DIR/.env`; engine-wide secrets stay in
`.claude/settings.local.json`. The split is by ownership, not by sensitivity:

  brand  — identifies or authenticates ONE brand's account (its WordPress site,
           its Facebook Page, its Pinterest board, its mailbox).
  engine — shared across every brand (the LLM quota, the tracing project, and
           the OAuth *app* credentials, since one developer app issues tokens
           for all brands' accounts).

Keeping the OAuth app credentials engine-side is what lets a new brand onboard
without registering its own Meta/Pinterest/TikTok developer app.
"""

from __future__ import annotations

from pathlib import Path

BRAND_ENV_TEMPLATE: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("WordPress", ("WP_URL", "WP_USER", "WP_APP_PASSWORD", "WP_AUTHOR_ID")),
    (
        "Facebook / Instagram browser login",
        (
            "FACEBOOK_USERNAME",
            "FACEBOOK_PASSWORD",
            "INSTAGRAM_USERNAME",
            "INSTAGRAM_PASSWORD",
        ),
    ),
    (
        "Meta Graph API (issued by the shared app's OAuth flow)",
        (
            "FB_PAGE_ID",
            "FB_PAGE_TOKEN",
            "FB_USER_TOKEN",
            "IG_ACCOUNT_ID",
        ),
    ),
    (
        "Pinterest (tokens only -- app credentials are engine-wide)",
        (
            "PINTEREST_ACCESS_TOKEN",
            "PINTEREST_REFRESH_TOKEN",
            "PINTEREST_BOARD_ID",
        ),
    ),
    ("TikTok account", ("TIKTOK_USERNAME", "TIKTOK_PASSWORD")),
    ("Affiliates", ("AMAZON_ASSOCIATES_TAG",)),
    ("Hosting / FTP", ("FTP_HOST", "FTP_USER", "FTP_PASS")),
    (
        "Brand mailbox",
        (
            "HOSTINGER_MAIL_URL",
            "HOSTINGER_MAIL_USER",
            "HOSTINGER_MAIL_PASSWORD",
        ),
    ),
    (
        "Google Sheets service account",
        (
            "GOOGLE_SERVICE_ACCOUNT_PATH",
            "GOOGLE_APPLICATION_CREDENTIALS",
        ),
    ),
)


def brand_env_keys() -> tuple[str, ...]:
    """Every brand-owned key name, flattened."""
    return tuple(key for _section, keys in BRAND_ENV_TEMPLATE for key in keys)


def render_brand_env_stub(brand_name: str) -> str:
    """Render the commented `.env` skeleton a new brand starts life with."""
    lines = [
        f"# Brand-owned credentials for {brand_name}.",
        "#",
        "# Loaded into os.environ by lib.local_env.load_brand_env_into_environ()",
        "# BEFORE .claude/settings.local.json, so values here win for this brand.",
        "# Engine-wide secrets (LLM key, OAuth app credentials, tracing) live in",
        "# .claude/settings.local.json and must NOT be duplicated here.",
        "#",
        "# Fill in a value and remove the leading '#' to activate the line.",
    ]
    for section, keys in BRAND_ENV_TEMPLATE:
        lines.append("")
        lines.append(f"# --- {section} ---")
        lines.extend(f"#{key}=" for key in keys)
    return "\n".join(lines) + "\n"


def write_brand_env_stub(brand_dir: Path) -> bool:
    """Create `<brand_dir>/.env` if absent. Returns True when it wrote one.

    Never overwrites: an already-provisioned brand's file holds real secrets,
    and re-provisioning must not clobber them (same rule that protects
    instagram_accounts.csv).
    """
    env_path = brand_dir / ".env"
    if env_path.exists():
        return False
    env_path.write_text(render_brand_env_stub(brand_dir.name), encoding="utf-8")
    env_path.chmod(0o600)
    return True
