"""NewHomesForSale.co.uk scraper.

Transport for ``newhomesforsale.co.uk`` — a UK new-build aggregator
that lists ~2,600 developments at any time, including developer-direct
stock that doesn't always reach Rightmove / OnTheMarket / Zoopla.

The search pages render listing cards server-side as plain HTML; no
JS execution is required and there is no Cloudflare gate. The detail
pages are marketing/enquiry-form pages with very little structured
data beyond the JSON-LD ``RealEstateListing`` blob (and that JSON-LD
is malformed in places). Consequently this module focuses on
search-card extraction; ``fetch_listing`` is provided as a thin
fallback that returns whatever can be pulled from the detail page.
"""

from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, Tag
from requests import Response, Session
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from property_core.models.newhomesforsale import (
    NewHomesForSaleDevelopment,
    NewHomesForSaleDevelopmentDetail,
)

_BASE = "https://www.newhomesforsale.co.uk"

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
}


class NewHomesForSaleError(Exception):
    """Raised when NewHomesForSale data cannot be fetched or parsed."""


class RetryableError(Exception):
    """Raised for transient errors that should trigger a retry."""


_DEV_ID_RE = re.compile(r"^dev_(\d+)$")
_POSTCODE_RE = re.compile(r"\b([A-Z]{1,2}\d{1,2}[A-Z]?\s*\d[A-Z]{2})\b")
# A price amount is a £ followed by a comma-grouped number with no
# trailing magnitude letter (so "£12k", "£1.5bn", "£475m" never match a
# headline price - those are marketing/incidental amounts, not the
# development's asking price).
_PRICE_AMOUNT_RE = re.compile(r"£\s*([\d][\d,]*)(?![\d.,]*[a-zA-Z])")
_PRICE_RANGE_RE = re.compile(
    r"£\s*([\d][\d,]*)(?![\d.,]*[a-zA-Z])\s*(?:-|–|to)\s*"
    r"£?\s*([\d][\d,]*)(?![\d.,]*[a-zA-Z])"
)
# Plausible new-build asking price floor; anything below this is a parse
# artefact (plot counts, "£12k stamp duty", share fractions of pounds).
_MIN_PLAUSIBLE_PRICE = 10_000
_DISTANCE_MILES_RE = re.compile(r"approximately\s+([\d.]+)\s*miles?")


def fetch_listings(
    search_url: str,
    *,
    timeout: float = 15.0,
    rate_limit_seconds: float = 0.4,
    retry_attempts: int = 3,
    retry_backoff: float = 1.5,
) -> list[NewHomesForSaleDevelopment]:
    """Fetch NewHomesForSale developments from a search URL.

    NewHomesForSale paginates with ``?page=N``; this implementation
    fetches the single page that ``search_url`` points to. Most NHFS
    searches fit on one page (default ~20 cards) — callers that need
    deeper paging can construct successive ``?page=N`` URLs themselves
    and concatenate.

    Args:
        search_url: Absolute NewHomesForSale URL (build with
            :class:`property_core.newhomesforsale_location.NewHomesForSaleLocationAPI`).
        timeout: HTTP request timeout in seconds.
        rate_limit_seconds: Sleep before the request to be polite on
            repeated calls. Set to 0 to disable.
        retry_attempts / retry_backoff: tenacity retry on transient
            5xx / network failures.

    Returns:
        List of :class:`NewHomesForSaleDevelopment`.

    Raises:
        NewHomesForSaleError: when the HTTP request fails permanently
            or the page contains no recognisable development cards.
    """
    if rate_limit_seconds:
        time.sleep(rate_limit_seconds)

    session = Session()
    response = _get_with_retries(
        session=session,
        url=search_url,
        timeout=timeout,
        retry_attempts=retry_attempts,
        retry_backoff=retry_backoff,
    )
    return _parse_search_html(response.text)


