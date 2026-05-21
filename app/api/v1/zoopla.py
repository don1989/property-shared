"""Zoopla API endpoints: search URL builder, listings, and listing detail.

Both search and detail are reachable via curl_cffi (libcurl-impersonate
replays a Chrome TLS fingerprint that defeats Cloudflare's bot mode).
On heavily-mitigated egress IPs (e.g. some Hetzner ranges) all
``curl_cffi`` profiles can still be blocked; in that case set
``ZOOPLA_PROXY_URL`` to a residential proxy (e.g.
``http://user:pass@gate.smartproxy.com:7000``) and Zoopla calls route
through it. See docs/zoopla-onthemarket-discovery.md.
"""

from __future__ import annotations

import os
from functools import partial
from typing import Literal, Optional

import anyio
from fastapi import APIRouter, HTTPException, Query

from app.schemas.zoopla import (
    ZooplaListingDetailResponse,
    ZooplaListingsResponse,
    ZooplaSearchURLResponse,
)
from property_core.zoopla_location import ZooplaLocationAPI
from property_core.zoopla_scraper import fetch_listing, fetch_listings

router = APIRouter(prefix="/zoopla", tags=["zoopla"])


def _zoopla_proxy() -> str | None:
    """Return ``ZOOPLA_PROXY_URL`` from the environment, ``None`` if unset."""
    raw = os.environ.get("ZOOPLA_PROXY_URL")
    return raw.strip() or None if raw else None


def _zoopla_enabled() -> bool:
    """Whether Zoopla scraping is enabled in this deployment.

    Defaults to ``True`` so local dev / library use needs no config.
    Hosted deployments on flagged datacenter ASNs (e.g. Hetzner) should
    set ``ZOOPLA_ENABLED=false`` until a residential proxy is wired up
    via ``ZOOPLA_PROXY_URL``.
    """
    raw = (os.environ.get("ZOOPLA_ENABLED") or "true").strip().lower()
    return raw in ("true", "1", "yes", "on")


def _require_enabled() -> None:
    if not _zoopla_enabled():
        raise HTTPException(
            status_code=503,
            detail=(
                "Zoopla is disabled in this deployment (ZOOPLA_ENABLED=false). "
                "Cloudflare on zoopla.co.uk gates many datacenter ASNs even "
                "with curl_cffi profile rotation; set ZOOPLA_PROXY_URL to a "
                "residential proxy and ZOOPLA_ENABLED=true to re-enable."
            ),
        )


@router.get("/search-url", response_model=ZooplaSearchURLResponse)
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
) -> ZooplaSearchURLResponse:
    """Build a Zoopla search URL from a postcode/area name."""
    try:
        url = ZooplaLocationAPI().build_search_url(
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
        return ZooplaSearchURLResponse(url=url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/listings", response_model=ZooplaListingsResponse)
async def listings(
    search_url: str = Query(..., min_length=10),
    max_pages: Optional[int] = Query(None, ge=1, le=10),
) -> ZooplaListingsResponse:
    """Fetch listing results from a Zoopla search URL.

    Uses ``curl_cffi`` to defeat Cloudflare; no browser required.
    """
    _require_enabled()
    try:
        results = await anyio.to_thread.run_sync(
            partial(fetch_listings, search_url, max_pages=max_pages, proxy=_zoopla_proxy())
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


@router.get("/listing/{property_id}", response_model=ZooplaListingDetailResponse)
async def listing_detail(property_id: str) -> ZooplaListingDetailResponse:
    """Fetch full details for an individual Zoopla listing."""
    _require_enabled()
    try:
        result = await anyio.to_thread.run_sync(
            partial(fetch_listing, property_id, proxy=_zoopla_proxy())
        )
        return ZooplaListingDetailResponse(result=result)
    except ImportError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=502, detail=f"Zoopla listing detail failed: {exc}"
        ) from exc
