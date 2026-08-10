"""Manage a brand's encrypted platform credentials (`lib.brand_secrets`).

    python -m scripts.brand_secrets --generate-key
    python -m scripts.brand_secrets --list
    python -m scripts.brand_secrets --set FB_PAGE_TOKEN         # prompts, hidden
    python -m scripts.brand_secrets --delete FB_PAGE_TOKEN
    python -m scripts.brand_secrets --import-from-env            # dry run
    python -m scripts.brand_secrets --import-from-env --apply
    python -m scripts.brand_secrets --verify

`--set` reads the value from a hidden prompt by default. `--value` exists for
scripting but puts the credential in shell history and `ps` output -- for a
tool whose purpose is keeping these values out of readable places, prefer the
prompt.

`--import-from-env` reads `<brand_dir>/.env` and copies the managed keys into
the encrypted table. It is **additive and non-destructive**: the `.env` file is
never modified, so a bad migration is recoverable by simply not using the
table. Deleting the values from `.env` is a deliberate manual step you take
after `--verify` passes.

Values are never printed. `--list` shows key names and a masked tail so you can
tell two tokens apart without putting either in your scrollback or logs.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

_ENGINE_ROOT = Path(__file__).resolve().parent.parent
if str(_ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(_ENGINE_ROOT))

from lib import brand_secrets
from lib.local_env import load_brand_env, load_brand_env_into_environ, load_local_env


def _infer_brand_dir(explicit: Path | None) -> Path:
    """Same convention as the crew pipelines' `_infer_brand_dir` — plus a
    reality check on the result.

    The check matters more here than in the pipelines: a typo'd `--brand-dir`
    (`brands/dogfodandfun`) would otherwise store secrets under a brand id
    nothing loads, `--verify` would bless them ("not in .env to compare"),
    and the operator would then delete the real credentials from `.env` on the
    strength of that pass — leaving publishes with no credential at all.
    """
    if explicit:
        candidate = explicit
    else:
        raw = os.environ.get("BRAND_DIR")
        if raw:
            candidate = Path(raw)
        else:
            brands_root = _ENGINE_ROOT / "brands"
            candidates = sorted(
                d for d in brands_root.glob("*") if d.is_dir() and not d.name.startswith((".", "_"))
            )
            if len(candidates) != 1:
                raise SystemExit(
                    "BRAND_DIR not set and brands/ has no single brand -- pass --brand-dir"
                )
            candidate = candidates[0]

    resolved = candidate.resolve()
    if not resolved.is_dir() or not (resolved / "config.json").exists():
        raise SystemExit(
            f"'{resolved}' is not a brand directory (missing config.json) -- "
            "check the path for typos before storing secrets under its name"
        )
    return resolved


def _mask(value: str) -> str:
    """Enough to identify a credential, not enough to use it."""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{'*' * 8}{value[-6:]} (len={len(value)})"


# SecretsError is caught PER KEY in _cmd_list/_cmd_verify so a single
# undecryptable row (e.g. written under a rotated master key) doesn't abort
# the whole listing/verification. Uncaught, `get_secret`'s SecretsError made
# the '(unreadable)' branches dead code and turned --verify into
# fail-on-first instead of the per-key report it exists to be.


def _cmd_list(brand_id: str) -> int:
    keys = brand_secrets.list_keys(brand_id)
    if not keys:
        print(f"no secrets stored for brand '{brand_id}'")
        return 0
    print(f"{len(keys)} secret(s) for brand '{brand_id}':")
    for key in keys:
        try:
            value = brand_secrets.get_secret(brand_id, key)
        except brand_secrets.SecretsError:
            value = None
        print(f"  {key:22} {_mask(value) if value else '(unreadable)'}")
    return 0


def _cmd_import(brand_id: str, brand_dir: Path, *, apply: bool) -> int:
    """Copy managed keys from `<brand_dir>/.env` into the encrypted table."""
    env = load_brand_env(brand_dir)
    present = [k for k in brand_secrets.MANAGED_KEYS if env.get(k, "").strip()]
    missing = [k for k in brand_secrets.MANAGED_KEYS if not env.get(k, "").strip()]

    print(f"brand      : {brand_id}")
    print(f"source     : {brand_dir / '.env'}")
    print(f"to import  : {len(present)} -- {', '.join(present) or '(none)'}")
    if missing:
        print(f"not in .env: {', '.join(missing)}")

    if not apply:
        print("\n(dry run -- nothing written; pass --apply)")
        return 0

    written = 0
    for key in present:
        if brand_secrets.set_secret(brand_id, key, env[key].strip()):
            written += 1
        else:
            print(f"  FAILED to write {key}", file=sys.stderr)
    print(f"\nwrote {written}/{len(present)} secret(s)")
    return 0 if written == len(present) else 1


def _cmd_verify(brand_id: str, brand_dir: Path) -> int:
    """Confirm every stored secret decrypts and matches `.env`.

    This is the gate to run BEFORE removing anything from `.env`: it proves the
    encrypted copy is usable, not merely present.
    """
    env = load_brand_env(brand_dir)
    keys = brand_secrets.list_keys(brand_id)
    if not keys:
        print(f"no secrets stored for brand '{brand_id}' -- nothing to verify")
        return 1

    mismatches = 0
    for key in keys:
        try:
            stored = brand_secrets.get_secret(brand_id, key)
        except brand_secrets.SecretsError:
            stored = None
        if stored is None:
            print(f"  {key:22} UNREADABLE")
            mismatches += 1
            continue
        file_value = env.get(key, "").strip()
        if not file_value:
            print(f"  {key:22} ok (decrypts; not in .env to compare)")
        elif stored == file_value:
            print(f"  {key:22} ok (matches .env)")
        else:
            print(f"  {key:22} DIFFERS from .env  db={_mask(stored)} env={_mask(file_value)}")
            mismatches += 1

    if mismatches:
        print(f"\n{mismatches} problem(s) -- do NOT remove these from .env yet")
        return 1
    print(f"\nall {len(keys)} secret(s) decrypt correctly")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--brand-dir", type=Path, default=None)
    p.add_argument("--generate-key", action="store_true", help="print a fresh master key")
    p.add_argument("--list", action="store_true", help="list key names (values masked)")
    p.add_argument("--set", metavar="KEY", default=None)
    p.add_argument(
        "--value",
        default=None,
        help="value for --set (omit to be prompted with hidden input -- preferred: "
        "a flag value lands in shell history and `ps` output)",
    )
    p.add_argument("--delete", metavar="KEY", default=None)
    p.add_argument("--import-from-env", action="store_true")
    p.add_argument("--verify", action="store_true")
    p.add_argument("--apply", action="store_true", help="write (with --import-from-env)")
    args = p.parse_args(argv)

    # No DB and no master key needed just to mint one.
    if args.generate_key:
        print(brand_secrets.generate_master_key())
        return 0

    brand_dir = _infer_brand_dir(args.brand_dir)
    # apply_secrets=False: this CLI *manages* the store; overlaying the store's
    # own values onto its process env first would let a bad row mask itself.
    load_brand_env_into_environ(brand_dir, apply_secrets=False)
    load_local_env()
    brand_id = brand_dir.name

    try:
        if args.list:
            return _cmd_list(brand_id)
        if args.set:
            value = args.value
            if value is None:
                value = getpass.getpass(f"value for {args.set} (hidden): ")
            ok = brand_secrets.set_secret(brand_id, args.set, value)
            print(f"{'wrote' if ok else 'FAILED to write'} {args.set} for {brand_id}")
            return 0 if ok else 1
        if args.delete:
            ok = brand_secrets.delete_secret(brand_id, args.delete)
            print(
                f"{'deleted' if ok else 'not deleted (missing, or DB error -- see logs)'}: {args.delete}"
            )
            return 0 if ok else 1
        if args.import_from_env:
            return _cmd_import(brand_id, brand_dir, apply=args.apply)
        if args.verify:
            return _cmd_verify(brand_id, brand_dir)
    except brand_secrets.SecretsError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    p.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
