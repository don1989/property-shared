# Changelog

## v1.14.0 (2026-05-21)

### New data source: NewHomesForSale.co.uk

UK new-build developments aggregator covering ~2,600 active
developments — including developer-direct stock that often doesn't
reach Rightmove / OnTheMarket / Zoopla on listing day. Closes the
"new-builds I'm missing" gap that the v1.13.0 ``new_build`` filter on
the portal builders couldn't address.

- **New module**: ``property_core.newhomesforsale_scraper`` exposes
  ``fetch_listings(search_url)`` and ``fetch_listing(url)``. Plain
  ``requests`` + BeautifulSoup; no Cloudflare gating; no JS.
- **New URL builder**: ``NewHomesForSaleLocationAPI.build_search_url(
  county=..., town=...)`` produces the slug-based search URL.
- **New model**: ``NewHomesForSaleDevelopment`` (search-card record
  with id, name, URL, developer, address, postcode, locality, region,
  bedroom range, property type, price range, distance-from-search,
  hero image, photo count). NHFS detail pages are sparse (mostly
  enquiry forms) — the search card is the primary record.

### Surface

- **API**: ``GET /v1/newhomesforsale/search-url``,
  ``GET /v1/newhomesforsale/listings``, ``GET /v1/newhomesforsale/listing``.
- **CLI**: ``property-cli newhomesforsale search-url|listings|listing``.
- **MCP** (both plain server and ``propertydata`` MCP App):
  ``newhomesforsale_search``, ``newhomesforsale_listing``.

### Usage

```bash
# Browse all new-build developments in a county
property-cli newhomesforsale search-url Hertfordshire

# Narrow to a town
property-cli newhomesforsale search-url Hertfordshire --town Hitchin

# Fetch and pretty-print the results
property-cli newhomesforsale listings \
  'https://www.newhomesforsale.co.uk/new-homes/hertfordshire/hitchin/'
```

## v1.13.0 (2026-05-21)

Merge from upstream `paulieb89/property-shared` — pulls in the MCP
primitive refactor, EPC browse tooling, response-shape quality fixes,
and several core service bug fixes. All Zoopla / OnTheMarket / station
search work added since the fork is preserved and extended with the
same response-shape patterns.

### Added — MCP Prompts (workflow primitives, not tools)
- `full_property_analysis` — comprehensive single-property analysis.
  Replaces the removed `property_report` / `get_property_data`
  composition tools by instructing the LLM to call the underlying
  primitives explicitly so every input is visible in the conversation.
- `area_comparison` — multi-postcode investment comparison workflow.
- `investment_analysis` — single-property buy-to-let evaluation.

### Added — MCP Resources (queryable reference data)
- `councils://list` — full 99-council planning portal registry.
- `council://{code}` — single-council profile lookup.
- `sdlt-bands://current` — April 2025 SDLT schedule with surcharges.
- `epc-ratings://reference` — A–G band definitions + 2025 rental floor.

### Added — Tools
- `property_epc_search(postcode)` — browse EPC certs at a postcode as
  a slim list. Designed for Rightmove listings that omit the house
  number.
- `epc_certificate(lmk_key)` — direct cert lookup by lmk_key,
  bypasses fuzzy address matching.
- `RightmoveListingDetail.floor_area_sqm` / `floor_area_sqft` —
  numeric floor area extracted from the `sizings` array for
  cross-referencing against EPC.
- Multimodal image support on `rightmove_listing(include_images=True)`.

### Changed — MCP response quality
- New `_slim()` helper strips `raw` / `images` / `floorplans` /
  `epc_match` recursively. Combined with `exclude_none=True`, cuts
  PPD comps token cost ~40% (232 → 140 tokens per transaction).
- Pattern applied across MCP tools including Zoopla, OnTheMarket,
  PPD, blocks, yield, rental, report.
- Full docstring on `property_yield` (37 → 877 chars) so the LLM
  knows what every param does and what shape comes back.
- `auto_escalate` exposed on `/v1/analysis/yield` and
  `/v1/analysis/rental` REST endpoints (was core-only).
- `propertydata` MCP App reaches parity with plain MCP —
  `property_blocks`, `ppd_transactions` now available there too.
- Renamed `get_epc_certificate` → `epc_certificate` on plain MCP
  for cross-surface naming consistency.

### Removed (breaking for MCP tool consumers only)
- `property_report` tool — use the `full_property_analysis` prompt.
- `get_property_data` tool — same.
- `component_test`, `image_test` dev utilities from `propertydata`.

