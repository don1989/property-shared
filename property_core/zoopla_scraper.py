"""Zoopla scraper (search + listing detail) via curl_cffi.

Plain ``requests`` is fully Cloudflare-blocked across zoopla.co.uk because
CF fingerprints on TLS handshake characteristics, not just User-Agent.
``curl_cffi`` (libcurl-impersonate) replays a real Chrome TLS/HTTP-2
fingerprint and gets clean ``200`` responses on both search and detail
pages. No browser automation needed.

Search-card HTML structure: ``<a data-testid='listing-card-content'>``
inside a ``<div class='layout_layoutGrid...'>`` wrapper that also holds
the photo gallery and agent footer. CSS-Modules class names carry hash
suffixes (e.g. ``price_priceText__TArfK``); selectors match by class-name
*prefix* to absorb future hash rotation.

Listing-detail page exposes:
- ``<script type='application/ld+json'>`` with a ``RealEstateListing``
  block (name, description, datePosted, image, additionalProperty list
  with Bedrooms/Bathrooms/Floor size, offers.price/priceCurrency).
- A ``BreadcrumbList`` ld+json block for the location hierarchy.
- A ``ListingAnalyticsTaxonomy`` JSON object embedded in one of the RSC
  ``self.__next_f.push([...])`` chunks, with the canonical attribute
  data (branch info, tenure, furnishedState, hasEpc, hasFloorplan, etc.).
- A ``<ul class='NtsInfo_ntsInfoList...'>`` "Need to see info" block with
  Tenure / Council tax band rows.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup, Tag

from property_core.models.zoopla import ZooplaListing, ZooplaListingDetail


class ZooplaError(Exception):
    """Raised when Zoopla data cannot be fetched or parsed."""


_BASE = "https://www.zoopla.co.uk"
_DEFAULT_IMPERSONATE = "chrome120"

# Profiles tried in order when the default (or caller-supplied) profile gets
# blocked by Cloudflare. Chosen to span both major TLS-fingerprint families
# (Chromium-based and Safari/Firefox); if all four fail from the same egress
# IP, that IP is genuinely on Cloudflare's heavy-mitigation list and a
# residential proxy is the next step.
_FALLBACK_PROFILES: tuple[str, ...] = (
    "chrome120",
    "safari17_2_ios",
    "firefox133",
    "chrome116",
)

_DETAIL_HREF_RE = re.compile(r"^/for-sale/details/(\d+)/?")
_DETAIL_HREF_RENT_RE = re.compile(r"^/to-rent/details/(\d+)/?")
_DETAIL_HREF_NEWHOME_RE = re.compile(r"^/new-homes/details/(\d+)/?")

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
_LAYOUT_GRID_RE = re.compile(r"^layout_layoutGrid")
_NTS_INFO_ITEM_RE = re.compile(r"^NtsInfo_ntsInfoListItem")
_NTS_INFO_TITLE_RE = re.compile(r"^NtsInfo_ntsInfoItemTitle")
_NTS_INFO_TEXT_WRAP_RE = re.compile(r"^NtsInfo_ntsInfoItemTextWrapper")

_PROPERTY_PHOTO_HOST = "lid.zoocdn.com"

_ANALYTICS_TAXONOMY_MARKER = '"__typename":"ListingAnalyticsTaxonomy"'

_RSC_CHUNK_RE = re.compile(r'self\.__next_f\.push\(\[\d+,\s*"((?:[^"\\]|\\.)*)"\]\)')


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def fetch_listings(
    search_url: str,
    *,
    timeout: float = 25.0,
    max_pages: Optional[int] = None,
    rate_limit_seconds: float = 0.6,
    impersonate: str = _DEFAULT_IMPERSONATE,
    fallback_profiles: tuple[str, ...] = _FALLBACK_PROFILES,
    proxy: str | None = None,
) -> List[ZooplaListing]:
    """Fetch Zoopla search results across one or more pages.

    Args:
        search_url: Absolute Zoopla search URL (build with
            ``ZooplaLocationAPI``).
        timeout: HTTP request timeout in seconds.
        max_pages: Cap on pages walked. ``None`` walks until no more cards.
        rate_limit_seconds: Polite delay between page fetches.
        impersonate: ``curl_cffi`` browser-fingerprint profile to try first
            (e.g. ``"chrome120"``, ``"safari17_2_ios"``, ``"firefox133"``).
        fallback_profiles: Profiles tried in order if the first one is
            Cloudflare-blocked. Pass ``()`` to disable rotation.
        proxy: Optional proxy URL (e.g. residential proxy).

    Returns:
        List of ``ZooplaListing`` from all pages walked.

    Raises:
        ImportError: If ``curl_cffi`` is not installed.
        ZooplaError: If every profile is blocked (Cloudflare interstitial)
            or HTML structure parsing fails.
    """
    starting_page = _starting_page(search_url)

    # Try profiles until the first page comes back clean, then stick with
    # that session for any subsequent pagination.
    session, first_html = _fetch_with_profile_rotation(
        url=search_url,
        impersonate=impersonate,
        fallback_profiles=fallback_profiles,
        proxy=proxy,
        timeout=timeout,
    )

    listings: List[ZooplaListing] = []
    page_listings = _parse_search_html(first_html)
    if not page_listings:
        raise ZooplaError(
            f"No listings parsed from {search_url}. The page returned "
            f"{len(first_html)} bytes but no listing-card-content anchors "
            "were found — Zoopla may have changed its markup."
        )
    listings.extend(page_listings)

    page_counter = 1
    seen_pages: set[str] = {search_url}
    next_url: str | None = (
        _next_page_url(search_url, starting_page + page_counter)
        if (max_pages is None or page_counter < max_pages)
        else None
    )

    while next_url and next_url not in seen_pages:
        seen_pages.add(next_url)
        if rate_limit_seconds:
            time.sleep(rate_limit_seconds)
        page_counter += 1

        html = _get(session, next_url, timeout=timeout)
        page_listings = _parse_search_html(html)
        listings.extend(page_listings)

        if max_pages is not None and page_counter >= max_pages:
            break
        next_url = (
            _next_page_url(search_url, starting_page + page_counter)
            if page_listings
            else None
        )

    return listings


def fetch_listing(
    property_url_or_id: str,
    *,
    timeout: float = 25.0,
    impersonate: str = _DEFAULT_IMPERSONATE,
    fallback_profiles: tuple[str, ...] = _FALLBACK_PROFILES,
    proxy: str | None = None,
) -> ZooplaListingDetail:
    """Fetch full property details from an individual Zoopla listing page.

    Args:
        property_url_or_id: Full Zoopla URL or numeric listing id.
        timeout: HTTP request timeout in seconds.
        impersonate: ``curl_cffi`` browser-fingerprint profile to try first.
        fallback_profiles: Profiles tried in order if the first one is
            Cloudflare-blocked. Pass ``()`` to disable rotation.
        proxy: Optional proxy URL.

    Returns:
        ``ZooplaListingDetail`` populated from the ld+json
        ``RealEstateListing``, ``BreadcrumbList``, embedded analytics
        taxonomy, and the ``NtsInfo_ntsInfoList`` rows.
    """
    url = _normalize_listing_url(property_url_or_id)
    listing_id = _id_from_url(url)
    if listing_id is None:
        raise ZooplaError(
            f"Could not extract Zoopla listing id from {property_url_or_id!r}"
        )

    _, html = _fetch_with_profile_rotation(
        url=url,
        impersonate=impersonate,
        fallback_profiles=fallback_profiles,
        proxy=proxy,
        timeout=timeout,
    )
    return _parse_listing_html(html, listing_id=listing_id, url=url)


def _profiles_to_try(initial: str, fallbacks: tuple[str, ...]) -> list[str]:
    """Return ``[initial, ...fallbacks not equal to initial]``.

    Caller's chosen profile is always tried first; the rest follow in
    declaration order. Pass ``fallbacks=()`` to disable rotation.
    """
    out = [initial]
    for p in fallbacks:
        if p not in out:
            out.append(p)
    return out


def _fetch_with_profile_rotation(
    *,
    url: str,
    impersonate: str,
    fallback_profiles: tuple[str, ...],
    proxy: str | None,
    timeout: float,
) -> tuple[Any, str]:
    """Try ``url`` against each profile in turn; return the first
    ``(session, html)`` tuple that succeeds.

    Raises ``ZooplaError`` listing every profile that failed only if all
    of them did.
    """
    profiles = _profiles_to_try(impersonate, fallback_profiles)
    failures: list[str] = []
    for profile in profiles:
        session = _new_session(impersonate=profile, proxy=proxy)
        try:
            html = _get(session, url, timeout=timeout)
            return session, html
        except ZooplaError as exc:
            failures.append(f"{profile}: {exc}")
            continue
    raise ZooplaError(
        f"All {len(profiles)} curl_cffi profiles were blocked for {url}. "
        f"Failures: {'; '.join(failures)}. Consider a residential proxy."
    )


# ---------------------------------------------------------------------------
# Transport plumbing
# ---------------------------------------------------------------------------


def _new_session(*, impersonate: str, proxy: str | None) -> Any:
    """Construct a curl_cffi Session that impersonates a real browser."""
    try:
        from curl_cffi import requests as cf_requests
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise ImportError(
            "Zoopla scraping requires curl_cffi. Install with: "
            "pip install curl_cffi"
        ) from exc

    session_kwargs: dict[str, Any] = {"impersonate": impersonate}
    if proxy:
        session_kwargs["proxies"] = {"http": proxy, "https": proxy}
    return cf_requests.Session(**session_kwargs)


def _get(session: Any, url: str, *, timeout: float) -> str:
    try:
        response = session.get(url, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        raise ZooplaError(f"Request to {url} failed: {exc}") from exc

    status = getattr(response, "status_code", 0)
    text = getattr(response, "text", "")
    if status == 403 or "Just a moment" in text[:5000]:
        raise ZooplaError(
            f"Zoopla returned a Cloudflare challenge for {url} (status {status}). "
            "Try a different impersonate= profile (e.g. 'safari17_2_ios') "
            "or a residential proxy via the proxy= argument."
        )
    if status >= 400:
        raise ZooplaError(f"Request to {url} returned status {status}")
    return text


def _starting_page(search_url: str) -> int:
    """Return the ``pn=N`` value from ``search_url``, defaulting to ``1``."""
    parsed = urlparse(search_url)
    query_items = dict(parse_qsl(parsed.query, keep_blank_values=True))
    try:
        return max(1, int(query_items.get("pn", "1")))
    except (TypeError, ValueError):
        return 1


def _next_page_url(search_url: str, next_page: int) -> str:
    """Return ``search_url`` with ``pn={next_page}`` set in the query string."""
    parsed = urlparse(search_url)
    query_items = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query_items["pn"] = str(next_page)
    new_query = urlencode(query_items, doseq=True)
    return urlunparse(parsed._replace(query=new_query))


def _normalize_listing_url(url_or_id: str) -> str:
    s = url_or_id.strip()
    if s.startswith("http"):
        return s
    return f"{_BASE}/for-sale/details/{s}/"


def _id_from_url(url: str) -> str | None:
    parsed = urlparse(url)
    m = (
        _DETAIL_HREF_RE.match(parsed.path)
        or _DETAIL_HREF_RENT_RE.match(parsed.path)
        or _DETAIL_HREF_NEWHOME_RE.match(parsed.path)
    )
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Search-page parsing
# ---------------------------------------------------------------------------


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
    m = (
        _DETAIL_HREF_RE.match(href)
        or _DETAIL_HREF_RENT_RE.match(href)
        or _DETAIL_HREF_NEWHOME_RE.match(href)
    )
    if not m:
        return None
    listing_id = m.group(1)
    url = f"{_BASE}{href}"

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
        raw={"html": str(outer if outer is not None else anchor)},
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


# ---------------------------------------------------------------------------
# Detail-page parsing
# ---------------------------------------------------------------------------


def _parse_listing_html(
    html: str, *, listing_id: str, url: str
) -> ZooplaListingDetail:
    soup = BeautifulSoup(html, "html.parser")

    real_estate, breadcrumb = _extract_ldjson_blocks(soup)
    analytics = _extract_analytics_taxonomy(html)
    nts_info = _extract_nts_info(soup)

    title_text = _text_or_none(soup.title)
    meta_description: str | None = None
    md = soup.find("meta", attrs={"name": "description"})
    if md is not None:
        meta_description = (md.get("content") or "").strip() or None

    # Gallery: every lid.zoocdn.com URL (deduped, original order). Zoopla
    # ships variants like ``foo.jpg``, ``foo.jpg:p``, ``foo.jpg\`` (escape
    # leakage from the JSON-string source); strip the suffix so dedup
    # collapses them to one canonical URL.
    images: list[str] = []
    seen: set[str] = set()
    for src in re.findall(r'https://lid\.zoocdn\.com/[^"\'\s\\]+', html):
        clean = src.rstrip("\\")
        if clean.endswith(":p"):
            clean = clean[:-2]
        if clean not in seen:
            seen.add(clean)
            images.append(clean)

    return ZooplaListingDetail.build(
        listing_id=listing_id,
        url=url,
        title_text=title_text,
        meta_description=meta_description,
        real_estate=real_estate,
        breadcrumb=breadcrumb,
        analytics=analytics,
        nts_info=nts_info,
        images=images,
    )


def _extract_ldjson_blocks(soup: BeautifulSoup) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """Return ``(RealEstateListing, BreadcrumbList)`` JSON dicts.

    Either may be empty if the block is missing.
    """
    real_estate: Dict[str, Any] = {}
    breadcrumb: Dict[str, Any] = {}
    for s in soup.find_all("script", type="application/ld+json"):
        if not s.string:
            continue
        try:
            obj = json.loads(s.string)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        t = obj.get("@type")
        if t == "RealEstateListing":
            real_estate = obj
        elif t == "BreadcrumbList":
            breadcrumb = obj
    return real_estate, breadcrumb


def _extract_analytics_taxonomy(html: str) -> Dict[str, Any]:
    """Find the ``ListingAnalyticsTaxonomy`` object in the RSC chunks.

    Returns ``{}`` if absent or unparseable. Walks each
    ``self.__next_f.push([...])`` chunk, decodes the JS string, then
    locates the enclosing ``{...}`` around the marker and parses it.
    """
    for raw_chunk in _RSC_CHUNK_RE.findall(html):
        if "ListingAnalyticsTaxonomy" not in raw_chunk:
            continue
        try:
            decoded = raw_chunk.encode().decode("unicode_escape")
        except UnicodeDecodeError:
            continue
        marker_idx = decoded.find(_ANALYTICS_TAXONOMY_MARKER)
        if marker_idx < 0:
            continue
        # Walk backwards to the opening brace of the enclosing object.
        depth = 0
        start = marker_idx
        for j in range(marker_idx, -1, -1):
            ch = decoded[j]
            if ch == "}":
                depth += 1
            elif ch == "{":
                if depth == 0:
                    start = j
                    break
                depth -= 1
        else:
            continue
        # Walk forwards to the matching close brace.
        depth = 0
        end = start
        for j in range(start, len(decoded)):
            ch = decoded[j]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = j + 1
                    break
        try:
            obj = json.loads(decoded[start:end])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    return {}


def _extract_nts_info(soup: BeautifulSoup) -> Dict[str, str]:
    """Return a flat ``{label: value}`` dict from the
    ``<ul class='NtsInfo_ntsInfoList...'>`` block.

    Each ``<li>`` row has a title ``<p>`` and a value wrapper ``<div>``;
    we drop any nested ``<button>``/``<dialog>`` content so the value is
    just the displayed text.
    """
    out: Dict[str, str] = {}
    for li in soup.find_all("li", class_=_NTS_INFO_ITEM_RE):
        title_el = li.find("p", class_=_NTS_INFO_TITLE_RE)
        wrap = li.find("div", class_=_NTS_INFO_TEXT_WRAP_RE)
        if title_el is None or wrap is None:
            continue
        label = title_el.get_text(strip=True).rstrip(":").strip()
        # Take the first text-bearing <p> inside the value wrapper.
        value_el = wrap.find("p")
        value = value_el.get_text(" ", strip=True) if value_el else None
        if label and value:
            out[label] = value
    return out
