# CLAUDE.md

@GUIDELINES.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Property Shared is a FastAPI service + pure-Python core library for UK property data. It integrates:
- **PPD** (Price Paid Data) - Land Registry transactions via SPARQL/Linked Data API
- **EPC** - Energy Performance Certificates (requires API credentials)
- **Rightmove** - Property listings via scraping with built-in politeness
- **Zoopla** - Search-result listings via headless Playwright (search only — listing detail pages blocked by Cloudflare Turnstile; requires `[planning]` extra)
- **PrimeLocation** - Search-result listings + listing detail via `curl_cffi` (TLS impersonation defeats Cloudflare). ZPG sibling of Zoopla: shares the `lid.zoocdn.com` CDN, ld+json `RealEstateListing`/`BreadcrumbList`, and `NtsInfo` tenure/council-tax block, but ships an older front-end template (`ListingsSearchResultsCard_styles_*` / `NtsInfo_styles_*` class names)
- **OnTheMarket** - Search-result listings + listing detail via plain `requests`; tenure / lease / EPC / council tax extracted from the Key information section
- **Planning** - UK council planning applications via vision-guided browser automation (99 verified councils)
- **Stamp Duty** - SDLT calculator with April 2025 bands, additional property/FTB/non-resident surcharges
- **Block Analyzer** - Groups PPD transactions by building to find bulk-buy opportunities
- **Companies House** - Company search and lookup via free API (requires API key)

## Commands

```bash
# Install dependencies (with dev extras)
uv sync --extra dev

# Run API server
uv run --env-file .env property-api              # production mode
uv run --env-file .env uvicorn app.main:app --reload  # dev mode with reload

# Run CLI (core mode - no server needed)
uv run --extra cli property-cli meta
uv run --extra cli property-cli ppd comps "SW1A 1AA" --months 24
uv run --extra cli property-cli rightmove search-url "SW1A 1AA"
uv run --extra cli property-cli primelocation search-url "SW1A 1AA"
uv run --extra cli property-cli primelocation listings "https://www.primelocation.com/for-sale/property/london/"
uv run --extra cli property-cli calc stamp-duty 300000
uv run --extra cli property-cli ppd blocks "B1 1AA"
uv run --extra cli property-cli companies search "Tesco"
uv run --extra cli property-cli analysis yield "NG1 1AA"
uv run --extra cli property-cli analysis rental "NG1 1AA"

# CLI targeting running API (add --api-url)
uv run --extra cli property-cli ppd comps "SW1A 1AA" --api-url http://localhost:8000

# Tests
uv run --extra dev --extra apps pytest                        # unit tests (mocked)
RUN_LIVE_TESTS=1 uv run --extra dev --extra apps pytest       # live network tests

# Single test
uv run --extra dev --extra apps pytest tests/test_ppd_service_live.py -v
```

## Deployment — Coolify (two apps, one repo)

