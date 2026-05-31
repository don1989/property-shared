"""OnTheMarket scraper (search + listing detail).

Plain ``requests`` works against onthemarket.com — no Cloudflare interstitial,
no JS challenge. Search cards are Schema.org microdata; listing detail
exposes a ``dataLayer.push({...})`` blob for canonical fields plus a
``<h2>Key information</h2>`` section for tenure / EPC / council tax.

The function signatures mirror ``rightmove_scraper`` so consumers can keep
the same shape.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup, Tag
from requests import Response, Session
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from property_core.models.onthemarket import OnTheMarketListing, OnTheMarketListingDetail


_BASE = "https://www.onthemarket.com"

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-GB,en;q=0.9",
}


class RetryableError(Exception):
    """Raised for transient errors that should trigger a retry."""


class OnTheMarketError(Exception):
    """Raised when OnTheMarket data cannot be fetched or parsed."""


class OnTheMarketLocationNotFound(OnTheMarketError):
    """Raised when OnTheMarket returns a 404 with a ``Location 'X' not
    recognised`` interstitial — i.e. the search slug doesn't map to any
    location OnTheMarket knows about. Callers see this instead of an empty
    listing set so the failure mode is unambiguous."""

    def __init__(self, slug: str, url: str) -> None:
        self.slug = slug
        self.url = url
        super().__init__(
            f"OnTheMarket does not recognise location slug {slug!r} "
            f"(GET {url} -> 404). Check the slug on the OnTheMarket "
            f"website's search box and adjust the postcode/area string."
        )


_DETAIL_HREF_RE = re.compile(r"^/details/(\d+)/?")
_LOCATION_NOT_RECOGNISED_RE = re.compile(
    r"Location ['\"]([^'\"]+)['\"] not recognised", re.IGNORECASE
)
_DATALAYER_RE = re.compile(
    r"dataLayer\.push\s*\(\s*(\{.*?\})\s*\)\s*;?", re.DOTALL
)
_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL
)
# Tokens that mark a media URL as something other than a property photo —
# floor plans and the EPC graph live in the same image arrays as photos.
_NON_PHOTO_TOKENS = ("epc-graph", "floor-plan", "floorplan")


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def fetch_listings(
    search_url: str,
    *,
    timeout: float = 15.0,
    max_pages: Optional[int] = None,
    rate_limit_seconds: float = 0.6,
    retry_attempts: int = 3,
    retry_backoff: float = 1.5,
) -> List[OnTheMarketListing]:
    """Fetch OnTheMarket search results across one or more pages.

    Args:
        search_url: Absolute OnTheMarket search URL (build with
            ``OnTheMarketLocationAPI``).
        timeout: HTTP request timeout in seconds.
        max_pages: Cap on pages walked. ``None`` = walk until no more cards.
        rate_limit_seconds: Polite delay between page fetches.
        retry_attempts: Number of retries on transient errors.
        retry_backoff: Exponential backoff multiplier.

    Returns:
        List of ``OnTheMarketListing``.
    """
    listings: List[OnTheMarketListing] = []
    next_url: str | None = search_url
    page_counter = 0
    # Honour an existing ?page=N in the caller's URL: subsequent pages step
    # forward from there.
    starting_page = _starting_page(search_url)
    seen_urls: set[str] = set()
    session = Session()

    while next_url:
        if next_url in seen_urls:
            break
        seen_urls.add(next_url)

        if rate_limit_seconds and page_counter > 0:
            time.sleep(rate_limit_seconds)
        page_counter += 1

        response = _get_with_retries(
            session=session,
            url=next_url,
            timeout=timeout,
            retry_attempts=retry_attempts,
            retry_backoff=retry_backoff,
        )
        page_listings = _parse_search_html(response.text)
        listings.extend(page_listings)

        if max_pages is not None and page_counter >= max_pages:
            break

        if not page_listings:
            break

        next_url = _next_page_url(search_url, starting_page + page_counter)

    return listings


def _starting_page(search_url: str) -> int:
    """Return the ``page=N`` value from ``search_url``, defaulting to ``1``."""
    parsed = urlparse(search_url)
    query_items = dict(parse_qsl(parsed.query, keep_blank_values=True))
    try:
        return max(1, int(query_items.get("page", "1")))
    except (TypeError, ValueError):
        return 1


def fetch_listing(
    property_url_or_id: str,
    *,
    timeout: float = 15.0,
    retry_attempts: int = 3,
    retry_backoff: float = 1.5,
) -> OnTheMarketListingDetail:
    """Fetch full property details from an individual OnTheMarket listing page.

    Args:
        property_url_or_id: Full OnTheMarket URL or just the numeric id.
        timeout: HTTP request timeout in seconds.
        retry_attempts: Number of retries on transient errors.
        retry_backoff: Exponential backoff multiplier.

    Returns:
        ``OnTheMarketListingDetail`` populated from the dataLayer + HTML.
    """
    url = _normalize_listing_url(property_url_or_id)
    listing_id = _id_from_url(url)
    if listing_id is None:
        raise OnTheMarketError(f"Could not extract listing id from {property_url_or_id!r}")

    session = Session()
    response = _get_with_retries(
        session=session,
        url=url,
        timeout=timeout,
        retry_attempts=retry_attempts,
        retry_backoff=retry_backoff,
    )
    return _parse_detail_html(response.text, listing_id=listing_id, url=url)


# ---------------------------------------------------------------------------
# HTTP plumbing
# ---------------------------------------------------------------------------


def _make_request(session: Session, url: str, timeout: float) -> Response:
    try:
        response = session.get(url, headers=_DEFAULT_HEADERS, timeout=timeout)
    except requests.RequestException as exc:
        raise RetryableError(f"Network error: {exc}") from exc

    if response.status_code == 429 or response.status_code >= 500:
        raise RetryableError(f"Server responded with {response.status_code}")
    if response.status_code == 404:
        # OTM returns 404 + a "Location 'X' not recognised" interstitial when
        # the search slug doesn't exist (rather than redirecting or 200ing with
        # zero results). Surface that as a typed exception so callers don't
        # confuse it with a transient failure or with a genuinely empty
        # search.
        match = _LOCATION_NOT_RECOGNISED_RE.search(response.text)
        if match:
            raise OnTheMarketLocationNotFound(slug=match.group(1), url=url)
    if response.status_code >= 400:
        raise OnTheMarketError(f"Request failed with status code {response.status_code}")
    return response


def _get_with_retries(
    *,
    session: Session,
    url: str,
    timeout: float,
    retry_attempts: int = 3,
    retry_backoff: float = 1.5,
) -> Response:
    @retry(
        stop=stop_after_attempt(retry_attempts),
        wait=wait_exponential(multiplier=retry_backoff, min=1, max=30),
        retry=retry_if_exception_type(RetryableError),
        reraise=True,
    )
    def _fetch() -> Response:
        return _make_request(session, url, timeout)

    try:
        return _fetch()
    except RetryableError as exc:
        raise OnTheMarketError(
            f"Request failed after {retry_attempts} retries: {exc}"
        ) from exc


def _next_page_url(search_url: str, next_page: int) -> str:
    parsed = urlparse(search_url)
    query_items = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query_items["page"] = str(next_page)
    new_query = urlencode(query_items, doseq=True)
    return urlunparse(parsed._replace(query=new_query))


def _normalize_listing_url(url_or_id: str) -> str:
    from property_core.url_guard import validate_listing_url

    s = url_or_id.strip()
    if s.startswith("http"):
        return validate_listing_url(s, allowed_suffixes=("onthemarket.com",))
    return f"{_BASE}/details/{s}/"


def _id_from_url(url: str) -> str | None:
    parsed = urlparse(url)
    m = _DETAIL_HREF_RE.match(parsed.path)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Search-page parsing
# ---------------------------------------------------------------------------


def _parse_search_html(html: str) -> List[OnTheMarketListing]:
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.find_all("article", attrs={"data-component": "search-result-property-card"})
    out: List[OnTheMarketListing] = []
    for card in cards:
        listing = _parse_search_card(card)
        if listing is not None:
            out.append(listing)
    return out


def _parse_search_card(card: Tag) -> Optional[OnTheMarketListing]:
    detail_link = card.find("a", href=_DETAIL_HREF_RE)
    if detail_link is None:
        return None
    href = detail_link.get("href", "")
    m = _DETAIL_HREF_RE.match(href)
    if m is None:
        return None
    listing_id = m.group(1)
    url = f"{_BASE}{href}" if href.startswith("/") else href

    display_price = _text_or_none(
        card.find(attrs={"data-component": "price-title"})
    )

    bedrooms_text: str | None = None
    bathrooms_text: str | None = None
    # Bedrooms have a canonical Schema.org hook; prefer it over span position.
    bedroom_el = card.find(attrs={"itemprop": "numberOfBedrooms"})
    if bedroom_el is not None:
        bedrooms_text = bedroom_el.get_text(strip=True) or None
    bb = card.find(attrs={"data-component": "BedBathCounts"})
    if bb is not None:
        # Bathrooms have no itemprop, but BedBathCounts is always
        # "<bedrooms span> <bathrooms span>" — pick the second span with
        # non-empty digit text to skip any decorative/icon spans.
        digit_spans = [
            s.get_text(strip=True)
            for s in bb.find_all("span")
            if s.get_text(strip=True).strip().isdigit()
        ]
        if bedrooms_text is None and digit_spans:
            bedrooms_text = digit_spans[0]
        if len(digit_spans) >= 2:
            bathrooms_text = digit_spans[1]

    address = _text_or_none(card.find(attrs={"itemprop": "address"}))

    summary: str | None = None
    desc_meta = card.find("meta", attrs={"itemprop": "description"})
    if desc_meta is not None:
        summary = (desc_meta.get("content") or "").strip() or None

    postcode = _meta_content(card, "postalCode")
    locality = _meta_content(card, "addressLocality")

    pill = card.find(attrs={"data-component": "pill"})
    status = _text_or_none(pill)

    added_text: str | None = None
    for span in card.find_all("span"):
        txt = span.get_text(strip=True)
        if txt.startswith("Added"):
            added_text = txt
            break

    agent_panel = card.find(attrs={"data-component": "agent-panel"})
    agent_name: str | None = None
    agent_telephone: str | None = None
    if agent_panel is not None:
        name_el = agent_panel.find(attrs={"itemprop": "name"})
        if name_el is not None:
            # itemprop=name may be a plain element or a meta tag.
            if name_el.name == "meta":
                agent_name = (name_el.get("content") or "").strip() or None
            else:
                agent_name = name_el.get_text(strip=True) or None
        tel_el = agent_panel.find(attrs={"itemprop": "telephone"})
        if tel_el is not None:
            agent_telephone = tel_el.get_text(strip=True) or None

    images: list[str] = []
    seen: set[str] = set()
    for im in card.find_all("img", attrs={"itemprop": "contentUrl"}):
        src = im.get("src") or ""
        if src and src not in seen:
            seen.add(src)
            images.append(src)
    if not images:
        # Spotlight cards put the first image inside the swiper without itemprop.
        for im in card.find_all("img"):
            src = im.get("src") or ""
            if "media.onthemarket.com" in src and src not in seen:
                seen.add(src)
                images.append(src)

    return OnTheMarketListing.build(
        listing_id=listing_id,
        url=url,
        display_price=display_price,
        bedrooms_text=bedrooms_text,
        bathrooms_text=bathrooms_text,
        address=address,
        summary=summary,
        postcode=postcode,
        locality=locality,
        status=status,
        added_text=added_text,
        agent_name=agent_name,
        agent_telephone=agent_telephone,
        images=images,
        raw={"html": str(card)},
    )


def _meta_content(card: Tag, itemprop: str) -> Optional[str]:
    meta = card.find("meta", attrs={"itemprop": itemprop})
    if meta is None:
        return None
    val = (meta.get("content") or "").strip()
    return val or None


def _text_or_none(tag: Tag | None) -> str | None:
    if tag is None:
        return None
    txt = tag.get_text(strip=True)
    return txt or None


# ---------------------------------------------------------------------------
# Detail-page parsing
# ---------------------------------------------------------------------------


def _parse_detail_html(
    html: str, *, listing_id: str, url: str
) -> OnTheMarketListingDetail:
    soup = BeautifulSoup(html, "html.parser")

    data_layer = _extract_data_layer(html)

    title = _text_or_none(soup.h1)

    description: str | None = None
    desc_el = soup.find(attrs={"itemprop": "description"})
    if desc_el is not None:
        if desc_el.name == "meta":
            description = (desc_el.get("content") or "").strip() or None
        else:
            description = desc_el.get_text("\n", strip=True) or None

    meta_description: str | None = None
    md = soup.find("meta", attrs={"name": "description"})
    if md is not None:
        meta_description = (md.get("content") or "").strip() or None

    # The detail page's price-title elements are the "similar properties"
    # sidebar, NOT the listing itself. The listing's canonical price lives
    # only in the dataLayer payload, so we derive display_price from it.
    display_price: str | None = None
    if data_layer.get("price"):
        display_price = f"£{data_layer['price']}"

    images = _extract_detail_images(html, soup)

    key_information = _extract_key_information(soup)

    nearest_stations = _stations_from_next_data(html)

    return OnTheMarketListingDetail.build(
        listing_id=listing_id,
        url=url,
        data_layer=data_layer,
        title=title,
        description=description,
        meta_description=meta_description,
        display_price=display_price,
        images=images,
        key_information=key_information,
        nearest_stations=nearest_stations,
    )


def _extract_data_layer(html: str) -> Dict[str, Any]:
    """Extract the FIRST ``dataLayer.push({...})`` payload that contains
    ``"property-id"`` — that's the canonical listing record.

    Returns an empty dict if not found.
    """
    matches = _DATALAYER_RE.findall(html)
    for raw in matches:
        if "property-id" not in raw:
            continue
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            continue
    return {}


def _extract_detail_images(html: str, soup: BeautifulSoup) -> List[str]:
    """Return property-photo URLs for a listing detail page.

    Tries, in order, preferring the fullest gallery available:
      1. The ``__NEXT_DATA__`` redux blob's
         ``props.initialReduxState.property.images`` array — the full gallery.
      2. The ``<div data-component='hero-images'>`` DOM section — the visible
         subset (typically the first few images).
      3. The ``og:image`` meta tag — a single hero image.

    EPC graphs and floor plans are filtered out. URLs are deduped (order
    preserved) and restricted to absolute ``https://`` URLs. Returns ``[]``
    on any parse failure rather than raising — markup drift degrades to an
    empty gallery, never a crash.
    """
    images = _images_from_next_data(html)
    if not images:
        images = _images_from_hero(soup)
    if not images:
        images = _images_from_og_image(soup)
    return _clean_image_urls(images)


def _images_from_next_data(html: str) -> List[str]:
    """Pull the full gallery from the ``__NEXT_DATA__`` redux state.

    Each image entry carries a high-res ``largeUrl`` (falling back to the
    thumbnail ``url``) and an ``isImage`` flag.
    """
    match = _NEXT_DATA_RE.search(html)
    if not match:
        return []
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return []
    try:
        items = data["props"]["initialReduxState"]["property"]["images"]
    except (KeyError, TypeError):
        return []
    if not isinstance(items, list):
        return []
    out: List[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("isImage") is False:
            continue
        url = item.get("largeUrl") or item.get("url")
        if isinstance(url, str) and url:
            out.append(url)
    return out


_STATION_DISTANCE_RE = re.compile(r"([\d.]+)\s*mi", re.IGNORECASE)


def _stations_from_next_data(html: str) -> List[dict]:
    """Pull nearby stations from the ``__NEXT_DATA__`` redux state.

    Each entry carries a ``name`` / ``fullName``, a ``displayDistance`` like
    ``"0.2mi."`` and ``allNetworks`` (Tube / Rail / ...). Returned in the
    ``{name, distance, unit, types}`` shape the canonical mapper expects.
    Returns ``[]`` on any parse failure rather than raising, so markup drift
    degrades to no stations instead of a crash.
    """
    match = _NEXT_DATA_RE.search(html)
    if not match:
        return []
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return []
    try:
        items = data["props"]["initialReduxState"]["property"]["station"]
    except (KeyError, TypeError):
        return []
    if not isinstance(items, list):
        return []
    out: List[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = item.get("fullName") or item.get("name")
        if not isinstance(name, str) or not name:
            continue
        miles: float | None = None
        display = item.get("displayDistance")
        if isinstance(display, str):
            m = _STATION_DISTANCE_RE.search(display)
            if m:
                try:
                    miles = float(m.group(1))
                except ValueError:
                    miles = None
        networks = item.get("allNetworks") or []
        types = [n.get("type") for n in networks if isinstance(n, dict) and n.get("type")]
        out.append({"name": name, "distance": miles, "unit": "miles", "types": types})
    return out


def _images_from_hero(soup: BeautifulSoup) -> List[str]:
    hero = soup.find(attrs={"data-component": "hero-images"})
    if hero is None:
        return []
    out: List[str] = []
    for im in hero.find_all("img"):
        src = im.get("src") or im.get("data-src") or ""
        if src:
            out.append(src)
    return out


def _images_from_og_image(soup: BeautifulSoup) -> List[str]:
    og = soup.find("meta", attrs={"property": "og:image"})
    if og is None:
        return []
    content = (og.get("content") or "").strip()
    return [content] if content else []


def _clean_image_urls(urls: List[str]) -> List[str]:
    """Dedupe, drop non-photo media (EPC graphs / floor plans), keep only
    absolute ``https://`` URLs and preserve order."""
    out: List[str] = []
    seen: set[str] = set()
    for url in urls:
        if not isinstance(url, str):
            continue
        u = url.strip()
        if not u.startswith("https://"):
            continue
        low = u.lower()
        if any(token in low for token in _NON_PHOTO_TOKENS):
            continue
        if u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out


def _extract_key_information(soup: BeautifulSoup) -> Dict[str, str]:
    """Walk the ``<h2>Key information</h2>`` section and return a flat
    label -> value dict. Each child ``<div>`` of the section has two
    ``<span>`` children: first is the label (with an icon), second is the value.
    """
    out: Dict[str, str] = {}
    heading = None
    for h in soup.find_all(["h2", "h3"]):
        if h.get_text(strip=True).lower() == "key information":
            heading = h
            break
    if heading is None:
        return out

    container = None
    for sib in heading.next_siblings:
        if hasattr(sib, "name") and sib.name == "div":
            container = sib
            break
    if container is None:
        return out

    for row in container.find_all("div", recursive=False):
        spans = row.find_all("span", recursive=False)
        if len(spans) < 2:
            continue
        label = spans[0].get_text(strip=True).rstrip(":").strip()
        value = spans[1].get_text(" ", strip=True)
        if label:
            out[label] = value
    return out
