#!/usr/bin/env python3
"""Write the API's OpenAPI schema to a file, without running a server.

`frontend/package.json`'s `gen:api:fetch` curled a live
`http://127.0.0.1:5001/openapi.json`, which meant regenerating the typed client
required a developer to have the API running locally and to remember to do it.
Neither held: the committed client sat 31 paths behind the API, and nothing
noticed, because the frontend hand-writes the types it needs rather than
importing the generated ones.

Importing the app in-process makes regeneration a pure function of the source
tree, which is what lets CI check the committed client for staleness the same
way `tools.profiles_build --check` checks `data/rate_limits.json`.

Usage::

    python -m tools.dump_openapi                       # to stdout
    python -m tools.dump_openapi -o frontend/openapi.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# The app titles itself "Persona — <brand name>", which would make this
# artifact vary by whichever brand happened to be loaded. A lockfile has to be
# a pure function of the source tree or it cannot be checked, so the title is
# normalised. Nothing consumes it: the generated TypeScript ignores `info`.
_STABLE_TITLE = "Persona API"


def build_schema() -> dict[str, object]:
    from api.approval_api import app

    schema = dict(app.openapi())
    info = dict(schema.get("info") or {})
    info["title"] = _STABLE_TITLE
    schema["info"] = info
    return schema


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-o", "--output", type=Path, default=None, help="write here (default stdout)"
    )
    args = parser.parse_args()

    text = json.dumps(build_schema(), indent=2, sort_keys=True) + "\n"
    if args.output is None:
        sys.stdout.write(text)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
        print(f"wrote {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
