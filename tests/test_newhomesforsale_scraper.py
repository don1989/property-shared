"""Unit tests for the NewHomesForSale scraper / URL builder."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from property_core.newhomesforsale_location import NewHomesForSaleLocationAPI
from property_core.newhomesforsale_scraper import (
    NewHomesForSaleError,
    _parse_detail_html,
    _parse_search_html,
    fetch_listing,
    fetch_listings,
)

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# URL builder
# ---------------------------------------------------------------------------


def test_url_builder_county_only():
    url = NewHomesForSaleLocationAPI().build_search_url(county="Hertfordshire")
    assert url == "https://www.newhomesforsale.co.uk/new-homes/hertfordshire/"


def test_url_builder_county_and_town():
    url = NewHomesForSaleLocationAPI().build_search_url(
        county="Hertfordshire", town="Hitchin"
    )
    assert url == "https://www.newhomesforsale.co.uk/new-homes/hertfordshire/hitchin/"


def test_url_builder_lowercases_and_slugs():
    url = NewHomesForSaleLocationAPI().build_search_url(
        county="Greater London", town="Royal Borough of Greenwich"
    )
    assert url == (
        "https://www.newhomesforsale.co.uk/new-homes/"
        "greater-london/royal-borough-of-greenwich/"
    )


def test_url_builder_empty_county_raises():
    with pytest.raises(ValueError):
        NewHomesForSaleLocationAPI().build_search_url(county="")


# ---------------------------------------------------------------------------
# Search-page parsing
# ---------------------------------------------------------------------------


def _load_search_fixture() -> str:
    return (FIXTURES / "nhfs_search.html").read_text()


def test_search_extracts_expected_card_count():
    listings = _parse_search_html(_load_search_fixture())
    # The Hitchin fixture has 18 cards on the page; if NHFS changes its
    # layout this will catch it before it ships.
    assert len(listings) >= 15, f"expected ~18 cards, parsed {len(listings)}"


def test_search_first_card_field_extraction():
    """Verify every key field on the first card against verbatim fixture data."""
    listings = _parse_search_html(_load_search_fixture())
    assert listings, "expected at least one card in the fixture"
    dev = listings[0]
    # The first card on the Hitchin fixture is Forster Park
    assert dev.id == "38646"
    assert dev.name == "Forster Park"
    assert dev.url.endswith("/new-homes/hertfordshire/stevenage/forster-park-bellway/")
    assert dev.developer == "Bellway"
    assert dev.locality == "Stevenage"
    assert dev.region == "Hertfordshire"
    assert dev.postcode == "SG1 4BB"
    assert dev.bedrooms_min == 3 and dev.bedrooms_max == 5
    assert "houses" in (dev.property_type or "").lower()
    assert dev.price_min == 457_500 and dev.price_max == 875_000
    assert dev.photo_count == 20
    # Distance hint only appears when the search has a location anchor (Hitchin)
    assert dev.distance_text and "approximately" in dev.distance_text
    assert dev.distance_miles == pytest.approx(3.0, abs=0.05)
    assert dev.hero_image and dev.hero_image.startswith(
        "https://www.newhomesforsale.co.uk/"
    )


def test_search_extracts_developer_and_price_range_variations():
    """Across the 18 fixture cards we should see developer and price variation."""
    listings = _parse_search_html(_load_search_fixture())
    developers = {l.developer for l in listings if l.developer}
    assert len(developers) >= 3, f"expected multiple developers, got {developers}"
    # At least one card should have a price range parsed
    with_prices = [l for l in listings if l.price_min is not None]
    assert with_prices, "expected at least one card with a parsed price range"
    # Min < max (or equal) on every priced card
    for l in with_prices:
        assert l.price_max is not None and l.price_max >= l.price_min


def test_search_returns_empty_list_on_empty_html():
    assert _parse_search_html("<html><body></body></html>") == []


# ---------------------------------------------------------------------------
# Detail-page parsing
# ---------------------------------------------------------------------------


def test_detail_extracts_postcode_and_meta():
    """Detail pages are sparse — we just verify the few useful fields land."""
    html = (FIXTURES / "nhfs_detail.html").read_text()
    result = _parse_detail_html(
        html,
        "https://www.newhomesforsale.co.uk/new-homes/greater-london/woolwich/royal-arsenal-riverside-berkeley-homes/",
    )
    assert result.postcode == "SE18 6FR"
    assert result.og_title == "Royal Arsenal Riverside"
    assert "Berkeley" in (result.og_description or "")
    assert "Royal Arsenal Riverside" in (result.title or "")
    # Address should include the postcode and the street/locality/region
    assert result.address and "SE18 6FR" in result.address
    assert "Woolwich" in (result.address or "")


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


def test_fetch_listing_rejects_numeric_id():
    """Numeric ids can't be resolved without a prior search (NHFS detail
    URLs need a county/town/slug path)."""
    with pytest.raises(NewHomesForSaleError, match="absolute URL"):
        fetch_listing("18117")


def test_fetch_listings_raises_on_persistent_5xx():
    """5xx is retryable; after exhausted retries the scraper raises
    ``NewHomesForSaleError`` rather than returning an empty list."""
    from unittest.mock import MagicMock, patch

    mock_response = MagicMock()
    mock_response.status_code = 503
    mock_response.text = ""

    with patch(
        "property_core.newhomesforsale_scraper.Session.get",
        return_value=mock_response,
    ):
        with pytest.raises(NewHomesForSaleError, match="retries"):
            fetch_listings(
                "https://www.newhomesforsale.co.uk/new-homes/hertfordshire/hitchin/",
                rate_limit_seconds=0,
                retry_attempts=2,
                retry_backoff=0,
            )


def test_fetch_listings_raises_on_4xx():
    """4xx is non-retryable; the scraper raises immediately."""
    from unittest.mock import MagicMock, patch

    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_response.text = ""

    with patch(
        "property_core.newhomesforsale_scraper.Session.get",
        return_value=mock_response,
    ):
        with pytest.raises(NewHomesForSaleError, match="404"):
            fetch_listings(
                "https://www.newhomesforsale.co.uk/new-homes/nowhere/",
                rate_limit_seconds=0,
            )


def test_fetch_listings_empty_page_returns_empty_list():
    """A 200 with no developmentSummary cards is a legitimate empty
    result (e.g. a county with no listed developments), not an error."""
    from unittest.mock import MagicMock, patch

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = "<html><body><p>No results</p></body></html>"

    with patch(
        "property_core.newhomesforsale_scraper.Session.get",
        return_value=mock_response,
    ):
        result = fetch_listings(
            "https://www.newhomesforsale.co.uk/new-homes/somewhere/",
            rate_limit_seconds=0,
        )
    assert result == []


# ---------------------------------------------------------------------------
# Live fetch (gated)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    os.getenv("RUN_LIVE_TESTS") != "1", reason="Set RUN_LIVE_TESTS=1"
)
def test_fetch_listings_live():
    """End-to-end: Hitchin should always have at least a handful of devs."""
    import requests

    try:
        listings = fetch_listings(
            "https://www.newhomesforsale.co.uk/new-homes/hertfordshire/hitchin/",
            rate_limit_seconds=0,
        )
    except requests.RequestException as exc:
        pytest.skip(f"NewHomesForSale unavailable: {exc}")
    assert listings, "expected at least one Hitchin development"
    sample = listings[0]
    assert sample.id and sample.name and sample.url
