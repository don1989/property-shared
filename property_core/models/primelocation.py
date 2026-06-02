"""Domain models for PrimeLocation data.

PrimeLocation (primelocation.com) is a ZPG sibling of Zoopla. The two
sites share the same image CDN (``lid.zoocdn.com``), the same ld+json
contract on listing-detail pages (``RealEstateListing`` +
``BreadcrumbList``), and the same "Need to see info" (``NtsInfo``)
tenure / council-tax block — but PrimeLocation ships an *older*
front-end template, so the CSS-Modules class names differ:

- Search cards are anchored on a wrapping
  ``<div class='ListingsSearchResultsCard_styles_listingRowStyle...'
  id='listing_{id}'>`` (Zoopla uses ``<a data-testid='listing-card-content'>``).
  Card sub-elements use ``ListingsSearchResultsCard_styles_*`` prefixes
  (``priceTextStyle__``, ``addressStyle__``, ``amenityItemStyle__``).
- The price also carries ``data-testid='listing-price'``.
- Listing-detail ``NtsInfo`` rows use ``NtsInfo_styles_*`` class names
  (Zoopla uses ``NtsInfo_*``).
- There is **no** single ``ListingAnalyticsTaxonomy`` object. Canonical
  attributes (displayAddress, outcode/incode, listingStatus, branch
  info, tenure) live as scalars inside the React Server Components
  stream, which the scraper flattens into a Zoopla-shaped ``taxonomy``
  dict so this model's ``build()`` can mirror ``ZooplaListingDetail``.
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
    # PrimeLocation cards render area as 'sq. ft' (with a period) as well as
    # 'sq ft'; the optional '\.?' matches both (mirrors _FLOOR_AREA_RE below).
    pattern = re.compile(r"^([\d,]+)\s*sq\.?\s*ft\b", re.I)
    for item in amenities:
        m = pattern.match(item.strip())
        if m:
            try:
                return int(m.group(1).replace(",", ""))
            except ValueError:
                return None
    return None


class PrimeLocationListing(BaseModel):
    """A single PrimeLocation search-result card.

    Field provenance:
    - ``id``: numeric id from the wrapping ``id='listing_{id}'`` div /
      the ``/for-sale/details/{id}/`` detail anchor.
    - ``url``: absolute URL built from that href.
    - ``display_price`` / ``price``: text and parsed integer from
      ``<p data-testid='listing-price' class='...priceTextStyle__...'>``.
    - ``price_qualifier``: ``...priceTitleStyle__...`` text ("Guide
      price", "OIRO", etc.).
    - ``amenities``: verbatim ``...amenityItemStyle__...`` items
      ("2 beds", "2 baths", "1 reception", "1,218 sq ft").
    - ``bedrooms`` / ``bathrooms`` / ``receptions`` / ``floor_area_sqft``:
      regex-parsed from those amenity strings.
    - ``address``: ``...addressStyle__...`` text.
    - ``summary``: ``...summaryStyle__...`` text.
    - ``premium_attributes``: ``...attributeTextStyle__...`` items
      ("Furnished", "Garden", etc.).
    - ``badges``: ``...statusListSlimStyle__...`` items ("Property of
      the week", "Reduced", etc.).
    - ``agent_name`` / ``agent_logo``: ``alt`` / ``src`` of
      ``...agentLogoImageStyle__...``.
    - ``images``: ``lid.zoocdn.com`` URLs inside the card.
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
    ) -> PrimeLocationListing:
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
            return _coerce_str(entry.get("value"))
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


class PrimeLocationListingDetail(BaseModel):
    """Full detail for an individual PrimeLocation listing.

    Provenance mirrors ``ZooplaListingDetail``:
    - ``price`` / ``currency``: ld+json ``RealEstateListing.offers``.
    - ``display_price``: parsed from the ``<title>`` tag.
    - ``title`` / ``description`` / ``date_posted``: ld+json ``name`` /
      ``description`` / ``datePosted``.
    - ``meta_description``: ``<meta name='description'>`` content.
    - ``bedrooms`` / ``bathrooms``: ld+json ``additionalProperty``, with
      the flattened RSC ``taxonomy`` (``numBeds`` / ``numBaths``) as a
      fallback.
    - ``floor_area`` / ``floor_area_sqft``: ld+json
      ``additionalProperty[name='Floor size']`` (text + parsed int).
    - ``address`` / ``postcode`` / ``outcode`` / ``incode`` /
      ``listing_status`` / ``listing_condition`` / ``furnished_state``:
      flattened RSC ``taxonomy`` scalars.
    - ``tenure``: ``NtsInfo`` "Tenure" row, falling back to taxonomy.
    - ``council_tax_band``: parsed from the ``NtsInfo`` "Council tax
      band" row.
    - ``agent_name`` / ``agent_logo`` / ``branch_id``: taxonomy
      ``branchName`` / ``branchLogoUrl`` / ``branchId``.
    - ``breadcrumbs``: ld+json ``BreadcrumbList`` names in order.
    - ``images``: every ``lid.zoocdn.com`` URL on the page, deduped.
    - ``nts_info``: the full flat ``{label: value}`` "Need to see info"
      block (Tenure, Service charge, Council tax band, Ground rent, ...).
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
        taxonomy: Dict[str, Any],
        nts_info: Dict[str, str],
        images: List[str],
    ) -> PrimeLocationListingDetail:
        offers = real_estate.get("offers") or {}
        price = _coerce_int(offers.get("price")) or _coerce_int(taxonomy.get("price"))
        currency = _coerce_str(offers.get("priceCurrency")) or _coerce_str(
            taxonomy.get("currencyCode")
        )

        # display_price: the £-prefixed string in the page <title>
        display_price: Optional[str] = None
        if title_text:
            m = re.search(r"£\s*[\d,]+", title_text)
            if m:
                display_price = m.group(0).replace(" ", "")

        bedrooms = _coerce_int(_ld_additional_property(real_estate, "Bedrooms"))
        if bedrooms is None:
            bedrooms = _coerce_int(taxonomy.get("numBeds"))
        bathrooms = _coerce_int(_ld_additional_property(real_estate, "Bathrooms"))
        if bathrooms is None:
            bathrooms = _coerce_int(taxonomy.get("numBaths"))

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
            floor_area_sqft = _coerce_int(taxonomy.get("sizeSqFeet"))

        # tenure: prefer NtsInfo (rendered text), fall back to taxonomy
        tenure = _coerce_str(nts_info.get("Tenure"))
        if not tenure:
            tenure_raw = _coerce_str(taxonomy.get("tenure"))
            if tenure_raw:
                tenure = tenure_raw.replace("_", " ").title()

        council_tax_band: Optional[str] = None
        for label, value in nts_info.items():
            if label.lower().startswith("council tax"):
                m = _COUNCIL_TAX_BAND_RE.search(value)
                council_tax_band = m.group(1).upper() if m else value
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
            receptions=_coerce_int(taxonomy.get("numRecepts")),
            floor_area=floor_area,
            floor_area_sqft=floor_area_sqft,
            address=_coerce_str(taxonomy.get("displayAddress")),
            postcode=_coerce_str(taxonomy.get("location")),
            outcode=_coerce_str(taxonomy.get("outcode")),
            incode=_coerce_str(taxonomy.get("incode")),
            property_type=_coerce_str(taxonomy.get("propertyType")),
            listing_status=_coerce_str(taxonomy.get("listingStatus")),
            listing_condition=_coerce_str(taxonomy.get("listingCondition")),
            furnished_state=_coerce_str(taxonomy.get("furnishedState")),
            tenure=tenure,
            council_tax_band=council_tax_band,
            agent_name=_coerce_str(taxonomy.get("branchName")),
            agent_logo=_coerce_str(taxonomy.get("branchLogoUrl")),
            branch_id=_coerce_int(taxonomy.get("branchId")),
            breadcrumbs=_breadcrumbs(breadcrumb),
            images=images,
            nts_info=nts_info,
            raw={
                "ld_real_estate": real_estate,
                "ld_breadcrumb": breadcrumb,
                "taxonomy": taxonomy,
                "nts_info": nts_info,
            },
        )
