"""API envelope schemas for PrimeLocation endpoints.

Domain model lives in property_core.models.primelocation. This file
defines only the API response wrappers.
"""

from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field

from property_core.models.primelocation import (  # noqa: F401
    PrimeLocationListing,
    PrimeLocationListingDetail,
)


class PrimeLocationSearchURLResponse(BaseModel):
    """Response for search URL creation."""

    url: str


class PrimeLocationListingsResponse(BaseModel):
    """Listings results for a PrimeLocation search."""

    count: int
    results: List[PrimeLocationListing] = Field(default_factory=list)


class PrimeLocationListingDetailResponse(BaseModel):
    """Response for an individual PrimeLocation listing detail."""

    result: PrimeLocationListingDetail
