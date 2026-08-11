"""Nightly Postgres backup: pg_dump -> gzip -> local retention -> optional R2.

The `postgres_data` Docker volume is the ONLY copy of every idea, caption,
schedule, publish record and encrypted secret -- one Docker Desktop reset
away from zero. This writes a compressed logical dump to
`$BRAND_DIR/backups/db/` (the brand dir lives on the real filesystem,
OUTSIDE Docker's VM) and, when R2 credentials are configured, pushes each
dump offsite to Cloudflare R2 (S3-compatible; free tier dwarfs our ~15 KB
gzipped dumps by six orders of magnitude).

Dumping runs `pg_dump` INSIDE the postgres container (`docker exec`), so the
client always matches the server version exactly and the host needs no
postgres tooling. That makes this a HOST script (launchd), not a worker-
container flow -- the worker has no docker socket, deliberately.

R2 credentials come from the encrypted per-brand store
(`lib.brand_secrets`: R2_ACCOUNT_ID, R2_ACCESS_KEY_ID,
R2_SECRET_ACCESS_KEY, R2_BUCKET), falling back to plain env vars. Absent
credentials downgrade to local-only with a WARNING -- a missing offsite
copy should nag, not break the local one.

Usage::

    python -m scripts.pg_backup                 # dump + prune + upload
    python -m scripts.pg_backup --no-upload     # local only
    python -m scripts.pg_backup --list          # inventory local + remote
    python -m scripts.pg_backup --verify-latest # integrity-check newest dump

Restore (documented here because a backup nobody can restore is theater)::

    gunzip -c <file>.sql.gz | docker exec -i persona-postgres-1 \
        psql -U persona -d persona
"""

from __future__ import annotations

import argparse
import gzip
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

_ENGINE_ROOT = Path(__file__).resolve().parent.parent
if str(_ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(_ENGINE_ROOT))

from lib.local_env import load_brand_env_into_environ, load_local_env
from lib.observability import get_logger

logger = get_logger(__name__)

RETENTION_DAYS = 90
_CONTAINER = os.environ.get("PG_BACKUP_CONTAINER", "persona-postgres-1")
_DB_USER = "persona"
_DB_NAME = "persona"
_R2_KEYS = ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET")


def _backup_dir(brand_dir: Path) -> Path:
    return brand_dir / "backups" / "db"