### Fixed (core service bugs)
- `address_matching.extract_number` — strips `FLAT N,` /
  `APARTMENT N,` / `UNIT N,` prefixes before number extraction so
  flat certs stop scoring near-zero against no-house-number targets.
- `address_matching.extract_street` — takes 3 words instead of 2 so
  "Cavendish Crescent North" no longer collides with "South".
- `address_matching.match_epc_address` — raises minimum threshold
  30 → 50 when target has no house number.
- `calculate_yield` — rental radius now escalates 0.5mi → 1mi →
  1.5mi → 2mi when no listings found; new
  `rental_search_radius_miles` field on `YieldAnalysis` surfaces
  which radius produced the data.
- `PPDService.comps()` now defaults to residential-only
  (`transaction_category="A"`, `property_type` restricted to F+D+S+T)
  for parity with the production MCP defaults. New `"ALL"` sentinel
  on `property_type` for the unfiltered firehose. `filter_outliers`
  param added (default `False`).
- REST `/v1/ppd/comps` now defaults `auto_escalate=true` for parity
  with both MCP servers.

### Preserved
All Zoopla / OnTheMarket scrapers, station search anchors, OTM
travel-duration filter, OnTheMarketLocationNotFound exception,
Coolify deploy guide, and the `ZOOPLA_ENABLED` / `ZOOPLA_PROXY_URL`
env-gating from v1.11.x are unchanged.

## v1.11.1 (2026-05-10)

### Operational
- **`ZOOPLA_ENABLED` env var** (default `true`): when set to `false` on a
  deployment, `/v1/zoopla/*` endpoints return `503` with a clear message
  and both MCP servers stop advertising `zoopla_search` /
  `zoopla_listing` tools. Local CLI / library use is unchanged.
- **`ZOOPLA_PROXY_URL` env var**: routes Zoopla calls through a
  residential proxy when set. Plumbed through API + both MCP servers.
- **Profile rotation in `zoopla_scraper`**: `fetch_listing` and
  `fetch_listings` now try the caller's profile first, then fall through
  `_FALLBACK_PROFILES = (chrome120, safari17_2_ios, firefox133, chrome116)`.
  When all profiles fail the raised `ZooplaError` lists each failure for
  fast triage. New `fallback_profiles=()` kwarg opts out.
- **Coolify guide updated** to set `ZOOPLA_ENABLED=false` by default —
  Cloudflare on zoopla.co.uk gates many datacenter ASNs (Hetzner /
  Vultr / OVH) regardless of TLS fingerprint, so a residential proxy
  is required for hosted Zoopla.

### Background
The v1.11.0 curl_cffi switch defeats CF's TLS fingerprint detection but
not its IP-reputation gate. From a clean residential IP everything
works (verified against zoopla.co.uk live). From flagged datacenter IPs
all four impersonation profiles get a 403, hence the env-gating.

## v1.11.0 (2026-05-10)

### New Features
- **Zoopla listing detail** (`fetch_zoopla_listing(url_or_id)`) is now
  implemented. Returns a `ZooplaListingDetail` with price, address,
  postcode, bedrooms, bathrooms, floor area / sqft, tenure, council tax
  band, agent name + branch id, listing status, listing condition,
  furnished state, chain-free flag, EPC / floorplan flags, breadcrumbs,
  date posted, image gallery, and the verbatim "Need to see info" rows.
- **Zoopla search now works without a browser.** The scraper switched
  from headless Playwright to `curl_cffi` (libcurl-impersonate replays
  a real Chrome TLS handshake). `pip install 'property-shared[planning]'`
  is no longer required for Zoopla — `curl_cffi` is a base dependency.
  Search and detail pages both come back with a clean `200`, ~10× faster
  than the Playwright path and with no chromium install.
- **API**: new `/v1/zoopla/listing/{id}` endpoint.
- **MCP** (both servers): new `zoopla_listing` tool.
- **CLI**: new `property-cli zoopla listing <id|url>` command.

### Deployment
- Hosting moved from Fly.io to Coolify (self-hosted). New guide at
  `docs/coolify-deploy.md`. The Fly deploy jobs in
  `.github/workflows/release.yml` were removed; only the PyPI publish
  step remains. Auto-deploy is now via Coolify webhook on push to `main`.
- `fly.toml` and `fly.app.toml` deleted. All `*.fly.dev` URLs in
  README / USER_GUIDE / LAUNCHGUIDE / property_app docs replaced with
  `https://<your-mcp-domain>` placeholders.
