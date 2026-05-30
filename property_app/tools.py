"""Plain MCP tools — standalone lookups registered on the main server.

These are real MCP tools callable by LLMs. Each wraps a property_core
function with lazy imports and returns data directly (no Prefab UI).
The stamp_duty tool is the exception: it uses ``app=True`` to return
a PrefabApp with inline Metric + DataTable components.
"""
from __future__ import annotations

from typing import Any, Annotated

from pydantic import Field

from property_app.server import mcp


def _slim(obj: Any) -> Any:
    """Strip raw/images/floorplans/epc_match for LLM-friendly output."""
    if isinstance(obj, dict):
        return {k: _slim(v) for k, v in obj.items()
                if k not in ("raw", "images", "floorplans", "epc_match")}
    if isinstance(obj, list):
        return [_slim(item) for item in obj]
    return obj


# ---------------------------------------------------------------------------
# 1. Stamp Duty calculator (Prefab UI)
# ---------------------------------------------------------------------------


def calc_stamp_duty(
    price: int,
    additional_property: bool = False,
    first_time_buyer: bool = False,
    non_resident: bool = False,
) -> dict:
    """Raw stamp duty calculation — returns dict. Used by the MCP tool and tests."""
    from property_core import calculate_stamp_duty

    result = calculate_stamp_duty(
        price,
        additional_property=additional_property,
        first_time_buyer=first_time_buyer,
        non_resident=non_resident,
    )
    return result.model_dump(mode="json")


@mcp.tool(
    app=True,
    annotations={"readOnlyHint": True, "idempotentHint": True},
    tags={"calculator"},
)
def stamp_duty(
    price: Annotated[int, Field(description="Purchase price in GBP")],
    additional_property: Annotated[
        bool, Field(description="Buying an additional property (+5% surcharge)")
    ] = False,
    first_time_buyer: Annotated[
        bool, Field(description="First-time buyer relief (up to 300k nil rate)")
    ] = False,
    non_resident: Annotated[
        bool, Field(description="Non-UK resident (+2% surcharge)")
    ] = False,
):
    """Calculate UK Stamp Duty Land Tax for a residential property purchase.

    Returns SDLT total, effective rate, and band-by-band breakdown.
    """
    from prefab_ui.app import PrefabApp
    from prefab_ui.components import (
        Column,
        DataTable,
        DataTableColumn,
        Grid,
        Heading,
        Metric,
        Separator,
    )

    from fastmcp.tools import ToolResult

    from property_app.formatting import fmt_gbp, fmt_pct

    data = calc_stamp_duty(price, additional_property, first_time_buyer, non_resident)

    # Build band rows for the data table
    band_rows = [
        {
            "band": b["band"],
            "rate": f"{b['rate']}%",
            "amount": fmt_gbp(b["amount"]),
            "tax": fmt_gbp(b["tax"]),
        }
        for b in data.get("breakdown", [])
    ]

    view = Column(
        children=[
            Heading("Stamp Duty (SDLT)", level=2),
            Grid(
                columns=3,
                children=[
                    Metric(label="Purchase Price", value=fmt_gbp(price)),
                    Metric(label="Total SDLT", value=fmt_gbp(data["total_sdlt"])),
                    Metric(
                        label="Effective Rate",
                        value=fmt_pct(data["effective_rate"]),
                    ),
                ],
            ),
            Separator(),
            DataTable(
                columns=[
                    DataTableColumn(key="band", header="Band"),
                    DataTableColumn(key="rate", header="Rate", align="right"),
                    DataTableColumn(key="amount", header="Amount", align="right"),
                    DataTableColumn(key="tax", header="Tax", align="right"),
                ],
                rows=band_rows,
            ),
        ],
        gap=4,
    )

    # Text fallback so the model can reason about results (Lesson 24)
    return ToolResult(
        content=(
            f"SDLT for \u00a3{price:,}: \u00a3{data['total_sdlt']:,.0f} "
            f"({fmt_pct(data['effective_rate'])} effective rate)"
        ),
        structured_content=view,
    )


# ---------------------------------------------------------------------------
# 2. Planning search
# ---------------------------------------------------------------------------


def search_planning(postcode: str) -> dict:
    """Raw planning search — returns dict. Used by the MCP tool and tests."""
    from property_core import PlanningService

    result = PlanningService().search(postcode)
    # PlanningService.search() already returns a dict
    return result


