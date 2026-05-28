"""Resolve UK county names from a postcode (or outcode) or a town name.

NewHomesForSale anchors its search URLs to a county slug. Clients typically
have a postcode or a town, so this module fills the gap:

- ``postcode_to_county`` uses postcodes.io (free, no key). Tries full
  postcodes first, then falls back to the outcode endpoint for partial
  postcodes like "HP4". Prefers ``admin_county``; falls back to
  ``admin_district`` (which is what postcodes.io returns for London and a
  handful of unitary authorities); finally ``region``.
- ``town_to_county`` uses Nominatim (OpenStreetMap, free, no key). Picks
  the first ``place`` result in ``country_code == "gb"`` and returns its
  ``county`` address component (or ``state_district`` / ``state`` if
  county is absent).

Both lookups are cached in-process to respect the upstream services'
rate limits and keep tool latency low. Nominatim's usage policy requires
a descriptive User-Agent.
"""

from __future__ import annotations

import re
from typing import Optional

import httpx

from property_core.postcode_client import PostcodeClient


_POSTCODE_RE = re.compile(r"^[A-Z]{1,2}[0-9][A-Z0-9]?\s*[0-9][A-Z]{2}$")
_OUTCODE_RE = re.compile(r"^[A-Z]{1,2}[0-9][A-Z0-9]?$")

_postcode_to_county_cache: dict[str, Optional[str]] = {}
_town_to_county_cache: dict[str, Optional[str]] = {}
_outcode_latlon_cache: dict[str, Optional[tuple[float, float]]] = {}

_NOMINATIM_BASE = "https://nominatim.openstreetmap.org"
_NOMINATIM_USER_AGENT = (
    "property-shared/location-resolution (https://github.com/don1989/property-shared)"
)


def _normalise_postcode(value: str) -> str:
    return (value or "").replace(" ", "").upper()


def _looks_like_outcode(value: str) -> bool:
    return bool(_OUTCODE_RE.match(value))


def _looks_like_full_postcode(value: str) -> bool:
    return bool(_POSTCODE_RE.match(value))


def postcode_to_county(postcode: str, *, timeout: float = 10.0) -> Optional[str]:
    """Resolve a UK postcode (or outcode) to a county name.

    Prefers ``admin_county``; falls back to ``admin_district`` (London and a
    few unitary authorities have no admin_county) and finally to ``region``.
    Returns ``None`` if the postcode is unrecognised or upstream is
    unavailable. Results are cached in-process.
    """
    if not postcode or not postcode.strip():
        return None
    key = _normalise_postcode(postcode)
    if key in _postcode_to_county_cache:
        return _postcode_to_county_cache[key]

    county: Optional[str] = None

    if _looks_like_full_postcode(key):
        result = PostcodeClient(timeout=timeout).lookup(key)
        if result is not None:
            county = (
                result.admin_county
                or result.admin_district
                or result.region
            )

    if county is None and _looks_like_outcode(key):
        county = _lookup_outcode(key, timeout=timeout)

    if county is None and not _looks_like_full_postcode(key) and not _looks_like_outcode(key):
        # Last resort: try the input as-is against the outcode endpoint so a
        # caller passing "hp4" (lowercase) or "HP 4" still resolves.
        county = _lookup_outcode(key, timeout=timeout)

    _postcode_to_county_cache[key] = county
    return county


def outcode_latlon(value: str, *, timeout: float = 10.0) -> Optional[tuple[float, float]]:
    """Return the centroid (latitude, longitude) of a UK outcode.

    Accepts a full postcode (the outcode portion is used) or an outcode like
    ``"HP4"``. Returns ``None`` when the outcode is unknown or upstream is
    unavailable. Results are cached in-process.
    """
    if not value or not value.strip():
        return None
    normalised = _normalise_postcode(value)
    if _looks_like_full_postcode(normalised):
        outcode = normalised[:-3] if len(normalised) > 3 else normalised
    else:
        outcode = normalised
    if outcode in _outcode_latlon_cache:
        return _outcode_latlon_cache[outcode]
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(f"https://api.postcodes.io/outcodes/{outcode}")
            if resp.status_code == 404:
                _outcode_latlon_cache[outcode] = None
                return None
            resp.raise_for_status()
            data = resp.json().get("result")
    except httpx.HTTPError:
        return None
    if not isinstance(data, dict):
        _outcode_latlon_cache[outcode] = None
        return None
    lat = data.get("latitude")
    lon = data.get("longitude")
    if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
        result = (float(lat), float(lon))
        _outcode_latlon_cache[outcode] = result
        return result
    _outcode_latlon_cache[outcode] = None
    return None


def _lookup_outcode(outcode: str, *, timeout: float) -> Optional[str]:
    """Hit postcodes.io's outcode endpoint and pick the best county field."""
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(f"https://api.postcodes.io/outcodes/{outcode}")
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            data = resp.json().get("result")
    except httpx.HTTPError:
        return None
    if not isinstance(data, dict):
        return None
    counties = data.get("admin_county") or []
    districts = data.get("admin_district") or []
    region = data.get("region")
    if isinstance(counties, list) and counties:
        return counties[0]
    if isinstance(districts, list) and districts:
        return districts[0]
    if isinstance(region, str):
        return region
    return None


def town_to_county(town: str, *, timeout: float = 10.0) -> Optional[str]:
    """Resolve a UK town name to a county name via Nominatim.

    Picks the first hit with ``country_code == "gb"``. Returns ``None`` if no
    GB match is found or upstream is unavailable. Results are cached
    in-process.
    """
    if not town or not town.strip():
        return None
    key = town.strip().lower()
    if key in _town_to_county_cache:
        return _town_to_county_cache[key]

    county: Optional[str] = None
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(
                f"{_NOMINATIM_BASE}/search",
                params={
                    "q": town,
                    "format": "jsonv2",
                    "addressdetails": "1",
                    "countrycodes": "gb",
                    "limit": "5",
                },
                headers={"User-Agent": _NOMINATIM_USER_AGENT},
            )
            resp.raise_for_status()
            results = resp.json()
    except httpx.HTTPError:
        _town_to_county_cache[key] = None
        return None

    if not isinstance(results, list):
        _town_to_county_cache[key] = None
        return None

    for item in results:
        if not isinstance(item, dict):
            continue
        addr = item.get("address") or {}
        if not isinstance(addr, dict):
            continue
        if addr.get("country_code") != "gb":
            continue
        candidate = (
            addr.get("county")
            or addr.get("state_district")
            or addr.get("state")
        )
        if candidate:
            county = candidate
            break

    _town_to_county_cache[key] = county
    return county
