"""Zoopla scraper (search-only via headless Playwright).

Zoopla is wholly Cloudflare-gated against datacenter ``requests`` clients.
Phase 1 discovery (see docs/zoopla-onthemarket-discovery.md) verified that
headless Playwright with a Chrome UA + en-GB locale + standard viewport
gets through the search pages but **not** per-listing detail URLs (which
serve a Cloudflare Turnstile interstitial). Therefore only ``fetch_listings``
is implemented here; there is no ``fetch_listing`` for Zoopla.

The function is synchronous, mirroring ``rightmove_scraper.fetch_listings``.
Consumers in async contexts should wrap calls in
``anyio.to_thread.run_sync(...)``.

Playwright is an optional dependency (``planning`` extra). Importing this
module without ``playwright`` installed raises a clear ``ImportError`` at
call-time, not at module-import time, so the rest of ``property_core``
remains importable in lean environments.
"""

from __future__ import annotations

import re
import time
from typing import List, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup, Tag

from property_core.models.zoopla import ZooplaListing


class ZooplaError(Exception):
    """Raised when Zoopla data cannot be fetched or parsed."""


_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
)

_VIEWPORT = {"width": 1280, "height": 900}
_LOCALE = "en-GB"

_STEALTH_INIT = (
    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
    "Object.defineProperty(navigator, 'languages', {get: () => ['en-GB','en']});"
    "Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});"
    "window.chrome = { runtime: {} };"
)

_BASE = "https://www.zoopla.co.uk"
_DETAIL_HREF_RE = re.compile(r"^/for-sale/details/(\d+)/?")
_DETAIL_HREF_RENT_RE = re.compile(r"^/to-rent/details/(\d+)/?")
_PRICE_TEXT_RE = re.compile(r"^price_priceText")
_PRICE_TITLE_RE = re.compile(r"^price_priceTitle")
_AMENITY_LIST_RE = re.compile(r"^amenities_amenityListSlim")
_AMENITY_ITEM_RE = re.compile(r"^amenities_amenityItemSlim")
_ADDRESS_RE = re.compile(r"^summary_address")
_SUMMARY_RE = re.compile(r"^summary_summary")
_PREMIUM_LIST_RE = re.compile(r"^premium-attributes_attributeList")
_PREMIUM_TEXT_RE = re.compile(r"^premium-attributes_attributeText")
_BADGE_LIST_RE = re.compile(r"^badges_badgesListSlim")
_AGENT_LOGO_RE = re.compile(r"^agent-logo_agentLogoImage")
_PROPERTY_PHOTO_HOST = "lid.zoocdn.com"
_LAYOUT_GRID_RE = re.compile(r"^layout_layoutGrid")


