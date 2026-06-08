"""Recipe scraping: fetch HTML and extract raw schema.org Recipe dicts.

Adapter-based design (SOLID/OCP): ``RecipeAdapter`` is the plug point for
future site-specific extractors. ``JsonLdAdapter`` is the universal default.

JSON-LD extraction prefers ``extruct``/``bs4`` when installed, but falls back
to a pure-stdlib HTML parser so tests never require third-party packages.
"""
from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from html.parser import HTMLParser

try:  # optional, used only for real network runs
    import requests
except ImportError:  # pragma: no cover - exercised only when dep missing
    requests = None  # type: ignore[assignment]

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Full browser-like header set. Some recipe hosts (e.g. AllRecipes) return 403
# to a bare User-Agent; sending the headers a real browser navigation includes
# gets a normal 200.
_BROWSER_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Upgrade-Insecure-Requests": "1",
}

# Matches AllRecipes-style canonical recipe URLs: /recipe/<id>/<slug>/
_RECIPE_LINK_RE = re.compile(
    r"https?://[^\s\"'<>]*?/recipe/\d+/[a-z0-9\-]+/?",
    re.IGNORECASE,
)


class _LdJsonCollector(HTMLParser):
    """Collect the raw text of every ``<script type=application/ld+json>``."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._in_ld_json: bool = False
        self.blocks: list[str] = []
        self._buffer: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if tag.lower() != "script":
            return
        attr_map = {k.lower(): (v or "") for k, v in attrs}
        if attr_map.get("type", "").strip().lower() == "application/ld+json":
            self._in_ld_json = True
            self._buffer = []

    def handle_data(self, data: str) -> None:
        if self._in_ld_json:
            self._buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self._in_ld_json:
            self._in_ld_json = False
            self.blocks.append("".join(self._buffer))
            self._buffer = []


def _extract_ld_json_blocks(html: str) -> list[str]:
    """Return raw text of all ld+json script blocks, parser-agnostic."""
    try:  # prefer bs4 if present
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        scripts = soup.find_all(
            "script", attrs={"type": "application/ld+json"}
        )
        return [s.string or s.get_text() or "" for s in scripts]
    except ImportError:
        parser = _LdJsonCollector()
        parser.feed(html)
        return parser.blocks


def _type_matches_recipe(type_value: object) -> bool:
    """True if a schema.org ``@type`` denotes a Recipe (str or list)."""
    if isinstance(type_value, str):
        return type_value.split("/")[-1].lower() == "recipe"
    if isinstance(type_value, list):
        return any(
            isinstance(t, str) and t.split("/")[-1].lower() == "recipe"
            for t in type_value
        )
    return False


def _find_recipe(node: object) -> dict[str, object] | None:
    """Depth-first walk for the first Recipe object, handling @graph/lists."""
    if isinstance(node, dict):
        if _type_matches_recipe(node.get("@type")):
            return node
        graph = node.get("@graph")
        if isinstance(graph, list):
            found = _find_recipe(graph)
            if found is not None:
                return found
        return None
    if isinstance(node, list):
        for item in node:
            found = _find_recipe(item)
            if found is not None:
                return found
    return None


class RecipeAdapter(ABC):
    """Plug point for site-specific recipe extraction strategies."""

    @abstractmethod
    def matches(self, url: str) -> bool:
        """Whether this adapter handles the given URL."""

    @abstractmethod
    def extract(self, html: str, url: str) -> dict[str, object] | None:
        """Return a raw schema.org Recipe dict, or None if not found."""


class JsonLdAdapter(RecipeAdapter):
    """Universal adapter: parses schema.org Recipe from JSON-LD blocks."""

    def matches(self, url: str) -> bool:
        return True

    def extract(self, html: str, url: str) -> dict[str, object] | None:
        for block in _extract_ld_json_blocks(html):
            text = (block or "").strip()
            if not text:
                continue
            try:
                data = json.loads(text)
            except (ValueError, TypeError):
                # Tolerate concatenated JSON objects in a single block.
                data = self._loads_lenient(text)
                if data is None:
                    continue
            recipe = _find_recipe(data)
            if recipe is not None:
                return recipe
        return None

    @staticmethod
    def _loads_lenient(text: str) -> object | None:
        """Best-effort parse of a block with multiple JSON values."""
        decoder = json.JSONDecoder()
        results: list[object] = []
        idx = 0
        length = len(text)
        while idx < length:
            while idx < length and text[idx] in " \t\r\n":
                idx += 1
            if idx >= length:
                break
            try:
                obj, end = decoder.raw_decode(text, idx)
            except ValueError:
                break
            results.append(obj)
            idx = end
        if not results:
            return None
        return results if len(results) > 1 else results[0]


def fetch_html(url: str, *, timeout: int = 20) -> str:
    """Fetch a page's HTML with a normal User-Agent (real runs only)."""
    if requests is None:  # pragma: no cover - dep-missing guard
        raise RuntimeError(
            "requests is not installed; pass html= to scrape() for offline use"
        )
    response = requests.get(url, headers=_BROWSER_HEADERS, timeout=timeout)
    response.raise_for_status()
    return response.text


def extract_recipe_links(html: str, base_url: str = "") -> list[str]:
    """Return de-duplicated canonical recipe URLs found on a listing/hub page.

    Matches AllRecipes-style ``/recipe/<id>/<slug>/`` links. ``base_url`` is
    accepted for future relative-link resolution; current sources emit absolute
    URLs. Order is preserved (first occurrence wins) so paging stays stable.
    """
    seen: set[str] = set()
    links: list[str] = []
    for match in _RECIPE_LINK_RE.findall(html):
        url = match.rstrip("/")
        if url not in seen:
            seen.add(url)
            links.append(url)
    return links


def scrape(
    url: str,
    html: str | None = None,
    adapters: list[RecipeAdapter] | None = None,
) -> dict[str, object] | None:
    """Return the raw schema.org Recipe dict for ``url``.

    If ``html`` is None the page is fetched (network). Tests pass ``html``
    directly to stay fully offline. The first matching adapter wins.
    """
    if html is None:
        html = fetch_html(url)
    chosen = adapters if adapters is not None else [JsonLdAdapter()]
    for adapter in chosen:
        if adapter.matches(url):
            recipe = adapter.extract(html, url)
            if recipe is not None:
                return recipe
    return None


# Keep regex import meaningful for linters even if unused elsewhere.
_WS_RE = re.compile(r"\s+")
