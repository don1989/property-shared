"""PrimeLocation scraper (search + listing detail) via curl_cffi.

PrimeLocation (primelocation.com) is a ZPG sibling of Zoopla and is
fronted by the same Cloudflare bot-mode gate, so plain ``requests`` is
blocked on TLS fingerprint. ``curl_cffi`` (libcurl-impersonate) replays
a real browser TLS/HTTP-2 fingerprint and gets clean ``200`` responses;
the same profile-rotation strategy as the Zoopla scraper is used.

PrimeLocation ships an older front-end template than Zoopla, so the
markup differs even though the data contracts overlap:

Search cards
    Each result is a ``<div class='ListingsSearchResultsCard_styles_
    listingRowStyle...' id='listing_{id}'>`` wrapper containing the
    detail anchor (``.../for-sale/details/{id}/``), the price
    (``data-testid='listing-price'`` / ``...priceTextStyle__...``),
    address (``...addressStyle__...``), amenities
    (``...amenityItemStyle__...``), attributes (``...attributeTextStyle__...``),
    status badges (``...statusListSlimStyle__...``) and agent logo
    (``...agentLogoImageStyle__...``). CSS-Modules hashes are matched by
    class-name *prefix* to absorb rotation.

Listing detail
    - ``<script type='application/ld+json'>`` ``RealEstateListing``
      (name, description, datePosted, offers.price/priceCurrency,
      additionalProperty Bedrooms/Bathrooms/Floor size) and
      ``BreadcrumbList`` (location hierarchy).
    - A ``<ul class='NtsInfo_styles_ntsInfoList...'>`` "Need to see
      info" block (Tenure, Service charge, Council tax band, Ground
      rent, ...).
    - Canonical attribute scalars (displayAddress, outcode, incode,
      listingStatus, listingCondition, tenure, branch info) live inside
      the React Server Components ``self.__next_f.push([...])`` stream
      as GraphQL fragments rather than one taxonomy object; they are
      flattened here into a Zoopla-shaped ``taxonomy`` dict.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup, Tag

from property_core.models.primelocation import (
    PrimeLocationListing,
    PrimeLocationListingDetail,
)


class PrimeLocationError(Exception):
    """Raised when PrimeLocation data cannot be fetched or parsed."""


_BASE = "https://www.primelocation.com"
_DEFAULT_IMPERSONATE = "chrome131"

# Profiles tried in order when the default (or caller-supplied) profile gets
# blocked by Cloudflare. Mirrors the Zoopla scraper: recent fingerprints first
# (Cloudflare scores partly on how *current* the TLS fingerprint looks), with
# proven older profiles kept as the rotation tail so there's still a valid
# option on older curl_cffi builds (the package ships to PyPI with
# curl_cffi>=0.7). Names the installed curl_cffi doesn't recognise are filtered
# out via _supported_profiles().
_FALLBACK_PROFILES: tuple[str, ...] = (
    "chrome131",
    "safari18_0_ios",
    "firefox135",
    "chrome120",
    "safari17_2_ios",
    "firefox133",
)

_DETAIL_HREF_RE = re.compile(r"^/for-sale/details/(\d+)/?")
_DETAIL_HREF_RENT_RE = re.compile(r"^/to-rent/details/(\d+)/?")
_DETAIL_HREF_NEWHOME_RE = re.compile(r"^/new-homes/details/(\d+)/?")

# Search-card CSS-Modules class prefixes (PrimeLocation template).
_CARD_ROW_RE = re.compile(r"^ListingsSearchResultsCard_styles_listingRowStyle")
_PRICE_TEXT_RE = re.compile(r"^ListingsSearchResultsCard_styles_priceTextStyle")
_PRICE_TITLE_RE = re.compile(r"^ListingsSearchResultsCard_styles_priceTitleStyle")
_ADDRESS_RE = re.compile(r"^ListingsSearchResultsCard_styles_addressStyle")
_SUMMARY_RE = re.compile(r"^ListingsSearchResultsCard_styles_summaryStyle")
_AMENITY_ITEM_RE = re.compile(r"^ListingsSearchResultsCard_styles_amenityItemStyle")
_ATTRIBUTE_TEXT_RE = re.compile(r"^ListingsSearchResultsCard_styles_attributeTextStyle")
_STATUS_LIST_RE = re.compile(r"^ListingsSearchResultsCard_styles_statusListSlimStyle")
_AGENT_LOGO_RE = re.compile(r"^ListingsSearchResultsCard_styles_agentLogoImageStyle")

# Listing-detail "Need to see info" rows (PrimeLocation: NtsInfo_styles_*).
_NTS_INFO_ITEM_RE = re.compile(r"^NtsInfo_styles_ntsInfoListItem")
_NTS_INFO_TITLE_RE = re.compile(r"^NtsInfo_styles_ntsInfoItemTitle")
_NTS_INFO_TEXT_WRAP_RE = re.compile(r"^NtsInfo_styles_ntsInfoItemTextWrapper")

_LISTING_ID_RE = re.compile(r"^listing_(\d+)$")
_PROPERTY_PHOTO_HOST = "lid.zoocdn.com"

_RSC_CHUNK_RE = re.compile(r'self\.__next_f\.push\(\[\d+,\s*"((?:[^"\\]|\\.)*)"\]\)')

# Scalar attributes pulled out of the flattened RSC stream. String keys are
# extracted with a quoted-string regex (so values containing commas survive);
# the int key is matched as a bare number.
_TAXONOMY_STR_KEYS: tuple[str, ...] = (
    "displayAddress",
    "outcode",
    "incode",
    "postalCode",
    "listingStatus",
    "listingCondition",
    "furnishedState",
    "propertyType",
    "tenure",
    "branchName",
    "logoUrl",
    "priceCurrency",
)
_TAXONOMY_INT_KEYS: tuple[str, ...] = (
    "branchId",
    "numBeds",
    "numBaths",
    "numRecepts",
    "sizeSqFeet",
)


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
) -> List[PrimeLocationListing]:
    """Fetch PrimeLocation search results across one or more pages.

    Args:
        search_url: Absolute PrimeLocation search URL (build with
            ``PrimeLocationLocationAPI``).
        timeout: HTTP request timeout in seconds.
        max_pages: Cap on pages walked. ``None`` walks until no more cards.
        rate_limit_seconds: Polite delay between page fetches.
        impersonate: ``curl_cffi`` browser-fingerprint profile to try first.
        fallback_profiles: Profiles tried in order if the first one is
            Cloudflare-blocked. Pass ``()`` to disable rotation.
        proxy: Optional proxy URL (e.g. residential proxy).

    Returns:
        List of ``PrimeLocationListing`` from all pages walked.

    Raises:
        ImportError: If ``curl_cffi`` is not installed.
        PrimeLocationError: If every profile is blocked (Cloudflare
            interstitial) or HTML structure parsing fails.
    """
    starting_page = _starting_page(search_url)

    session, first_html = _fetch_with_profile_rotation(
        url=search_url,
        impersonate=impersonate,
        fallback_profiles=fallback_profiles,
        proxy=proxy,
        timeout=timeout,
    )

    page_listings = _parse_search_html(first_html)
    if not page_listings:
        raise PrimeLocationError(
            f"No listings parsed from {search_url}. The page returned "
            f"{len(first_html)} bytes but no listing-card rows were found — "
            "PrimeLocation may have changed its markup."
        )

    listings: List[PrimeLocationListing] = []
    seen_ids: set[str] = set()

    def _add_new(page: List[PrimeLocationListing]) -> int:
        """Append listings whose id we haven't seen; return how many were new."""
        added = 0
        for listing in page:
            if listing.id not in seen_ids:
                seen_ids.add(listing.id)
                listings.append(listing)
                added += 1
        return added

    _add_new(page_listings)

    page_counter = 1
    seen_pages: set[str] = {search_url}
    while max_pages is None or page_counter < max_pages:
        next_url = _next_page_url(search_url, starting_page + page_counter)
        if next_url in seen_pages:
            break
        seen_pages.add(next_url)
        if rate_limit_seconds:
            time.sleep(rate_limit_seconds)
        page_counter += 1

        html = _get(session, next_url, timeout=timeout)
        page_listings = _parse_search_html(html)
        if not page_listings:
            break
        # Some ZPG search pages clamp an out-of-range pn back to the last valid
        # page, re-serving the same cards. Stop once a page adds no new ids,
        # otherwise an unbounded (max_pages=None) walk would loop forever.
        if _add_new(page_listings) == 0:
            break

    return listings