@mcp.tool(
    annotations={"readOnlyHint": True},
    tags={"planning"},
)
def planning_search(
    postcode: Annotated[str, Field(description="UK postcode to look up planning portal for")],
) -> dict:
    """Find the planning portal URL for a UK postcode.

    Returns council info and portal search URLs. Does not scrape
    planning applications -- use the returned URLs to search directly.
    """
    return search_planning(postcode)


# ---------------------------------------------------------------------------
# 3. Company search
# ---------------------------------------------------------------------------


def search_company(query: str) -> dict:
    """Raw company name search — returns dict. Used by the MCP tool and tests."""
    from property_core import CompaniesHouseClient

    client = CompaniesHouseClient()
    result = client.search(query)

    if result is None:
        return {"error": "Not found"}
    return _slim(result.model_dump(mode="json"))


@mcp.tool(
    annotations={"readOnlyHint": True, "openWorldHint": True},
    tags={"companies"},
)
def company_search(
    query: Annotated[
        str,
        Field(description="Company name to search (e.g. 'Tesco'). For direct lookup by number, use the company://{company_number} resource."),
    ],
) -> dict:
    """Search Companies House by company name. Returns a list of matches.

    For a direct lookup by company number, use the company://{company_number}
    resource instead (e.g. read_resource("company://00445790")).
    """
    return search_company(query)


# ---------------------------------------------------------------------------
# 4. EPC lookup (async)
# ---------------------------------------------------------------------------


async def lookup_epc(postcode: str, address: str | None = None) -> dict:
    """Raw EPC lookup — returns dict. Used by the MCP tool and tests.

    With address: returns the matched certificate.
    Without address: returns all certificates at the postcode with area summary.
    """
    from collections import Counter

    from property_core import EPCClient

    client = EPCClient()

    if address:
        result = await client.search_by_postcode(postcode, address=address)
        if result is None:
            return {"error": "No EPC data"}
        return _slim(result.model_dump(mode="json"))

    # Area mode
    certs = await client.search_all_by_postcode(postcode)
    if not certs:
        return {"error": "No EPC data"}

    ratings = Counter(c.rating for c in certs if c.rating)
    types = Counter(c.property_type for c in certs if c.property_type)
    areas = [c.floor_area for c in certs if c.floor_area]

    summary = {
        "count": len(certs),
        "rating_distribution": dict(sorted(ratings.items())),
        "property_type_breakdown": dict(sorted(types.items())),
        "floor_area_min": min(areas) if areas else None,
        "floor_area_max": max(areas) if areas else None,
        "floor_area_avg": round(sum(areas) / len(areas), 1) if areas else None,
    }
    # Return only the summary — skip the full 25-cert list to save tokens in
    # the LLM context (data tools return dicts that the MCP framework puts in
    # content[]; a 25-cert payload is ~20KB). For individual property detail,
    # callers should re-call with a specific address.
    return _slim({
        "postcode": postcode,
        "summary": summary,
        "note": "Call lookup_epc again with a specific address for individual property details.",
    })


@mcp.tool(
    annotations={"readOnlyHint": True, "openWorldHint": True},
    tags={"epc"},
    timeout=30.0,
)
async def epc_lookup(
    postcode: Annotated[str, Field(description="UK postcode to search for EPC certificates")],
    address: Annotated[
        str | None,
        Field(description="Street address to match (omit for area view)"),
    ] = None,
) -> dict:
    """Energy Performance Certificate data for a UK property or postcode area.

    With address: returns the matched certificate for that specific property.
    Without address: returns all certificates at the postcode with area
    aggregation — rating distribution, floor area range, property type
    breakdown. Use the area mode for postcode-level views rather than
    a single-property lookup.
    """
    return await lookup_epc(postcode, address=address)


async def browse_epc_certs(postcode: str) -> list[dict] | None:
    """Raw EPC cert browse — returns slim list. Used by the MCP tool and tests."""
    from property_core import EPCClient

    client = EPCClient()
    certs = await client.search_all_by_postcode(postcode)
    if not certs:
        return None

    keep = {"address", "rating", "score", "floor_area", "property_type",
            "floor_level", "habitable_rooms", "inspection_date", "lmk_key"}

    return _slim([
        {k: v for k, v in c.model_dump(mode="json", exclude_none=True).items() if k in keep}
        for c in certs
    ])


