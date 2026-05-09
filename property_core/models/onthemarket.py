"""Domain models for OnTheMarket data.

Phase 1 discovery (see docs/zoopla-onthemarket-discovery.md) showed:
- ``__NEXT_DATA__`` is present on both search and listing pages but
  ``props.pageProps`` is empty — useless for parsing.
- Search cards use Schema.org microdata: each card is an
  ``<article data-component='search-result-property-card' itemscope
  itemtype='https://schema.org/SingleFamilyResidence'>`` with stable
  ``itemprop`` attributes (``numberOfBedrooms``, ``address``,
  ``addressLocality``, ``postalCode``, ``description``,
  ``contentUrl`` for photos, ``name``/``telephone`` on the agent panel).
- Listing detail pages emit a ``dataLayer.push({...})`` call carrying the
  canonical structured fields (``price``, ``postcode``, ``branch-id``,
  ``property-id``, ``channel``, ``status``, ``addressline_2``,
  ``property-type``, ``parent-locations``, ``trans-type-id``).
- Listing detail also has a ``<h2>Key information</h2>`` section with
  Tenure / Ground rent / Service charge / Council tax / EPC / Broadband /
  Mobile signal rendered as ``<div><span>Label:</span><span>Value</span></div>``.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


_NUMERIC_RE = re.compile(r"\d[\d,]*")


def _parse_int(text: str | None) -> Optional[int]:
    if text is None or text == "":
        return None
    s = str(text).strip()
    m = _NUMERIC_RE.search(s)
    if not m:
        return None
    try:
        return int(m.group(0).replace(",", ""))
    except ValueError:
        return None


def _parse_float(text: str | None) -> Optional[float]:
    if text is None or text == "":
        return None
    try:
        return float(str(text).replace(",", ""))
    except ValueError:
        return None


def _coerce_str(val: Any) -> Optional[str]:
    """Coerce a dataLayer value to ``str | None``.

    OnTheMarket's dataLayer pushes strings 99% of the time, but a redeploy
    could ship an int (e.g. ``branch-id``-shaped values mistakenly under a
    string key). Pydantic 2 rejects ``int`` for an ``Optional[str]`` field,
    so coerce here rather than crash mid-scrape.
    """
    if val is None or val == "":
        return None
    return str(val)


class OnTheMarketListing(BaseModel):
    """A single OnTheMarket search-result card.

    Field provenance:
    - ``id``: parsed from ``<a href='/details/{id}/'>``.
    - ``url``: absolute URL built from that href.
    - ``price`` / ``display_price``: from
      ``<div data-component='price-title'>`` text (e.g. ``"£1,200,000"``).
    - ``bedrooms`` / ``bathrooms``: from the two ``<span>``s inside
      ``<div data-component='BedBathCounts'>`` (first = bedrooms, second =
      bathrooms). Bedrooms is also exposed via ``itemprop='numberOfBedrooms'``.
    - ``address``: from ``<address itemprop='address'>``.
    - ``summary``: from ``<meta itemprop='description'>``'s ``content``
      (e.g. ``"2 bedroom flat for sale - Buckingham Palace Road, ..."``).
    - ``postcode``: from ``<meta itemprop='postalCode'>``'s ``content``
      (may be empty for some cards).
    - ``locality``: from ``<meta itemprop='addressLocality'>``'s ``content``.
    - ``status``: from ``<div data-component='pill'>`` text
      (e.g. ``"Spotlight Property"``, ``"Featured"``, ``"New"``,
      ``"Sold STC"``, ``"Premium Listing"``).
    - ``added_text``: free-text from the agent panel
      (e.g. ``"Added > 14 days"``, ``"Added today"``).
    - ``agent_name``: from ``<* itemprop='name'>`` inside the agent panel.
    - ``agent_telephone``: from ``<a itemprop='telephone'>``.
    - ``images``: list of ``<img itemprop='contentUrl'>`` ``src`` values
      (hosted at ``media.onthemarket.com``).
    """

    id: str
    url: str
    price: Optional[int] = None
    display_price: Optional[str] = None
    bedrooms: Optional[int] = None
    bathrooms: Optional[int] = None
    address: Optional[str] = None
    summary: Optional[str] = None
    postcode: Optional[str] = None
    locality: Optional[str] = None
    status: Optional[str] = None
    added_text: Optional[str] = None
    agent_name: Optional[str] = None
    agent_telephone: Optional[str] = None
    images: List[str] = Field(default_factory=list)
    raw: Optional[Dict[str, Any]] = Field(default=None, exclude=True)

    @classmethod
    def build(
        cls,
        *,
        listing_id: str,
        url: str,
        display_price: str | None,
        bedrooms_text: str | None,
        bathrooms_text: str | None,
        address: str | None,
        summary: str | None,
        postcode: str | None,
        locality: str | None,
        status: str | None,
        added_text: str | None,
        agent_name: str | None,
        agent_telephone: str | None,
        images: List[str],
        raw: Dict[str, Any] | None = None,
    ) -> OnTheMarketListing:
        return cls(
            id=listing_id,
            url=url,
            price=_parse_int(display_price),
            display_price=display_price,
            bedrooms=_parse_int(bedrooms_text),
            bathrooms=_parse_int(bathrooms_text),
            address=address,
            summary=summary,
            postcode=postcode,
            locality=locality,
            status=status,
            added_text=added_text,
            agent_name=agent_name,
            agent_telephone=agent_telephone,
            images=images,
            raw=raw,
        )


class OnTheMarketListingDetail(BaseModel):
    """Full property detail from an individual OnTheMarket listing page.

    Field provenance:
    - ``id``, ``price``, ``postcode``, ``branch_id``, ``channel`` (sale/rent),
      ``status``, ``addressline_2``, ``property_type``, ``trans_type_id``,
      ``parent_locations``: all from the page's ``dataLayer.push({...})``
      payload (verified verbatim).
    - ``url``: canonical URL built from the listing id.
    - ``title``: from ``<h1>`` (e.g. ``"2 bedroom flat for sale"``).
    - ``description``: from ``<* itemprop='description'>`` text.
    - ``display_price``: from ``<div data-component='price-title'>``.
    - ``images``: from ``<div data-component='hero-images'> img[src]``.
    - ``key_information``: dict of label -> value pairs scraped from the
      ``<h2>Key information</h2>`` section
      (Tenure / Ground rent / Service charge / Council tax / Broadband /
      Mobile signal etc.).
    - ``tenure``, ``years_remaining_on_lease``, ``annual_ground_rent``,
      ``annual_service_charge``, ``council_tax_band``, ``epc_rating``:
      parsed from the ``key_information`` strings.
    - ``meta_description``: from ``<meta name='description'>``.
    """

    id: str
    url: str
    price: Optional[int] = None
    display_price: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    meta_description: Optional[str] = None
    postcode: Optional[str] = None
    addressline_2: Optional[str] = None
    parent_locations: List[str] = Field(default_factory=list)
    property_type: Optional[str] = None
    channel: Optional[str] = None  # "sale" / "rent"
    status: Optional[str] = None
    trans_type_id: Optional[str] = None
    branch_id: Optional[int] = None
    images: List[str] = Field(default_factory=list)
    key_information: Dict[str, str] = Field(default_factory=dict)
    tenure: Optional[str] = None
    years_remaining_on_lease: Optional[int] = None
    annual_ground_rent: Optional[int] = None
    annual_service_charge: Optional[int] = None
    council_tax_band: Optional[str] = None
    epc_rating: Optional[str] = None
    raw: Optional[Dict[str, Any]] = Field(default=None, exclude=True)

    @classmethod
    def build(
        cls,
        *,
        listing_id: str,
        url: str,
        data_layer: Dict[str, Any],
        title: str | None,
        description: str | None,
        meta_description: str | None,
        display_price: str | None,
        images: List[str],
        key_information: Dict[str, str],
    ) -> OnTheMarketListingDetail:
        # Pull canonical fields from the dataLayer payload (verbatim keys).
        dl_price = _parse_int(data_layer.get("price"))
        # Fall back to display_price if dataLayer is missing.
        if dl_price is None:
            dl_price = _parse_int(display_price)

        ki = key_information

        return cls(
            id=listing_id,
            url=url,
            price=dl_price,
            display_price=display_price,
            title=title,
            description=description,
            meta_description=meta_description,
            postcode=_coerce_str(data_layer.get("postcode")),
            addressline_2=_coerce_str(data_layer.get("addressline_2")),
            parent_locations=[
                str(loc) for loc in (data_layer.get("parent-locations") or [])
            ],
            property_type=_coerce_str(data_layer.get("property-type")),
            channel=_coerce_str(data_layer.get("channel")),
            status=_coerce_str(data_layer.get("status")),
            trans_type_id=_coerce_str(data_layer.get("trans-type-id")),
            branch_id=_parse_int(data_layer.get("branch-id")),
            images=images,
            key_information=key_information,
            tenure=_extract_tenure(ki),
            years_remaining_on_lease=_extract_lease_years(ki),
            annual_ground_rent=_extract_ground_rent(ki),
            annual_service_charge=_extract_service_charge(ki),
            council_tax_band=_extract_council_tax_band(ki),
            epc_rating=_extract_epc_rating(ki),
            raw={"data_layer": data_layer, "key_information": key_information},
        )


def _extract_tenure(ki: Dict[str, str]) -> Optional[str]:
    val = ki.get("Tenure")
    if not val:
        return None
    head = val.split("|")[0].strip()
    return head or None


def _extract_lease_years(ki: Dict[str, str]) -> Optional[int]:
    val = ki.get("Tenure")
    if not val:
        return None
    m = re.search(r"(\d+)\s*yrs?\s*left", val, re.I)
    return int(m.group(1)) if m else None


def _extract_ground_rent(ki: Dict[str, str]) -> Optional[int]:
    val = ki.get("Ground rent")
    if not val:
        return None
    m = re.search(r"£\s*([\d,]+)", val)
    if not m:
        return None
    try:
        return int(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _extract_service_charge(ki: Dict[str, str]) -> Optional[int]:
    val = ki.get("Service charge")
    if not val:
        return None
    m = re.search(r"£\s*([\d,]+)", val)
    if not m:
        return None
    try:
        return int(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _extract_council_tax_band(ki: Dict[str, str]) -> Optional[str]:
    val = ki.get("Council tax")
    if not val:
        return None
    m = re.search(r"Band\s*([A-H])\b", val, re.I)
    return m.group(1).upper() if m else None


def _extract_epc_rating(ki: Dict[str, str]) -> Optional[str]:
    val = ki.get("EPC rating") or ki.get("EPC")
    if not val:
        return None
    m = re.search(r"\b([A-G])\b", val)
    return m.group(1).upper() if m else None