- New `MCP_PUBLIC_URL` env var on the MCP service. Used as the Prefab
  CSP allowlist domain and as the base for the `/img` proxy URLs in
  the `component_test` tool. Defaults to `http://localhost:8080` so
  local dev runs without configuration.

### Internals
- New extracted JSON state path: parses the `ListingAnalyticsTaxonomy`
  object out of one of the `self.__next_f.push([...])` RSC chunks on
  the listing-detail page for branch / furnished / has_epc / has_floorplan
  / chain_free / listing_condition data that's not in the ld+json block.
- New `ZooplaListingDetail` Pydantic model + 1 unit test asserting on
  every parsed field against the captured fixture.

## v1.10.0 (2026-05-09)

### New Features
- **OnTheMarket scraper** (search + listing detail). New transport
  `property_core/onthemarket_scraper.py` with `fetch_listings(search_url)`
  and `fetch_listing(url_or_id)`. Plain `requests` + BeautifulSoup; cards
  parsed via Schema.org microdata, detail pages via `dataLayer.push({...})`
  and a `<h2>Key information</h2>` section (tenure, lease years,
  ground rent, service charge, council tax band, EPC rating).
- **Zoopla scraper** (search only). New transport
  `property_core/zoopla_scraper.py` with `fetch_listings(search_url)`
  via headless Playwright. Listing detail pages are gated behind a
  Cloudflare Turnstile interstitial that does not auto-resolve in
  headless Chromium, so `fetch_listing()` is intentionally not provided.
  Requires the `planning` extra (`pip install 'property-shared[planning]'`
  + `playwright install chromium`).
- **URL builders**: `OnTheMarketLocationAPI` and `ZooplaLocationAPI` —
  pure-string builders that turn a postcode/area name + filters into
  search URLs.
- **Models**: `OnTheMarketListing`, `OnTheMarketListingDetail`, `ZooplaListing`
  in `property_core.models`.
- **Consumers wired up**:
  - **API**: `/v1/zoopla/search-url`, `/v1/zoopla/listings`,
    `/v1/onthemarket/search-url`, `/v1/onthemarket/listings`,
    `/v1/onthemarket/listing/{id}`.
  - **MCP** (both `app/mcp/server.py` and `property_app/tools.py`):
    `zoopla_search`, `onthemarket_search`, `onthemarket_listing`.
  - **CLI**: `property-cli zoopla search-url|listings`,
    `property-cli onthemarket search-url|listings|listing`.
- **Discovery report** committed at `docs/zoopla-onthemarket-discovery.md`
  documenting verbatim field provenance, blocking constraints, and
  selector strategy.
## v1.12.0 (2026-05-17, upstream)

### Added
- `property_epc_search(postcode)` — browse all EPC certificates at a postcode as a slim list (address, rating, floor\_area, property\_type, floor\_level, habitable\_rooms, inspection\_date, lmk\_key). Designed for Rightmove listings where the house number is not shown.
- `epc_certificate(lmk_key)` — direct EPC certificate lookup by lmk\_key, faster than address-based lookup as it skips fuzzy matching. Available on both MCP servers (`property-shared.fly.dev/mcp` and `propertydata.fly.dev/mcp`).
- `RightmoveListingDetail.floor_area_sqm` / `floor_area_sqft` — numeric floor area extracted from the Rightmove `sizings` array. Key discriminator for EPC cross-referencing without address matching.

### Fixed
- `address_matching.extract_number` — now strips `FLAT N,` / `APARTMENT N,` / `UNIT N,` prefixes before extracting the building number, preventing flat EPC certs from scoring near-zero against no-house-number targets.
- `address_matching.extract_street` — now takes 3 words instead of 2, including directional qualifiers (North, South, East, West). Eliminates wrong-street false positives (e.g. "Cavendish Crescent North" vs "Cavendish Crescent South" previously both mapped to "cavendish crescent").
- `address_matching.match_epc_address` — raises minimum match threshold from 30 → 50 when the target address has no house number, since word-overlap alone is insufficient to discriminate between properties on the same street.

## v1.11.0 (2026-05-12, upstream)

