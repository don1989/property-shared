"""Domain helpers for NewHomesForSale developments.

NHFS search results are anchored to a county/town slug, not a postcode,
so the returned developments can sit well outside the named town (a
"Hitchin" search returns developments up to 10mi away). This module
post-filters a list of developments by crow-flies distance from a
caller-supplied anchor postcode.
"""

from __future__ import annotations

import asyncio
import logging
import math
from typing import Iterable

from property_core.models.newhomesforsale import NewHomesForSaleDevelopment
from property_core.postcode_client import PostcodeClient

logger = logging.getLogger(__name__)

_EARTH_RADIUS_MILES = 3958.7613


def _haversine_miles(
    lat1: float, lon1: float, lat2: float, lon2: float
) -> float:
    """Great-circle distance between two lat/lon points, in miles."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return _EARTH_RADIUS_MILES * c


def _normalise_postcode(postcode: str) -> str:
    return postcode.replace(" ", "").upper()


async def filter_developments_by_distance(
    developments: Iterable[NewHomesForSaleDevelopment],
    *,
    anchor_postcode: str,
    max_miles: float,
    client: PostcodeClient | None = None,
) -> list[NewHomesForSaleDevelopment]:
    """Filter NHFS developments to those within ``max_miles`` of a postcode.

    Geocodes the anchor postcode and bulk-geocodes the developments'
    postcodes via :class:`PostcodeClient`. Computes crow-flies distance,
    mutates each kept development's ``distance_to_anchor_miles`` field,
    and returns the kept developments sorted ascending by distance.

    Developments without a postcode, or whose postcode the geocoder
    cannot resolve, are dropped with a ``logger.warning``.

    Args:
        developments: Iterable of NHFS developments to filter.
        anchor_postcode: UK postcode to measure distance from.
        max_miles: Inclusive distance cap (miles).
        client: Optional injected ``PostcodeClient`` (tests).

    Raises:
        ValueError: if ``anchor_postcode`` cannot be geocoded, or if
            ``max_miles`` is not positive.
    """
    if max_miles <= 0:
        raise ValueError("max_miles must be positive")

    developments = list(developments)
    if not developments:
        return []

    client = client or PostcodeClient()

    anchor = await asyncio.to_thread(client.lookup, anchor_postcode)
    anchor_lat: float | None = anchor.latitude if anchor else None
    anchor_lon: float | None = anchor.longitude if anchor else None
    if anchor_lat is None or anchor_lon is None:
        # Fall back to the outcode centroid for partial postcodes (e.g. "HP4")
        # or full postcodes the per-postcode endpoint doesn't recognise.
        from property_core.location_resolution import outcode_latlon

        outcode_result = await asyncio.to_thread(outcode_latlon, anchor_postcode)
        if outcode_result is not None:
            anchor_lat, anchor_lon = outcode_result

    if anchor_lat is None or anchor_lon is None:
        raise ValueError(
            f"Anchor postcode {anchor_postcode!r} could not be geocoded"
        )

    dev_postcodes = [d.postcode for d in developments if d.postcode]
    geocoded = (
        await asyncio.to_thread(client.bulk_lookup, dev_postcodes)
        if dev_postcodes
        else {}
    )

    kept: list[tuple[float, NewHomesForSaleDevelopment]] = []
    for dev in developments:
        if not dev.postcode:
            logger.warning(
                "Dropping NHFS development %r (%s): missing postcode",
                dev.name,
                dev.id,
            )
            continue
        result = geocoded.get(_normalise_postcode(dev.postcode))
        if result is None or result.latitude is None or result.longitude is None:
            logger.warning(
                "Dropping NHFS development %r (%s): postcode %s not geocoded",
                dev.name,
                dev.id,
                dev.postcode,
            )
            continue
        distance = _haversine_miles(
            anchor_lat, anchor_lon, result.latitude, result.longitude
        )
        if distance <= max_miles:
            dev.distance_to_anchor_miles = round(distance, 2)
            kept.append((distance, dev))

    kept.sort(key=lambda pair: pair[0])
    return [dev for _, dev in kept]
