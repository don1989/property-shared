"""Pydantic models for NewHomesForSale.co.uk."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class NewHomesForSaleDevelopment(BaseModel):
    """A single new-build development as listed on a NewHomesForSale search page.

    Parsed from the ``developmentSummary`` card on
    ``/new-homes/{county}/{town}/`` pages. NHFS detail pages contain
    enquiry forms rather than richer listing data, so the search card
    is the primary record; the detail URL is retained for callers that
    want to follow it for marketing material.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(description="NHFS development id (digits, from the ``dev_NNNN`` HTML id)")
    name: str = Field(description="Development name, e.g. 'Forster Park'")
    url: str = Field(description="Absolute NHFS detail URL")
    developer: str | None = Field(default=None, description="Developer/housebuilder name")
    address: str | None = Field(default=None, description="Single-line address as shown on the card")
    postcode: str | None = Field(default=None, description="Extracted from address (UK postcode pattern)")
    locality: str | None = Field(default=None, description="Town part of the address")
    region: str | None = Field(default=None, description="County part of the address")
    property_type: str | None = Field(
        default=None,
        description="Free-text e.g. 'houses', 'apartments', 'houses and bungalows'",
    )
    bedrooms_text: str | None = Field(
        default=None,
        description="Verbatim bedroom range, e.g. '3, 4 & 5'",
    )
    bedrooms_min: int | None = Field(default=None, description="Lowest bedroom count")
    bedrooms_max: int | None = Field(default=None, description="Highest bedroom count")
    price_min: int | None = Field(default=None, description="GBP, lowest listed price")
    price_max: int | None = Field(default=None, description="GBP, highest listed price")
    description: str | None = Field(default=None, description="Marketing blurb (truncated)")
    hero_image: str | None = Field(default=None, description="Absolute URL of the hero image")
    photo_count: int | None = Field(default=None, description="Photo count badge on the card")
    distance_text: str | None = Field(
        default=None,
        description="Distance from search anchor as the crow flies (only present when the search was anchored on a location)",
    )
    distance_miles: float | None = Field(
        default=None,
        description="Numeric distance parsed from ``distance_text`` (miles, crow flies)",
    )
    distance_to_anchor_miles: float | None = Field(
        default=None,
        description=(
            "Crow-flies distance from a caller-supplied anchor postcode (miles). "
            "Populated only when the development was passed through "
            "``filter_developments_by_distance``."
        ),
    )
    raw: dict[str, Any] | None = Field(default=None, exclude=True)


class NewHomesForSaleDevelopmentDetail(BaseModel):
    """Detail-page record for a single NewHomesForSale development.

    NHFS detail pages are deliberately sparse — they host enquiry
    forms rather than rich listing data, so this model surfaces only
    the fields that are reliably parseable from the og-meta tags and
    the ``PostalAddress`` block in the (partly-malformed) JSON-LD.
    For richer listing data (price range, beds, developer, etc.),
    use the search-card record returned by ``fetch_listings``.
    """

    model_config = ConfigDict(populate_by_name=True)

    url: str = Field(description="The fetched URL (after redirects)")
    title: str | None = Field(default=None, description="Page <h1> text")
    og_title: str | None = Field(default=None, description="og:title meta")
    og_description: str | None = Field(default=None, description="og:description meta")
    og_image: str | None = Field(default=None, description="og:image meta")
    address: str | None = Field(
        default=None,
        description="Full single-line address assembled from the JSON-LD PostalAddress",
    )
    postcode: str | None = Field(default=None, description="UK postcode")
    raw: dict[str, Any] | None = Field(default=None, exclude=True)