### Breaking Changes
- Removed `property_report` MCP tool from `property-shared.fly.dev/mcp` and from `propertydata.fly.dev/mcp`. Also removed `get_property_data` from `propertydata.fly.dev/mcp`. Both were multi-source composition tools that hid which input produced which output and were prone to data-quality bugs (e.g. the v1.10.x yield calc was silently dividing current rent by a historical sale price).
- Replaced by a `full_property_analysis` MCP **prompt** on both servers. The prompt instructs the LLM to call the underlying primitive tools (`property_comps`/`search_comps`, `property_yield`/`get_yield`, `property_epc`/`epc_lookup`, `rightmove_search`) explicitly and synthesise. Every input is now visible in the LLM's working text.
- REST `POST /v1/property/report` and CLI `property-cli report generate` are unchanged — they call `PropertyReportService` directly without going through MCP.
- Downstream consumers (`uk-property-mcp`, `property-descriptions-mcp`): if they exposed `property_report` as a tool, that registration needs to be removed on their next release.

### Added — MCP Resources (non-breaking)
- `councils://list` — full UK planning portal registry (99 councils) as a queryable resource. LLMs can read this once instead of repeatedly calling `planning_search` for individual lookups.
- `council://{code}` — single-council profile by code/slug.
- `sdlt-bands://current` — April 2025 UK Stamp Duty Land Tax band schedule, including additional-property + non-resident surcharges and first-time buyer relief. LLMs can cite the bands directly without forcing a `stamp_duty` calculator call.
- `epc-ratings://reference` — A–G EPC band definitions, SAP score ranges, and regulatory context (April 2025 rental minimum of band C). Grounds LLM EPC explanations in canonical data rather than training-data recall.

### Removed — dev utilities
- `component_test` and `image_test` MCP tools removed from `propertydata.fly.dev/mcp`. These were internal dev artifacts that polluted the production tool selection surface.

### Added — MCP Prompts (non-breaking)
- `full_property_analysis` — replaces the removed `property_report` / `get_property_data` tools.
- `area_comparison` — multi-postcode comparison workflow (compares 2-3 postcodes on price, yield, market depth).
- `investment_analysis` — single-property buy-to-let evaluation (yield, SDLT, EPC compliance, key risks).

## v1.10.0 (2026-05-12, upstream)

### Breaking Changes
- REST API `/v1/ppd/comps` now defaults `auto_escalate=true`. Previously the REST API was the odd one out —  All three interfaces now behave identically: thin markets auto-widen from postcode→sector→district, with the `escalated_from`/`escalated_to` fields in the response indicating any widening that occurred. Pass `auto_escalate=false` to opt out.
- `PPDService.comps()` now defaults `transaction_category="A"` (standard residential sales). Category-B rows (bulk transfers, non-standard conveyances) are excluded unless callers explicitly opt back in via `transaction_category=None`. This fixes data-parity with the production `prop` MCP server.
- `PPDService.comps()` `property_type=None` no longer means "no filter" — it now restricts results to the residential set (F+D+S+T). Pass the new sentinel `property_type="ALL"` for the unfiltered Land Registry firehose (including commercial/other). Specific codes (`"F"`/`"D"`/`"S"`/`"T"`/`"O"`) continue to filter to a single type.
- `PPDService.comps()` now accepts `filter_outliers: bool = False`. When set to `True`, a 1.5×IQR filter is applied to prices — outliers are dropped from BOTH the computed stats and the returned `transactions` list, so the response is internally consistent. Needs ≥4 prices, otherwise no-op.
- The three new defaults and the `"ALL"` sentinel are exposed across all consumer interfaces — REST `/v1/ppd/comps`, MCP `property_comps`, MCP app `search_comps`/`comps_dashboard`, and CLI `property-cli ppd comps` (with `--transaction-category`, `--property-type`, `--filter-outliers`/`--no-filter-outliers`). CLI accepts `--transaction-category all` as the firehose escape hatch.

## v1.4.0 (2026-03-28)

### New Features
- **`property_type` filter on yield and report** — `calculate_yield()`, `generate_report()`, and all consumers (MCP `property_yield`/`property_report`, API `/v1/analysis/yield`/`/v1/property/report`, CLI `analysis yield`/`report generate`) now accept `property_type` (F/D/S/T) to filter comparable sales. Prevents skewed figures in mixed-stock areas.
- **`sort_by` on Rightmove search** — `build_search_url()` and all consumers (MCP `rightmove_search`, API `/v1/rightmove/search-url`, CLI `rightmove search-url`) now accept `sort_by`: `newest`, `oldest`, `price_low`, `price_high`, `most_reduced`.

### Fixed
- MCP tool descriptions no longer imply analytical inference — "deal analysis" → "data pull", "yield estimate" → "yield calculation", dropped "market assessment" and "refurb potential"
- `rightmove_listing` MCP tool docstring now shows both URL and numeric ID formats are accepted

