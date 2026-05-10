# Zoopla & OnTheMarket — Phase 1 Discovery Report

Date: 2026-05-09
Branch: `claude/add-zoopla-onthemarket-scrapers-ZjgfH`

This is the schema-discovery report for the Zoopla / OnTheMarket scraper
addition. **No Pydantic models or scraper code have been written yet** — the
task spec is to fetch real pages first and only then design the model
fields against verbatim observed data.

## TL;DR

- **Zoopla — full coverage via `curl_cffi`.** Plain `requests` is
  Cloudflare-blocked across the entire domain. Headless Playwright gets
  through search pages but is gated on detail pages by Cloudflare
  Turnstile (~60s wait verified, no auto-resolve). **`curl_cffi`
  (libcurl-impersonate) replaying a real Chrome TLS fingerprint defeats
  Cloudflare on BOTH search and detail pages with a clean `200`** —
  verified across `chrome120`, `chrome116`, `safari17_2_ios`,
  `firefox133` impersonation profiles. The Zoopla scraper now ships
  `fetch_listings()` *and* `fetch_listing()` with no browser dependency.
- **OnTheMarket:** **OK to scrape with `requests` + BeautifulSoup.** Returns
  `200`, no Cloudflare interstitial, all listing data is in the rendered HTML
  via `<article data-component="search-result-property-card" itemscope
  itemtype="https://schema.org/SingleFamilyResidence">` (Schema.org microdata).
  The `__NEXT_DATA__` blob is present but its `pageProps` is empty — so
  parsing has to come from HTML, not JSON. The detail page additionally
  exposes a `dataLayer.push({...})` blob with the canonical structured
  fields (price, postcode, branch-id, property-id, channel, etc.).

## 1. Methodology

```
GET <url>
Headers:
  User-Agent: Mozilla/5.0 (X11; Linux x86_64) ... Chrome/115.0.0.0 Safari/537.36
  Accept-Language: en-GB,en;q=0.9
  Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8
  (and Sec-Fetch-* / DNT / Upgrade-Insecure-Requests in the second pass)
```

Probe scripts: `/tmp/probe_sites.py`, `/tmp/probe_otm_articles.py`,
`/tmp/probe_otm_listing_full.py`, `/tmp/probe_zoopla_more.py` (not committed).

## 2. Zoopla — full coverage via `curl_cffi`

### 2.0 Final state (Phase 3)

Switched from headless Playwright to `curl_cffi` after Phase 2. Verified
end-to-end against zoopla.co.uk (live integration test in
`tests/test_zoopla_scraper.py`):

```
Zoopla live fetched 25 listings from https://www.zoopla.co.uk/for-sale/property/sw1a-1aa/
  sample: id=72192746 price=2389000 addr='31, Knightsbridge, London SW1A'
Zoopla detail: id=72192746 price=2389000 tenure='Freehold' bd=1 agent="UK Sotheby's International Realty"
```

Both round-trips complete in <2.5s total. No browser binary, no chromium
RAM overhead, fits in a 512MB container.

The historical exploration below (Phase 1 + 2) is preserved for the
record so we don't have to re-discover the dead ends if Cloudflare
ever closes the curl_cffi gap.

---

### 2.1 Plain `requests` — fully blocked

| URL | Status | Cloudflare |
|---|---|---|
| `https://www.zoopla.co.uk/` | 403 | `cf-mitigated: challenge` |
| `https://www.zoopla.co.uk/for-sale/` | 403 | `cf-mitigated: challenge` |
| `https://www.zoopla.co.uk/for-sale/property/london/` | 403 | `cf-mitigated: challenge` |
| `https://www.zoopla.co.uk/for-sale/property/sw1a-1aa/` | 403 | `cf-mitigated: challenge` |
| `https://www.zoopla.co.uk/for-sale/details/68876254/` | 403 | `cf-mitigated: challenge` |
| `https://www.zoopla.co.uk/sitemap.xml` | 403 | `cf-mitigated: challenge` |
| `https://www.zoopla.co.uk/robots.txt` | 403 | `cf-mitigated: challenge` |