This repo deploys to a self-hosted [Coolify](https://coolify.io/)
instance. Two Coolify Applications point at the same Git repo, each
using a different Dockerfile:

| App | Dockerfile | Port | Health | What it is |
|-----|-----------|------|--------|------------|
| `property-shared` | `Dockerfile` | 8080 | `/v1/health` | FastAPI REST API — core data layer |
| `propertydata` | `Dockerfile.app` | 8080 | `/health` | FastMCP MCP server — tools + Prefab dashboards |

`property-shared` exposes both the REST API (`/v1/`) and a plain MCP
server (`/mcp`) — works in any MCP client. `propertydata` is the MCP
app with Prefab UI dashboards — claude.ai only.

Auto-deploy is via Coolify webhook on push to `main` (no GitHub Actions
deploy job). The MCP service requires `MCP_TRANSPORT=http` env var or
the container won't open a TCP listener — see `docs/coolify-deploy.md`
for the full step-by-step.

**CI (release.yml)** — runs only on `release: published` and only
publishes to PyPI. Deploys are handled by Coolify directly.

## Architecture

```
property_core/              # Pure Python library (no FastAPI, no DB assumptions)
├── models/                 # Domain Pydantic models
│   ├── ppd.py              # PPDTransaction, PPDCompsResponse, SubjectProperty, etc.
│   ├── epc.py              # EPCData
│   ├── rightmove.py        # RightmoveListing, RightmoveListingDetail
│   ├── report.py           # PropertyReport, YieldAnalysis, RentalAnalysis, etc.
│   ├── block.py            # BlockUnit, BlockBuilding, BlockAnalysisResponse
│   └── companies_house.py  # CompanyRecord, CompanySearchResult, CompanyOfficer
├── ppd_client.py           # Transport: Land Registry SPARQL + Linked Data API → typed models
├── epc_client.py           # Transport: EPC registry (async) → typed EPCData models
├── rightmove_scraper.py    # Transport: listings scraper (sync) → typed Pydantic models
├── rightmove_location.py   # Transport: search URL builder (sync)
├── onthemarket_scraper.py  # Transport: OnTheMarket scraper (sync, requests-based) → typed models
├── onthemarket_location.py # Transport: OnTheMarket search URL builder (sync)
├── zoopla_scraper.py       # Transport: Zoopla search scraper (sync, Playwright) → typed models
├── zoopla_location.py      # Transport: Zoopla search URL builder (sync)
├── primelocation_scraper.py  # Transport: PrimeLocation search + detail scraper (sync, curl_cffi) → typed models
├── primelocation_location.py # Transport: PrimeLocation search URL builder (sync)
├── postcode_client.py      # Transport: postcodes.io → typed PostcodeResult model
├── companies_house_client.py # Transport: Companies House API (sync httpx, basic auth)
├── ppd_service.py          # Domain service: PPD comps, search, stats (sync)
├── planning_service.py     # Domain service: council matching + URL building (sync)
├── report_service.py       # Orchestrator: multi-source aggregation (async)
├── rental_service.py       # Rental analysis with optional yield calculation (async)
├── yield_service.py        # Yield analysis: PPD sales + Rightmove rentals → YieldAnalysis
├── interpret.py            # Opt-in interpretation helpers (classify_yield, generate_insights, etc.)
├── enrichment.py           # EPC enrichment pipeline + compute_enriched_stats()
├── address_matching.py     # Fuzzy address matching for EPC enrichment
├── stamp_duty.py           # SDLT calculator: April 2025 bands, surcharges, FTB relief
├── block_service.py        # Block analyzer: groups PPD transactions by building
├── planning_scraper.py     # Vision-guided planning portal scraper (Playwright + OpenAI)
└── planning_councils.json  # Verified council database (99 councils, 6 system types)

app/                        # FastAPI service (thin HTTP wrapper)
├── api/v1/                 # Versioned routers (import directly from property_core)
├── mcp/server.py           # Plain FastMCP server — tools only, no Prefab UI (property-shared.fly.dev/mcp)
├── schemas/                # API envelope models (convenience re-exports from core)
├── core/config.py          # Settings via pydantic-settings (reads .env)
└── web/                    # Demo UI at /demo

property_cli/               # Typer CLI (imports only from property_core)
└── main.py                 # All commands; --api-url switches to HTTP mode

property_app/               # FastMCP MCP app server (4th consumer of property_core)
├── server.py               # FastMCP tools + Prefab dashboards entry point
├── tools.py                # @mcp.tool() definitions
└── dashboards/             # Prefab UI dashboard views
```

**Three-layer separation**:
- Transport clients (HTTP/SPARQL → typed Pydantic models)
- Domain services (orchestration → typed Pydantic models)
- Consumer layers: API, MCP app, CLI (all import directly from property_core)

**Data flow**: Consumer → Core service (domain logic) → Core client (network)

## Key Patterns

- **Dual-mode CLI**: Commands call `property_core` directly by default (fast, offline-capable). Add `--api-url` to route through the HTTP API instead.
- **Domain service guardrails**: `property_core/ppd_service.py` enforces limits (MAX_LIMIT=200, FORM_MAX_LIMIT=50) and normalizes responses. API routers are thin wrappers.
- **`raw` field pattern**: Transport-parsed models carry a `raw: dict | None` field (PPDTransaction, EPCData, RightmoveListing, CompanyRecord, PostcodeResult). Report, block, planning, and aggregate models do NOT have raw fields.
- **Core returns data, consumers interpret**: `property_core` services return raw numbers only (yield %, counts, price differences). Interpretation labels ("strong"/"weak", "good"/"insufficient", key insights) are generated by consumers using opt-in helpers from `property_core.interpret` — `classify_yield()`, `classify_data_quality()`, `classify_price_position()`, `estimate_value_range()`, `generate_insights()`.
- **Configurable defaults**: Parameters have defaults but are overridable — `auto_escalate=False`, `additional_property=False`, `filter_outliers=True`, `property_type="F"`, `thin_market_threshold=5`.
- **Area stats**: `PPDCompsResponse` includes `percentile_25`, `percentile_75` for price quartiles. When an address is provided and found, also includes `subject_price_percentile` (0-100) and `subject_vs_median_pct` (e.g., +10.8 means 10.8% above median).
- **EPC enrichment**: PPD comps can be enriched with EPC floor area via `enrich_epc=true` on the comps endpoint (or `--enrich-epc` in CLI). Groups comps by postcode, fetches all EPC certs per postcode (one API call each), fuzzy-matches addresses, and attaches derived fields (`epc_floor_area_sqm`, `price_per_sqft`, `epc_rating`, etc.) plus the full matched cert (`epc_match`) and confidence score (`epc_match_score`). After enrichment, call `compute_enriched_stats()` to populate `median_price_per_sqft` and `epc_match_rate`.
- **Standalone rental analysis**: `analyze_rentals(postcode, purchase_price=N, filter_outliers=True)` returns rental market stats (median/average rent, listing count) with optional gross yield calculation. Rental range uses IQR-based outlier filtering by default.
- **Station distance rounding**: `fetch_listing()` rounds station distances to 1 decimal place (e.g., "1.9 miles" not "1.8666295329683218 miles").
- **Live test gating**: Tests making real network calls check `RUN_LIVE_TESTS=1` and skip gracefully on 503 or missing credentials.

## Environment Variables

Copy `.env.example` to `.env`. Key variables:
- `EPC_API_EMAIL` / `EPC_API_KEY` - Required for EPC endpoints
- `RIGHTMOVE_DELAY_SECONDS` - Rate limit delay (default 0.6s)
- `OPENAI_API_KEY` - Required for planning scraper (vision extraction)
- `PLAYWRIGHT_PROXY_URL` - Optional residential proxy for planning scraper (councils block datacenter IPs)
- `COMPANIES_HOUSE_API_KEY` - Required for Companies House endpoints (free key from https://developer.company-information.service.gov.uk/)
- `COMPANIES_HOUSE_SANDBOX` - Set to `true` to use sandbox API (default `false`)
- `ZOOPLA_ENABLED` / `ZOOPLA_PROXY_URL` - Toggle Zoopla scraping (default enabled) and route it through a residential proxy on Cloudflare-blocked egress IPs
- `PRIMELOCATION_ENABLED` / `PRIMELOCATION_PROXY_URL` - Same toggle/proxy pair for PrimeLocation (a ZPG sibling behind the same Cloudflare gate; default enabled)

## Using as a Library

Install from PyPI: `pip install property-shared`

```python
# Services and functions
from property_core import PPDService, PlanningService, PropertyReportService
from property_core import PricePaidDataClient, EPCClient, RightmoveLocationAPI, PostcodeClient
from property_core import CompaniesHouseClient, analyze_blocks
from property_core import fetch_listings, fetch_listing, analyze_rentals
from property_core import PrimeLocationLocationAPI, fetch_primelocation_listings, fetch_primelocation_listing
from property_core import calculate_yield, calculate_stamp_duty
from property_core import enrich_comps_with_epc, compute_enriched_stats

# Models (available at top level since v1.2)
from property_core import (
    PPDTransaction, PPDCompsResponse, PPDTransactionRecord,
    EPCData, RightmoveListing, RightmoveListingDetail,
    PropertyReport, YieldAnalysis, RentalAnalysis,
    BlockAnalysisResponse, CompanyRecord, StampDutyResult,
)

# Planning scraper (requires playwright, openai)
from property_core.planning_scraper import scrape_planning_application, search_planning_by_postcode
```

## MCP Servers

Two MCP servers, both in this repo:

| Server | File | URL | Clients |
|--------|------|-----|---------|
| Plain tools | `app/mcp/server.py` | `property-shared.fly.dev/mcp` | Any MCP client |
| MCP app + dashboards | `property_app/server.py` | `propertydata.fly.dev/mcp` | claude.ai only |

Local dev: `.mcp.json` includes `property-shared-local` pointing to `http://localhost:8000/mcp` (the plain server, running via `property-api`).
