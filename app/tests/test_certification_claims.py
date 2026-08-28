"""Certification-claim gate — `lib.certification_claims`.

Two live drafts asserted "VOHC-accepted" for products the published list does
not carry: Pet Honesty Dental Wipes (the brand is absent entirely) and
Virbac's C.E.T. Enzymatic chews (the accepted Virbac products are VEGGIEDENT).
The medical-claims gate passed both, at 88 and 92, because nothing there
checks certifications.

The gate anchors on the AFFILIATE LINK rather than on product names in prose.
Name-matching was tried first and does not work: Title Case headings read
exactly like brand names, so it either fails "What VOHC Acceptance Really
Means" or misses a claim whose product is named after it.
"""

from __future__ import annotations

from typing import Any

import pytest

from lib.certification_claims import (
    unverified_certification_claims,
    validate_certification_claims,
    verified_asins,
)

VERIFIED_NOTE = "VOHC-accepted, verified against vohc.org's list on 2026-08-28."

CATALOG: list[dict[str, Any]] = [
    {"key": "greenies", "asin": "B075MV73ZH", "display": "Canine Greenies", "notes": VERIFIED_NOTE},
    {
        "key": "petrodex",
        "asin": "B00MFWJLDS",
        "display": "Petrodex Enzymatic Toothpaste",
        "notes": "NOT on the VOHC accepted-products list.",
    },
]


def _link(asin: str) -> str:
    return f'<a href="https://www.amazon.com/dp/{asin}?tag=x-20">buy</a>'


def test_claim_beside_a_verified_product_passes() -> None:
    html = f"<td>VOHC accepted</td><td>{_link('B075MV73ZH')}</td>"
    assert unverified_certification_claims(html, CATALOG) == []


def test_claim_beside_an_unverified_product_is_flagged() -> None:
    """The Pet Honesty case: a real link, a real claim, no verification."""
    html = f"<td>VOHC accepted</td><td>{_link('B00MFWJLDS')}</td>"
    flagged = unverified_certification_claims(html, CATALOG)
    assert len(flagged) == 1
    assert "Petrodex" in flagged[0]


def test_a_product_named_after_the_claim_is_still_caught() -> None:
    """Prose names the product after the claim ("one VOHC-accepted wipe is
    the ..."), which is why the window looks both ways."""
    html = f"<p>One VOHC-accepted wipe we use is {_link('B00MFWJLDS')}</p>"
    assert unverified_certification_claims(html, CATALOG)


@pytest.mark.parametrize(
    "prose",
    [
        "<p>The VOHC reviews evidence and only accepts products meeting its standards.</p>",
        "<p>A VOHC-approved product has proven effectiveness, not just marketing.</p>",
        "<h2>What VOHC Acceptance Really Means (and Doesn't)</h2>",
    ],
)
def test_general_writing_about_a_seal_is_not_a_claim(prose: str) -> None:
    """Explaining what a seal means names no product and links to none.
    Flagging it would block correct writing and get the gate routed around."""
    assert unverified_certification_claims(prose, CATALOG) == []


def test_a_product_link_with_no_claim_near_it_passes() -> None:
    html = f"<p>We feed this daily. {_link('B00MFWJLDS')}</p>"
    assert unverified_certification_claims(html, CATALOG) == []


def test_a_distant_claim_does_not_taint_a_later_product() -> None:
    """Windowing keeps a claim in one table row from blaming the next."""
    html = f"<td>VOHC accepted</td><td>{_link('B075MV73ZH')}</td>" + ("<p>filler</p>" * 60)
    html += f"<td>no claim here</td><td>{_link('B00MFWJLDS')}</td>"
    assert unverified_certification_claims(html, CATALOG) == []


def test_verified_asins_reads_the_operator_marker() -> None:
    assert verified_asins(CATALOG) == {"B075MV73ZH"}


def test_a_missing_notes_field_counts_as_unverified() -> None:
    """Absence of the marker means "nobody checked", never "it is fine"."""
    catalog = [{"asin": "B0AAAAAAAA", "display": "Mystery Chew"}]
    html = f"<td>VOHC approved</td>{_link('B0AAAAAAAA')}"
    assert unverified_certification_claims(html, catalog)


def test_validate_raises_and_names_the_product() -> None:
    html = f"<td>VOHC accepted</td><td>{_link('B00MFWJLDS')}</td>"
    with pytest.raises(ValueError, match="Petrodex"):
        validate_certification_claims(html, CATALOG, title="Dental Guide")


def test_validate_is_silent_when_everything_checks_out() -> None:
    html = f"<td>VOHC accepted</td><td>{_link('B075MV73ZH')}</td>"
    validate_certification_claims(html, CATALOG, title="Dental Guide")


def test_empty_input_is_not_a_violation() -> None:
    assert unverified_certification_claims("", CATALOG) == []
