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
    additional_property: bool = True,
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
    ] = True,
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
    )


def lookup_zoopla_listing(property_id: str) -> dict:
    """Raw Zoopla listing detail — returns dict."""
    import os
    from property_core import fetch_zoopla_listing

    proxy = (os.environ.get("ZOOPLA_PROXY_URL") or "").strip() or None
    listing = fetch_zoopla_listing(property_id, proxy=proxy)
    return _slim(listing.model_dump(mode="json"))


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
    )

    listings = fetch_onthemarket_listings(search_url, max_pages=1)
    prices = [l.price for l in listings if l.price and l.price > 0]
    median_price = int(stat_median(prices)) if prices else None

    return {
        "search_url": search_url,
        "count": len(listings),
        "listings": [_slim(l.model_dump(mode="json")) for l in listings],
        "median_price": median_price,
    }


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
    )


def lookup_onthemarket_listing(property_id: str) -> dict:
    """Raw OnTheMarket listing detail — returns dict."""
    from property_core import fetch_onthemarket_listing

    listing = fetch_onthemarket_listing(property_id)
    return _slim(listing.model_dump(mode="json"))


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
    rating, hero images, and the verbatim Key information block.
    """
    return lookup_onthemarket_listing(property_id)


# ---------------------------------------------------------------------------
# Component Test (temporary — maps what claude.ai renderer supports)
# ---------------------------------------------------------------------------


def _public_url() -> str:
    """Return the public origin of this MCP service (no trailing slash).

    Read from the ``MCP_PUBLIC_URL`` env var, set per-deployment in
    Coolify (or any other host). Falls back to localhost so dev runs
    don't require any env config.
    """
    import os
    raw = (os.environ.get("MCP_PUBLIC_URL") or "http://localhost:8080").rstrip("/")
    return raw


def _component_test_config():
    from fastmcp.apps import PrefabAppConfig, ResourceCSP
    return PrefabAppConfig(csp=ResourceCSP(resource_domains=[_public_url()]))


@mcp.tool(
    app=_component_test_config(),
    annotations={"readOnlyHint": True},
    tags={"test"},
)
def component_test():
    """Render a sampler of Prefab components to test claude.ai renderer support."""
    from fastmcp.tools import ToolResult
    from prefab_ui.components import (
        Badge,
        Card,
        CardContent,
        Column,
        Dot,
        ForEach,
        Grid,
        Heading,
        Image,
        Metric,
        Muted,
        Progress,
        Ring,
        Row,
        Separator,
        Tabs,
        Tab,
        Text,
    )
    from prefab_ui.components.charts import BarChart, ChartSeries

    sample_chart_data = [
        {"label": "Q1", "value": 42},
        {"label": "Q2", "value": 58},
        {"label": "Q3", "value": 35},
        {"label": "Q4", "value": 71},
    ]

    view = Column(
        children=[
            Heading("Component Test", level=2),
            Muted("Each section tests a component. If a section is missing, that component crashed the renderer."),
            Separator(),

            # Section 1: Badge
            Heading("1. Badge", level=3),
            Row(children=[
                Badge(label="Flat", variant="default"),
                Badge(label="Strong", variant="success"),
                Badge(label="Weak", variant="destructive"),
                Badge(label="Pending", variant="outline"),
            ], gap=2),
            Separator(),

            # Section 2: Progress + Ring + Dot
            Heading("2. Progress / Ring / Dot", level=3),
            Progress(value=65),
            Row(children=[
                Ring(value=75, size="lg"),
                Dot(variant="success"),
                Dot(variant="destructive"),
                Dot(variant="warning"),
            ], gap=4),
            Separator(),

            # Section 3: BarChart
            Heading("3. BarChart", level=3),
            BarChart(
                data=sample_chart_data,
                series=[ChartSeries(dataKey="value", label="Revenue")],
                xAxis="label",
            ),
            Separator(),

            # Section 4: Tabs
            Heading("4. Tabs", level=3),
            Tabs(children=[
                Tab(title="Overview", children=[
                    Text("This is the overview tab content"),
                ]),
                Tab(title="Details", children=[
                    Text("This is the details tab content"),
                ]),
            ]),
            Separator(),

            # Section 5: ForEach
            Heading("5. ForEach", level=3),
            ForEach(
                key="test_items",
                children=[
                    Row(children=[
                        Text("{{ $item.name }}"),
                        Badge(label="{{ $item.price }}"),
                    ], gap=2),
                ],
            ),
            Separator(),

            # Section 6: Metric with formatted values
            Heading("6. Metric Grid", level=3),
            Grid(columns=3, children=[
                Metric(label="Count", value="50"),
                Metric(label="Median", value="£150,000"),
                Metric(label="Yield", value="6.9%"),
            ]),
            Separator(),

            # Section 7: Card layout
            Heading("7. Cards", level=3),
            Grid(columns=2, children=[
                Card(children=[CardContent(children=[
                    Heading("Sales", level=4),
                    Metric(label="Median", value="£150,000"),
                    Metric(label="Count", value="50"),
                ])]),
                Card(children=[CardContent(children=[
                    Heading("Rentals", level=4),
                    Metric(label="Median/mo", value="£866"),
                    Metric(label="Count", value="25"),
                ])]),
            ]),
            Separator(),

            # Section 8: Image (data URI — no CSP needed)
            Heading("8. Image (data URI)", level=3),
            Image(
                src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAGQAAABkCAYAAABw4pVUAAAACXBIWXMAAAsTAAALEwEAmpwYAAABIklEQVR4nO3SQQ0AIAwAQfj3jAdWuECSzbkB+tme2Z0f2fkdwH8MiRkSMyRmSMyQmCExQ2KGxAyJGRIzJGZIzJCYITFDYobEDIkZEjMkZkjMkJghMUNihsQMiRkSMyRmSMyQmCExQ2KGxAyJGRIzJGZIzJCYITFDYobEDIkZEjMkZkjMkJghMUNihsQMiRkSMyRmSMyQmCExQ2KGxAyJGRIzJGZIzJCYITFDYobEDIkZEjMkZkjMkJghMUNihsQMiRkSMyRmSMyQmCExQ2KGxAyJGRIzJGZIzJCYITFDYobEDIkZEjMkZkjMkJghMUNihsQMiRkSMyRmSMyQmCExQ2KGxAyJGRIzJGZIzJCYITFDYobEDIn9AC9RBGTuOhELAAAAAElFTkSuQmCC",
                alt="100x100 test square",
                height="100px",
            ),

            Separator(),

            # Section 9: Image (proxied external URL)
            Heading("9. Image (proxied URL)", level=3),
            Image(
                src=(
                    f"{_public_url()}/img?url=https%3A%2F%2Fmedia.rightmove.co.uk"
                    "%3A443%2Fdir%2Fcrop%2F10%3A9-16%3A9%2Fproperty-photo"
                    "%2Fb17d74096%2F174125315%2Fb17d74096dbcccb8c49b510d19b48625_max_476x317.jpeg"
                ),
                alt="Proxied Rightmove image",
                height="200px",
            ),
        ],
        gap=4,
    )

    from prefab_ui.app import PrefabApp

    app_view = PrefabApp(
        state={
            "test_items": [
                {"name": "Item A", "price": 100},
                {"name": "Item B", "price": 200},
                {"name": "Item C", "price": 300},
            ],
        },
        view=view,
    )

    return ToolResult(
        content="Component test: Badge, Progress, Ring, Dot, BarChart, Tabs, ForEach, Metric, Card, Image",
        structured_content=app_view,
    )


@mcp.tool(
    annotations={"readOnlyHint": True},
    tags={"test"},
)
def image_test():
    """Test returning an image as a FastMCP ImageContent block."""
    import httpx
    from fastmcp.utilities.types import Image as McpImage

    resp = httpx.get(
        "https://media.rightmove.co.uk:443/dir/crop/10:9-16:9/"
        "property-photo/b17d74096/174125315/"
        "b17d74096dbcccb8c49b510d19b48625_max_476x317.jpeg",
        timeout=10.0,
    )
    return [
        "Here is a Rightmove property photo:",
        McpImage(data=resp.content, format="jpeg"),
    ]
