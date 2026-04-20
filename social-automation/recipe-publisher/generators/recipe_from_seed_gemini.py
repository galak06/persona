"""Gemini-backed voice generation — drop-in replacement for the Anthropic path.

Same contract as `recipe_from_seed.generate_from_seed`: takes (topic, seed),
returns the voice dict that matches the submit_voice tool schema. Used when
`VOICE_PROVIDER=gemini` is set in the environment (or when no Anthropic key is
available).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import httpx

from .recipe_from_seed import VOICE_TOOL, _build_user_message
from .seeds import RecipeSeed, seed_to_body_sections

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"
_GEMINI_MODEL = os.getenv("GEMINI_VOICE_MODEL", "gemini-2.5-flash")
_GEMINI_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)


def generate_from_seed_gemini(topic: str, seed: RecipeSeed) -> dict[str, Any]:
    """Call Gemini function-calling for voice fields. Same output shape as the Anthropic path."""
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY not set — needed for VOICE_PROVIDER=gemini")

    sections = seed_to_body_sections(seed)
    system_prompt = (_PROMPTS_DIR / "recipe_system.md").read_text()
    user_msg = _build_user_message(topic, seed, sections)

    tool_decl = {
        "name": VOICE_TOOL["name"],
        "description": VOICE_TOOL["description"],
        "parameters": _to_gemini_schema(VOICE_TOOL["input_schema"]),
    }
    payload = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_msg}]}],
        "tools": [{"functionDeclarations": [tool_decl]}],
        "toolConfig": {
            "functionCallingConfig": {
                "mode": "ANY",
                "allowedFunctionNames": [VOICE_TOOL["name"]],
            }
        },
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 4096},
    }

    url = _GEMINI_ENDPOINT.format(model=_GEMINI_MODEL)
    logger.info("gemini voice call model=%s topic=%r seed=%s", _GEMINI_MODEL, topic, seed.id)
    r = httpx.post(url, params={"key": key}, json=payload, timeout=120.0)
    if r.status_code >= 400:
        raise RuntimeError(f"gemini voice HTTP {r.status_code}: {r.text[:500]}")

    data = r.json()
    cands = data.get("candidates") or []
    if not cands:
        raise RuntimeError(f"gemini returned no candidates: {data!r}")
    parts = cands[0].get("content", {}).get("parts", [])
    for p in parts:
        fc = p.get("functionCall") or p.get("function_call")
        if fc and fc.get("name") == VOICE_TOOL["name"]:
            args = fc.get("args") or fc.get("arguments") or {}
            if isinstance(args, str):  # some responses ship args as a JSON string
                args = json.loads(args)
            return _normalize_voice(args)
    raise RuntimeError(f"gemini did not call submit_voice; parts={parts!r}")


def _to_gemini_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Translate Anthropic-style JSON Schema to Gemini's Schema proto shape.

    Main differences: type names are uppercase enum strings; some JSON Schema
    keywords are silently dropped by Gemini (minItems/maxItems). We keep them —
    newer Gemini versions honor them and older ones ignore them.
    """
    type_map = {
        "object": "OBJECT",
        "string": "STRING",
        "integer": "INTEGER",
        "number": "NUMBER",
        "boolean": "BOOLEAN",
        "array": "ARRAY",
    }
    out: dict[str, Any] = {}
    for k, v in schema.items():
        if k == "type" and isinstance(v, str):
            out["type"] = type_map.get(v, v.upper())
        elif k == "properties" and isinstance(v, dict):
            out["properties"] = {pk: _to_gemini_schema(pv) for pk, pv in v.items()}
        elif k == "items" and isinstance(v, dict):
            out["items"] = _to_gemini_schema(v)
        else:
            out[k] = v
    return out


def _normalize_voice(args: dict[str, Any]) -> dict[str, Any]:
    """Best-effort tidy of Gemini's output so downstream code sees a stable shape."""
    out = dict(args)
    # FAQ may come as a list of dicts or a list of {"question", "answer"} — both fine.
    # Ensure strings, not None, on required fields.
    for req in ("intro", "nallas_verdict", "meta_description", "image_brief", "ig_caption"):
        if out.get(req) is None:
            out[req] = ""
    if "faq" not in out or out["faq"] is None:
        out["faq"] = []
    return out
