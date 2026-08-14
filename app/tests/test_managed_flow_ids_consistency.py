"""Pin the four independent copies of "which flows does `enabled_flows` gate"
to each other.

PR #61 renamed `fb-scanner` -> `fb-engager` in two of them and missed the
other two. Nothing failed: `MANAGED_FLOW_IDS` decides which cards the Flow
status panel gates, `_STAGE1_FLOWS` decides which ones ever get a
`schedule_tasks` row, and the frontend copy is not imported by anything at
all -- so a brand could show an `fb-engager` card whose "Run now" 404s
forever, which is exactly what shipped.

The lists live in different languages and are read at different times
(request, provisioning, build), so there is no shared constant to collapse
them into. Comparing them in a test is the cheap substitute.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from lib.brand_provisioning import _STAGE1_FLOWS
from lib.brands_db.models import MANAGED_FLOW_IDS
from lib.flow_readiness import _FLOW_ORDER, _FLOW_SCRIPTS, _PANEL_HIDDEN_FLOWS, _PANEL_ORDER

_APP_ROOT = Path(__file__).resolve().parent.parent
_BRANDS_TS = _APP_ROOT / "frontend" / "src" / "api" / "brands.ts"
_FACEBOOK_PROFILE = _APP_ROOT / "profiles" / "facebook.json"


def _frontend_managed_flow_ids() -> list[str]:
    """Parse the `MANAGED_FLOW_IDS` array literal out of `brands.ts`.

    A regex rather than a build step: the constant is a flat list of string
    literals on one line, and standing up a JS toolchain inside the Python
    suite to read one array would cost far more than it protects.
    """
    source = _BRANDS_TS.read_text(encoding="utf-8")
    match = re.search(r"export const MANAGED_FLOW_IDS = \[(.*?)\] as const;", source, re.S)
    assert match, f"MANAGED_FLOW_IDS array literal not found in {_BRANDS_TS}"
    return re.findall(r'"([^"]+)"', match.group(1))


def test_stage1_flows_matches_managed_flow_ids() -> None:
    """Every gated flow must be provisionable, or its "Run now" 404s."""
    assert set(_STAGE1_FLOWS) == set(MANAGED_FLOW_IDS)


def test_flow_readiness_registry_matches_managed_flow_ids() -> None:
    """The UI renders one card per `_FLOW_ORDER` entry and reads its script
    path from `_FLOW_SCRIPTS`; a flow missing from either is invisible."""
    assert set(_FLOW_ORDER) == set(MANAGED_FLOW_IDS)
    assert set(_FLOW_SCRIPTS) == set(MANAGED_FLOW_IDS)


def test_panel_hidden_flows_are_still_gated_flows() -> None:
    """Hiding a card is display-only: a hidden flow stays in `MANAGED_FLOW_IDS`
    (so `enabled_flows` still gates it), keeps its `_FLOW_SCRIPTS` entry, and
    is merely dropped from what `flow_status()` renders. Hiding a flow that
    isn't managed would silently un-gate it instead."""
    assert _PANEL_HIDDEN_FLOWS <= set(MANAGED_FLOW_IDS)
    assert set(_PANEL_ORDER) == set(_FLOW_ORDER) - _PANEL_HIDDEN_FLOWS
    assert _PANEL_ORDER == tuple(f for f in _FLOW_ORDER if f in _PANEL_ORDER)


def test_frontend_managed_flow_ids_matches_python() -> None:
    """The TS copy is documented as mirroring the Python one -- enforce it."""
    assert set(_frontend_managed_flow_ids()) == set(MANAGED_FLOW_IDS)


def test_frontend_managed_flow_ids_uses_presentation_order() -> None:
    """Order is meaningful in the frontend copy (it drives card order), so
    pin it to `_FLOW_ORDER` rather than to the unordered frozenset."""
    assert _frontend_managed_flow_ids() == list(_FLOW_ORDER)


def test_managed_flows_have_a_profile_entry() -> None:
    """`profiles/*.json` is what `tools.profiles_build` composes the schedule
    artifacts from. A managed flow with no profile entry gets no
    `schedule.json` task and therefore no dispatchable row -- and, because
    the artifacts are generated, hand-editing them back is silently reverted
    by the next build (the #61 failure mode).
    """
    profile_flow_ids = {
        flow["id"] for flow in json.loads(_FACEBOOK_PROFILE.read_text(encoding="utf-8"))["flows"]
    }
    facebook_managed = {f for f in MANAGED_FLOW_IDS if f.startswith("fb-")}
    assert facebook_managed <= profile_flow_ids


def test_fb_engager_owns_the_cron_and_superseded_flows_are_gone() -> None:
    """`fb-scanner`/`fb-comment` are the two-stage pair `fb-engager` replaced.
    PR #61 only disabled their cron (reversible); the single-pass cutover
    deleted their profile entries outright. A reappearing entry means the
    profile was reverted and the old pipeline is dispatching again alongside
    its replacement. fb-engager itself must keep the daily 15:33 schedule --
    it is the sole Facebook engagement flow now, so losing its cron silences
    FB engagement entirely (nothing else would ever dispatch it).
    """
    flows = {
        flow["id"]: flow
        for flow in json.loads(_FACEBOOK_PROFILE.read_text(encoding="utf-8"))["flows"]
    }
    for flow_id in ("fb-scanner", "fb-comment"):
        assert flow_id not in flows, f"{flow_id} entry is back; fb-engager supersedes it"
    assert "fb-engager" in flows, "fb-engager missing from profiles/facebook.json"
    assert flows["fb-engager"]["schedule"]["cron"] == "33 15 * * *"
