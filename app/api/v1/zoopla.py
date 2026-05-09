"""Zoopla API endpoints: search URL builder + listings.

There is no listing-detail endpoint because Zoopla's per-listing detail
pages serve a Cloudflare Turnstile challenge that does not auto-resolve
in headless Chromium. See docs/zoopla-onthemarket-discovery.md.
"""

from __future__ import annotations

from functools import partial
from typing import Literal, Optional

import anyio
from fastapi import APIRouter, HTTPException, Query

from app.schemas.zoopla import ZooplaListingsResponse, ZooplaSearchURLResponse
from property_core.zoopla_location import ZooplaLocationAPI
from property_core.zoopla_scraper import fetch_listings

router = APIRouter(prefix="/zoopla", tags=["zoopla"])


@router.get("/search-url", response_model=ZooplaSearchURLResponse)
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
) -> ZooplaSearchURLResponse:
    """Build a Zoopla search URL from a postcode/area name."""
    try:
        url = ZooplaLocationAPI().build_search_url(
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
        return ZooplaSearchURLResponse(url=url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/listings", response_model=ZooplaListingsResponse)
async def listings(
    search_url: str = Query(..., min_length=10),
    max_pages: Optional[int] = Query(None, ge=1, le=10),
) -> ZooplaListingsResponse:
    """Fetch listing results from a Zoopla search URL.

    Uses headless Playwright. Requires the ``planning`` extra:
    ``pip install 'property-shared[planning]'`` plus
    ``playwright install chromium``.
    """
    try:
        results = await anyio.to_thread.run_sync(
            partial(fetch_listings, search_url, max_pages=max_pages)
        )
        return ZooplaListingsResponse(count=len(results), results=results)
    except ImportError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=502, detail=f"Zoopla listings failed: {exc}"
        ) from exc
