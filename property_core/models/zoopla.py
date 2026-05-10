"""Domain models for Zoopla data.

Phase 1 + 2 discovery (see docs/zoopla-onthemarket-discovery.md):
- Plain ``requests`` is fully blocked by Cloudflare on the whole
  ``zoopla.co.uk`` domain. ``curl_cffi`` (libcurl-impersonate) replays a
  real Chrome TLS fingerprint and gets clean ``200`` responses on both
  search and listing-detail pages, so no browser automation is needed.
- Search cards: HTML anchored on
  ``<a data-testid='listing-card-content'>`` with CSS-Modules class
  prefixes (``price_priceText__``, ``amenities_amenityListSlim__``, etc.)
  matched by selectors so hash rotation doesn't break extraction.
- Listing detail: combines the page's ld+json ``RealEstateListing`` block
  (price/description/datePosted/Floor size), the ld+json
  ``BreadcrumbList``, an embedded ``ListingAnalyticsTaxonomy`` JSON
  object pulled from the React Server Components stream, and the
  ``<ul class='NtsInfo_ntsInfoList...'>`` "Need to see info" block
  (Tenure, Council tax band).
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


_FLOOR_AREA_RE = re.compile(r"([\d,]+)\s*sq\.?\s*ft", re.I)
_COUNCIL_TAX_BAND_RE = re.compile(r"\bband\s*([A-H])\b", re.I)


def _coerce_int(val: Any) -> Optional[int]:
    if val is None or val == "":
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        s = str(val)
        m = _NUMERIC_RE.search(s)
        if not m:
            return None
        try:
            return int(m.group(0).replace(",", ""))
        except ValueError:
            return None


def _coerce_str(val: Any) -> Optional[str]:
    if val is None or val == "":
        return None
    return str(val)


def _ld_additional_property(real_estate: Dict[str, Any], name: str) -> Optional[str]:
    """Pluck a ``{name, value}`` entry from ld+json ``additionalProperty``."""
    items = real_estate.get("additionalProperty") or []
    if not isinstance(items, list):
        return None
    for entry in items:
        if isinstance(entry, dict) and entry.get("name") == name:
            value = entry.get("value")
            return _coerce_str(value)
    return None


def _breadcrumbs(breadcrumb: Dict[str, Any]) -> List[str]:
    items = breadcrumb.get("itemListElement") or []
    out: list[str] = []
    if not isinstance(items, list):
        return out
    for entry in items:
        if isinstance(entry, dict) and entry.get("name"):
            out.append(str(entry["name"]))
    return out


class ZooplaListingDetail(BaseModel):
    """Full detail for an individual Zoopla listing.

    Field provenance — all values trace to fixtures captured in Phase 1:
    - ``price`` / ``currency``: ld+json ``RealEstateListing.offers.price`` /
      ``priceCurrency``.
    - ``display_price``: parsed from the ``<title>`` tag (the rendered
      ``£N,NNN,NNN`` string).
    - ``title``: ld+json ``name`` (e.g. ``"1 bed flat for sale 31, ..."``).
    - ``description``: ld+json ``description`` (HTML — kept raw).
    - ``date_posted``: ld+json ``datePosted`` (ISO timestamp).
    - ``meta_description``: ``<meta name='description'>`` content.
    - ``bedrooms`` / ``bathrooms``: ld+json
      ``additionalProperty[name=Bedrooms|Bathrooms]`` values, with the
      embedded ``ListingAnalyticsTaxonomy`` ``numBeds`` / ``numBaths`` as
      a fallback.
    - ``receptions``: analytics ``numRecepts`` (no ld+json equivalent).
    - ``floor_area``: raw text from ld+json
      ``additionalProperty[name='Floor size']`` (e.g. ``"7,668 sq. ft"``).
      ``floor_area_sqft`` is the parsed integer.
    - ``address``: analytics ``displayAddress``.
    - ``postcode``: analytics ``location`` (full postcode).
    - ``outcode`` / ``incode``: analytics ``outcode`` / ``incode``.
    - ``property_type``: analytics ``propertyType``.
    - ``listing_status``: analytics ``listingStatus`` (e.g.
      ``"for_sale"``).
    - ``listing_condition``: analytics ``listingCondition`` (e.g.
      ``"pre-owned"``, ``"new-build"``).
    - ``furnished_state``: analytics ``furnishedState``.
    - ``chain_free`` / ``has_epc`` / ``has_floorplan`` /
      ``is_retirement_home`` / ``is_shared_ownership``: analytics
      booleans.
    - ``tenure``: ``NtsInfo`` row labelled "Tenure", with analytics
      ``tenure`` as a fallback.
    - ``council_tax_band``: parsed from the ``NtsInfo`` row labelled
      "Council tax band".
    - ``agent_name`` / ``agent_logo`` / ``branch_id``: analytics
      ``branchName`` / ``branchLogoUrl`` / ``branchId``.
    - ``breadcrumbs``: ld+json ``BreadcrumbList`` ``name`` items in
      order (e.g. ``["Zoopla", "For sale", "London", "South London"]``).
    - ``images``: every ``lid.zoocdn.com`` URL on the page, deduped.
    """

    id: str
    url: str
    price: Optional[int] = None
    currency: Optional[str] = None
    display_price: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    meta_description: Optional[str] = None
    date_posted: Optional[str] = None
    bedrooms: Optional[int] = None
    bathrooms: Optional[int] = None
    receptions: Optional[int] = None
    floor_area: Optional[str] = None
    floor_area_sqft: Optional[int] = None
    address: Optional[str] = None
    postcode: Optional[str] = None
    outcode: Optional[str] = None
    incode: Optional[str] = None
    property_type: Optional[str] = None
    listing_status: Optional[str] = None
    listing_condition: Optional[str] = None
    furnished_state: Optional[str] = None
    chain_free: Optional[bool] = None
    has_epc: Optional[bool] = None
    has_floorplan: Optional[bool] = None
    is_retirement_home: Optional[bool] = None
    is_shared_ownership: Optional[bool] = None
    tenure: Optional[str] = None
    council_tax_band: Optional[str] = None
    agent_name: Optional[str] = None
    agent_logo: Optional[str] = None
    branch_id: Optional[int] = None
    breadcrumbs: List[str] = Field(default_factory=list)
    images: List[str] = Field(default_factory=list)
    nts_info: Dict[str, str] = Field(default_factory=dict)
    raw: Optional[Dict[str, Any]] = Field(default=None, exclude=True)

    @classmethod
    def build(
        cls,
        *,
        listing_id: str,
        url: str,
        title_text: str | None,
        meta_description: str | None,
        real_estate: Dict[str, Any],
        breadcrumb: Dict[str, Any],
        analytics: Dict[str, Any],
        nts_info: Dict[str, str],
        images: List[str],
    ) -> ZooplaListingDetail:
        offers = real_estate.get("offers") or {}
        price = _coerce_int(offers.get("price")) or _coerce_int(analytics.get("price"))
        currency = _coerce_str(offers.get("priceCurrency")) or _coerce_str(
            analytics.get("currencyCode")
        )

        # display_price: the £-prefixed string in the page <title>
        display_price: Optional[str] = None
        if title_text:
            m = re.search(r"£\s*[\d,]+", title_text)
            if m:
                display_price = m.group(0).replace(" ", "")

        bedrooms = _coerce_int(_ld_additional_property(real_estate, "Bedrooms"))
        if bedrooms is None:
            bedrooms = _coerce_int(analytics.get("numBeds"))
        bathrooms = _coerce_int(_ld_additional_property(real_estate, "Bathrooms"))
        if bathrooms is None:
            bathrooms = _coerce_int(analytics.get("numBaths"))

        floor_area = _ld_additional_property(real_estate, "Floor size")
        floor_area_sqft: Optional[int] = None
        if floor_area:
            m = _FLOOR_AREA_RE.search(floor_area)
            if m:
                try:
                    floor_area_sqft = int(m.group(1).replace(",", ""))
                except ValueError:
                    floor_area_sqft = None
        if floor_area_sqft is None:
            floor_area_sqft = _coerce_int(analytics.get("sizeSqFeet"))

        # tenure: prefer NtsInfo (rendered text), fall back to analytics taxonomy
        tenure = _coerce_str(nts_info.get("Tenure"))
        if not tenure:
            tenure_raw = _coerce_str(analytics.get("tenure"))
            if tenure_raw:
                tenure = tenure_raw.replace("_", " ").title()

        council_tax_band: Optional[str] = None
        for label, value in nts_info.items():
            if label.lower().startswith("council tax"):
                m = _COUNCIL_TAX_BAND_RE.search(value)
                if m:
                    council_tax_band = m.group(1).upper()
                else:
                    council_tax_band = value
                break

        return cls(
            id=listing_id,
            url=url,
            price=price,
            currency=currency,
            display_price=display_price,
            title=_coerce_str(real_estate.get("name")) or title_text,
            description=_coerce_str(real_estate.get("description")),
            meta_description=meta_description,
            date_posted=_coerce_str(real_estate.get("datePosted")),
            bedrooms=bedrooms,
            bathrooms=bathrooms,
            receptions=_coerce_int(analytics.get("numRecepts")),
            floor_area=floor_area,
            floor_area_sqft=floor_area_sqft,
            address=_coerce_str(analytics.get("displayAddress")),
            postcode=_coerce_str(analytics.get("location")),
            outcode=_coerce_str(analytics.get("outcode")),
            incode=_coerce_str(analytics.get("incode")),
            property_type=_coerce_str(analytics.get("propertyType")),
            listing_status=_coerce_str(analytics.get("listingStatus")),
            listing_condition=_coerce_str(analytics.get("listingCondition")),
            furnished_state=_coerce_str(analytics.get("furnishedState")),
            chain_free=_coerce_bool(analytics.get("chainFree")),
            has_epc=_coerce_bool(analytics.get("hasEpc")),
            has_floorplan=_coerce_bool(analytics.get("hasFloorplan")),
            is_retirement_home=_coerce_bool(analytics.get("isRetirementHome")),
            is_shared_ownership=_coerce_bool(analytics.get("isSharedOwnership")),
            tenure=tenure,
            council_tax_band=council_tax_band,
            agent_name=_coerce_str(analytics.get("branchName")),
            agent_logo=_coerce_str(analytics.get("branchLogoUrl")),
            branch_id=_coerce_int(analytics.get("branchId")),
            breadcrumbs=_breadcrumbs(breadcrumb),
            images=images,
            nts_info=nts_info,
            raw={
                "ld_real_estate": real_estate,
                "ld_breadcrumb": breadcrumb,
                "analytics_taxonomy": analytics,
                "nts_info": nts_info,
            },
        )


def _coerce_bool(val: Any) -> Optional[bool]:
    if val is None or val == "":
        return None
    if isinstance(val, bool):
        return val
    s = str(val).strip().lower()
    if s in ("true", "1", "yes"):
        return True
    if s in ("false", "0", "no"):
        return False
    return None
