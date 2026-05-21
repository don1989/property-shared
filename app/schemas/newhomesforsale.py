"""API envelope schemas for NewHomesForSale endpoints.

Domain models live in ``property_core.models.newhomesforsale``; this
file defines only the API response wrappers.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from property_core.models.newhomesforsale import (  # noqa: F401
    NewHomesForSaleDevelopment,
    NewHomesForSaleDevelopmentDetail,
)


class NewHomesForSaleSearchURLResponse(BaseModel):
    url: str


class NewHomesForSaleListingsResponse(BaseModel):
    count: int
    results: list[NewHomesForSaleDevelopment] = Field(default_factory=list)


class NewHomesForSaleListingDetailResponse(BaseModel):
    result: NewHomesForSaleDevelopmentDetail