@mcp.tool(
    annotations={"readOnlyHint": True, "openWorldHint": True},
    tags={"epc"},
    timeout=30.0,
)
async def epc_search(
    postcode: Annotated[str, Field(description="UK postcode to browse EPC certificates for")],
) -> list[dict] | None:
    """Browse all EPC certificates at a postcode — use when you have no house number.

    Returns a slim list of every certificate at the postcode. Each entry contains:
      address, rating, score, floor_area (sqm), property_type, floor_level,
      habitable_rooms, inspection_date, lmk_key.

    Workflow for Rightmove listings where the house number is not shown:
      1. Call rightmove_listing to obtain floor_area_sqm, property_type, and
         any floor-level signals in the description (e.g. "top floor", "ground floor").
      2. Call epc_search(postcode) to retrieve the full cert list.
      3. You MUST cross-reference each cert's floor_area against the listing's
         floor_area_sqm (accept within ±5 sqm) AND property_type must match.
         Also use floor_level and habitable_rooms where available.
      4. If a single cert matches, call epc_certificate(lmk_key) for the full detail.
      5. If multiple certs match equally, present all candidates — do not guess.
         If floor_area is unavailable on the listing, filter by property_type only
         and return all candidates.

    Returns None if no certificates exist for the postcode.
    """
    return await browse_epc_certs(postcode)


async def fetch_epc_certificate(lmk_key: str) -> dict | None:
    """Raw EPC cert fetch by lmk_key — returns dict. Used by the MCP tool and tests."""
    from property_core import EPCClient

    client = EPCClient()
    result = await client.get_certificate(lmk_key)
    if result is None:
        return None
    return _slim(result.model_dump(mode="json", exclude_none=True))


@mcp.tool(
    annotations={"readOnlyHint": True, "openWorldHint": True},
    tags={"epc"},
    timeout=30.0,
)
async def epc_certificate(
    lmk_key: Annotated[str, Field(description="EPC certificate hash (lmk_key) from epc_search results")],
) -> dict | None:
    """Fetch a single EPC certificate by its lmk_key (certificate hash).

    Use after epc_search has identified the correct cert — this is faster
    than epc_lookup(postcode, address) as it makes a direct lookup with no
    fuzzy matching or postcode re-fetch.

    lmk_key is returned in every epc_search result.

    Returns the full EPC certificate or None if not found.
    """
    return await fetch_epc_certificate(lmk_key)


# ---------------------------------------------------------------------------
# 5. Rightmove search
# ---------------------------------------------------------------------------


def search_rightmove(
    postcode: str,
    property_type: str = "sale",
    min_bedrooms: int | None = None,
    max_price: int | None = None,
    radius: float | None = None,
    building_type: str | None = None,
    new_build: bool = False,
) -> dict:
    """Raw Rightmove search — returns dict. Used by the MCP tool and tests."""
    from statistics import median as stat_median

    from property_core import RightmoveLocationAPI, fetch_listings

    loc_api = RightmoveLocationAPI()
    search_url = loc_api.build_search_url(
        postcode,
        property_type=property_type,
        min_bedrooms=min_bedrooms,
        max_price=max_price,
        radius=radius,
        building_type=building_type,
        new_build=new_build,
    )

    listings = fetch_listings(search_url, max_pages=1)
    prices = [l.price for l in listings if l.price and l.price > 0]
    median_price = int(stat_median(prices)) if prices else None

    return {
        "search_url": search_url,
        "count": len(listings),
        "listings": [_slim(l.model_dump(mode="json")) for l in listings],
        "median_price": median_price,
    }


_RM_TENURE_MAP = {
    "FREEHOLD": "freehold",
    "LEASEHOLD": "leasehold",
    "SHARE_OF_FREEHOLD": "share_of_freehold",
    "SHARED_FREEHOLD": "share_of_freehold",
}


def _normalise_tenure(value: str | None) -> str:
    if not value:
        return "unknown"
    return _RM_TENURE_MAP.get(value.upper(), value.lower())


def _map_nearest_stations(stations: list[dict] | None) -> list[dict]:
    if not stations:
        return []
    out: list[dict] = []
    for s in stations:
        if not isinstance(s, dict):
            continue
        name = s.get("name")
        if not name:
            continue
        unit = (s.get("unit") or "").lower()
        distance = s.get("distance")
        try:
            distance_val = float(distance) if distance is not None else None
        except (TypeError, ValueError):
            distance_val = None
        if distance_val is not None and unit in ("kilometres", "km"):
            distance_val = round(distance_val * 0.621371, 2)
        elif distance_val is not None:
            distance_val = round(distance_val, 2)
        out.append({"name": name, "distance_miles": distance_val})
    return out


