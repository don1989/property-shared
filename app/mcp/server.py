"""Plain FastMCP server — property tools, no Prefab UI.

Exposes property_core functions as MCP tools. Suitable for any MCP client
regardless of ext-apps / Prefab UI support.
"""
from __future__ import annotations

from fastmcp import FastMCP
from fastmcp.server.http import create_streamable_http_app

mcp = FastMCP(
    "property-data",
    instructions=(
        "UK property data tools. Use property_report for a full data pull when you "
        "have a street address + postcode. For postcode-only queries use property_comps "
        "and property_yield separately. ppd_transactions for specific property history, "
        "rightmove_search to browse listings, rightmove_listing for full detail on one "
        "listing, property_epc for energy certificates, rental_analysis for rental market "
        "figures, stamp_duty for SDLT, property_blocks for block-buy analysis, "
        "planning_search for council planning portals, company_search to find a company "
        "by name."
    ),
)


@mcp.tool()
def property_comps(
    postcode: str,
    months: int = 24,
    property_type: str | None = None,
    search_level: str = "sector",
    address: str | None = None,
) -> dict:
    """Comparable sales from Land Registry Price Paid Data."""
    from property_core import PPDService
    return PPDService().comps(
        postcode=postcode,
        months=months,
        property_type=property_type,
        search_level=search_level,
        address=address,
    ).model_dump()


@mcp.tool()
async def property_yield(
    postcode: str,
    months: int = 24,
    search_level: str = "sector",
    property_type: str | None = None,
) -> dict:
    """Rental yield analysis for a postcode."""
    from property_core import calculate_yield
    return (await calculate_yield(
        postcode=postcode,
        months=months,
        search_level=search_level,
        property_type=property_type,
    )).model_dump()


@mcp.tool()
async def rental_analysis(
    postcode: str,
    radius: float = 0.5,
    purchase_price: int | None = None,
) -> dict:
    """Rental market analysis and achievable rent estimate."""
    from property_core import analyze_rentals
    return (await analyze_rentals(
        postcode=postcode,
        radius=radius,
        purchase_price=purchase_price,
    )).model_dump()


@mcp.tool()
async def property_epc(postcode: str, address: str | None = None) -> dict | None:
    """EPC energy certificate lookup by postcode (+ optional address filter)."""
    from property_core import EPCClient
    result = await EPCClient().search_by_postcode(postcode=postcode, address=address)
    return result.model_dump() if result else None


@mcp.tool()
def stamp_duty(
    price: int,
    additional_property: bool = False,
    first_time_buyer: bool = False,
    non_resident: bool = False,
) -> dict:
    """UK Stamp Duty Land Tax (SDLT) calculation with full breakdown."""
    from property_core import calculate_stamp_duty
    return calculate_stamp_duty(
        price=price,
        additional_property=additional_property,
        first_time_buyer=first_time_buyer,
        non_resident=non_resident,
    ).model_dump()


@mcp.tool()
async def rightmove_search(url: str, max_pages: int = 3) -> list[dict]:
    """Fetch Rightmove listings from a search URL."""
    from property_core import fetch_listings
    listings = await fetch_listings(url=url, max_pages=max_pages)
    return [l.model_dump() for l in listings]


@mcp.tool()
def rightmove_listing(property_url_or_id: str) -> dict:
    """Full detail for a single Rightmove listing (URL or numeric ID)."""
    from property_core import fetch_listing
    return fetch_listing(property_url_or_id).model_dump()


@mcp.tool()
async def property_blocks(
    postcode: str,
    search_level: str = "sector",
    months: int = 24,
) -> dict:
    """Block-buy analysis — identify buildings with multiple flat sales."""
    from property_core import analyze_blocks
    return (await analyze_blocks(
        postcode=postcode,
        search_level=search_level,
        months=months,
    )).model_dump()


@mcp.tool()
def company_search(name: str) -> dict:
    """Search Companies House for a company by name."""
    from property_core import CompaniesHouseClient
    result = CompaniesHouseClient().search(name)
    return result.model_dump() if result else {"items": []}


@mcp.tool()
async def property_report(
    address: str,
    postcode: str,
    months: int = 24,
) -> dict:
    """Full property data pull — comps + EPC + yield + market in one call.

    Requires both a street address and postcode, e.g. address='10 Downing Street',
    postcode='SW1A 2AA'.
    """
    from property_core.report_service import PropertyReportService
    result = await PropertyReportService().generate_report(
        address_query=f"{address}, {postcode}",
        ppd_months=months,
    )
    return result.model_dump()


@mcp.tool()
def planning_search(postcode: str) -> dict:
    """Find the council planning portal URL for a postcode."""
    from property_core import PlanningService
    return PlanningService().search(postcode)


@mcp.tool()
def ppd_transactions(
    postcode: str,
    limit: int = 10,
    property_type: str | None = None,
) -> dict:
    """Raw Land Registry Price Paid transactions for a postcode."""
    from property_core import PPDService
    return PPDService().search_transactions(
        postcode=postcode,
        postcode_prefix=None,
        limit=limit,
        property_type=property_type,
    )


_http_app = create_streamable_http_app(
    mcp,
    streamable_http_path="/mcp",
    json_response=True,
    stateless_http=True,
)


def build_asgi_app():
    """Streamable-HTTP ASGI app for MCPMiddleware in app/main.py."""
    return _http_app