def fetch_listing(
    url_or_id: str,
    *,
    timeout: float = 15.0,
    rate_limit_seconds: float = 0.4,
    retry_attempts: int = 3,
    retry_backoff: float = 1.5,
) -> NewHomesForSaleDevelopmentDetail:
    """Fetch a single NewHomesForSale development detail page.

    NHFS detail pages are deliberately sparse — most useful fields are
    duplicated from the search card. The returned model captures the
    reliably-parseable subset (title, postcode, address, og-tags); for
    richer listing data use ``fetch_listings`` and read the
    ``NewHomesForSaleDevelopment`` records.

    Args:
        url_or_id: Absolute NHFS URL like
            ``"https://www.newhomesforsale.co.uk/new-homes/.../foo-bar/"``.
            Numeric development ids cannot be resolved without a prior
            search (NHFS detail URLs include a county/town/slug path),
            so they are rejected with :class:`NewHomesForSaleError`.

    Raises:
        NewHomesForSaleError: on bad input, persistent network failure,
            or upstream HTTP error.
    """
    if not url_or_id.startswith("http"):
        raise NewHomesForSaleError(
            "fetch_listing requires an absolute URL; numeric ids cannot be resolved "
            "without a prior search (NHFS detail URLs include a county/town/slug path)"
        )
    if rate_limit_seconds:
        time.sleep(rate_limit_seconds)

    session = Session()
    response = _get_with_retries(
        session=session,
        url=url_or_id,
        timeout=timeout,
        retry_attempts=retry_attempts,
        retry_backoff=retry_backoff,
    )
    return _parse_detail_html(response.text, response.url)


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
    if response.status_code >= 400:
        raise NewHomesForSaleError(
            f"Request failed with status code {response.status_code}"
        )
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
        raise NewHomesForSaleError(
            f"Request failed after {retry_attempts} retries: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# HTML parsing
# ---------------------------------------------------------------------------


def _parse_search_html(html: str) -> list[NewHomesForSaleDevelopment]:
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.find_all(class_="developmentSummary")
    out: list[NewHomesForSaleDevelopment] = []
    for card in cards:
        if not isinstance(card, Tag):
            continue
        dev = _parse_card(card)
        if dev is not None:
            out.append(dev)
    return out


def _parse_card(card: Tag) -> NewHomesForSaleDevelopment | None:
    raw_id = card.get("id") or ""
    if isinstance(raw_id, list):
        raw_id = raw_id[0] if raw_id else ""
    m = _DEV_ID_RE.match(raw_id)
    if not m:
        return None
    dev_id = m.group(1)

    h2 = card.find("h2")
    name = h2.get_text(strip=True) if h2 else None
    if not name:
        return None

    detail_url = _extract_detail_url(card)
    developer = _extract_developer(card)
    hero_image = _extract_hero_image(card)
    distance_text = _extract_distance_text(card)
    distance_miles = _parse_distance_miles(distance_text)
    photo_count = _extract_photo_count(card)

    text_lines = [
        line.strip()
        for line in card.get_text(separator="\n", strip=True).split("\n")
        if line.strip()
    ]
    address, postcode, locality, region = _extract_address(text_lines)
    bedrooms_text, bedrooms_min, bedrooms_max, property_type = _extract_unit_info(
        text_lines
    )
    price_min, price_max = _extract_price_range(card)
    description = _extract_description(text_lines)

    return NewHomesForSaleDevelopment(
        id=dev_id,
        name=name,
        url=detail_url or "",
        developer=developer,
        address=address,
        postcode=postcode,
        locality=locality,
        region=region,
        property_type=property_type,
        bedrooms_text=bedrooms_text,
        bedrooms_min=bedrooms_min,
        bedrooms_max=bedrooms_max,
        price_min=price_min,
        price_max=price_max,
        description=description,
        hero_image=hero_image,
        photo_count=photo_count,
        distance_text=distance_text,
        distance_miles=distance_miles,
        raw=None,
    )


def _extract_detail_url(card: Tag) -> str | None:
    for anchor in card.find_all("a", href=True):
        href = anchor["href"]
        if isinstance(href, list):
            href = href[0]
        # Detail URLs have at least 4 segments: /new-homes/{county}/{town}/{dev-slug}/
        if href.startswith("/new-homes/") and href.count("/") >= 5:
            return urljoin(_BASE, href)
    return None


def _extract_developer(card: Tag) -> str | None:
    for img in card.find_all("img"):
        src = img.get("src") or ""
        if isinstance(src, list):
            src = src[0] if src else ""
        if "/developer/" in src and img.get("alt"):
            alt = img["alt"]
            return alt[0] if isinstance(alt, list) else alt
    return None


def _extract_hero_image(card: Tag) -> str | None:
    gallery = card.find(class_="gallery")
    if not isinstance(gallery, Tag):
        return None
    style = gallery.get("style") or ""
    if isinstance(style, list):
        style = style[0] if style else ""
    m = re.search(r"url\('?([^')]+)'?\)", style)
    if not m:
        return None
    return urljoin(_BASE, m.group(1))


def _extract_distance_text(card: Tag) -> str | None:
    el = card.find(
        attrs={"data-bs-content": lambda v: isinstance(v, str) and "approximately" in v}
    )
    if not isinstance(el, Tag):
        return None
    value = el["data-bs-content"]
    return value[0] if isinstance(value, list) else value


def _parse_distance_miles(distance_text: str | None) -> float | None:
    if not distance_text:
        return None
    m = _DISTANCE_MILES_RE.search(distance_text)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _extract_photo_count(card: Tag) -> int | None:
    el = card.find(class_="imageCount")
    if not isinstance(el, Tag):
        return None
    text = el.get_text(strip=True)
    digits = re.search(r"\d+", text)
    return int(digits.group(0)) if digits else None


def _extract_address(
    text_lines: list[str],
) -> tuple[str | None, str | None, str | None, str | None]:
    """Find the address line and split it into postcode / locality / region.

    Address lines are of the form ``"Stevenage, Hertfordshire, SG1 4BB"``.
    """
    for line in text_lines:
        m = _POSTCODE_RE.search(line)
        if not m:
            continue
        postcode = m.group(1).strip().upper()
        # Normalise the internal space
        postcode = re.sub(r"\s+", " ", postcode)
        # Split on commas; drop the postcode segment
        parts = [p.strip() for p in line.split(",") if p.strip()]
        non_pc = [p for p in parts if not _POSTCODE_RE.search(p)]
        locality = non_pc[0] if len(non_pc) >= 1 else None
        region = non_pc[1] if len(non_pc) >= 2 else None
        return line, postcode, locality, region
    return None, None, None, None


def _extract_unit_info(
    text_lines: list[str],
) -> tuple[str | None, int | None, int | None, str | None]:
    """Pull the bedroom/property-type line.

    Examples seen on real cards:
      "3, 4 & 5 bedroom houses"
      "2 & 4 bedroom houses"
      "3, 4 & 5 bedroom houses and 3 bedroom bungalows"
      "1 & 2 bedroom apartments"
    """
    for line in text_lines:
        if "bedroom" not in line.lower():
            continue
        digits = [int(d) for d in re.findall(r"\d+", line)]
        if not digits:
            continue
        bedrooms_min = min(digits)
        bedrooms_max = max(digits)
        # Verbatim digit cluster before "bedroom"
        m = re.match(r"^([\d\s,&]+)\s*[Bb]edroom", line)
        bedrooms_text = m.group(1).strip() if m else None
        # Property type: words after "bedroom(s)"
        type_m = re.search(r"[Bb]edrooms?\s+(.+)$", line)
        property_type = type_m.group(1).strip() if type_m else None
        return bedrooms_text, bedrooms_min, bedrooms_max, property_type
    return None, None, None, None


def _extract_price_range(card: Tag) -> tuple[int | None, int | None]:
    """Pull the headline asking price from the card's ``<p class="price">``.

    The price always lives in a dedicated ``<p class="price">`` element,
    so we read it directly rather than scanning the whole card's text
    (which also contains marketing copy like "STAMP DUTY PAID up to
    £12k" or "£1.5bn regeneration area" that previously poisoned the
    parse).

    Shared-ownership developments quote the share price first and the
    real headline second, e.g.::

        £53,125 - £58,125 for a 25% share
        £212,500 - £232,500 Full Market Value

    For those we return the "Full Market Value" figure (the price a
    buyer would compare against ordinary listings), not the share.

    Bogus amounts (below :data:`_MIN_PLAUSIBLE_PRICE`, or abbreviated
    like "£12k") are never emitted - we return ``None`` rather than a
    junk number.
    """
    price_el = card.find("p", class_="price")
    if not isinstance(price_el, Tag):
        return None, None

    # Prefer the "Full Market Value" amount on shared-ownership cards;
    # it is the like-for-like headline price. The FMV amount is the £
    # range / single £amount that immediately precedes the words "Full
    # Market Value".
    text = price_el.get_text(" ", strip=True)
    fmv = re.search(
        r"(£[\s\d,]+(?:(?:-|–|to)\s*£?[\s\d,]+)?)\s*Full Market Value",
        text,
        re.IGNORECASE,
    )
    if fmv:
        price_min, price_max = _prices_from_text(fmv.group(1))
        if price_min is not None:
            return price_min, price_max

    return _prices_from_text(text)


def _prices_from_text(text: str) -> tuple[int | None, int | None]:
    """Parse a £range / single £amount from one price string."""
    m = _PRICE_RANGE_RE.search(text)
    if m:
        low = _parse_price(m.group(1))
        high = _parse_price(m.group(2))
        if low is not None and high is not None:
            return low, high
    m = _PRICE_AMOUNT_RE.search(text)
    if m:
        single = _parse_price(m.group(1))
        if single is not None:
            return single, single
    return None, None


def _parse_price(raw: str) -> int | None:
    cleaned = raw.replace(",", "").strip()
    try:
        value = int(cleaned)
    except (TypeError, ValueError):
        return None
    if value < _MIN_PLAUSIBLE_PRICE:
        return None
    return value


def _extract_description(text_lines: list[str]) -> str | None:
    """Pick the longest non-CTA, non-address, non-price line as the blurb.

    NHFS truncates marketing copy with an ellipsis at ~150 chars on
    the search card, so the description is reliably the longest text
    line after the structural fields are accounted for.
    """
    cta_words = {
        "Make an enquiry",
        "Request a viewing",
        "Request a brochure",
        "More information",
        "Featured development",
    }
    candidates = [
        line
        for line in text_lines
        if line not in cta_words
        and not line.startswith("£")
        and "bedroom" not in line.lower()
        and not _POSTCODE_RE.search(line)
        and len(line) > 30
    ]
    if not candidates:
        return None
    return max(candidates, key=len)


def _parse_detail_html(html: str, final_url: str) -> NewHomesForSaleDevelopmentDetail:
    """Pull what we can from a NHFS detail page.

    NHFS detail pages mostly host enquiry forms; the structured data
    lives in og:* meta tags and a partly-malformed ``ld+json`` blob.
    """
    soup = BeautifulSoup(html, "html.parser")

    def meta(name: str) -> str | None:
        el = soup.find("meta", attrs={"property": name})
        if isinstance(el, Tag) and el.get("content"):
            content = el["content"]
            return content[0] if isinstance(content, list) else content
        return None

    h1 = soup.find("h1")
    title = h1.get_text(strip=True) if h1 else meta("og:title")

    postcode: str | None = None
    address: str | None = None
    postal = _extract_postal_address(html)
    if postal:
        address = ", ".join(
            v
            for v in [
                postal.get("streetAddress"),
                postal.get("addressLocality"),
                postal.get("addressRegion"),
                postal.get("postalCode"),
            ]
            if v
        )
        postcode = postal.get("postalCode")

    if not postcode:
        m = _POSTCODE_RE.search(html)
        if m:
            postcode = m.group(1).strip()

    return NewHomesForSaleDevelopmentDetail(
        url=final_url,
        title=title,
        og_title=meta("og:title"),
        og_description=meta("og:description"),
        og_image=meta("og:image"),
        address=address,
        postcode=postcode,
        raw=None,
    )


def _extract_postal_address(html: str) -> dict[str, str] | None:
    """Pull the ``PostalAddress`` object from the malformed ld+json blob."""
    m = re.search(r'<script[^>]+ld\+json[^>]*>(.*?)</script>', html, re.DOTALL)
    if not m:
        return None
    # NHFS concatenates multiple JSON objects in one script tag with
    # interleaved field names; use raw_decode to walk through what
    # parses.
    import json

    raw = m.group(1).strip()
    decoder = json.JSONDecoder()
    idx = 0
    while idx < len(raw):
        while idx < len(raw) and raw[idx] in " \t\n\r,":
            idx += 1
        if idx >= len(raw):
            break
        try:
            obj, end = decoder.raw_decode(raw, idx)
        except json.JSONDecodeError:
            next_start = raw.find("{", idx + 1)
            if next_start < 0:
                break
            idx = next_start
            continue
        if isinstance(obj, dict) and obj.get("@type") == "PostalAddress":
            return {k: v for k, v in obj.items() if isinstance(v, str)}
        idx = end
    return None