def lookup_rightmove_listing(property_id: str) -> dict:
    """Raw Rightmove listing detail. Returns dict shaped for buyer-agent clients.

    Accepts a numeric property id or a full rightmove.co.uk URL. Uses
    ``fetch_listing`` which rotates curl_cffi profiles to defeat Cloudflare
    fingerprinting before falling back to plain requests.
    """
    from property_core import fetch_listing
    from property_core.rightmove_scraper import RightmoveError

    try:
        detail = fetch_listing(property_id)
    except RightmoveError as exc:
        # This tool swallows scraper errors into an {"error": ...} dict rather
        # than letting them bubble up, so report it explicitly. Lazy import;
        # capture_exception is a no-op when Sentry isn't initialised.
        import sentry_sdk

        sentry_sdk.capture_exception(exc)
        return {"error": str(exc)}

    raw_telephone: str | None = None
    raw = detail.raw or {}
    if isinstance(raw, dict):
        contact_info = raw.get("contactInfo")
        if isinstance(contact_info, dict):
            phones = contact_info.get("telephoneNumbers")
            if isinstance(phones, dict):
                raw_telephone = (
                    phones.get("localNumber")
                    or phones.get("internationalNumber")
                )
        if raw_telephone is None:
            customer = raw.get("customer")
            if isinstance(customer, dict):
                raw_telephone = (
                    customer.get("contactTelephoneNumber")
                    or customer.get("telephoneNumber")
                )

    epc_rating: str | None = None
    epc_section = raw.get("epcRatings") if isinstance(raw, dict) else None
    if isinstance(epc_section, dict):
        epc_rating = (
            epc_section.get("currentRating")
            or epc_section.get("current")
            or epc_section.get("rating")
        )
    if epc_rating is None:
        epc_graphs = raw.get("epcGraphs") if isinstance(raw, dict) else None
        if isinstance(epc_graphs, list):
            for entry in epc_graphs:
                if isinstance(entry, dict):
                    rating = entry.get("epcRating") or entry.get("rating")
                    if rating:
                        epc_rating = rating
                        break

    photo_urls = list(detail.images or [])
    floorplans = list(detail.floorplans or [])
    floorplan_url = floorplans[0] if floorplans else None

    return {
        "id": detail.id,
        "url": detail.url,
        "price": detail.price,
        "currency": detail.currency,
        "address": detail.address,
        "postcode": detail.postcode,
        "latitude": detail.latitude,
        "longitude": detail.longitude,
        "property_type": detail.property_type,
        "bedrooms": detail.bedrooms,
        "bathrooms": detail.bathrooms,
        "floor_area_sqm": detail.floor_area_sqm,
        "floor_area_sqft": detail.floor_area_sqft,
        "tenure": _normalise_tenure(detail.tenure_type),
        "council_tax_band": detail.council_tax_band,
        "ground_rent": detail.annual_ground_rent,
        "service_charge": detail.annual_service_charge,
        "lease_years_remaining": detail.years_remaining_on_lease,
        "epc_rating": epc_rating,
        "agent_name": detail.agent_name,
        "agent_branch": detail.agent_branch,
        "agent_telephone": raw_telephone,
        "photo_urls": photo_urls,
        "floorplan_url": floorplan_url,
        "nearest_stations": _map_nearest_stations(detail.nearest_stations),
        "description": detail.description,
        "key_features": list(detail.key_features or []),
        "listing_status": detail.listing_status,
        "first_visible_date": detail.first_visible_date,
    }


@mcp.tool(
    annotations={"readOnlyHint": True, "openWorldHint": True},
    tags={"rightmove", "listings"},
    timeout=30.0,
)
def rightmove_listing(
    property_id: Annotated[
        str,
        Field(
            description=(
                "Rightmove property id (numeric, e.g. '88722216') or a full "
                "rightmove.co.uk property URL."
            )
        ),
    ],
) -> dict:
    """Full property detail for a single Rightmove listing.

    Returns price, address, postcode, lat/lon, property type, bedrooms,
    bathrooms, floor area, tenure (normalised lowercase), council tax band,
    lease economics (ground rent, service charge, years remaining),
    EPC rating where available, agent name / branch / telephone,
    photo URLs, the first floorplan URL, nearest stations (with
    distance in miles), description text, key features bullet list,
    and listing status (e.g. 'SOLD_STC').
    """
    return lookup_rightmove_listing(property_id)


