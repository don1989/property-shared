# UK Property Data

## Tagline
Land Registry comps, Rightmove listings, EPC, rental yields, and stamp duty in one MCP server.

## Description
UK Property Data gives AI agents direct access to official UK property data sources — no API key required. Pull comparable sales from Land Registry, current Rightmove listings (for sale and rent), EPC energy certificates, rental market figures, and stamp duty calculations. Use it for investment analysis, buy-to-let underwriting, or researching any UK address. Runs as a remote hosted server (streamable HTTP) or locally via uvx.

## Setup Requirements
No API keys or environment variables required. All data comes from UK public registers (Land Registry PPD, EPC Register, HMRC) and Rightmove.

## Category
Finance

## Features
- Pull comparable sales (comps) for any UK postcode from Land Registry Price Paid Data
- Search and retrieve Rightmove listings for sale or rent with filters
- Look up EPC energy certificates by street address and postcode
- Calculate rental yields for buy-to-let analysis
- Run full property investment reports (comps + EPC + yield + rental in one call)
- Calculate stamp duty (SDLT) for any purchase price and buyer type
- Analyse rental market figures by postcode
- Search council planning portals for planning applications
- Look up company ownership and corporate structures via Companies House
- Block-buy analysis for portfolio investors
- No API key required — all data from UK public sources

## Getting Started
- "Run a property report for 14 Church Street, NG1 2AB"
- "What are comparable sales in NG1 over the last 6 months?"
- "Find Rightmove listings under £200k in Sheffield with at least 2 bedrooms"
- "What's the rental yield on a £180k flat in Nottingham renting at £850/month?"
- "Look up the EPC rating for 42 High Street, Leeds LS1 1AA"
- "Calculate stamp duty on a £350,000 second home purchase"
- Tool: property_report — Full investment analysis for a UK address (comps, EPC, yield, rental)
- Tool: property_comps — Comparable sales by postcode from Land Registry
- Tool: rightmove_search — Search Rightmove listings for sale or rent
- Tool: property_epc — EPC energy certificate lookup
- Tool: property_yield — Rental yield calculation
- Tool: rental_analysis — Rental market figures by postcode
- Tool: stamp_duty — SDLT calculation for any purchase scenario

## Tags
uk-property, land-registry, rightmove, epc, rental-yield, buy-to-let, stamp-duty, property-investment, uk-real-estate, comparable-sales, property-data, mcp, fastmcp, no-api-key

## Documentation URL
https://bouch.dev/products/property-report

## Health Check URL
`https://<your-api-domain>/v1/health` — once your Coolify API service has its custom domain wired up (see `docs/coolify-deploy.md`).