def _dump(brand_dir: Path) -> Path:
    """One gzipped dump into the brand backups dir. Raises on any failure --
    an empty or partial backup recorded as success is worse than a loud crash."""
    result = subprocess.run(
        ["docker", "exec", _CONTAINER, "pg_dump", "-U", _DB_USER, _DB_NAME],
        capture_output=True,
        timeout=300,
        check=True,
    )
    raw = result.stdout
    if len(raw) < 10_000 or b"CREATE TABLE" not in raw:
        raise RuntimeError(
            f"dump looks wrong ({len(raw)} bytes, CREATE TABLE "
            f"{'present' if b'CREATE TABLE' in raw else 'MISSING'}) -- refusing to store it"
        )

    out_dir = _backup_dir(brand_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    target = out_dir / f"{_DB_NAME}-{stamp}.sql.gz"
    tmp = target.with_suffix(".gz.tmp")
    tmp.write_bytes(gzip.compress(raw, compresslevel=9))
    tmp.rename(target)  # atomic: never a half-written .sql.gz under the real name
    logger.info("pg_backup_written", path=str(target), raw_bytes=len(raw))
    return target


def _prune_local(brand_dir: Path) -> int:
    cutoff = datetime.now(UTC) - timedelta(days=RETENTION_DAYS)
    removed = 0
    for f in sorted(_backup_dir(brand_dir).glob(f"{_DB_NAME}-*.sql.gz")):
        if datetime.fromtimestamp(f.stat().st_mtime, UTC) < cutoff:
            f.unlink()
            removed += 1
    if removed:
        logger.info("pg_backup_pruned_local", removed=removed)
    return removed


def _r2_config(brand_id: str) -> dict[str, str] | None:
    """R2 credentials from the encrypted store, else env. None if incomplete."""
    values: dict[str, str] = {}
    try:
        from lib import brand_secrets

        for key in _R2_KEYS:
            values[key] = brand_secrets.get_secret(brand_id, key) or os.environ.get(key, "")
    except Exception:  # store unavailable -- env may still carry them
        values = {key: os.environ.get(key, "") for key in _R2_KEYS}
    return values if all(values.values()) else None


def _r2_client(cfg: dict[str, str]) -> Any:
    import boto3

    return boto3.client(
        "s3",
        endpoint_url=f"https://{cfg['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
        aws_access_key_id=cfg["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=cfg["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )


def _upload_and_prune_r2(cfg: dict[str, str], dump_path: Path) -> None:
    """Push the new dump, then prune remote copies past retention. Raises on
    upload failure (the offsite copy is the point); prune is best-effort."""
    client = _r2_client(cfg)
    key = f"pg/{dump_path.name}"
    client.upload_file(str(dump_path), cfg["R2_BUCKET"], key)
    logger.info("pg_backup_uploaded_r2", bucket=cfg["R2_BUCKET"], key=key)

    try:
        cutoff = datetime.now(UTC) - timedelta(days=RETENTION_DAYS)
        listing = client.list_objects_v2(Bucket=cfg["R2_BUCKET"], Prefix="pg/")
        stale = [o["Key"] for o in listing.get("Contents", []) if o["LastModified"] < cutoff]
        if stale:
            client.delete_objects(
                Bucket=cfg["R2_BUCKET"],
                Delete={"Objects": [{"Key": k} for k in stale]},
            )
            logger.info("pg_backup_pruned_r2", removed=len(stale))
    except Exception as exc:
        logger.warning("pg_backup_r2_prune_failed", error=str(exc))


def _cmd_list(brand_dir: Path, brand_id: str) -> int:
    local = sorted(_backup_dir(brand_dir).glob(f"{_DB_NAME}-*.sql.gz"))
    print(f"local ({len(local)}):")
    for f in local[-5:]:
        print(f"  {f.name}  {f.stat().st_size:,} bytes")
    cfg = _r2_config(brand_id)
    if cfg is None:
        print("r2: not configured")
        return 0
    listing = _r2_client(cfg).list_objects_v2(Bucket=cfg["R2_BUCKET"], Prefix="pg/")
    contents = listing.get("Contents", [])
    print(f"r2 ({len(contents)}):")
    for o in contents[-5:]:
        print(f"  {o['Key']}  {o['Size']:,} bytes")
    return 0


def _cmd_verify_latest(brand_dir: Path) -> int:
    files = sorted(_backup_dir(brand_dir).glob(f"{_DB_NAME}-*.sql.gz"))
    if not files:
        print("no local backups to verify")
        return 1
    latest = files[-1]
    content = gzip.decompress(latest.read_bytes())  # raises on corruption
    ok = b"CREATE TABLE" in content and b"PostgreSQL database dump complete" in content
    print(f"{latest.name}: {'OK' if ok else 'INCOMPLETE'} ({len(content):,} bytes raw)")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--brand-dir", type=Path, default=None)
    p.add_argument("--no-upload", action="store_true", help="skip R2 even if configured")
    p.add_argument("--list", action="store_true")
    p.add_argument("--verify-latest", action="store_true")
    args = p.parse_args(argv)

    brand_dir = (args.brand_dir or Path(os.environ.get("BRAND_DIR", ""))).resolve()
    if not brand_dir.is_dir():
        print("ERROR: BRAND_DIR not set (or --brand-dir missing)", file=sys.stderr)
        return 1
    load_brand_env_into_environ(brand_dir, apply_secrets=False)
    load_local_env()
    brand_id = brand_dir.name

    if args.list:
        return _cmd_list(brand_dir, brand_id)
    if args.verify_latest:
        return _cmd_verify_latest(brand_dir)

    try:
        dump_path = _dump(brand_dir)
    except Exception as exc:
        logger.error("pg_backup_dump_failed", error=str(exc))
        print(f"ERROR: dump failed: {exc}", file=sys.stderr)
        return 1
    _prune_local(brand_dir)

    if args.no_upload:
        print(f"ok (local only): {dump_path.name}")
        return 0

    cfg = _r2_config(brand_id)
    if cfg is None:
        logger.warning("pg_backup_r2_not_configured")
        print(
            f"ok (LOCAL ONLY -- no offsite copy): {dump_path.name}\n"
            "  Configure R2 via: python -m scripts.brand_secrets --set R2_ACCOUNT_ID  (etc.)"
        )
        return 0

    try:
        _upload_and_prune_r2(cfg, dump_path)
    except Exception as exc:
        logger.error("pg_backup_r2_upload_failed", error=str(exc))
        print(
            f"ERROR: local dump ok ({dump_path.name}) but R2 upload failed: {exc}", file=sys.stderr
        )
        return 1
    print(f"ok (local + r2): {dump_path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