@mcp.tool(
    annotations={"readOnlyHint": True, "openWorldHint": True},
    tags={"rightmove", "listings"},
    timeout=60.0,
)
def rightmove_search(
    postcode: Annotated[str, Field(description="UK postcode to search around")],
    property_type: Annotated[
        str, Field(description="'sale' or 'rent'")
    ] = "sale",
    min_bedrooms: Annotated[
        int | None, Field(description="Minimum number of bedrooms")
    ] = None,
    max_price: Annotated[
        int | None, Field(description="Maximum price filter")
    ] = None,
    radius: Annotated[
        float | None, Field(description="Search radius in miles")
    ] = None,
    building_type: Annotated[
        str | None,
        Field(description="Building type filter: F=flat, D=detached, S=semi, T=terraced"),
    ] = None,
    new_build: Annotated[
        bool,
        Field(description="Restrict to new-builds (uses /new-homes-for-sale/ index)"),
    ] = False,
) -> dict:
    """Search Rightmove property listings by postcode.

    Builds a search URL, fetches the first page of results, and returns
    listing summaries with a median price.
    """
    return search_rightmove(
        postcode,
        property_type=property_type,
        min_bedrooms=min_bedrooms,
        max_price=max_price,
        radius=radius,
        building_type=building_type,
        new_build=new_build,
    )



# ---------------------------------------------------------------------------
# 6. Zoopla search
# ---------------------------------------------------------------------------


def search_zoopla(
    postcode: str,
    property_type: str = "sale",
    min_bedrooms: int | None = None,
    max_price: int | None = None,
    radius: float | None = None,
    building_type: str | None = None,
    new_build: bool = False,
) -> dict:
    """Raw Zoopla search — returns dict. Used by the MCP tool and tests.

    Note: Zoopla detail pages are blocked by Cloudflare; only search-card
    data is available.
    """
    import os
    from statistics import median as stat_median

    from property_core import ZooplaLocationAPI, fetch_zoopla_listings

    loc_api = ZooplaLocationAPI()
    search_url = loc_api.build_search_url(
        postcode,
        property_type=property_type,
        min_bedrooms=min_bedrooms,
        max_price=max_price,
        radius=radius,
        building_type=building_type,
        new_build=new_build,
    )

    proxy = (os.environ.get("ZOOPLA_PROXY_URL") or "").strip() or None
    listings = fetch_zoopla_listings(search_url, max_pages=1, proxy=proxy)
    prices = [l.price for l in listings if l.price and l.price > 0]
    median_price = int(stat_median(prices)) if prices else None

    return {
        "search_url": search_url,
        "count": len(listings),
        "listings": [_slim(l.model_dump(mode="json")) for l in listings],
        "median_price": median_price,
    }


# Zoopla tools env-gated. Hidden from the registered tool list when
# ZOOPLA_ENABLED is not truthy (default behaviour on Coolify deployments
# where the VPS's Hetzner ASN is Cloudflare-blocked for zoopla.co.uk).
# Set ZOOPLA_ENABLED=true (and ZOOPLA_PROXY_URL=...) to register them.
import os as _os

_ZOOPLA_ENABLED = (_os.environ.get("ZOOPLA_ENABLED") or "true").strip().lower() in (
    "true", "1", "yes", "on",
)


def lookup_zoopla_listing(property_id: str) -> dict:
    """Raw Zoopla listing detail — returns dict."""
    import os
    from property_core import fetch_zoopla_listing

    proxy = (os.environ.get("ZOOPLA_PROXY_URL") or "").strip() or None
    listing = fetch_zoopla_listing(property_id, proxy=proxy)
    return _slim(listing.model_dump(mode="json"))


if _ZOOPLA_ENABLED:

    @mcp.tool(
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"zoopla", "listings"},
        timeout=120.0,
    )
    def zoopla_search(
        postcode: Annotated[str, Field(description="UK postcode or area name to search")],
        property_type: Annotated[str, Field(description="'sale' or 'rent'")] = "sale",
        min_bedrooms: Annotated[
            int | None, Field(description="Minimum number of bedrooms")
        ] = None,
        max_price: Annotated[
            int | None, Field(description="Maximum price filter")
        ] = None,
        radius: Annotated[
            float | None, Field(description="Search radius in miles")
        ] = None,
        building_type: Annotated[
            str | None,
            Field(description="Building type filter: F=flat, D=detached, S=semi, T=terraced"),
        ] = None,
        new_build: Annotated[
            bool,
            Field(description="Restrict to new-builds (uses /new-homes/for-sale/ index)"),
        ] = False,
    ) -> dict:
        """Search Zoopla property listings by postcode.

        Builds a search URL, fetches the first page via curl_cffi (TLS
        fingerprint impersonation defeats Cloudflare), and returns listing
        summaries with a median price.
        """
        return search_zoopla(
            postcode,
            property_type=property_type,
            min_bedrooms=min_bedrooms,
            max_price=max_price,
            radius=radius,
            building_type=building_type,
            new_build=new_build,
        )


    @mcp.tool(
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"zoopla", "listings"},
        timeout=30.0,
    )
    def zoopla_listing(
        property_id: Annotated[
            str,
            Field(description="Zoopla property id (numeric, e.g. '72192746') or full URL"),
        ],
    ) -> dict:
        """Full property detail data for a Zoopla listing.

        Returns price, address, postcode, bedrooms/bathrooms/floor area,
        tenure, council tax band, agent name, listing status, EPC/floorplan
        flags, furnished state, breadcrumbs, and the verbatim "Need to see
        info" rows.
        """
        return lookup_zoopla_listing(property_id)