## v1.3.1 (2026-03-21)

### Fixed
- Merged `form_search()` into `sparql_search()` — fixes SPARQL 503 errors on address-based searches by using a single unified query path
- Fixed `docs/examples.md` and `docs/examples.py` to use `classify_yield()` / `classify_data_quality()` from interpret module instead of removed model attributes

### Developer Experience
- Wired `GUIDELINES.md` into `CLAUDE.md` via `@` import — architecture docs now load automatically every session
- Added 5 path-specific `.claude/rules/` files — context-appropriate guidance loads when touching `property_core/`, `mcp_server/`, `app/api/`, `property_cli/`, or `tests/`
- Added 3 workflow skills: `/add-data-source`, `/add-mcp-tool`, `/add-endpoint`
- Added `openaiDeveloperDocs` and `property-shared` HTTP MCP server entries to `.mcp.json`

## v1.3.0 (2026-03-21)

### Breaking Changes
- `yield_assessment` and `data_quality` fields on `YieldAnalysis` are no longer populated by `calculate_yield()` — they default to `None`. Use `property_core.interpret.classify_yield()` and `classify_data_quality()` instead.
- `yield_assessment` field on `RentalAnalysis` is no longer populated by `analyze_rentals()` — use `classify_yield()` on `gross_yield_pct`.
- `key_insights`, `estimated_value_low`, `estimated_value_high` fields on `PropertyReport` are no longer populated by `generate_report()` — use `generate_insights()` and `estimate_value_range()`.
- `price_vs_median` field on `MarketAnalysis` is no longer populated — `price_difference_pct` (raw number) is still computed. Use `classify_price_position()` for the label.
- `YieldAnalysis.data_quality` type changed from `str` (default `"insufficient"`) to `Optional[str]` (default `None`).
- `PropertyReportService.generate_report()` no longer accepts `value_range_pct` or `price_vs_median_pct` parameters.

### New Features
- **`property_core.interpret` module** — opt-in interpretation helpers: `classify_yield()`, `classify_data_quality()`, `classify_price_position()`, `estimate_value_range()`, `generate_insights()`. All exported from `property_core`.
- `PPDService.comps()` now accepts `thin_market_threshold` parameter (default 5) — previously hard-coded.

### Design
- **property_core returns numbers, consumers interpret them.** Services no longer generate assessment labels, quality judgments, insight text, or estimated value ranges. All raw data (yield %, counts, price difference %) is still returned. Consumers (MCP server, CLI) call interpret helpers for presentation.

## v1.2.0 (2026-03-21)

### Breaking Changes
- `calculate_stamp_duty()` default `additional_property` changed from `True` to `False` — callers that relied on the investor default must now pass `additional_property=True` explicitly
- `PPDService.comps()` default `auto_escalate` changed from `True` to `False` — callers that relied on auto-escalation must pass `auto_escalate=True` explicitly

### Configurable Defaults
- `calculate_yield()`: new `strong_yield_pct`, `average_yield_pct`, `min_comps_good` parameters for customizing yield assessment thresholds
- `analyze_rentals()`: new `filter_outliers` parameter (default True) to control IQR filtering on rent range, plus `strong_yield_pct` and `average_yield_pct` for yield thresholds
- `analyze_blocks()`: new `property_type` parameter (default "F") — pass `None` to search all property types
- `PropertyReportService.generate_report()`: new `value_range_pct` (default 15.0) and `price_vs_median_pct` (default 5.0) parameters for configurable interpretation thresholds

### New Features
- API: `GET /v1/analysis/yield` and `GET /v1/analysis/rental` endpoints
- API: `auto_escalate` query parameter on `GET /v1/ppd/comps`
- CLI: `property-cli analysis yield` and `property-cli analysis rental` commands
- CLI: PPD commands now use `PPDService` instead of raw `PricePaidDataClient` for consistent guardrails

### Fixed
- Model exports: `YieldAnalysis` now exported from `property_core.models`
- Top-level model imports: `PPDTransaction`, `EPCData`, `RightmoveListing`, `PropertyReport`, `BlockAnalysisResponse`, `CompanyRecord`, and more available directly from `property_core`
- API stamp duty default now matches core library default (`additional_property=False`)
- CLI stamp duty default now matches core library default (`--no-additional` by default)

### Removed
- `app/services/` wrapper layer — API routers now import directly from `property_core` (same pattern as MCP server and CLI). Removed `epc_service.py`, `rightmove_service.py`, and `app/utils/polite.py`

