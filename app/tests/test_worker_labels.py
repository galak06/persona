"""Worker labels — `lib.worker_labels`.

The label is `<brand_id>-<flow_id>`, matching what
`lib.brand_provisioning._flow_to_task` writes. It used to hardcode
`"dogfood-"`, which disagreed with provisioning: a re-provision created a
second row per flow, the dispatcher opened a run under one id while the
script closed the other, and the UI's row stayed `running` forever. A second
brand would have had every engager run reported under one brand's prefix.
"""

from __future__ import annotations

import pytest

from lib.worker_labels import (
    LEGACY_TASK_ID_PREFIX,
    current_brand_id,
    flow_id_from_task_id,
    worker_label_for_flow,
)


class TestLabelIsBrandDerived:
    def test_explicit_brand_wins(self) -> None:
        assert worker_label_for_flow("ig-engager", "acme") == "acme-ig-engager"

    def test_brand_comes_from_the_environment_when_not_passed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BRAND_DIR", "/srv/brands/acme")
        assert current_brand_id() == "acme"
        assert worker_label_for_flow("fb-engager") == "acme-fb-engager"

    def test_two_brands_get_different_labels(self) -> None:
        """The multi-brand bug: one hardcoded prefix meant every brand's runs
        landed on one row."""
        a = worker_label_for_flow("ig-engager", "acme")
        b = worker_label_for_flow("ig-engager", "dogfoodandfun")
        assert a != b

    def test_no_brand_raises_rather_than_guessing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A label built without a brand is the defect this module prevents;
        returning a bare flow id would silently recreate it."""
        monkeypatch.delenv("BRAND_DIR", raising=False)
        with pytest.raises(RuntimeError, match="BRAND_DIR"):
            worker_label_for_flow("ig-engager")


class TestFlowIdExtraction:
    def test_brand_prefix_is_stripped(self) -> None:
        assert flow_id_from_task_id("acme-ig-engager", "acme") == "ig-engager"

    def test_legacy_prefix_is_still_recognised(self) -> None:
        """Un-migrated rows must still resolve, or they lose their per-flow
        log file and their launchd label."""
        assert flow_id_from_task_id(f"{LEGACY_TASK_ID_PREFIX}ig-engager", "acme") == "ig-engager"

    def test_an_unrecognised_prefix_yields_empty(self) -> None:
        assert flow_id_from_task_id("other-thing", "acme") == ""

    def test_a_flow_id_containing_hyphens_survives(self) -> None:
        assert (
            flow_id_from_task_id("acme-social-posts-compose", "acme") == "social-posts-compose"
        )
