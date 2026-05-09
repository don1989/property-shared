"""OnTheMarket API endpoints: search URL, listings, and listing detail."""

from __future__ import annotations

from functools import partial
from typing import Literal, Optional

import anyio
from fastapi import APIRouter, HTTPException, Query

from app.schemas.onthemarket import (
    OnTheMarketListingDetailResponse,
    OnTheMarketListingsResponse,
    OnTheMarketSearchURLResponse,
)
from property_core.onthemarket_location import OnTheMarketLocationAPI
from property_core.onthemarket_scraper import fetch_listing, fetch_listings

router = APIRouter(prefix="/onthemarket", tags=["onthemarket"])


@router.get("/search-url", response_model=OnTheMarketSearchURLResponse)
async def search_url(
    postcode: str = Query(..., min_length=2),
    property_type: Literal["sale", "rent"] = "sale",
    building_type: Optional[str] = Query(None, description="F=flat, D=detached, S=semi, T=terraced"),
    min_price: Optional[int] = Query(None, ge=0),
    max_price: Optional[int] = Query(None, ge=0),
    min_bedrooms: Optional[int] = Query(None, ge=0),
    max_bedrooms: Optional[int] = Query(None, ge=0),
    radius: Optional[float] = Query(None, ge=0),
    page: Optional[int] = Query(None, ge=1),
) -> OnTheMarketSearchURLResponse:
    """Build an OnTheMarket search URL from a postcode/area name."""
    try:
        url = OnTheMarketLocationAPI().build_search_url(
            postcode,
            property_type=property_type,
            building_type=building_type,
            min_price=min_price,
            max_price=max_price,
            min_bedrooms=min_bedrooms,
            max_bedrooms=max_bedrooms,
            radius=radius,
            page=page,
        )
        return OnTheMarketSearchURLResponse(url=url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/listings", response_model=OnTheMarketListingsResponse)
async def listings(
    search_url: str = Query(..., min_length=10),
    max_pages: Optional[int] = Query(None, ge=1, le=20),
) -> OnTheMarketListingsResponse:
    """Fetch listing results from an OnTheMarket search URL."""
    try:
        results = await anyio.to_thread.run_sync(
            partial(fetch_listings, search_url, max_pages=max_pages)
        )
        return OnTheMarketListingsResponse(count=len(results), results=results)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=502, detail=f"OnTheMarket listings failed: {exc}"
        ) from exc


@router.get("/listing/{property_id}", response_model=OnTheMarketListingDetailResponse)
async def listing_detail(property_id: str) -> OnTheMarketListingDetailResponse:
    """Fetch full details for an individual OnTheMarket listing."""
    try:
        result = await anyio.to_thread.run_sync(partial(fetch_listing, property_id))
        return OnTheMarketListingDetailResponse(result=result)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=502, detail=f"OnTheMarket listing detail failed: {exc}"
        ) from exc
