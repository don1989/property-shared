"""Domain models for Zoopla data.

Phase 1 discovery (see docs/zoopla-onthemarket-discovery.md) showed:
- Plain ``requests`` is fully blocked by Cloudflare on the whole zoopla.co.uk
  domain. Search pages are reachable via headless Playwright; listing detail
  pages serve a Cloudflare Turnstile interstitial that does not auto-resolve
  in headless Chromium.
- Zoopla is on the Next.js App Router with React Server Components, so
  ``__NEXT_DATA__`` is not present. The single ``<script
  type="application/ld+json">`` block only carries site-level metadata.
- Listing data lives in HTML, anchored by ``data-testid='listing-card-content'``
  on the ``<a>`` element. CSS-Modules class names (e.g.
  ``price_priceText__TArfK``) carry hash suffixes that may rotate; selectors
  should match by class-name *prefix*, not full name.

This file holds the search-card model only. There is no
``ZooplaListingDetail`` because detail pages are not currently scrapable
under the project's no-stealth-deps constraint.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


_NUMERIC_RE = re.compile(r"\d[\d,]*")


def _parse_price(text: str | None) -> Optional[int]:
    if not text:
        return None
    m = _NUMERIC_RE.search(text)
    if not m:
        return None
    digits = m.group(0).replace(",", "")
    try:
        return int(digits)
    except ValueError:
        return None


def _parse_amenity_int(amenities: List[str], keyword: str) -> Optional[int]:
    """Pick an integer out of an amenity string like '2 beds' / '1 bath'."""
    pattern = re.compile(rf"^(\d+)\s*{keyword}s?\b", re.I)
    for item in amenities:
        m = pattern.match(item.strip())
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                return None
    return None


def _parse_amenity_sqft(amenities: List[str]) -> Optional[int]:
    pattern = re.compile(r"^([\d,]+)\s*sq\s*ft\b", re.I)
    for item in amenities:
        m = pattern.match(item.strip())
        if m:
            try:
                return int(m.group(1).replace(",", ""))
            except ValueError:
                return None
    return None


class ZooplaListing(BaseModel):
    """A single Zoopla search-result card.

    Field provenance:
    - ``id``: numeric id parsed from the ``<a data-testid='listing-card-content'
      href='/for-sale/details/{id}/'>`` URL.
    - ``url``: absolute URL built from that href.
    - ``display_price`` / ``price``: text and parsed integer from
      ``<p class='price_priceText__...'>``.
    - ``price_qualifier``: text from ``<p class='price_priceTitle__...'>``
      ("Guide price", "OIRO", etc.).
    - ``amenities``: verbatim list from ``<span
      class='amenities_amenityItemSlim__...'>`` items.
    - ``bedrooms`` / ``bathrooms`` / ``receptions`` / ``floor_area_sqft``:
      regex-parsed from amenity strings like ``"2 beds"``, ``"2 baths"``,
      ``"1 reception"``, ``"1218 sq ft"``.
    - ``address``: text from ``<address class='summary_address__...'>``.
    - ``summary``: text from ``<p class='summary_summary__...'>``.
    - ``premium_attributes``: ``<span class='premium-attributes_attributeText__...'>``
      items (e.g. "Parking", "Swimming Pool").
    - ``badges``: ``<ul class='badges_badgesListSlim__...'>`` items
      (e.g. "Leasehold", "Reduced", "Featured").
    - ``agent_name`` / ``agent_logo``: ``alt`` and ``src`` of
      ``<img class='agent-logo_agentLogoImageSlim__...'>``.
    - ``images``: ``<img>`` ``src`` URLs hosted at ``lid.zoocdn.com``
      inside the wrapping ``<div class='layout_layoutGridSlim__...'>``.
    """

    id: str
    url: str
    price: Optional[int] = None
    display_price: Optional[str] = None
    price_qualifier: Optional[str] = None
    address: Optional[str] = None
    summary: Optional[str] = None
    bedrooms: Optional[int] = None
    bathrooms: Optional[int] = None
    receptions: Optional[int] = None
    floor_area_sqft: Optional[int] = None
    amenities: List[str] = Field(default_factory=list)
    premium_attributes: List[str] = Field(default_factory=list)
    badges: List[str] = Field(default_factory=list)
    agent_name: Optional[str] = None
    agent_logo: Optional[str] = None
    images: List[str] = Field(default_factory=list)
    raw: Optional[Dict[str, Any]] = Field(default=None, exclude=True)

    @classmethod
    def build(
        cls,
        *,
        listing_id: str,
        url: str,
        display_price: str | None,
        price_qualifier: str | None,
        amenities: List[str],
        address: str | None,
        summary: str | None,
        premium_attributes: List[str],
        badges: List[str],
        agent_name: str | None,
        agent_logo: str | None,
        images: List[str],
        raw: Dict[str, Any] | None = None,
    ) -> ZooplaListing:
        return cls(
            id=listing_id,
            url=url,
            price=_parse_price(display_price),
            display_price=display_price,
            price_qualifier=price_qualifier,
            address=address,
            summary=summary,
            bedrooms=_parse_amenity_int(amenities, "bed"),
            bathrooms=_parse_amenity_int(amenities, "bath"),
            receptions=_parse_amenity_int(amenities, "reception"),
            floor_area_sqft=_parse_amenity_sqft(amenities),
            amenities=amenities,
            premium_attributes=premium_attributes,
            badges=badges,
            agent_name=agent_name,
            agent_logo=agent_logo,
            images=images,
            raw=raw,
        )
