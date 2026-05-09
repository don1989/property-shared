"""API envelope schemas for OnTheMarket endpoints.

Domain models live in property_core.models.onthemarket. This file defines
only the API response wrappers.
"""

from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field

from property_core.models.onthemarket import (  # noqa: F401
    OnTheMarketListing,
    OnTheMarketListingDetail,
)


class OnTheMarketSearchURLResponse(BaseModel):
    """Response for search URL creation."""

    url: str


class OnTheMarketListingsResponse(BaseModel):
    """Listings results for an OnTheMarket search."""

    count: int
    results: List[OnTheMarketListing] = Field(default_factory=list)


class OnTheMarketListingDetailResponse(BaseModel):
    """Response for an individual OnTheMarket listing detail."""

    result: OnTheMarketListingDetail