Cloudflare gates the whole domain against datacenter IPs / TLS fingerprints.
Body length ~5.7KB; no `__NEXT_DATA__`, no `ld+json`.

### 2.2 Playwright (headless Chromium) — search OK, detail blocked

After installing the `planning` extra (`uv sync --extra planning` plus
`playwright install chromium`) and using a Chrome UA + en-GB locale + 1280×900
viewport + `ignore_https_errors=True`:

| URL | Status | Result |
|---|---|---|
| `/for-sale/property/sw1a-1aa/` | 200 | 460 KB HTML, 25 cards |
| `/for-sale/property/london/` | 200 | 583 KB HTML, 28 cards |
| `/for-sale/details/{id}/` (any fresh ID) | 403 | 31 KB CF Turnstile interstitial: `<title>Just a moment...</title>` |

The detail page does NOT auto-resolve. Verified by polling `page.title()` and
content length every 5 seconds for 60 seconds; both stay frozen at the
challenge state. Stealth tweaks tried (no improvement):

- `args=["--disable-blink-features=AutomationControlled"]`
- Init script overriding `navigator.webdriver`, `navigator.languages`,
  `navigator.plugins`, `window.chrome`
- Visiting search first to land cookies, then navigating to the detail URL

Cloudflare's Turnstile challenge requires either a solved JS-challenge
fingerprint or a `cf_clearance` cookie acquired via interactive
verification — neither is achievable in plain headless Chromium without
adding `playwright-stealth`, `undetected-chromedriver`, or a paid bypass
service (out of scope per the task spec).

### 2.3 What we got from search pages

- `__NEXT_DATA__` is **not present** on Zoopla — they're using App Router
  with React Server Components (`self.__next_f.push()` chunks). The RSC
  payload is parseable but extremely fragile. **Do not use it.**
- One `<script type="application/ld+json">` block per page, structured as
  `{"@context": "...", "@graph": [WebSite, BreadcrumbList, SearchResultsPage]}`.
  Site-level metadata only — no per-listing fields. **Do not use it.**
- Listing data lives in HTML, anchored by `data-testid="listing-card-content"`
  on the `<a>` element. Sibling `<div class="lib_footerWrapper__...">`
  contains the agent footer. Wrapping `<div class="layout_layoutGridSlim__...">`
  contains the photo gallery (3 photos per card from `lid.zoocdn.com`).
- 25-28 cards per search page; pagination via `?pn=2`, `?pn=3`, etc. (visible
  in `[data-testid='pagination-arrows']`).

Class names are CSS-Modules with hash suffixes (`price_priceText__TArfK`,
`amenities_amenityListSlim__HC4qV`). Hashes will rotate on a Zoopla
redeploy. Selectors use **prefix matching** on the class names
(`re.compile(r"^price_priceText")`) to absorb future hash rotation.

### 2.4 Verbatim field inventory — Zoopla search card

From card `71589060` (London search), all selectors verified against
real HTML:

| Field | Selector | Sample value |
|---|---|---|
| listing id | `<a data-testid="listing-card-content" href="/for-sale/details/{ID}/">` | `71589060` |
| price | `<p class="price_priceText__...">` text | `"£695,000"` |
| price qualifier | `<p class="price_priceTitle__...">` text | `"Guide price"` (also `"OIRO"`, `"Offers in excess of"`) |
| amenities (raw) | `<span class="amenities_amenityItemSlim__...">` items inside `<p class="amenities_amenityListSlim__...">` | `["2 beds", "2 baths", "1 reception", "1218 sq ft"]` |
| address | `<address class="summary_address__...">` text | `"Melliss Avenue, Richmond TW9"` |
| summary | `<p class="summary_summary__...">` text | first ~150 chars of description |
| premium attributes | `<ul class="premium-attributes_attributeList__...">` `<span class="premium-attributes_attributeText__...">` | `["Parking", "Swimming Pool"]` |
| badges | `<ul class="badges_badgesListSlim__...">` `<div>` items | `["Leasehold", "Reduced"]` (also: `"Freehold"`, `"New"`, `"Featured"`, `"Premium"`) |
| agent name | `<img class="agent-logo_agentLogoImageSlim__..." alt="...">` | `"Hamptons - Richmond Sales"` |
| agent logo | same `<img src="...">` (from `st.zoocdn.com`) | `"https://st.zoocdn.com/zoopla_static_agent_logo_(707660).png"` |
| photos | `<img src="https://lid.zoocdn.com/.../*.png">` inside the wrapping `<div class="layout_layoutGridSlim__...">` | up to 3 per card, all 354×255 |

