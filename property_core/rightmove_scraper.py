"""Rightmove scraper (pure Python).

Scrapes both search results (``fetch_listings``) and individual property detail
pages (``fetch_listing``).

Search results use the embedded ``__NEXT_DATA__`` payload.
Property detail pages use the embedded ``window.PAGE_MODEL`` payload.

Intentionally conservative:
- polite delay between page fetches (``rate_limit_seconds``)
- retry on transient errors (429/5xx)
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
from requests import Response, Session
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from property_core.models.rightmove import RightmoveListing, RightmoveListingDetail


DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-GB,en;q=0.9",
}


class RetryableError(Exception):
    """Raised for transient errors that should trigger a retry."""


class RightmoveError(Exception):
    """Raised when Rightmove data cannot be fetched or parsed."""


_CURL_CFFI_PROFILES: tuple[str, ...] = (
    "chrome120",
    "safari17_2_ios",
    "firefox133",
    "chrome116",
)


def fetch_listing(
    property_url_or_id: str,
    *,
    timeout: float = 15.0,
    retry_attempts: int = 5,
    retry_backoff: float = 1.5,
    impersonate_profiles: tuple[str, ...] = _CURL_CFFI_PROFILES,
    proxy: str | None = None,
) -> RightmoveListingDetail:
    """Fetch full property details from an individual Rightmove listing page.

    Uses curl_cffi with TLS-fingerprint impersonation as the primary transport,
    rotating through ``impersonate_profiles`` if Cloudflare challenges the first
    profile. Falls back to plain ``requests`` if curl_cffi is not installed.

    Args:
        property_url_or_id: Full Rightmove URL or just the numeric property ID.
        timeout: HTTP request timeout in seconds.
        retry_attempts: Number of retries on transient errors (requests fallback).
        retry_backoff: Exponential backoff multiplier (requests fallback).
        impersonate_profiles: curl_cffi browser fingerprint profiles to rotate
            through. Pass ``()`` to skip curl_cffi entirely.
        proxy: Optional proxy URL.

    Returns:
        RightmoveListingDetail with all available fields from the detail page.

    Raises:
        RightmoveError: If every transport attempt fails, including a clear
            message when Cloudflare returns a challenge.
    """
    url = _normalize_property_url(property_url_or_id)

    # A 200 response with no PAGE_MODEL payload is almost always a transient
    # bot-challenge / interstitial rather than a real layout change, so refetch
    # the page a few times before giving up (mirrors _get_search_results). A
    # genuine parse error (invalid JSON, missing propertyData) is re-raised
    # immediately, and the original soft-block error is preserved if every
    # attempt is blocked, so the caller's error contract is unchanged.
    attempts = max(1, retry_attempts)
    last_soft_block: RightmoveError | None = None
    for attempt in range(attempts):
        html = _fetch_listing_html(
            url,
            timeout=timeout,
            retry_attempts=retry_attempts,
            retry_backoff=retry_backoff,
            impersonate_profiles=impersonate_profiles,
            proxy=proxy,
        )
        try:
            property_data = _extract_page_model(html)
        except RightmoveError as exc:
            if "Could not locate PAGE_MODEL" not in str(exc):
                raise
            last_soft_block = exc
            if attempt < attempts - 1:
                time.sleep(retry_backoff * (attempt + 1))
            continue
        return RightmoveListingDetail.from_page_model(property_data, url=url)
    assert last_soft_block is not None
    raise last_soft_block


def _fetch_listing_html(
    url: str,
    *,
    timeout: float,
    retry_attempts: int,
    retry_backoff: float,
    impersonate_profiles: tuple[str, ...],
    proxy: str | None,
) -> str:
    failures: list[str] = []

    if impersonate_profiles:
        try:
            from curl_cffi import requests as cf_requests
        except ImportError:
            failures.append("curl_cffi: not installed")
        else:
            for profile in impersonate_profiles:
                kwargs: dict[str, Any] = {"impersonate": profile}
                if proxy:
                    kwargs["proxies"] = {"http": proxy, "https": proxy}
                try:
                    session = cf_requests.Session(**kwargs)
                    resp = session.get(url, timeout=timeout)
                except Exception as exc:  # noqa: BLE001
                    failures.append(f"{profile}: request error: {exc}")
                    continue
                status = getattr(resp, "status_code", 0)
                text = getattr(resp, "text", "") or ""
                if status == 403 or "Just a moment" in text[:5000]:
                    failures.append(f"{profile}: Cloudflare challenge (status {status})")
                    continue
                if status >= 400:
                    failures.append(f"{profile}: status {status}")
                    continue
                return text

    try:
        session = Session()
        response = _get_with_retries(
            session=session,
            url=url,
            timeout=timeout,
            retry_attempts=retry_attempts,
            retry_backoff=retry_backoff,
        )
        if "Just a moment" in response.text[:5000]:
            raise RightmoveError(
                f"Rightmove returned a Cloudflare challenge for {url}. "
                "Tried profiles: " + "; ".join(failures) if failures else
                f"Rightmove returned a Cloudflare challenge for {url}."
            )
        return response.text
    except RightmoveError as exc:
        if failures:
            raise RightmoveError(
                f"Could not fetch {url}. curl_cffi attempts: {'; '.join(failures)}. "
                f"Requests fallback: {exc}"
            ) from exc
        raise


def fetch_listings(
    search_url: str,
    *,
    timeout: float = 15.0,
    max_pages: Optional[int] = None,
    rate_limit_seconds: float = 0.6,
    retry_attempts: int = 5,
    retry_backoff: float = 1.5,
) -> list[RightmoveListing]:
    """Fetch listings from a Rightmove search URL across pages."""
    listings: list[RightmoveListing] = []
    next_url = search_url
    page_counter = 0
    seen_indices: set[str] = set()
    session = Session()

    while next_url:
        if rate_limit_seconds and page_counter > 0:
            time.sleep(rate_limit_seconds)
        page_counter += 1

        search_results = _get_search_results(
            session=session,
            url=next_url,
            timeout=timeout,
            retry_attempts=retry_attempts,
            retry_backoff=retry_backoff,
        )
        properties = search_results.get("properties") or []
        listings.extend(RightmoveListing.from_next_data(prop) for prop in properties)

        pagination = search_results.get("pagination") or {}
        next_index = pagination.get("next")

        if max_pages is not None and page_counter >= max_pages:
            break

        if not next_index or str(next_index) in seen_indices:
            break

        seen_indices.add(str(next_index))
        next_url = _url_with_index(search_url, next_index)

    return listings


def _get_search_results(
    *, session: Session, url: str, timeout: float, retry_attempts: int, retry_backoff: float
) -> Dict[str, Any]:
    # _get_with_retries already retries network errors / 429 / 5xx per fetch.
    # On top of that, a 200 response with no __NEXT_DATA__ payload is almost
    # always a transient bot-challenge / interstitial page rather than a real
    # layout change, so refetch the whole page a few times before giving up
    # instead of silently dropping Rightmove (the only portal with photos)
    # from the entire scan. The original RightmoveError is preserved if every
    # attempt is blocked, so the caller's error contract is unchanged.
    attempts = max(1, retry_attempts)
    last_soft_block: RightmoveError | None = None
    for attempt in range(attempts):
        response = _get_with_retries(
            session=session,
            url=url,
            timeout=timeout,
            retry_attempts=retry_attempts,
            retry_backoff=retry_backoff,
        )
        soup = BeautifulSoup(response.text, "html.parser")
        try:
            return _extract_search_results(soup)
        except RightmoveError as exc:
            if "embedded search data" not in str(exc):
                raise
            last_soft_block = exc
            if attempt < attempts - 1:
                time.sleep(retry_backoff * (attempt + 1))
    assert last_soft_block is not None
    raise last_soft_block


def _is_rightmove_not_found(soup: BeautifulSoup) -> bool:
    """True when Rightmove served its 'page not found' page rather than results.

    Distinct from a Cloudflare/bot interstitial: this is a genuine 404 for an
    invalid or expired search URL, so refetching it just wastes retries.
    """
    title = soup.find("title")
    title_text = title.get_text() if title else ""
    if "find the place you were looking for" in title_text.lower():
        return True
    return soup.find("link", href=re.compile("pagenotfound")) is not None


def _extract_search_results(soup: BeautifulSoup) -> Dict[str, Any]:
    data_script = soup.find("script", id="__NEXT_DATA__")
    if not data_script or not data_script.string:
        # A genuine 404 page won't gain __NEXT_DATA__ on retry, so surface a
        # distinct error (which the caller's retry loop does NOT retry) instead
        # of the transient "embedded search data" soft-block message.
        if _is_rightmove_not_found(soup):
            raise RightmoveError(
                "Rightmove returned a page-not-found page; the search URL is "
                "invalid or expired"
            )
        raise RightmoveError("Could not locate embedded search data on the page")
    try:
        parsed = json.loads(data_script.string)
    except json.JSONDecodeError as exc:
        raise RightmoveError(f"Page contained invalid JSON: {exc}") from exc
    try:
        return parsed["props"]["pageProps"]["searchResults"]
    except KeyError as exc:
        raise RightmoveError("Search results were not present in the page payload") from exc


def _url_with_index(url: str, index: str | int) -> str:
    parsed = urlparse(url)
    query_items = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query_items["index"] = str(index)
    new_query = urlencode(query_items, doseq=True)
    return urlunparse(parsed._replace(query=new_query))


def _make_request(session: Session, url: str, timeout: float) -> Response:
    try:
        response = session.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
    except requests.RequestException as exc:
        raise RetryableError(f"Network error: {exc}") from exc

    if response.status_code == 429 or response.status_code >= 500:
        raise RetryableError(f"Server responded with {response.status_code}")
    if response.status_code >= 400:
        raise RightmoveError(f"Request failed with status code {response.status_code}")
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
        raise RightmoveError(f"Request failed after {retry_attempts} retries: {exc}") from exc


# --- Listing detail helpers ---

_PAGE_MODEL_RE = re.compile(r"window\.__PAGE_MODEL\s*=\s*(.+);")


def _normalize_property_url(url_or_id: str) -> str:
    """Accept a full Rightmove URL or numeric ID, return a canonical detail URL.

    A full URL is validated against the SSRF allowlist (Rightmove only); a bare
    id is composed into a known-safe URL server-side.
    """
    from property_core.url_guard import validate_listing_url

    url_or_id = url_or_id.strip()
    if url_or_id.startswith("http"):
        return validate_listing_url(url_or_id, allowed_suffixes=("rightmove.co.uk",))
    return f"https://www.rightmove.co.uk/properties/{url_or_id}"


def _decode_graph(nodes: list, idx: int, seen: frozenset = frozenset()) -> Any:
    """Recursively dereference Rightmove's pointer-graph encoding.

    Each node is either a primitive (returned as-is) or a dict/list whose
    values are integer indices into the same nodes array.
    """
    if idx in seen:
        return None
    val = nodes[idx]
    seen = seen | {idx}
    if isinstance(val, dict):
        return {k: _decode_graph(nodes, v, seen) for k, v in val.items()}
    if isinstance(val, list):
        return [_decode_graph(nodes, i, seen) for i in val]
    return val


def _extract_page_model(html: str) -> Dict[str, Any]:
    """Extract PAGE_MODEL JSON from a Rightmove property detail page."""
    match = _PAGE_MODEL_RE.search(html)
    if not match:
        # Some detail pages (commercial / land listings, and Rightmove's newer
        # Next.js layout) render a real page that never carries window.PAGE_MODEL.
        # Those won't gain it on retry, so raise a distinct error the retry loop
        # in fetch_listing does NOT retry, rather than spending five refetches
        # treating it as a transient bot-challenge.
        if 'id="__NEXT_DATA__"' in html:
            raise RightmoveError(
                "Rightmove listing uses an unsupported page format (no PAGE_MODEL; "
                "likely a commercial/land listing or the newer __NEXT_DATA__ layout)"
            )
        raise RightmoveError("Could not locate PAGE_MODEL data on the property page")
    try:
        outer = json.loads(match.group(1))
        nodes = json.loads(outer["data"])
    except (json.JSONDecodeError, KeyError) as exc:
        raise RightmoveError(f"PAGE_MODEL contained invalid JSON: {exc}") from exc
    root = _decode_graph(nodes, 0)
    property_data = root.get("propertyData")
    if not property_data:
        raise RightmoveError("propertyData not found in PAGE_MODEL")
    return property_data