# ---------------------------------------------------------------------------
# 7. OnTheMarket search + listing detail
# ---------------------------------------------------------------------------


def search_onthemarket(
    postcode: str,
    property_type: str = "sale",
    min_bedrooms: int | None = None,
    max_price: int | None = None,
    radius: float | None = None,
    building_type: str | None = None,
    new_build: bool = False,
) -> dict:
    """Raw OnTheMarket search — returns dict."""
    from statistics import median as stat_median

    from property_core import OnTheMarketLocationAPI, fetch_onthemarket_listings

    loc_api = OnTheMarketLocationAPI()
    search_url = loc_api.build_search_url(
        postcode,
        property_type=property_type,
        min_bedrooms=min_bedrooms,
        max_price=max_price,
        radius=radius,
        building_type=building_type,
        new_build=new_build,
    )

    listings = fetch_onthemarket_listings(search_url, max_pages=1)
    prices = [l.price for l in listings if l.price and l.price > 0]
    median_price = int(stat_median(prices)) if prices else None

    return {
        "search_url": search_url,
        "count": len(listings),
        "listings": [_onthemarket_search_listing_dict(l) for l in listings],
        "median_price": median_price,
    }


def _onthemarket_search_listing_dict(listing: Any) -> dict:
    """Slim a search-card listing and re-expose photos as `photo_urls`
    (matching the detail tool / Rightmove shape)."""
    data = _slim(listing.model_dump(mode="json"))
    data["photo_urls"] = list(listing.images or [])
    return data


@mcp.tool(
    annotations={"readOnlyHint": True, "openWorldHint": True},
    tags={"onthemarket", "listings"},
    timeout=60.0,
)
def onthemarket_search(
    postcode: Annotated[str, Field(description="UK postcode or area name to search")],
    property_type: Annotated[str, Field(description="'sale' or 'rent'")] = "sale",
    min_bedrooms: Annotated[
        int | None, Field(description="Minimum number of bedrooms")
    ] = None,
    max_price: Annotated[
        int | None, Field(description="Maximum price filter")
    ] = None,
    radius: Annotated[
        float | None, Field(description="Search radius in miles")
    ] = None,
    building_type: Annotated[
        str | None,
        Field(description="Building type filter: F=flat, D=detached, S=semi, T=terraced"),
    ] = None,
    new_build: Annotated[
        bool,
        Field(description="Restrict to new-builds (uses /new-homes/property/ index)"),
    ] = False,
) -> dict:
    """Search OnTheMarket property listings by postcode.

    Builds a search URL, fetches the first page, and returns listing
    summaries with a median price.
    """
    return search_onthemarket(
        postcode,
        property_type=property_type,
        min_bedrooms=min_bedrooms,
        max_price=max_price,
        radius=radius,
        building_type=building_type,
        new_build=new_build,
    )


def lookup_onthemarket_listing(property_id: str) -> dict:
    """Raw OnTheMarket listing detail — returns dict."""
    from property_core import fetch_onthemarket_listing

    listing = fetch_onthemarket_listing(property_id)
    data = _slim(listing.model_dump(mode="json"))
    # _slim drops the bulky `images` field; re-expose it as `photo_urls`
    # to mirror the Rightmove tool's shape (downstream apps read this).
    data["photo_urls"] = list(listing.images or [])
    # Normalise stations to the canonical {name, distance_miles} shape the
    # downstream apps read, same as the Rightmove tool.
    data["nearest_stations"] = _map_nearest_stations(getattr(listing, "nearest_stations", None))
    return data


