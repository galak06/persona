"""Product spotlight: one catalog product, featured from one published post, as
a Facebook Page post + an Instagram feed post sharing a single hook image.

Rows live in ``content_derivatives`` (``lib.derivatives_db``). The compose path
(``compose.compose_spotlight``, dispatched as ``scripts/social_derivative_compose.py``)
reuses the social-post crew, hook renderer and overlay composer unchanged and
adds only what is spotlight-specific: which products a post carries
(``post_products``), the product vocabulary shared by the prompt and the rules
(``product_terms``), the prompt override (``prompts``) and the affiliate
compliance rules (``rules``).

This package cannot publish: nothing in it imports a publisher, a worker or
the release sweep, and ``tests/test_social_derivative_compose_script.py``
asserts that on the AST.

The public surface is re-exported here, but the modules are NOT collapsed into
one: `api/` imports `product_terms` and `post_products` by module, and
`compose` imports `inputs` by module so tests can swap one collaborator at a
time. Both spellings work; the facade exists so a reader has one place to see
what the package offers.

**`compose_spotlight` is resolved lazily** (PEP 562). It is the only name here
that costs anything: it pulls the writer crew and the image client, ~3s and a
large dependency stack. `api/` reaches this package for `product_terms` and
`prompts`, which runs this module first -- so an eager import would put crewai
behind every FastAPI start-up and let a failure in that stack take the whole
API down instead of failing one flow. Everything else is cheap and stays eager.
"""

from typing import TYPE_CHECKING, Any

from lib.crew.spotlight.post_products import (
    asins_in_html,
    post_slug,
    product_excerpt,
    products_in_post,
)
from lib.crew.spotlight.product_terms import (
    brand_token,
    display_name,
    is_certification_verified,
    mentions_brand,
    verified_certifications,
)
from lib.crew.spotlight.prompts import (
    DISCLOSURE_MARKER,
    DISCLOSURE_SENTENCE,
    build_spotlight_task_description,
    spotlight_keyword,
)
from lib.crew.spotlight.result import SpotlightResult
from lib.crew.spotlight.rules import (
    FLAG_MASCOT_USAGE_UNVERIFIED,
    FLAG_PRODUCT_NOT_IN_POST,
    find_spotlight_flags,
    find_spotlight_violations,
)

if TYPE_CHECKING:  # the real symbol, for type checkers and `__all__`
    from lib.crew.spotlight.compose import compose_spotlight


def __getattr__(name: str) -> Any:
    """Resolve `compose_spotlight` on first touch, never on package import.

    A module-level hook, not a forwarding object, because this name is called
    (`lib.config` needs an object for the opposite reason: `settings` is read
    as an attribute at import time by its callers). The one caller that runs
    it -- `scripts/social_derivative_compose.py` -- imports the submodule
    directly and pays the cost knowingly.
    """
    if name == "compose_spotlight":
        from lib.crew.spotlight.compose import compose_spotlight

        return compose_spotlight
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "DISCLOSURE_MARKER",
    "DISCLOSURE_SENTENCE",
    "FLAG_MASCOT_USAGE_UNVERIFIED",
    "FLAG_PRODUCT_NOT_IN_POST",
    "SpotlightResult",
    "asins_in_html",
    "brand_token",
    "build_spotlight_task_description",
    "compose_spotlight",
    "display_name",
    "find_spotlight_flags",
    "find_spotlight_violations",
    "is_certification_verified",
    "mentions_brand",
    "post_slug",
    "product_excerpt",
    "products_in_post",
    "spotlight_keyword",
    "verified_certifications",
]
