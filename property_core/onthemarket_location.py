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

_TRAVEL_TYPES = {"walking", "cycling", "driving", "public-transport"}

# OnTheMarket's commute filter is a fixed dropdown; out-of-set values silently
# return zero results instead of being rejected or snapped, so validate here.
_TRAVEL_DURATIONS = frozenset({15, 30, 45, 60})


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
        travel_duration: int | None = None,
        travel_type: str | None = None,
        page: int | None = None,
        **extra_params: object,
    ) -> str:
        """Build an OnTheMarket search URL from a postcode/area/station.

        Args:
            postcode: postcode like ``"SW1A 1AA"``, an area name like
                ``"London"``, or a station slug like ``"Hitchin Station"``
                (OnTheMarket accepts station names directly in the slug —
                ``/property/hitchin-station/``).
            property_type: ``"sale"`` or ``"rent"``.
            building_type: PPD code ("F"/"D"/"S"/"T") or ``None`` for all.
            min_price / max_price: inclusive £ bounds.
            min_bedrooms / max_bedrooms: bedroom bounds.
            radius: search radius in miles (mutually exclusive with
                ``travel_duration``; OnTheMarket ignores ``radius`` when
                ``travel-duration`` is set).
            travel_duration: commute time in minutes from the slug
                anchor. Best paired with a station slug. OnTheMarket
                only honours ``15``, ``30``, ``45``, or ``60`` —
                other values silently return zero results, so they
                raise ``ValueError`` here.
            travel_type: ``"walking"``, ``"cycling"``, ``"driving"``, or
                ``"public-transport"``. Defaults to ``"walking"`` when
                ``travel_duration`` is set without a ``travel_type``.
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
        if travel_type is not None and travel_type not in _TRAVEL_TYPES:
            raise ValueError(
                f"travel_type must be one of {sorted(_TRAVEL_TYPES)}, got {travel_type!r}"
            )
        if travel_duration is not None:
            if travel_duration not in _TRAVEL_DURATIONS:
                raise ValueError(
                    f"travel_duration must be one of {sorted(_TRAVEL_DURATIONS)} "
                    f"(OnTheMarket's commute filter is a fixed dropdown), "
                    f"got {travel_duration!r}"
                )
            params["travel-duration"] = travel_duration
            params["travel-type"] = travel_type or "walking"
        elif travel_type is not None:
            params["travel-type"] = travel_type
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