@mcp.tool(
    annotations={"readOnlyHint": True, "openWorldHint": True},
    tags={"onthemarket", "listings"},
    timeout=30.0,
)
def onthemarket_listing(
    property_id: Annotated[
        str,
        Field(
            description="OnTheMarket property id (numeric, e.g. '19100332') or full URL"
        ),
    ],
) -> dict:
    """Full property detail data for an OnTheMarket listing.

    Returns price, address, postcode, channel (sale/rent), tenure, lease
    years remaining, ground rent, service charge, council tax band, EPC
    rating, photo_urls (the full property photo gallery), nearest_stations
    (name + distance in miles), and the verbatim Key information block.
    """
    return lookup_onthemarket_listing(property_id)


# ---------------------------------------------------------------------------
# 8. NewHomesForSale — UK new-build developments aggregator
# ---------------------------------------------------------------------------


async def search_newhomesforsale(
    county: str | None = None,
    town: str | None = None,
    near_postcode: str | None = None,
    max_miles: float = 1.0,
) -> dict:
    """Raw NHFS search. Returns dict. Used by the MCP tool and tests.

    ``county`` is optional. When omitted, the county is resolved from
    ``near_postcode`` (postcodes.io) or ``town`` (Nominatim). At least one of
    ``county``, ``near_postcode``, or ``town`` must be supplied.
    """
    import anyio

    from property_core import (
        NewHomesForSaleLocationAPI,
        fetch_nhfs_listings,
        filter_developments_by_distance,
        postcode_to_county,
        town_to_county,
    )

    resolved_county = county
    resolved_via: str | None = None
    if not resolved_county and near_postcode:
        resolved_county = await anyio.to_thread.run_sync(
            lambda: postcode_to_county(near_postcode)
        )
        if resolved_county:
            resolved_via = f"postcode:{near_postcode}"
    if not resolved_county and town:
        resolved_county = await anyio.to_thread.run_sync(
            lambda: town_to_county(town)
        )
        if resolved_county:
            resolved_via = f"town:{town}"

    if not resolved_county:
        if not (county or near_postcode or town):
            return {
                "error": (
                    "Provide one of county, near_postcode, or town. "
                    "county is required (or derived from a postcode or town)."
                )
            }
        return {
            "error": (
                f"Could not resolve a county from "
                f"county={county!r}, near_postcode={near_postcode!r}, "
                f"town={town!r}. Pass an explicit county."
            )
        }

    search_url = NewHomesForSaleLocationAPI().build_search_url(
        county=resolved_county, town=town
    )
    listings = await anyio.to_thread.run_sync(
        lambda: fetch_nhfs_listings(search_url, rate_limit_seconds=0)
    )
    if near_postcode:
        listings = await filter_developments_by_distance(
            listings,
            anchor_postcode=near_postcode,
            max_miles=max_miles,
        )
    payload = {
        "search_url": search_url,
        "county": resolved_county,
        "count": len(listings),
        "results": [_slim(l.model_dump(mode="json", exclude_none=True)) for l in listings],
    }
    if resolved_via:
        payload["resolved_via"] = resolved_via
    return payload


@mcp.tool(
    annotations={"readOnlyHint": True, "openWorldHint": True},
    tags={"newhomesforsale", "newbuild"},
    timeout=60.0,
)
async def newhomesforsale_search(
    county: Annotated[
        str | None,
        Field(
            description=(
                "UK county name or slug, e.g. 'Hertfordshire'. Optional. When "
                "omitted, resolved from near_postcode (postcodes.io) or town "
                "(Nominatim). At least one of county / near_postcode / town "
                "must be supplied."
            )
        ),
    ] = None,
    town: Annotated[
        str | None,
        Field(description="Optional town within the county, e.g. 'Hitchin'"),
    ] = None,
    near_postcode: Annotated[
        str | None,
        Field(
            description=(
                "Optional UK postcode — post-filter results to those within "
                "``max_miles`` (crow flies), sorted by distance ascending. "
                "Use this for 'near station' / 'near X' queries: NHFS town "
                "searches can return developments well outside the named town. "
                "Also used to derive ``county`` when not provided."
            )
        ),
    ] = None,
    max_miles: Annotated[
        float,
        Field(
            description="Distance cap in miles when ``near_postcode`` is set",
            gt=0,
            le=50,
        ),
    ] = 1.0,
) -> dict:
    """Fetch UK new-build developments from NewHomesForSale.co.uk.

    Aggregates ~2,600 UK new-build developments including
    developer-direct stock that often doesn't appear on Rightmove /
    OnTheMarket / Zoopla. Each result includes postcode, bedroom
    range, price range, developer name, and the NHFS URL.

    Pass any of: ``county`` (direct), ``near_postcode`` (resolves to county
    via postcodes.io), or ``town`` (resolves via Nominatim).
    """
    return await search_newhomesforsale(
        county=county,
        town=town,
        near_postcode=near_postcode,
        max_miles=max_miles,
    )