def fetch_listings(
    search_url: str,
    *,
    timeout_ms: int = 45_000,
    max_pages: Optional[int] = None,
    rate_limit_seconds: float = 1.0,
    proxy: str | None = None,
    headless: bool = True,
) -> List[ZooplaListing]:
    """Fetch Zoopla search results across one or more pages.

    Args:
        search_url: Absolute Zoopla search URL (build with ``ZooplaLocationAPI``).
        timeout_ms: Per-page navigation timeout in milliseconds.
        max_pages: Cap on pages walked. ``None`` = walk until no next page.
        rate_limit_seconds: Polite delay between page fetches.
        proxy: Optional proxy URL (e.g. residential proxy) passed to Playwright.
        headless: Run Chromium headless (default ``True``).

    Returns:
        List of ``ZooplaListing`` from all pages walked.

    Raises:
        ImportError: If ``playwright`` is not installed (install
            ``property-shared[planning]``).
        ZooplaError: If a page is blocked (Cloudflare interstitial) or
            structure parsing fails.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise ImportError(
            "Zoopla scraping requires Playwright. Install with: "
            "pip install 'property-shared[planning]' && playwright install chromium"
        ) from exc

    listings: List[ZooplaListing] = []
    page_counter = 0
    next_url: str | None = search_url
    seen_pages: set[str] = set()

    launch_kwargs: dict = {
        "headless": headless,
        "args": ["--disable-blink-features=AutomationControlled"],
    }
    if proxy:
        launch_kwargs["proxy"] = {"server": proxy}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(**launch_kwargs)
        try:
            ctx = browser.new_context(
                user_agent=_USER_AGENT,
                locale=_LOCALE,
                viewport=_VIEWPORT,
                ignore_https_errors=True,
            )
            ctx.add_init_script(_STEALTH_INIT)
            page = ctx.new_page()

            while next_url:
                if next_url in seen_pages:
                    break
                seen_pages.add(next_url)

                if rate_limit_seconds and page_counter > 0:
                    time.sleep(rate_limit_seconds)
                page_counter += 1

                response = page.goto(next_url, wait_until="domcontentloaded", timeout=timeout_ms)
                # Allow the cards to hydrate
                page.wait_for_timeout(1_500)
                html = page.content()

                if "Just a moment" in (page.title() or "") or (response and response.status == 403):
                    raise ZooplaError(
                        f"Zoopla returned a Cloudflare challenge for {next_url} "
                        f"(status {response.status if response else '?'}). "
                        "Try a residential proxy via the proxy= argument."
                    )

                page_listings = _parse_search_html(html)
                if not page_listings and page_counter == 1:
                    raise ZooplaError(
                        f"No listings parsed from {next_url}. The page returned "
                        f"{len(html)} bytes but no listing-card-content anchors were "
                        "found — Zoopla may have changed its markup."
                    )
                listings.extend(page_listings)

                if max_pages is not None and page_counter >= max_pages:
                    break

                next_url = _next_page_url(search_url, page_counter + 1) if page_listings else None
        finally:
            browser.close()

    return listings


def _next_page_url(search_url: str, next_page: int) -> str:
    """Return ``search_url`` with ``pn={next_page}`` set in the query string."""
    parsed = urlparse(search_url)
    query_items = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query_items["pn"] = str(next_page)
    new_query = urlencode(query_items, doseq=True)
    return urlunparse(parsed._replace(query=new_query))


def _parse_search_html(html: str) -> List[ZooplaListing]:
    soup = BeautifulSoup(html, "html.parser")
    anchors = soup.find_all("a", attrs={"data-testid": "listing-card-content"})
    out: List[ZooplaListing] = []
    for anchor in anchors:
        listing = _parse_card(anchor)
        if listing is not None:
            out.append(listing)
    return out


def _parse_card(anchor: Tag) -> Optional[ZooplaListing]:
    href = anchor.get("href") or ""
    m = _DETAIL_HREF_RE.match(href) or _DETAIL_HREF_RENT_RE.match(href)
    if not m:
        return None
    listing_id = m.group(1)
    url = f"{_BASE}{href}"

    # Walk up to the outer card wrapper that includes photo gallery + agent footer.
    outer = _find_outer_card(anchor)

    display_price = _text_or_none(anchor.find("p", class_=_PRICE_TEXT_RE))
    price_qualifier = _text_or_none(anchor.find("p", class_=_PRICE_TITLE_RE))

    amenity_list = anchor.find("p", class_=_AMENITY_LIST_RE)
    amenities: list[str] = []
    if amenity_list is not None:
        amenities = [
            t.get_text(strip=True)
            for t in amenity_list.find_all("span", class_=_AMENITY_ITEM_RE)
            if t.get_text(strip=True)
        ]

    address = _text_or_none(anchor.find("address", class_=_ADDRESS_RE))
    summary = _text_or_none(anchor.find("p", class_=_SUMMARY_RE))

    premium_list = anchor.find("ul", class_=_PREMIUM_LIST_RE)
    premium_attributes: list[str] = []
    if premium_list is not None:
        premium_attributes = [
            t.get_text(strip=True)
            for t in premium_list.find_all("span", class_=_PREMIUM_TEXT_RE)
            if t.get_text(strip=True)
        ]

    badge_list = anchor.find("ul", class_=_BADGE_LIST_RE)
    badges: list[str] = []
    if badge_list is not None:
        for li in badge_list.find_all("li"):
            txt = li.get_text(strip=True)
            if txt:
                badges.append(txt)

    agent_name: str | None = None
    agent_logo: str | None = None
    images: list[str] = []
    if outer is not None:
        agent_img = outer.find("img", class_=_AGENT_LOGO_RE)
        if agent_img is not None:
            agent_name = (agent_img.get("alt") or None) or None
            agent_logo = agent_img.get("src") or None
        for im in outer.find_all("img"):
            src = im.get("src") or ""
            if _PROPERTY_PHOTO_HOST in src:
                images.append(src)

    return ZooplaListing.build(
        listing_id=listing_id,
        url=url,
        display_price=display_price,
        price_qualifier=price_qualifier,
        amenities=amenities,
        address=address,
        summary=summary,
        premium_attributes=premium_attributes,
        badges=badges,
        agent_name=agent_name,
        agent_logo=agent_logo,
        images=images,
        raw=None,
    )


def _find_outer_card(anchor: Tag) -> Tag | None:
    """Walk up to the wrapping ``<div class='layout_layoutGrid...'>`` that
    contains both the anchor and the photo gallery siblings."""
    p: Tag | None = anchor
    for _ in range(8):
        if p is None or p.parent is None:
            return p
        p = p.parent
        cls = p.get("class") or []
        if any(_LAYOUT_GRID_RE.match(c) for c in cls):
            return p
    return p


def _text_or_none(tag: Tag | None) -> str | None:
    if tag is None:
        return None
    txt = tag.get_text(strip=True)
    return txt or None
