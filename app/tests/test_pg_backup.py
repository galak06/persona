"""Tests for `scripts/pg_backup.py` -- dump validation, retention, R2 config.

`docker exec` and boto3 are mocked; gzip round-trips are real.
"""
# ruff: noqa: S101

from __future__ import annotations

import gzip
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from scripts import pg_backup

_GOOD_DUMP = (
    b"-- PostgreSQL database dump\n"
    + b"CREATE TABLE brands (id TEXT);\n"
    + b"x" * 20_000
    + b"\n-- PostgreSQL database dump complete\n"
)


def _fake_run(stdout: bytes) -> Any:
    def _run(cmd: list[str], **_kw: Any) -> subprocess.CompletedProcess[bytes]:
        assert cmd[:2] == ["docker", "exec"]
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr=b"")

    return _run


# ── dump ──────────────────────────────────────────────────────────────────


def test_dump_writes_gzipped_file_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(subprocess, "run", _fake_run(_GOOD_DUMP))
    target = pg_backup._dump(tmp_path)
    assert target.suffix == ".gz"
    assert gzip.decompress(target.read_bytes()) == _GOOD_DUMP
    # No .tmp leftovers -- the rename is the atomicity guarantee.
    assert list(target.parent.glob("*.tmp")) == []


def test_dump_refuses_a_suspiciously_small_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty/partial dump stored as success is worse than a loud crash --
    90 days later every retained backup would be the same garbage."""
    monkeypatch.setattr(subprocess, "run", _fake_run(b"-- nothing here"))
    with pytest.raises(RuntimeError, match="refusing to store"):
        pg_backup._dump(tmp_path)
    # The sanity check fires BEFORE any write -- nothing on disk at all.
    assert not (tmp_path / "backups").exists()


def test_dump_refuses_missing_create_table(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", _fake_run(b"y" * 50_000))
    with pytest.raises(RuntimeError, match="CREATE TABLE MISSING"):
        pg_backup._dump(tmp_path)


# ── retention ─────────────────────────────────────────────────────────────


def test_prune_keeps_only_the_newest_dump(tmp_path: Path) -> None:
    d = tmp_path / "backups" / "db"
    d.mkdir(parents=True)
    older = d / "persona-20260101-000000.sql.gz"
    newer = d / "persona-20260102-000000.sql.gz"
    newest = d / "persona-20260103-000000.sql.gz"
    for f in (older, newer, newest):
        f.write_bytes(b"x")

    removed = pg_backup._prune_local(tmp_path)

    assert removed == 2
    assert not older.exists()
    assert not newer.exists()
    assert newest.exists()


# ── R2 config resolution ──────────────────────────────────────────────────


def test_r2_config_none_when_incomplete(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in pg_backup._R2_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("R2_BUCKET", "backups")  # one of four is not enough
    with patch("lib.brand_secrets.get_secret", return_value=None):
        assert pg_backup._r2_config("b1") is None


def test_r2_config_prefers_encrypted_store(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in pg_backup._R2_KEYS:
        monkeypatch.setenv(key, "from-env")
    with patch("lib.brand_secrets.get_secret", return_value="from-store"):
        cfg = pg_backup._r2_config("b1")
    assert cfg is not None
    assert set(cfg.values()) == {"from-store"}


def test_r2_config_falls_back_to_env_when_store_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in pg_backup._R2_KEYS:
        monkeypatch.setenv(key, "env-value")
    with patch("lib.brand_secrets.get_secret", side_effect=RuntimeError("no db")):
        cfg = pg_backup._r2_config("b1")
    assert cfg is not None
    assert set(cfg.values()) == {"env-value"}


# ── upload ────────────────────────────────────────────────────────────────


def test_upload_pushes_then_prunes_remote(tmp_path: Path) -> None:
    dump = tmp_path / "persona-20260811-000000.sql.gz"
    dump.write_bytes(b"x")
    cfg = dict.fromkeys(pg_backup._R2_KEYS, "v") | {"R2_BUCKET": "bkt"}
    client = MagicMock()
    client.list_objects_v2.return_value = {
        "Contents": [
            {"Key": "pg/persona-20200101-000000.sql.gz"},
            {"Key": f"pg/{dump.name}"},
        ]
    }
    with patch.object(pg_backup, "_r2_client", return_value=client):
        pg_backup._upload_and_prune_r2(cfg, dump)

    client.upload_file.assert_called_once_with(str(dump), "bkt", f"pg/{dump.name}")
    deleted = client.delete_objects.call_args.kwargs["Delete"]["Objects"]
    assert deleted == [{"Key": "pg/persona-20200101-000000.sql.gz"}]
