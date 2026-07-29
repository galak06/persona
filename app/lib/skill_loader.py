"""Load LLM system prompts from SKILL.md files (the machine-read sections).

Skills wired to automated flows carry a ``## LLM Prompt`` section (optionally
``## LLM Prompt: <name>`` when one skill needs several prompts). This module
extracts exactly that region, renders ``{{brand.*}}`` placeholders from the
brand's ``config.json``, and returns it for use as the LLM SYSTEM prompt.
Everything outside the section is Claude-Code choreography and is never sent
to the model.

Fail-fast is deliberate: a missing file/section, an empty region, or an
unknown/empty/unrendered placeholder raises ``SkillPromptError`` naming the
file — a broken skill file is a deployment defect and must abort the run
loudly, never degrade into a promptless or half-rendered LLM call.

The config ``file_paths.skills_dir`` key is intentionally NOT wired here: it
stays provisioning metadata. Resolving it would couple this module to
``lib.config`` (and thus to BRAND_DIR at import time); the engine's own
``.claude/skills/`` directory is the single runtime source of skill prompts.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse


@dataclass(frozen=True)
class BrandVars:
    """The five brand values renderable in a skill prompt as ``{{brand.<field>}}``."""

    name: str
    name_lower: str
    domain: str
    mascot: str
    persona: str


class SkillPromptError(RuntimeError):
    """A skill prompt could not be loaded or rendered. Message names the file."""


def default_skills_dir() -> Path:
    """Engine-root-relative skills directory: ``<engine>/.claude/skills``."""
    return Path(__file__).resolve().parents[1] / ".claude" / "skills"


@lru_cache(maxsize=8)
def load_brand_vars(brand_dir: str | None = None) -> BrandVars:
    """BrandVars from ``<brand_dir>/config.json`` (default: ``$BRAND_DIR``).

    Maps the ``site`` block: ``name`` (also lowered into ``name_lower``),
    ``url`` (host with a leading ``www.`` stripped, into ``domain``),
    ``brand_persona`` (into ``persona``) and ``mascot_name`` (into
    ``mascot``). Cached for the process lifetime — the brand config is fixed
    per deployment.
    """
    resolved = brand_dir or os.environ.get("BRAND_DIR")
    if not resolved:
        raise SkillPromptError(
            "skill_loader.load_brand_vars: BRAND_DIR is not set and no brand_dir was given"
        )
    config_path = Path(resolved) / "config.json"
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SkillPromptError(f"{config_path}: cannot read brand config: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SkillPromptError(f"{config_path}: brand config is not valid JSON: {exc}") from exc
    site = raw.get("site")
    if not isinstance(site, dict):
        raise SkillPromptError(f"{config_path}: missing 'site' object in brand config")
    name = str(site.get("name") or "").strip()
    url = str(site.get("url") or "").strip()
    if not name or not url:
        raise SkillPromptError(f"{config_path}: site.name and site.url are required")
    domain = urlparse(url).netloc.removeprefix("www.")
    if not domain:
        raise SkillPromptError(f"{config_path}: site.url {url!r} has no parseable host")
    return BrandVars(
        name=name,
        name_lower=name.lower(),
        domain=domain,
        mascot=str(site.get("mascot_name") or "").strip(),
        persona=str(site.get("brand_persona") or "").strip(),
    )


_HEADING_RE = re.compile(
    r"^##\s*llm\s+prompt(?:\s*:\s*(?P<name>[^#\n]+?))?\s*:?\s*$", re.IGNORECASE
)
_ANY_H1_H2_RE = re.compile(r"^#{1,2}\s")
_PLACEHOLDER_RE = re.compile(r"\{\{\s*brand\.(\w+)\s*\}\}")
_UNRENDERED_RE = re.compile(r"\{\{\s*brand\.")
_BRAND_FIELDS = frozenset({"name", "name_lower", "domain", "mascot", "persona"})


def _strip_frontmatter(lines: list[str]) -> list[str]:
    """Drop a leading ``---`` ... ``---`` YAML frontmatter fence, if present."""
    if not lines or lines[0].strip() != "---":
        return lines
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return lines[i + 1 :]
    return lines


def _section_lines(lines: list[str], section: str | None, path: Path) -> list[str]:
    """Lines of the matching ``## LLM Prompt`` region.

    The region runs from the heading to the next ``#``/``##`` heading or EOF;
    ``###`` subheadings stay INSIDE. Heading match is case-insensitive and
    tolerant of extra spaces and a trailing colon.
    """
    want = section.strip().lower() if section else None
    start: int | None = None
    for i, line in enumerate(lines):
        match = _HEADING_RE.match(line)
        if match is None:
            continue
        got = (match.group("name") or "").strip().lower() or None
        if got == want:
            start = i + 1
            break
    label = f"## LLM Prompt: {section}" if section else "## LLM Prompt"
    if start is None:
        raise SkillPromptError(f"{path}: no '{label}' section found")
    end = len(lines)
    for j in range(start, len(lines)):
        if _ANY_H1_H2_RE.match(lines[j]):
            end = j
            break
    return lines[start:end]


def _strip_marker_comment(text: str) -> str:
    """Drop one leading ``<!-- ... -->`` marker comment (may span lines)."""
    if not text.startswith("<!--"):
        return text
    close = text.find("-->")
    if close == -1:
        return text
    return text[close + 3 :].strip()


def _render(text: str, brand: BrandVars, path: Path) -> str:
    """Substitute ``{{brand.<field>}}`` placeholders; fail loudly on any defect."""

    def _sub(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in _BRAND_FIELDS:
            raise SkillPromptError(
                f"{path}: unknown placeholder '{{{{brand.{key}}}}}' "
                f"(known: {', '.join(sorted(_BRAND_FIELDS))})"
            )
        value: str = getattr(brand, key)
        if not value:
            raise SkillPromptError(
                f"{path}: placeholder '{{{{brand.{key}}}}}' renders empty — "
                "fill the matching field in the brand config's site block"
            )
        return value

    rendered = _PLACEHOLDER_RE.sub(_sub, text)
    if _UNRENDERED_RE.search(rendered):
        raise SkillPromptError(
            f"{path}: unrendered '{{{{brand.' fragment survives substitution — "
            "malformed placeholder (missing closing braces?)"
        )
    return rendered


@lru_cache(maxsize=32)
def load_skill_prompt(
    skill: str,
    *,
    section: str | None = None,
    skills_dir: Path | None = None,
    brand: BrandVars | None = None,
) -> str:
    """The rendered LLM system prompt for ``skill``.

    Extracts the ``## LLM Prompt`` region (or ``## LLM Prompt: <section>``)
    from ``<skills_dir>/<skill>/SKILL.md``, strips YAML frontmatter and one
    leading ``<!-- ... -->`` marker comment, then renders ``{{brand.*}}``
    against ``brand`` (default: ``load_brand_vars()``). ``skills_dir``
    defaults to ``default_skills_dir()``.

    Raises ``SkillPromptError`` (always naming the file) on a missing file or
    section, an empty region, or an unknown/empty/unrendered placeholder.
    """
    directory = skills_dir if skills_dir is not None else default_skills_dir()
    path = directory / skill / "SKILL.md"
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SkillPromptError(f"{path}: cannot read skill file: {exc}") from exc
    region = _section_lines(_strip_frontmatter(raw.splitlines()), section, path)
    text = _strip_marker_comment("\n".join(region).strip())
    if not text:
        label = f"## LLM Prompt: {section}" if section else "## LLM Prompt"
        raise SkillPromptError(f"{path}: '{label}' section is empty")
    return _render(text, brand if brand is not None else load_brand_vars(), path)
