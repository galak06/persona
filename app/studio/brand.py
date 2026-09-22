"""Brand identity for the content studio, loaded from a brand `config.json`.

The studio never hardcodes a niche, mascot, or species. Everything the
slide-planning prompt says about the brand comes from here, so the same
pipeline produces dog content, coffee content, or SaaS content depending
only on which config it was pointed at.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class BrandProfile:
    """The subset of a brand config the studio needs to plan a carousel."""

    name: str
    url: str
    persona: str
    mascot_name: str
    mascot_kind: str
    niche: str
    audience: str
    ig_handle: str

    @property
    def cta_ribbon(self) -> str:
        """Text for the closing slide's CTA ribbon."""
        domain = self.url.replace("https://", "").replace("http://", "").rstrip("/")
        return f"FULL GUIDE  →  {domain.upper()}" if domain else "FULL GUIDE IN BIO"

    @property
    def handle(self) -> str:
        """IG handle, always @-prefixed."""
        h = self.ig_handle.lstrip("@")
        return f"@{h}" if h else f"@{self.name.lower().replace(' ', '')}"

    @property
    def mascot_clause(self) -> str:
        """How prompts should refer to the mascot.

        When `mascot_kind` is blank the clause deliberately stays abstract —
        the model is never invited to guess a species it was not told.
        """
        if not self.mascot_name:
            return ""
        if self.mascot_kind:
            return f"{self.mascot_name}, the brand's {self.mascot_kind}"
        return f"{self.mascot_name}, the brand's mascot"

    @classmethod
    def from_config(cls, config_path: Path) -> BrandProfile:
        """Load a BrandProfile from a brand `config.json`."""
        raw: dict[str, Any] = json.loads(config_path.read_text(encoding="utf-8"))
        site: dict[str, Any] = raw.get("site") or {}
        ig: dict[str, Any] = (raw.get("social_channels") or {}).get("instagram") or {}
        return cls(
            name=str(site.get("name") or "Your Brand"),
            url=str(site.get("url") or ""),
            persona=str(site.get("brand_persona") or ""),
            mascot_name=str(site.get("mascot_name") or ""),
            mascot_kind=str(site.get("mascot_kind") or ""),
            niche=str(site.get("niche") or ""),
            audience=str(site.get("target_audience") or ""),
            ig_handle=str(ig.get("ig_username") or ""),
        )