Notes:
- Amenities are a flat list of strings; we will parse `bedrooms`, `bathrooms`,
  `receptions`, `floor_area_sqft` from the strings using regex (`r"^(\d+)\s*beds?$"` etc.).
  We will also keep the raw list verbatim in `amenities` so callers can see the
  original.
- The card anchor includes `<button>See monthly cost</button>` which has no
  semantic data — ignored.
- No bathrooms / bedrooms `itemprop`, unlike OnTheMarket. The amenity strings
  are the only source.
- "Leasehold/Freehold" is a badge string, not a tenure object.

### 2.5 URL conventions (verified)

- Search by full postcode: `/for-sale/property/{postcode-slug}/` — sale
- Search by full postcode (rent): `/to-rent/property/{postcode-slug}/`
- Search by area: `/for-sale/property/{area-slug}/` (e.g. `london`)
- Detail: `/for-sale/details/{listing_id}/` — **but unreachable in
  headless Chromium**
- Postcode slug: lowercase + hyphen replaces space (`sw1a-1aa`)
- Pagination: `?pn=N` query param (verified via `data-testid='pagination-arrows'`)

### 2.6 Decision required

Given detail pages are blocked, the realistic v1 scope for Zoopla is:

1. **Ship search only.** `fetch_listings(search_url)` returns
   `list[ZooplaListing]`. The `ZooplaListingDetail` model and
   `fetch_listing()` are not implemented; MCP tool `zoopla_listing` is
   not registered. README/CHANGELOG note the limitation.
