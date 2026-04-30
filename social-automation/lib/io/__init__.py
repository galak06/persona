"""File-IO helpers — atomic JSON read/write.

Replaces 9 reimplementations of `load_json`/`save_json` scattered across
scripts/. Every write is atomic by default (temp file + os.replace) so
a crashed process never leaves a half-written queue file or state file.
"""

from lib.io.jsonio import read_json, write_json

__all__ = ["read_json", "write_json"]
