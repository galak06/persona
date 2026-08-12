"""Encrypted per-brand platform credentials, stored in Postgres.

Replaces `<brand_dir>/.env` as the source of truth for the credentials that
are genuinely **per brand** — the Facebook/Instagram/WordPress values every
publisher reads off `os.environ`. Engine-wide keys (DEEPSEEK_API_KEY,
GEMINI_API_KEY, SERPER_API_KEY) and everything Docker Compose interpolates at
container start (POSTGRES_*, DATABASE_URL) deliberately stay in `.env`: a
process cannot fetch from the database the credentials it needs to reach the
database.

**Values are encrypted before they are written.** A plaintext secrets table
would be a downgrade from a 0600 file — rows land in every `pg_dump`, every
backup, and are readable by anything holding a DB connection. Encryption is
Fernet (AES-128-CBC + HMAC) with the key supplied by `PERSONA_SECRET_KEY`,
which stays the single secret in `.env`. Lose that key and the rows are
unrecoverable; leak it *and* a dump and you have lost the secrets — so it is
worth no less protection than the credentials it guards.

Why this exists at all, beyond tidiness: `.env` files are merged into
`os.environ` by loaders that **skip keys already present**
(`lib.local_env.load_brand_env_into_environ`), so any ambient value wins. A
stale `FB_PAGE_TOKEN` belonging to a deleted Meta app sat in a developer
settings file, shadowed the correct token in both `.env` files, and every
publish attempt died on `OAuthException code 190: "Application has been
deleted."` while the real credential sat right there unused. `load_into_environ`
below therefore **overrides** by default: for brand-owned keys the database is
authoritative, not whatever happens to be in the environment.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

from lib import db

if TYPE_CHECKING:
    from cryptography.fernet import Fernet

_log = logging.getLogger(__name__)

MASTER_KEY_ENV = "PERSONA_SECRET_KEY"

# The engine-wide env file that carries the master key for host runs (Compose
# passes it to containers as a real env var). Read for THAT ONE KEY only --
# an earlier draft merged this whole file into os.environ, which leaked
# container-shaped values into host processes, bled one brand's credentials
# into another, and (worst) injected a live DATABASE_URL into pytest after
# conftest's disposability guard had already run. Reviewed, confirmed, removed.
_ENGINE_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"

# The credentials that are per-brand and therefore belong here. Anything not
# on this list stays in .env -- this is a deliberate boundary, not a default:
# an engine-wide API key stored per brand would have to be duplicated for
# every brand and rotated N times.
MANAGED_KEYS = (
    "FB_PAGE_ID",
    "FB_PAGE_TOKEN",
    "FB_APP_ID",
    "FB_APP_SECRET",
    "IG_ACCOUNT_ID",
    "WP_URL",
    "WP_USER",
    "WP_APP_PASSWORD",
)


class SecretsError(RuntimeError):
    """Raised when the store cannot be used at all (missing/invalid master key).

    Deliberately fatal rather than defensive: unlike the rest of this repo's
    DB helpers, silently returning None here would let a publisher fall back
    to a stale ambient credential -- exactly the failure this module exists
    to prevent.
    """


def generate_master_key() -> str:
    """A fresh Fernet key, for `PERSONA_SECRET_KEY`. Printed by the CLI's
    `--generate-key`; never stored anywhere by this module."""
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode()


def resolve_master_key() -> str | None:
    """The master key, or None if not configured anywhere.

    Order: real environment first (Compose-injected in containers, explicit
    exports on the host), then a targeted single-key read of `app/.env` so
    host scripts work without exporting anything. Only this one key is ever
    read from that file -- see `_ENGINE_ENV_PATH`'s comment for the incident
    history behind that restriction.
    """
    raw = os.environ.get(MASTER_KEY_ENV, "").strip()
    if raw:
        return raw
    if _ENGINE_ENV_PATH.exists():
        prefix = f"{MASTER_KEY_ENV}="
        for line in _ENGINE_ENV_PATH.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith(prefix):
                value = line.strip()[len(prefix) :].strip()
                if value:
                    return value
    return None


def _fernet() -> Fernet:
    """Fernet built from the resolved master key."""
    from cryptography.fernet import Fernet

    raw = resolve_master_key()
    if raw is None:
        raise SecretsError(
            f"{MASTER_KEY_ENV} is not set -- brand secrets cannot be read or written. "
            "Generate one with `python -m scripts.brand_secrets --generate-key` "
            "and add it to app/.env."
        )
    try:
        return Fernet(raw.encode())
    except Exception as exc:
        raise SecretsError(f"{MASTER_KEY_ENV} is not a valid Fernet key: {exc}") from exc


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(token: str) -> str:
    from cryptography.fernet import InvalidToken

    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise SecretsError(
            "stored secret could not be decrypted -- PERSONA_SECRET_KEY does not "
            "match the key these rows were written with."
        ) from exc


# ─────────────────────────────────────────────────────────────────────────────
# Write


def set_secret(brand_id: str, key: str, value: str) -> bool:
    """Upsert one encrypted secret. Returns True on success.

    An empty value is refused loudly: `--set K --value "$UNSET_VAR"` would
    otherwise store "" -- which the loader then applies over a WORKING `.env`
    credential in every process, breaking publishes with a confusing
    "env var is required but unset/empty" far from the actual mistake.
    """
    if not value.strip():
        raise SecretsError(f"refusing to store an empty value for {brand_id}/{key}")
    try:
        db.execute(
            "INSERT INTO brand_secrets (brand_id, key, value_enc, updated_at) "
            "VALUES (%s, %s, %s, NOW()) "
            "ON CONFLICT (brand_id, key) DO UPDATE "
            "SET value_enc = EXCLUDED.value_enc, updated_at = NOW()",
            (brand_id, key, encrypt(value)),
        )
        return True
    except SecretsError:
        raise
    except Exception as exc:
        _log.warning("brand_secrets.set_secret failed for %s/%s: %s", brand_id, key, exc)
        return False


def delete_secret(brand_id: str, key: str) -> bool:
    """Delete one secret. True if a row was removed; False if there was no
    such row OR the delete failed (logged) -- callers that must distinguish
    should check `list_keys` afterwards."""
    try:
        rowcount = db.execute(
            "DELETE FROM brand_secrets WHERE brand_id = %s AND key = %s", (brand_id, key)
        )
        return rowcount > 0
    except Exception as exc:
        _log.warning("brand_secrets.delete_secret failed: %s", exc)
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Read


def get_secret(brand_id: str, key: str) -> str | None:
    """One decrypted secret, or None if absent."""
    try:
        row = db.fetch_one(
            "SELECT value_enc FROM brand_secrets WHERE brand_id = %s AND key = %s",
            (brand_id, key),
        )
    except Exception as exc:
        _log.warning("brand_secrets.get_secret failed: %s", exc)
        return None
    if not row:
        return None
    return decrypt(str(row["value_enc"]))


def list_keys(brand_id: str) -> list[str]:
    """Key NAMES only -- this module never returns a bulk plaintext dump, so a
    listing endpoint or log line cannot leak values by accident."""
    try:
        rows = db.fetch_all(
            "SELECT key FROM brand_secrets WHERE brand_id = %s ORDER BY key", (brand_id,)
        )
        return [str(r["key"]) for r in rows]
    except Exception as exc:
        _log.warning("brand_secrets.list_keys failed: %s", exc)
        return []


def load_into_environ(brand_id: str, *, override: bool = True) -> int:
    """Merge this brand's secrets into `os.environ`. Returns the count applied.

    `override=True` by default, which is the opposite of
    `lib.local_env.load_brand_env_into_environ`'s skip-if-present rule, and the
    entire point: a value in this table is the brand's real credential, while
    an ambient one may be anything a shell happened to export. Deferring to the
    environment is what let a deleted app's token shadow a working one.

    **All-or-nothing.** Every row is fetched (ONE query, one Fernet) and every
    value decrypted BEFORE anything touches `os.environ`. The incremental
    version failed review: with rows applied as they decrypt, one bad row
    mid-loop left the keys before it from the DB and the keys after it from a
    stale `.env` -- a mixed credential set (e.g. new FB_APP_ID with old
    FB_PAGE_TOKEN) worse than either source whole. A decrypt failure now
    raises `SecretsError` naming the bad keys, with the environment untouched.

    A brand with no rows loads nothing and returns 0 -- adoptable before
    migrating.
    """
    try:
        rows = db.fetch_all(
            "SELECT key, value_enc FROM brand_secrets WHERE brand_id = %s ORDER BY key",
            (brand_id,),
        )
    except Exception as exc:
        _log.warning("brand_secrets.load_into_environ fetch failed: %s", exc)
        return 0
    if not rows:
        return 0

    decrypted: dict[str, str] = {}
    bad: list[str] = []
    for row in rows:
        try:
            decrypted[str(row["key"])] = decrypt(str(row["value_enc"]))
        except SecretsError:
            bad.append(str(row["key"]))
    if bad:
        raise SecretsError(
            f"{len(bad)} stored secret(s) for '{brand_id}' would not decrypt "
            f"({', '.join(bad)}) -- environment left untouched. "
            f"Check {MASTER_KEY_ENV} matches the key the rows were written with."
        )

    applied = 0
    for key, value in decrypted.items():
        if ":" in key:
            # Namespaced app-data rows (e.g. lib.oauth.openart_store's
            # "openart:tokens") are structured secrets, not env vars --
            # merging multi-KB token JSON into every process environment
            # (inherited by every subprocess) would be a leak, not a load.
            continue
        if not override and key in os.environ:
            continue
        os.environ[key] = value
        applied += 1
    return applied