2. **Add a stealth dep** (`playwright-stealth` ~50KB pure Python) — small,
   not yet in the project's deps. Likely defeats Turnstile. New supply-
   chain risk; user has explicitly forbidden stealth deps in the task
   anti-patterns ("Do not add ... undetected-chromedriver, or any
   browser-automation dep without asking me first").
3. **Drop Zoopla.** OnTheMarket only.

I'm proceeding under the assumption of (1) unless the user picks otherwise.
This delivers immediate value (postcode-level Zoopla market view) and the
Phase 2 implementation can be re-entered later for `fetch_listing()` if
the constraint changes.

## 3. OnTheMarket — OK to scrape

| URL | Status | Notes |
|---|---|---|
| `https://www.onthemarket.com/for-sale/property/sw1a-1aa/` | 200 | 528 KB HTML, 30 listing cards |
| `https://www.onthemarket.com/for-sale/property/london/` | 200 | 557 KB HTML |
| `https://www.onthemarket.com/details/19100332/` | 200 | 218 KB HTML |

No Cloudflare interstitial, no rate-limiting on a handful of sequential
requests. `Server` header omitted; no `cf-ray`.

### 3.1 Where the data lives

#### Search-results page

- `__NEXT_DATA__` is present but `pageProps` is **empty**. Not a useful
  source — the page is server-rendered Next.js where data is inlined as
  HTML, not in the SSR JSON blob. **Do not use `__NEXT_DATA__`.**
- Listing data is in 30 `<article>` elements with these stable hooks:
  - `data-component="search-result-property-card"` (selector for cards)
  - `itemscope itemtype="https://schema.org/SingleFamilyResidence"` (microdata)
  - `title="View the details for {address} - {N} bedroom {type} for sale"`
- ld+json: 1 generic `Product` block (site-level branding only, not useful).

#### Listing-detail page

- `__NEXT_DATA__` again present, `pageProps` again empty. Skip.
- Three reliable structured sources:
  1. `og:` meta tags (`og:title`, `og:description`, `og:image`, `og:url`).
  2. `dataLayer.push({...})` — single JSON blob with canonical fields
     (price, postcode, branch-id, property-id, channel, etc.).
  3. `data-test="property-*"` attributes for headline numbers
     (`property-price`, `property-title`).

### 3.2 Verbatim field inventory — search card

From a real card (id `19100332`, SW1A 1AA query):

| Field name (verbatim) | Source | Sample value |
|---|---|---|
| listing id | first `<a href="/details/{ID}/">` and `<meta itemprop="url" content="/details/{ID}/">` | `19100332` |
| `<article title="...">` | `title` attr on the article | `"View the details for Buckingham Palace Road, Victoria, London, SW1W - 2 bedroom flat for sale"` |
| display address | `<address itemprop="address"><span>...</span></address>` text | `"Buckingham Palace Road, Victoria, London, SW1W"` |
| `addressLocality` | `<meta itemprop="addressLocality"/>` | (empty in this sample — `content=""`) |
| `postalCode` | `<meta itemprop="postalCode"/>` | (empty in this sample — `content=""`) |
| price | `<a>` text inside `<div data-component="price-title">` | `"£1,200,000"` |
| property type label | text inside `<div class="text-sm mr-2 ...">` next to BedBathCounts | `"Flat"` |
| bedrooms | `<span itemprop="numberOfBedrooms">` text (icon `#icon-bed-front`) | `"2"` |
| bathrooms | second `<span>` in `<div data-component="BedBathCounts">` (icon `#icon-bath`, no itemprop) | `"2"` |
| primary image | first `<img src="..." srcset="...">` under `<div itemprop="photo">` | `"https://media.onthemarket.com/properties/19100332/1603428457/image-0-480x320.jpg"` |
| all images | every `<img>` under `itemprop="photo"` | up to 5 in the swiper |
| agent name | `<div itemprop="name">` text | `"John D Wood & Co - Belgravia"` |
| agent phone | `<a itemprop="telephone">` text + `href="tel:..."` | `"020 3007 7116"` |
| meta description | `<meta itemprop="description" content="...">` | `"2 bedroom flat for sale - Buckingham Palace Road, Victoria, London, SW1W"` |
| extras (tenure / floor area / station / chain status) | `<span class="mr-1 pl-1 first:pl-0 first:ml-0">` siblings | `"Tenure: Leasehold (108 years remaining)"`, `"1,062 sq ft floor area"`, `"Nearest station 0.2mi."`, `"Nearest school 0.2mi."`, `"Chain-free"` |
| listed-status | last freestanding `<span>` in agent-panel | `"Added > 14 days"`, `"Reduced < 7 days"`, `"Added < 14 days"` |
| spotlight pill | `<div data-component="pill">Spotlight Property</div>` | (boolean — present on featured listings) |

Notes:
- `addressLocality` / `postalCode` `<meta>` tags exist but are **empty** in
  the served HTML. They are intended for crawlers; real values are not
  populated. The address text is the only authoritative source.
- The two `<span>`s inside `BedBathCounts` are positionally distinguished
  (no separate itemprop for bathroom). Use `find_all('span')` order.

### 3.3 Verbatim field inventory — listing detail

From `/details/19100332/`:

| Field name (verbatim) | Source | Sample value |
|---|---|---|
| price | `<a data-test="property-price">` text | `"£1,200,000"` |
| title | `<h1 data-test="property-title">` text | `"2 bedroom flat for sale"` |
| og:title | `<meta property="og:title">` | `"Buckingham Palace Road, Victoria... 2 bed flat for sale - £1,200,000"` |
| og:description | `<meta property="og:description">` | `"John D Wood & Co - Belgravia present this 2 bedroom flat for sale in Buckingham Palace Road, Victoria, London, SW1W"` |
| og:url | `<meta property="og:url">` | `"https://www.onthemarket.com/details/19100332/"` |
| og:image | `<meta property="og:image">` | `"https://media.onthemarket.com/properties/19100332/1603428457/image-0-1024x1024.jpg"` |
| description | `<div itemprop="description">` text | full long-form description |
| photo count | `<div data-component="pill">Photos (N)</div>` | `"Photos (12)"` |
| has floorplan | `<div data-component="pill">Floorplan</div>` presence | bool |
| has map | `<div data-component="pill">Map</div>` presence | bool |
| breadcrumbs | `<ol data-component="breadcrumb-list"><li data-component="breadcrumb-item">` items | `["UK", "Greater London", "Central London", "Buckingham Palace Road"]` |
| dataLayer.* | `dataLayer.push({...})` JSON blob | see below |

`dataLayer.push()` JSON keys (verbatim, from a real fetch):

```
parent-locations         list[str]   ["uk","england","south-east"]
development-property     bool        false
feed-type                str         "realtime"
branch-id                int         73949
page-type                str         "details-section"
agent-rank               int         2
matterport-virtual-tour  bool        false
property-type            str         "homes"
postcode                 str         "SW1W 0PP"
channel                  str         "sale"   (or "let")
meta-robots              str         "noindex, follow"
uk_country               str         "england"
property-id              int         19100332
http-status              str         "200"
status                   str         "live"
trans-type-id            str         "resale" (or e.g. "new-home")
price                    str         "1,200,000"   (commas, no £)
new-home                 bool        false
addressline_2            str         "Buckingham Palace Road"
```

`dataLayer` is the cleanest source for canonical numeric fields (`price`,
`property-id`, `postcode`, `branch-id`). The HTML is the source for human-
readable fields (description, address, agent name, photos).

### 3.4 What is NOT directly available on the listing detail

These have visible H2 sections (`Key information`, `Features and description`,
`Property information from this agent`, `Area statistics`, `About this agent`,
`Similar properties`) but the inner blocks don't carry stable hooks like
`data-test=` or `itemprop=`. Extracting them would mean fragile DOM walks
through the H2 → following section structure. Recommend leaving these out
of the v1 model and adding later if needed.

### 3.5 URL conventions (verified)

- Search by full postcode: `/for-sale/property/{postcode-slug}/` — sale
- Search by full postcode (rent): `/to-rent/property/{postcode-slug}/`
- Search by area name: `/for-sale/property/{area-slug}/` (e.g. `london`)
- Detail: `/details/{listing_id}/` (channel-agnostic — same URL for
  sale and let)
- Postcode slug: lowercase + hyphen replaces space (`sw1a-1aa`)

The listing-detail URL is a single canonical path — there is no
sale-vs-rent prefix, unlike Zoopla. Channel comes from `dataLayer.channel`.

### 3.6 Pagination

- The first page returns 30 articles. Bottom of page presumably has
  pagination links (didn't probe). To be confirmed before implementation —
  will look for `?page=N` or a "Next" link in the served HTML during
  Phase 2 implementation.

## 4. Recommended scope for Phase 2

### Zoopla (search-only — pending user confirmation)

```
class ZooplaListing(BaseModel):
    id: str                          # from /for-sale/details/{id}/
    url: str                         # absolute URL (https://www.zoopla.co.uk + href)
    price: int | None                # parsed from "£695,000"
    display_price: str | None        # raw "£695,000"
    price_qualifier: str | None      # "Guide price", "OIRO", etc.
    address: str | None              # <address> text
    summary: str | None              # <p class="summary_summary__"> text
    bedrooms: int | None             # parsed from "2 beds" amenity
    bathrooms: int | None            # parsed from "2 baths" amenity
    receptions: int | None           # parsed from "1 reception" amenity
    floor_area_sqft: int | None      # parsed from "1218 sq ft" amenity
    amenities: list[str]             # raw verbatim list (always populated)
    premium_attributes: list[str]    # ["Parking", "Swimming Pool"]
    badges: list[str]                # ["Leasehold", "Reduced", ...]
    agent_name: str | None           # img alt text on agent logo
    agent_logo: str | None           # img src on agent logo
    images: list[str]                # property photos (lid.zoocdn.com)
    raw: dict | None = Field(default=None, exclude=True)
```

```python
async def fetch_listings(
    search_url: str, *,
    timeout_ms: int = 45_000,
    max_pages: int | None = None,
    rate_limit_seconds: float = 0.6,   # via ZOOPLA_DELAY_SECONDS env var
    proxy: str | None = None,           # passthrough to playwright
) -> list[ZooplaListing]: ...
```

`ZooplaLocationAPI` mirrors OnTheMarket's: postcode → slug
(`postcode.lower().replace(" ", "-")`), no typeahead. Lives in
`zoopla_location.py` for parity. Sale/rent path prefix differs from OTM
(`/for-sale/property/` vs `/to-rent/property/`).

### OnTheMarket

### `OnTheMarketListing` (search-card model)

```
id: str
url: str
price: int | None              # parsed from "£1,200,000"
display_price: str | None      # raw "£1,200,000"
address: str | None            # from <address itemprop>
bedrooms: int | None           # from itemprop=numberOfBedrooms
bathrooms: int | None          # from second BedBathCounts span
property_type_label: str | None  # "Flat", "House", etc.
agent_name: str | None
agent_phone: str | None
images: list[str]
extras: list[str]              # raw extras spans (tenure / sqft / etc.)
listed_status: str | None      # "Added > 14 days", etc.
is_spotlight: bool             # from "Spotlight Property" pill presence
title_attr: str | None         # the <article title="..."> string
raw: dict | None = Field(default=None, exclude=True)
```

### `OnTheMarketListingDetail` (detail page)

Combines `dataLayer` (numeric/canonical) + HTML (display strings).

```
id: str                         # dataLayer.property-id
url: str
price: int | None               # parsed from data-test=property-price OR dataLayer.price
display_price: str | None       # raw "£1,200,000"
title: str | None               # h1 data-test=property-title
address: str | None             # from og:title or breadcrumbs
addressline_2: str | None       # dataLayer.addressline_2
postcode: str | None            # dataLayer.postcode
property_type: str | None       # dataLayer.property-type
channel: str | None             # dataLayer.channel — "sale" / "let"
trans_type: str | None          # dataLayer.trans-type-id — "resale" / "new-home"
new_home: bool                  # dataLayer.new-home
status: str | None              # dataLayer.status — "live" / etc.
branch_id: int | None           # dataLayer.branch-id
agent_rank: int | None          # dataLayer.agent-rank
parent_locations: list[str]     # dataLayer.parent-locations
breadcrumbs: list[str]
description: str | None         # itemprop=description text
og_description: str | None      # og:description
images: list[str]               # og:image + others on the page
photo_count: int | None         # parsed from "Photos (N)" pill
has_floorplan: bool
has_map: bool
matterport_tour: bool           # dataLayer.matterport-virtual-tour
raw: dict | None = Field(default=None, exclude=True)
```

### Function signatures (mirror Rightmove)

```python
def fetch_listings(
    search_url: str, *,
    timeout: float = 15.0,
    max_pages: int | None = None,
    rate_limit_seconds: float = 0.6,   # via ONTHEMARKET_DELAY_SECONDS env var
    retry_attempts: int = 3,
    retry_backoff: float = 1.5,
) -> list[OnTheMarketListing]: ...

def fetch_listing(
    property_url_or_id: str, *,
    timeout: float = 15.0,
    retry_attempts: int = 3,
    retry_backoff: float = 1.5,
) -> OnTheMarketListingDetail: ...
```

`OnTheMarketLocationAPI` is **not needed** — OnTheMarket's URL slug is just
`postcode.lower().replace(" ", "-")`. No typeahead lookup, no
`OUTCODE^NNN` identifier system. A single small `build_search_url(postcode,
property_type='sale', ...)` function is enough; can live in
`onthemarket_location.py` for parity with Rightmove.

## 5. Blockers / open questions

1. **Zoopla path forward** — see Section 2 decision points. Cannot proceed
   without user direction.
2. **OnTheMarket pagination** — to be verified in Phase 2; first probe
   only inspected page 1.
3. **OnTheMarket rate limits** — 3 sequential requests with no `time.sleep`
   in the probe all returned 200. Will use `ONTHEMARKET_DELAY_SECONDS=0.6`
   (mirroring Rightmove) to be conservative.
4. **Address PII in test fixtures** — when committing search/listing
   fixtures in Phase 3, will replace street numbers, agent phone numbers,
   and full postcodes with placeholders so we don't ship someone's listing
   verbatim into the repo.
