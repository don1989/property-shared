"""API envelope schemas for NewHomesForSale endpoints.

The domain model lives in
``property_core.models.newhomesforsale.NewHomesForSaleDevelopment``;
this file defines only the API response wrappers.
"""

from __future__ import annotations

from typing import Any, List

from pydantic import BaseModel, Field

from property_core.models.newhomesforsale import (  # noqa: F401
    NewHomesForSaleDevelopment,
)


class NewHomesForSaleSearchURLResponse(BaseModel):
    url: str


class NewHomesForSaleListingsResponse(BaseModel):
    count: int
    results: List[NewHomesForSaleDevelopment] = Field(default_factory=list)


class NewHomesForSaleListingDetailResponse(BaseModel):
    """Detail pages return a sparse dict (NHFS detail pages are mostly
    enquiry forms with little structured data)."""

    result: dict[str, Any]