### Documentation
- Rewrote GUIDELINES.md to match actual code conventions (file naming, architecture, design principles)
- Updated CLAUDE.md: removed `app/services/` from architecture, fixed `raw` field description (transport models only), added new CLI commands and API endpoints, updated library import examples

## v1.1.2 (2026-03-20)

### Documentation
- Updated USER_GUIDE.md with accurate code examples — fixed broken method names, signatures, and imports
- Added Stamp Duty, Block Analyzer, Companies House, and MCP Server documentation sections
- Added runnable examples in docs/examples.py for all new features
- Removed stale UKHPI/location slice notes

## v1.1.1 (2026-03-19)

### MCP Server
- Rewrote MCP server with FastMCP v3 (`fastmcp>=3.0.0`) — expanded from 7 investor-focused tools to 12 covering full property_shared data surface
- New tools: `ppd_transactions`, `rightmove_search`, `rightmove_listing`, `planning_search`, `rental_analysis`
- Fixed ToolResult content for Claude.ai compatibility — `_slim()` + `_content()` helpers put full JSON data in `content[]` so all LLM hosts see the data, not just summary lines

### Bug Fixes
- Fixed Rightmove listing field mapping: `floor_area_sqft` → `display_size`, `tenure` → `tenure_type`
- Moved URI-based SPARQL filters (property_type, estate_type, etc.) to client-side post-fetch in ppd_client.py — fixes 503 timeouts from Land Registry endpoint

## v1.1.0 (2026-03-18)

### New Features
- **Stamp Duty Calculator**: `calculate_stamp_duty()` — April 2025 SDLT bands with additional property (+5%), non-resident (+2%), and first-time buyer relief. API: `GET /v1/calculators/stamp-duty`, CLI: `property-cli calc stamp-duty`
- **Block Analyzer**: `analyze_blocks()` — groups PPD flat transactions by building to find blocks with multiple unit sales (investor exits, bulk-buy opportunities). API: `GET /v1/ppd/blocks`, CLI: `property-cli ppd blocks`
- **Companies House Client**: `CompaniesHouseClient` — search by name or lookup by company number, returns typed models with officers. API: `GET /v1/companies/search`, `GET /v1/companies/{number}`, CLI: `property-cli companies search`

### MCP Server
- Added `stamp_duty` and `property_blocks` tools

## v1.0.0 (2026-03-18)

First public release. Full-featured UK property data library + API.

### Core Library (`property_core`)
- **PPD (Price Paid Data)**: Land Registry transactions via SPARQL + Linked Data API with typed Pydantic models, address search, comps with area stats (median, percentiles, subject property comparison)
- **EPC**: Energy Performance Certificate lookup (async), enrichment pipeline for PPD comps with fuzzy address matching — adds floor area, price/sqft, EPC rating to transactions
- **Rightmove**: Listings scraper with search URL builder, individual listing detail (tenure, floorplans, station distances), rental analysis with IQR outlier filtering
- **Planning**: Council matching for 98 verified UK councils (6 system types), vision-guided Playwright + OpenAI scraper for planning applications
- **Yield Analysis**: PPD sales + Rightmove rentals → gross yield with market assessment
- **Property Reports**: Multi-source aggregation (PPD + EPC + Rightmove) → structured report with key insights, estimated value range, energy performance, rental analysis
- **Postcode**: postcodes.io lookup → typed PostcodeResult model
- **Typed throughout**: All transport clients and domain services return Pydantic v2 models with `raw` field carrying original source data

### API (`app`)
- FastAPI service with versioned routers (`/v1/`)
- Endpoints: health, meta, PPD (transactions, comps, address-search, download-url), EPC search, Rightmove (search-url, listings, listing detail), property report
- Async threading for sync scrapers, in-memory rate limiting for Rightmove
- Demo UI at `/demo`
- Deployed on Fly.io (LHR region)

### CLI (`property_cli`)
- Typer CLI with dual mode: core direct (fast, no server) or API mode (`--api-url`)
- Commands: meta, ppd (comps, search, transaction), epc search, rightmove (search-url, listings, listing), report generate

### MCP Server (`mcp_server`)
- FastMCP server exposing `property_comps` and `property_yield` tools
- Svelte UI for interactive dashboards (BOUCH design system)
- Model Context Sync for AI host state management
- Compatible with Claude.ai and ChatGPT MCP hosts

### Infrastructure
- Published to PyPI as `property-shared`
- Hatch build system with wheel/sdist
- `.dockerignore` and build excludes for clean images
- Fly.io deployment with auto-stop machines
