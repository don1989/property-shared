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
    address: Optional[str] = None
    addressline_2: Optional[str] = None
    parent_locations: List[str] = Field(default_factory=list)
    property_type: Optional[str] = None
    channel: Optional[str] = None  # "sale" / "rent"
    status: Optional[str] = None
    trans_type_id: Optional[str] = None
    branch_id: Optional[int] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    bedrooms: Optional[int] = None
    bathrooms: Optional[int] = None
    floor_area_sqm: Optional[int] = None
    floor_area_sqft: Optional[int] = None
    agent_name: Optional[str] = None
    agent_branch: Optional[str] = None
    agent_telephone: Optional[str] = None
    images: List[str] = Field(default_factory=list)
    floorplans: List[str] = Field(default_factory=list)
    nearest_stations: List[Dict[str, Any]] = Field(default_factory=list)
    key_features: List[str] = Field(default_factory=list)
    listing_status: Optional[str] = None
    first_visible_date: Optional[str] = None
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
        next_data_property: Optional[Dict[str, Any]] = None,
    ) -> OnTheMarketListingDetail:
        # Pull canonical fields from the dataLayer payload (verbatim keys).
        dl_price = _parse_int(data_layer.get("price"))
        # Fall back to display_price if dataLayer is missing.
        if dl_price is None:
            dl_price = _parse_int(display_price)

        ki = key_information
        nd = next_data_property or {}

        # __NEXT_DATA__ values take precedence over fallback HTML parsing
        # for fields it carries reliably (gallery, floorplans, stations,
        # geocode, floor area, agent contact, EPC rating).
        nd_images = _extract_nd_images(nd) if nd else []
        merged_images = nd_images if nd_images else list(images)

        nd_floorplans = _extract_nd_floorplans(nd) if nd else []

        nd_stations = _extract_nd_stations(nd) if nd else []
        nd_features = [
            str(f) for f in (nd.get("features") or []) if isinstance(f, str)
        ]
        nd_location = nd.get("location") or {}
        nd_lat = nd_location.get("lat") if isinstance(nd_location, dict) else None
        nd_lon = nd_location.get("lon") if isinstance(nd_location, dict) else None

        nd_agent = nd.get("agent") or {}
        agent_name = (
            nd_agent.get("companyName")
            or nd_agent.get("name")
        ) if isinstance(nd_agent, dict) else None
        agent_branch = nd_agent.get("name") if isinstance(nd_agent, dict) else None
        agent_telephone = (
            nd_agent.get("telephoneEnquiries")
            or nd_agent.get("telephone")
        ) if isinstance(nd_agent, dict) else None

        nd_epc = nd.get("epc") or {}
        nd_epc_rating = nd_epc.get("rating") if isinstance(nd_epc, dict) else None

        display_address = (
            nd.get("displayAddress") if isinstance(nd, dict) else None
        )

        listing_status = (
            nd.get("premiumText")
            or nd.get("propertySticker")
        ) if isinstance(nd, dict) else None

        first_visible_date = (
            nd.get("daysSinceAddedReduced") if isinstance(nd, dict) else None
        )

        return cls(
            id=listing_id,
            url=url,
            price=dl_price if dl_price is not None else _parse_int(nd.get("priceRaw")),
            display_price=display_price or _coerce_str(nd.get("price")),
            title=title,
            description=description or _coerce_str(nd.get("description")),
            meta_description=meta_description,
            postcode=_coerce_str(data_layer.get("postcode")),
            address=display_address,
            addressline_2=_coerce_str(data_layer.get("addressline_2")),
            parent_locations=[
                str(loc) for loc in (data_layer.get("parent-locations") or [])
            ],
            property_type=(
                _coerce_str(nd.get("humanisedPropertyType"))
                or _coerce_str(data_layer.get("property-type"))
            ),
            channel=_coerce_str(data_layer.get("channel")),
            status=_coerce_str(data_layer.get("status")),
            trans_type_id=_coerce_str(data_layer.get("trans-type-id")),
            branch_id=_parse_int(data_layer.get("branch-id")),
            latitude=_parse_float(nd_lat) if nd_lat is not None else None,
            longitude=_parse_float(nd_lon) if nd_lon is not None else None,
            bedrooms=_parse_int(nd.get("bedrooms")) if nd else None,
            bathrooms=_parse_int(nd.get("bathrooms")) if nd else None,
            floor_area_sqm=_parse_int(nd.get("minimumAreaSqM")) if nd else None,
            floor_area_sqft=_parse_int(nd.get("minimumAreaSqFt")) if nd else None,
            agent_name=agent_name,
            agent_branch=agent_branch,
            agent_telephone=agent_telephone,
            images=merged_images,
            floorplans=nd_floorplans,
            nearest_stations=nd_stations,
            key_features=nd_features,
            listing_status=listing_status,
            first_visible_date=first_visible_date,
            key_information=key_information,
            tenure=_extract_tenure(ki),
            years_remaining_on_lease=_extract_lease_years(ki),
            annual_ground_rent=_extract_ground_rent(ki),
            annual_service_charge=_extract_service_charge(ki),
            council_tax_band=_extract_council_tax_band(ki),
            epc_rating=nd_epc_rating or _extract_epc_rating(ki),
            raw={"data_layer": data_layer, "key_information": key_information},
        )


def _extract_nd_images(prop: Dict[str, Any]) -> List[str]:
    """Pull large gallery URLs from the property NEXT_DATA payload."""
    out: List[str] = []
    seen: set[str] = set()
    for img in (prop.get("images") or []):
        if not isinstance(img, dict):
            continue
        url = img.get("largeUrl") or img.get("url")
        if url and url not in seen:
            seen.add(url)
            out.append(url)
    return out


def _extract_nd_floorplans(prop: Dict[str, Any]) -> List[str]:
    """Pull floorplan URLs from the property NEXT_DATA payload."""
    out: List[str] = []
    seen: set[str] = set()
    for fp in (prop.get("floorplans") or []):
        if not isinstance(fp, dict):
            continue
        url = fp.get("largeUrl") or fp.get("original") or fp.get("url")
        if url and url not in seen:
            seen.add(url)
            out.append(url)
    return out


def _extract_nd_stations(prop: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Pull nearest-station entries from the property NEXT_DATA payload.

    Returns dicts shaped ``{"name", "distance_miles"}`` to match the
    rightmove_listing tool output.
    """
    out: List[Dict[str, Any]] = []
    for st in (prop.get("station") or []):
        if not isinstance(st, dict):
            continue
        name = st.get("fullName") or st.get("name")
        if not name:
            continue
        distance_miles = _parse_station_distance(st.get("displayDistance"))
        out.append({"name": name, "distance_miles": distance_miles})
    return out


def _parse_station_distance(text: Any) -> Optional[float]:
    """Convert OTM's ``"0.2mi."`` / ``"1.4mi"`` strings to a float in miles."""
    if not isinstance(text, str):
        return None
    m = re.search(r"([\d.]+)\s*mi", text)
    if not m:
        return None
    try:
        return round(float(m.group(1)), 2)
    except ValueError:
        return None


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