def lookup_newhomesforsale_listing(url: str) -> dict:
    """Raw NHFS development detail page lookup — returns dict."""
    from property_core import fetch_nhfs_listing

    return _slim(fetch_nhfs_listing(url).model_dump(mode="json", exclude_none=True))


@mcp.tool(
    annotations={"readOnlyHint": True, "openWorldHint": True},
    tags={"newhomesforsale", "newbuild"},
    timeout=30.0,
)
def newhomesforsale_listing(
    url: Annotated[
        str,
        Field(description="Absolute NHFS development URL"),
    ],
) -> dict:
    """Fetch a NewHomesForSale development detail page.

    Detail pages are sparse — most data lives on the search card. Use
    ``newhomesforsale_search`` for the primary record; use this only
    to confirm the og-image / address on the developer's page.
    """
    return lookup_newhomesforsale_listing(url)


# ---------------------------------------------------------------------------
# 9. Property blocks — find buildings with multiple flat sales
# ---------------------------------------------------------------------------


def analyse_blocks(
    postcode: str,
    search_level: str = "sector",
    months: int = 24,
    limit: int = 50,
    min_transactions: int = 2,
) -> dict:
    """Raw block analysis — returns dict. Used by the MCP tool and tests."""
    from property_core import analyze_blocks

    result = analyze_blocks(
        postcode=postcode,
        search_level=search_level,
        months=months,
        limit=limit,
        min_transactions=min_transactions,
    )
    return _slim(result.model_dump(mode="json", exclude_none=True))


@mcp.tool(
    annotations={"readOnlyHint": True},
    tags={"ppd", "blocks"},
    timeout=60.0,
)
def property_blocks(
    postcode: Annotated[str, Field(description="UK postcode (e.g. 'B1 1AA')")],
    search_level: Annotated[
        str, Field(description="postcode, sector, or district")
    ] = "sector",
    months: Annotated[int, Field(description="Sale lookback months", ge=1, le=120)] = 24,
    limit: Annotated[int, Field(description="Target number of blocks to return", ge=1, le=200)] = 50,
    min_transactions: Annotated[
        int, Field(description="Minimum sales per building to qualify as a block", ge=2)
    ] = 2,
) -> dict:
    """Identify buildings with multiple flat sales — block-buy opportunities.

    Groups Land Registry transactions by building (PAON/street) and returns
    blocks where at least min_transactions units sold in the lookback window.
    Useful for spotting investor exits, new-build releases, or portfolio
    bulk transfers.
    """
    return analyse_blocks(
        postcode=postcode,
        search_level=search_level,
        months=months,
        limit=limit,
        min_transactions=min_transactions,
    )


# ---------------------------------------------------------------------------
# 10. PPD transactions — raw Land Registry transactions for a postcode
# ---------------------------------------------------------------------------


def search_ppd_transactions(
    postcode: str,
    limit: int = 10,
    property_type: str | None = None,
) -> dict:
    """Raw PPD transaction search — returns dict. Used by the MCP tool and tests."""
    from property_core import PPDService

    result = PPDService().search_transactions(
        postcode=postcode,
        postcode_prefix=None,
        limit=limit,
        property_type=property_type,
    )
    return {
        **{k: v for k, v in result.items() if k != "results"},
        "results": [_slim(t.model_dump(mode="json", exclude_none=True)) for t in result["results"]],
    }


@mcp.tool(
    annotations={"readOnlyHint": True},
    tags={"ppd"},
)
def ppd_transactions(
    postcode: Annotated[str, Field(description="UK postcode")],
    limit: Annotated[int, Field(description="Max transactions to return", ge=1, le=200)] = 10,
    property_type: Annotated[
        str | None,
        Field(description="Filter by type: F=flat, D=detached, S=semi, T=terraced, O=other. Default None = all"),
    ] = None,
) -> dict:
    """Raw Land Registry Price Paid transactions for a postcode.

    Returns every recorded transaction at the postcode, unfiltered (includes
    category-B bulk transfers and commercial sales). For clean residential
    comparable sales, use property_comps instead.
    """
    return search_ppd_transactions(
        postcode=postcode,
        limit=limit,
        property_type=property_type,
    )
