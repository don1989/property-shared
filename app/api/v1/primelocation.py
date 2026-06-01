"""PrimeLocation API endpoints: search URL builder, listings, detail.

PrimeLocation is a ZPG sibling of Zoopla behind the same Cloudflare bot
gate. Both search and detail are reachable via curl_cffi (libcurl-
impersonate replays a Chrome TLS fingerprint). On heavily-mitigated
egress IPs all profiles can still be blocked; in that case set
``PRIMELOCATION_PROXY_URL`` to a residential proxy and PrimeLocation
calls route through it.
"""

from __future__ import annotations

import os
from functools import partial
from typing import Literal, Optional

import anyio
from fastapi import APIRouter, HTTPException, Query

from app.schemas.primelocation import (
    PrimeLocationListingDetailResponse,
    PrimeLocationListingsResponse,
    PrimeLocationSearchURLResponse,
)
from property_core.primelocation_location import PrimeLocationLocationAPI
from property_core.primelocation_scraper import fetch_listing, fetch_listings

router = APIRouter(prefix="/primelocation", tags=["primelocation"])


def _primelocation_proxy() -> str | None:
    """Return ``PRIMELOCATION_PROXY_URL`` from the environment, else ``None``."""
    raw = os.environ.get("PRIMELOCATION_PROXY_URL")
    return raw.strip() or None if raw else None


def _primelocation_enabled() -> bool:
    """Whether PrimeLocation scraping is enabled in this deployment.

    Defaults to ``True`` so local dev / library use needs no config.
    Hosted deployments on flagged datacenter ASNs should set
    ``PRIMELOCATION_ENABLED=false`` until a residential proxy is wired up
    via ``PRIMELOCATION_PROXY_URL``.
    """
    raw = (os.environ.get("PRIMELOCATION_ENABLED") or "true").strip().lower()
    return raw in ("true", "1", "yes", "on")


def _require_enabled() -> None:
    if not _primelocation_enabled():
        raise HTTPException(
            status_code=503,
            detail=(
                "PrimeLocation is disabled in this deployment "
                "(PRIMELOCATION_ENABLED=false). Cloudflare on "
                "primelocation.com gates many datacenter ASNs even with "
                "curl_cffi profile rotation; set PRIMELOCATION_PROXY_URL to a "
                "residential proxy and PRIMELOCATION_ENABLED=true to re-enable."
            ),
        )


@router.get("/search-url", response_model=PrimeLocationSearchURLResponse)
async def search_url(
    postcode: str = Query(..., min_length=2),
    property_type: Literal["sale", "rent"] = "sale",
    building_type: Optional[str] = Query(None, description="F=flat, D=detached, S=semi, T=terraced"),
    new_build: bool = Query(False, description="Restrict to new-build properties (uses /new-homes/for-sale/ index)"),
    min_price: Optional[int] = Query(None, ge=0),
    max_price: Optional[int] = Query(None, ge=0),
    min_bedrooms: Optional[int] = Query(None, ge=0),
    max_bedrooms: Optional[int] = Query(None, ge=0),
    radius: Optional[float] = Query(None, ge=0),
    page: Optional[int] = Query(None, ge=1),
) -> PrimeLocationSearchURLResponse:
    """Build a PrimeLocation search URL from a postcode/area name."""
    try:
        url = PrimeLocationLocationAPI().build_search_url(
            postcode,
            property_type=property_type,
            building_type=building_type,
            new_build=new_build,
            min_price=min_price,
            max_price=max_price,
            min_bedrooms=min_bedrooms,
            max_bedrooms=max_bedrooms,
            radius=radius,
            page=page,
        )
        return PrimeLocationSearchURLResponse(url=url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/listings", response_model=PrimeLocationListingsResponse)
async def listings(
    search_url: str = Query(..., min_length=10),
    max_pages: Optional[int] = Query(None, ge=1, le=10),
) -> PrimeLocationListingsResponse:
    """Fetch listing results from a PrimeLocation search URL.

    Uses ``curl_cffi`` to defeat Cloudflare; no browser required.
    """
    _require_enabled()
    try:
        results = await anyio.to_thread.run_sync(
            partial(fetch_listings, search_url, max_pages=max_pages, proxy=_primelocation_proxy())
        )
        return PrimeLocationListingsResponse(count=len(results), results=results)
    except ImportError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=502, detail=f"PrimeLocation listings failed: {exc}"
        ) from exc


@router.get("/listing/{property_id}", response_model=PrimeLocationListingDetailResponse)
async def listing_detail(property_id: str) -> PrimeLocationListingDetailResponse:
    """Fetch full details for an individual PrimeLocation listing."""
    _require_enabled()
    try:
        result = await anyio.to_thread.run_sync(
            partial(fetch_listing, property_id, proxy=_primelocation_proxy())
        )
        return PrimeLocationListingDetailResponse(result=result)
    except ImportError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=502, detail=f"PrimeLocation listing detail failed: {exc}"
        ) from exc
