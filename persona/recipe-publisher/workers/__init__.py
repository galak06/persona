"""Decoupled, DB-polling recipe artifact workers.

Each worker independently queries the recipe DB for rows needing its single
task, performs that task, and writes back the one artifact marker the next
worker polls on. There is no central orchestrator — workers are mutually
independent and idempotent. See the individual ``worker_*.py`` modules.

Importing this package also installs a sys.path bridge so the workers resolve
both the recipe-publisher packages (recipe_db / generators / publishers) and the
social-automation packages (lib.*) when run via ``python -m workers.<name>``,
regardless of cwd.
"""

from __future__ import annotations

import sys
from pathlib import Path

_RP = Path(__file__).resolve().parent.parent  # recipe-publisher/
_SA = _RP.parent  # social-automation/
for _p in (str(_SA), str(_RP)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
