"""NewHomesForSale URL builder.

NHFS search URLs are slug-based:
- ``/new-homes/{county-slug}/`` — all developments in a county
- ``/new-homes/{county-slug}/{town-slug}/`` — developments in a town

Unlike Rightmove/OnTheMarket/Zoopla there is no postcode-keyed
search; callers either pick a county/town directly or use postcodes.io
to derive the county slug from a postcode. The town slug is not always
derivable from postcodes.io output (the ``parliamentary_constituency``
or ``admin_ward`` fields don't reliably match NHFS's town slugs), so
this builder takes them as inputs and leaves discovery to callers.

Like the other portal URL builders, this module does not make network
calls — it is a pure string transform.
"""

from __future__ import annotations

import re
from urllib.parse import unquote

_BASE = "https://www.newhomesforsale.co.uk"

_SLUG_INVALID = re.compile(r"[^a-z0-9-]")


def _to_slug(value: str) -> str:
    """Lower-case, replace whitespace with hyphens, strip other characters."""
    if not value or not value.strip():
        raise ValueError("location component must be a non-empty string")
    s = unquote(value).strip().lower()
    s = re.sub(r"\s+", "-", s)
    s = _SLUG_INVALID.sub("", s)
    s = re.sub(r"-+", "-", s).strip("-")
    if not s:
        raise ValueError(f"could not derive slug from {value!r}")
    return s


class NewHomesForSaleLocationAPI:
    """Pure-string NewHomesForSale URL builder."""

    def build_search_url(
        self,
        *,
        county: str,
        town: str | None = None,
    ) -> str:
        """Build a NewHomesForSale developments-search URL.

        Args:
            county: county name or slug (e.g. ``"Hertfordshire"`` or
                ``"hertfordshire"``). Required — there is no national
                index page; results are always scoped to a county.
            town: optional town within the county (e.g. ``"Hitchin"``).
                When omitted, the URL targets the county-level page
                listing developments across the whole county.

        Returns:
            Absolute NHFS URL.
        """
        county_slug = _to_slug(county)
        if town:
            town_slug = _to_slug(town)
            return f"{_BASE}/new-homes/{county_slug}/{town_slug}/"
        return f"{_BASE}/new-homes/{county_slug}/"
