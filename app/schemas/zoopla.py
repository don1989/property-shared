"""API envelope schemas for Zoopla endpoints.

Domain model lives in property_core.models.zoopla. This file defines
only the API response wrappers.
"""

from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field

from property_core.models.zoopla import ZooplaListing  # noqa: F401


class ZooplaSearchURLResponse(BaseModel):
    """Response for search URL creation."""

    url: str


class ZooplaListingsResponse(BaseModel):
    """Listings results for a Zoopla search."""

    count: int
    results: List[ZooplaListing] = Field(default_factory=list)
