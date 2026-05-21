"""NewHomesForSale API endpoints: search URL, listings, listing detail."""

from __future__ import annotations

from functools import partial
from typing import Optional

import anyio
from fastapi import APIRouter, HTTPException, Query

from app.schemas.newhomesforsale import (
    NewHomesForSaleListingDetailResponse,
    NewHomesForSaleListingsResponse,
    NewHomesForSaleSearchURLResponse,
)
from property_core.newhomesforsale_location import NewHomesForSaleLocationAPI
from property_core.newhomesforsale_scraper import (
    NewHomesForSaleError,
    fetch_listing,
    fetch_listings,
)
from property_core.newhomesforsale_service import filter_developments_by_distance

router = APIRouter(prefix="/newhomesforsale", tags=["newhomesforsale"])


@router.get("/search-url", response_model=NewHomesForSaleSearchURLResponse)
async def search_url(
    county: str = Query(..., min_length=2, description="County name or slug, e.g. 'Hertfordshire'"),
    town: Optional[str] = Query(
        None, min_length=2, description="Optional town name within the county, e.g. 'Hitchin'"
    ),
) -> NewHomesForSaleSearchURLResponse:
    """Build a NewHomesForSale search URL for a county (optionally narrowed to a town)."""
    try:
        url = NewHomesForSaleLocationAPI().build_search_url(county=county, town=town)
        return NewHomesForSaleSearchURLResponse(url=url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/listings", response_model=NewHomesForSaleListingsResponse)
async def listings(
    search_url: str = Query(..., min_length=10),
    near_postcode: Optional[str] = Query(
        None,
        min_length=2,
        description=(
            "Optional UK postcode to post-filter results by crow-flies distance. "
            "When set, only developments within ``max_miles`` are returned, "
            "sorted ascending by distance. NHFS county/town searches can return "
            "developments well outside the named town."
        ),
    ),
    max_miles: float = Query(
        1.0,
        gt=0,
        le=50,
        description="Distance cap in miles when ``near_postcode`` is set.",
    ),
) -> NewHomesForSaleListingsResponse:
    """Fetch new-build developments from a NewHomesForSale search URL."""
    try:
        results = await anyio.to_thread.run_sync(
            partial(fetch_listings, search_url, rate_limit_seconds=0)
        )
        if near_postcode:
            results = await filter_developments_by_distance(
                results,
                anchor_postcode=near_postcode,
                max_miles=max_miles,
            )
        return NewHomesForSaleListingsResponse(count=len(results), results=results)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except NewHomesForSaleError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/listing", response_model=NewHomesForSaleListingDetailResponse)
async def listing_detail(
    url: str = Query(..., min_length=10, description="Absolute NHFS development URL"),
) -> NewHomesForSaleListingDetailResponse:
    """Fetch a single NewHomesForSale development detail page.

    NHFS detail pages are sparse — most useful fields are duplicated from
    the search card. Use ``/listings`` for the primary record and this
    endpoint only when you need the og-tags / address / postcode confirm.
    """
    try:
        result = await anyio.to_thread.run_sync(partial(fetch_listing, url))
        return NewHomesForSaleListingDetailResponse(result=result)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except NewHomesForSaleError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
