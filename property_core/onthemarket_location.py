"""OnTheMarket URL builder.

OnTheMarket search URLs are slug-based, with a different path for sale vs
rent and an explicit search-type qualifier:
- ``/for-sale/property/{slug}/`` (sale)
- ``/to-rent/property/{slug}/`` (rent)

Filter parameters go in the query string. Pagination uses ``?page=N``.

Like Zoopla, this module deliberately does not call the network. OnTheMarket
exposes a typeahead API but it's unnecessary — the slugged URL works directly
for both postcodes and area names.
"""

from __future__ import annotations

import re
from urllib.parse import unquote, urlencode

_BASE = "https://www.onthemarket.com"

_PROPERTY_PATHS = {
    "sale": "/for-sale/property",
    "rent": "/to-rent/property",
}

# PPD property type codes -> OnTheMarket "prop-types[]" filter values.
_BUILDING_TYPES = {
    "F": "flats-apartments",
    "D": "detached",
    "S": "semi-detached",
    "T": "terraced",
}

_SLUG_INVALID = re.compile(r"[^a-z0-9-]")


def _to_slug(value: str) -> str:
    """Lower-case, replace whitespace with hyphens, strip other characters.

    URL-encoded inputs (``"SW1A%201AA"``) are decoded first so the encoded
    space doesn't survive into the slug as ``"sw1a201aa"``.
    """
    if not value or not value.strip():
        raise ValueError("postcode/area must be a non-empty string")
    s = unquote(value).strip().lower()
    s = re.sub(r"\s+", "-", s)
    s = _SLUG_INVALID.sub("", s)
    s = re.sub(r"-+", "-", s).strip("-")
    if not s:
        raise ValueError(f"could not derive slug from {value!r}")
    return s


class OnTheMarketLocationAPI:
    """Pure-string OnTheMarket search-URL builder."""

    def build_search_url(
        self,
        postcode: str,
        *,
        property_type: str = "sale",
        building_type: str | None = None,
        min_price: int | None = None,
        max_price: int | None = None,
        min_bedrooms: int | None = None,
        max_bedrooms: int | None = None,
        radius: float | None = None,
        page: int | None = None,
        **extra_params: object,
    ) -> str:
        """Build an OnTheMarket search URL from a postcode/area.

        Args:
            postcode: postcode like "SW1A 1AA" or area name like "London".
            property_type: ``"sale"`` or ``"rent"``.
            building_type: PPD code ("F"/"D"/"S"/"T") or ``None`` for all.
            min_price / max_price: inclusive £ bounds.
            min_bedrooms / max_bedrooms: bedroom bounds.
            radius: search radius in miles.
            page: 1-indexed page number; emitted as ``?page=N``.

        Returns:
            Absolute OnTheMarket URL.
        """
        path = _PROPERTY_PATHS.get(property_type)
        if path is None:
            raise ValueError(f"property_type must be 'sale' or 'rent', got {property_type!r}")

        slug = _to_slug(postcode)
        url = f"{_BASE}{path}/{slug}/"

        params: dict[str, object] = {}
        if min_price is not None:
            params["min-price"] = min_price
        if max_price is not None:
            params["max-price"] = max_price
        if min_bedrooms is not None:
            params["min-bedrooms"] = min_bedrooms
        if max_bedrooms is not None:
            params["max-bedrooms"] = max_bedrooms
        if radius is not None:
            params["radius"] = radius
        if building_type:
            sub = _BUILDING_TYPES.get(building_type.upper())
            if sub:
                params["prop-types"] = sub
        if page is not None and page > 1:
            params["page"] = page

        params.update(extra_params)
        if params:
            url = f"{url}?{urlencode(params)}"
        return url