def fetch_listing(
    property_url_or_id: str,
    *,
    timeout: float = 25.0,
    impersonate: str = _DEFAULT_IMPERSONATE,
    fallback_profiles: tuple[str, ...] = _FALLBACK_PROFILES,
    proxy: str | None = None,
) -> PrimeLocationListingDetail:
    """Fetch full property details from an individual PrimeLocation page.

    Args:
        property_url_or_id: Full PrimeLocation URL or numeric listing id.
        timeout: HTTP request timeout in seconds.
        impersonate: ``curl_cffi`` browser-fingerprint profile to try first.
        fallback_profiles: Profiles tried in order if the first one is
            Cloudflare-blocked. Pass ``()`` to disable rotation.
        proxy: Optional proxy URL.

    Returns:
        ``PrimeLocationListingDetail`` populated from the ld+json
        ``RealEstateListing`` / ``BreadcrumbList``, the flattened RSC
        taxonomy, and the ``NtsInfo_styles_ntsInfoList`` rows.
    """
    url = _normalize_listing_url(property_url_or_id)
    listing_id = _id_from_url(url)
    if listing_id is None:
        raise PrimeLocationError(
            f"Could not extract PrimeLocation listing id from {property_url_or_id!r}"
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


def _supported_profiles(profiles: list[str]) -> list[str]:
    """Drop impersonation profiles the installed curl_cffi doesn't know.

    The package pins ``curl_cffi>=0.7`` and is published to PyPI, so a
    consumer may have an older build that lacks the newest fingerprint
    names. Filtering against the live ``BrowserType`` enum avoids wasting a
    rotation slot on a name the library would reject. If filtering would
    drop everything (or the enum can't be read), the input is returned
    unchanged and the request layer surfaces any error.
    """
    try:
        from curl_cffi.requests import BrowserType

        available = {e.value for e in BrowserType}
    except Exception:  # pragma: no cover - ancient/partial curl_cffi
        return profiles
    filtered = [p for p in profiles if p in available]
    return filtered or profiles


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

    Raises ``PrimeLocationError`` listing every profile that failed only
    if all of them did.
    """
    profiles = _supported_profiles(_profiles_to_try(impersonate, fallback_profiles))
    failures: list[str] = []
    for profile in profiles:
        session = _new_session(impersonate=profile, proxy=proxy)
        try:
            html = _get(session, url, timeout=timeout)
            return session, html
        except PrimeLocationError as exc:
            failures.append(f"{profile}: {exc}")
            continue
    raise PrimeLocationError(
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
            "PrimeLocation scraping requires curl_cffi. Install with: "
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
        raise PrimeLocationError(f"Request to {url} failed: {exc}") from exc

    status = getattr(response, "status_code", 0)
    text = getattr(response, "text", "")
    if status == 403 or "Just a moment" in text[:5000]:
        raise PrimeLocationError(
            f"PrimeLocation returned a Cloudflare challenge for {url} "
            f"(status {status}). Try a different impersonate= profile (e.g. "
            "'safari17_2_ios') or a residential proxy via the proxy= argument."
        )
    if status >= 400:
        raise PrimeLocationError(f"Request to {url} returned status {status}")
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
    from property_core.url_guard import validate_listing_url

    s = url_or_id.strip()
    if s.startswith("http"):
        return validate_listing_url(s, allowed_suffixes=("primelocation.com",))
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


def _parse_search_html(html: str) -> List[PrimeLocationListing]:
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.find_all("div", class_=_CARD_ROW_RE)
    out: List[PrimeLocationListing] = []
    for card in cards:
        listing = _parse_card(card)
        if listing is not None:
            out.append(listing)
    return out


def _card_listing_id(card: Tag) -> str | None:
    """Listing id from the ``id='listing_{id}'`` wrapper, falling back to the
    detail anchor href."""
    m = _LISTING_ID_RE.match(card.get("id") or "")
    if m:
        return m.group(1)
    for a in card.find_all("a", href=True):
        href = a["href"]
        am = (
            _DETAIL_HREF_RE.match(href)
            or _DETAIL_HREF_RENT_RE.match(href)
            or _DETAIL_HREF_NEWHOME_RE.match(href)
        )
        if am:
            return am.group(1)
    return None


def _card_detail_href(card: Tag) -> str | None:
    for a in card.find_all("a", href=True):
        href = a["href"]
        if (
            _DETAIL_HREF_RE.match(href)
            or _DETAIL_HREF_RENT_RE.match(href)
            or _DETAIL_HREF_NEWHOME_RE.match(href)
        ):
            return href
    return None


def _parse_card(card: Tag) -> Optional[PrimeLocationListing]:
    listing_id = _card_listing_id(card)
    href = _card_detail_href(card)
    if not listing_id or not href:
        return None
    url = f"{_BASE}{href}"

    display_price = _text_or_none(card.find("p", class_=_PRICE_TEXT_RE))
    price_qualifier = _text_or_none(card.find("p", class_=_PRICE_TITLE_RE))

    amenities = [
        t.get_text(" ", strip=True)
        for t in card.find_all("span", class_=_AMENITY_ITEM_RE)
        if t.get_text(strip=True)
    ]

    address = _text_or_none(card.find(class_=_ADDRESS_RE))
    summary = _text_or_none(card.find("p", class_=_SUMMARY_RE))

    premium_attributes = [
        t.get_text(" ", strip=True)
        for t in card.find_all("span", class_=_ATTRIBUTE_TEXT_RE)
        if t.get_text(strip=True)
    ]

    badges: list[str] = []
    status_list = card.find("ul", class_=_STATUS_LIST_RE)
    if status_list is not None:
        for li in status_list.find_all("li"):
            txt = li.get_text(" ", strip=True)
            if txt:
                badges.append(txt)

    agent_name: str | None = None
    agent_logo: str | None = None
    agent_img = card.find("img", class_=_AGENT_LOGO_RE)
    if agent_img is not None:
        agent_name = (agent_img.get("alt") or None) or None
        agent_logo = agent_img.get("src") or None

    images: list[str] = []
    seen: set[str] = set()
    for im in card.find_all(["img", "source"]):
        for attr in ("src", "srcset"):
            val = im.get(attr) or ""
            for token in re.findall(r"https://lid\.zoocdn\.com/[^\s,]+", val):
                clean = token.split(":p")[0]
                if clean not in seen:
                    seen.add(clean)
                    images.append(clean)

    return PrimeLocationListing.build(
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
        raw={"html": str(card)},
    )


def _text_or_none(tag: Tag | None) -> str | None:
    if tag is None:
        return None
    txt = tag.get_text(" ", strip=True)
    return txt or None


# ---------------------------------------------------------------------------
# Detail-page parsing
# ---------------------------------------------------------------------------


def _parse_listing_html(
    html: str, *, listing_id: str, url: str
) -> PrimeLocationListingDetail:
    soup = BeautifulSoup(html, "html.parser")

    real_estate, breadcrumb = _extract_ldjson_blocks(soup)
    taxonomy = _extract_taxonomy(html)
    nts_info = _extract_nts_info(soup)

    title_text = _text_or_none(soup.title)
    meta_description: str | None = None
    md = soup.find("meta", attrs={"name": "description"})
    if md is not None:
        meta_description = (md.get("content") or "").strip() or None

    # Gallery: every lid.zoocdn.com URL (deduped, original order). Variants
    # like ``foo.jpg`` / ``foo.jpg:p`` / ``foo.jpg\`` collapse to one URL.
    images: list[str] = []
    seen: set[str] = set()
    for src in re.findall(r'https://lid\.zoocdn\.com/[^"\'\s\\]+', html):
        clean = src.rstrip("\\")
        if clean.endswith(":p"):
            clean = clean[:-2]
        if clean not in seen:
            seen.add(clean)
            images.append(clean)

    return PrimeLocationListingDetail.build(
        listing_id=listing_id,
        url=url,
        title_text=title_text,
        meta_description=meta_description,
        real_estate=real_estate,
        breadcrumb=breadcrumb,
        taxonomy=taxonomy,
        nts_info=nts_info,
        images=images,
    )


def _extract_ldjson_blocks(soup: BeautifulSoup) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """Return ``(RealEstateListing, BreadcrumbList)`` JSON dicts.

    Either may be empty if the block is missing. Handles both top-level
    dicts and lists / ``@graph`` wrappers.
    """
    real_estate: Dict[str, Any] = {}
    breadcrumb: Dict[str, Any] = {}

    def _consider(obj: Any) -> None:
        nonlocal real_estate, breadcrumb
        if not isinstance(obj, dict):
            return
        t = obj.get("@type")
        if t == "RealEstateListing" and not real_estate:
            real_estate = obj
        elif t == "BreadcrumbList" and not breadcrumb:
            breadcrumb = obj

    for s in soup.find_all("script", type="application/ld+json"):
        if not s.string:
            continue
        try:
            parsed = json.loads(s.string)
        except json.JSONDecodeError:
            continue
        candidates = parsed if isinstance(parsed, list) else [parsed]
        for obj in candidates:
            if isinstance(obj, dict) and isinstance(obj.get("@graph"), list):
                for inner in obj["@graph"]:
                    _consider(inner)
            else:
                _consider(obj)
    return real_estate, breadcrumb


def _decode_rsc(html: str) -> str:
    """Concatenate and unescape every ``self.__next_f.push([...])`` chunk.

    Each chunk is the body of a JSON-encoded string, so it is decoded by
    wrapping it back in quotes and running ``json.loads`` — this resolves
    ``\\uXXXX`` / ``\\n`` / ``\\"`` escapes while leaving literal UTF-8
    bytes intact. The previous ``encode().decode('unicode_escape')`` path
    round-tripped through Latin-1 and corrupted real UTF-8 (e.g. ``£`` ->
    ``Â£``), garbling addresses, agent names and price strings. The lenient
    old method is kept only as a fallback for any chunk JSON can't parse.
    """
    out: list[str] = []
    for raw_chunk in _RSC_CHUNK_RE.findall(html):
        try:
            out.append(json.loads(f'"{raw_chunk}"'))
        except (json.JSONDecodeError, ValueError):
            try:
                out.append(raw_chunk.encode().decode("unicode_escape"))
            except (UnicodeDecodeError, ValueError):
                continue
    return "".join(out)


def _extract_taxonomy(html: str) -> Dict[str, Any]:
    """Flatten the canonical attribute scalars out of the RSC stream.

    PrimeLocation has no single ``ListingAnalyticsTaxonomy`` object;
    instead the values live as scalars across GraphQL fragments
    (``ListingLocation``, ``ListingBranch``, ...). We pull a curated set
    of keys into a flat dict keyed Zoopla-style so the model's
    ``build()`` can consume it. The first occurrence of each key wins.

    Returns ``{}`` if the RSC stream is absent or yields nothing.
    """
    decoded = _decode_rsc(html)
    if not decoded:
        return {}

    out: Dict[str, Any] = {}
    for key in _TAXONOMY_STR_KEYS:
        m = re.search(rf'"{key}"\s*:\s*"((?:[^"\\]|\\.)*)"', decoded)
        if m:
            out[key] = m.group(1)
    for key in _TAXONOMY_INT_KEYS:
        m = re.search(rf'"{key}"\s*:\s*(-?\d+)', decoded)
        if m:
            out[key] = m.group(1)

    # Map PrimeLocation-native keys onto the Zoopla-shaped names the model
    # already understands (postalCode -> location, logoUrl -> branchLogoUrl).
    if "postalCode" in out:
        out.setdefault("location", out["postalCode"])
    if "logoUrl" in out:
        out.setdefault("branchLogoUrl", out["logoUrl"])
    if "priceCurrency" in out:
        out.setdefault("currencyCode", out["priceCurrency"])
    return out


def _extract_nts_info(soup: BeautifulSoup) -> Dict[str, str]:
    """Return a flat ``{label: value}`` dict from the
    ``<ul class='NtsInfo_styles_ntsInfoList...'>`` block.

    Each ``<li>`` row has a title ``<p>`` and a value wrapper; nested
    ``<button>``/``<dialog>`` content is dropped so the value is just the
    displayed text.
    """
    out: Dict[str, str] = {}
    for li in soup.find_all("li", class_=_NTS_INFO_ITEM_RE):
        title_el = li.find("p", class_=_NTS_INFO_TITLE_RE)
        wrap = li.find("div", class_=_NTS_INFO_TEXT_WRAP_RE)
        if title_el is None or wrap is None:
            continue
        label = title_el.get_text(strip=True).rstrip(":").strip()
        value_el = wrap.find("p")
        value = value_el.get_text(" ", strip=True) if value_el else None
        if label and value:
            out[label] = value
    return out
