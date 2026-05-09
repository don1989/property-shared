"""Unit tests for the Zoopla scraper / URL builder.

Live tests are gated behind ``RUN_LIVE_TESTS=1`` since they require
Playwright + Chromium and a residential network egress.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from property_core.zoopla_location import ZooplaLocationAPI
from property_core.zoopla_scraper import _parse_search_html

FIXTURES = Path(__file__).parent / "fixtures"


def test_url_builder_basic_sale():
    url = ZooplaLocationAPI().build_search_url("SW1A 1AA")
    assert url == "https://www.zoopla.co.uk/for-sale/property/sw1a-1aa/"


def test_url_builder_basic_rent():
    url = ZooplaLocationAPI().build_search_url("SW1A 1AA", property_type="rent")
    assert url == "https://www.zoopla.co.uk/to-rent/property/sw1a-1aa/"


def test_url_builder_area_slug():
    url = ZooplaLocationAPI().build_search_url("Greater London")
    assert url == "https://www.zoopla.co.uk/for-sale/property/greater-london/"


def test_url_builder_filters():
    url = ZooplaLocationAPI().build_search_url(
        "SW1A 1AA",
        building_type="F",
        min_price=300_000,
        max_price=600_000,
        min_bedrooms=2,
        radius=0.5,
        page=2,
    )
    assert url.startswith("https://www.zoopla.co.uk/for-sale/property/sw1a-1aa/?")
    assert "price_min=300000" in url
    assert "price_max=600000" in url
    assert "beds_min=2" in url
    assert "radius=0.5" in url
    assert "property_sub_type=flats" in url
    assert "pn=2" in url


def test_url_builder_invalid_property_type():
    with pytest.raises(ValueError):
        ZooplaLocationAPI().build_search_url("SW1A 1AA", property_type="lease")


def test_url_builder_empty_postcode():
    with pytest.raises(ValueError):
        ZooplaLocationAPI().build_search_url("")


def test_url_builder_url_encoded_postcode_is_decoded():
    """Reviewer fix: URL-encoded inputs must not survive into the slug."""
    url = ZooplaLocationAPI().build_search_url("SW1A%201AA")
    assert url == "https://www.zoopla.co.uk/for-sale/property/sw1a-1aa/"


def test_starting_page_honours_existing_pn():
    """Reviewer fix: caller-supplied ?pn= must be the starting page, not overwritten."""
    from property_core.zoopla_scraper import _next_page_url, _starting_page

    url = "https://www.zoopla.co.uk/for-sale/property/sw1a-1aa/?pn=3"
    assert _starting_page(url) == 3
    assert "pn=4" in _next_page_url(url, _starting_page(url) + 1)
    # Default to 1 when not present
    assert _starting_page("https://www.zoopla.co.uk/for-sale/property/sw1a-1aa/") == 1


def test_search_card_populates_raw_html():
    """Reviewer fix: transport models must populate `raw` per house rules."""
    html = (FIXTURES / "zoopla_search.html").read_text()
    listings = _parse_search_html(html)
    assert listings[0].raw is not None
    assert "html" in listings[0].raw
    assert "listing-card-content" in listings[0].raw["html"]


def test_parse_search_html_extracts_cards():
    html = (FIXTURES / "zoopla_search.html").read_text()
    listings = _parse_search_html(html)
    assert len(listings) > 0, "expected at least one card from the saved fixture"

    first = listings[0]
    assert first.id.isdigit()
    assert first.url.startswith("https://www.zoopla.co.uk/for-sale/details/")
    assert first.price is not None and first.price > 0
    assert first.display_price and first.display_price.startswith("£")
    assert first.address  # non-empty
    assert first.bedrooms is not None
    assert first.amenities  # at least one amenity row
    # Most cards expose Leasehold/Freehold or a status as a badge
    assert any(b in {"Leasehold", "Freehold", "Reduced", "New", "Featured"} for b in first.badges) or first.badges == []


def test_parse_search_html_amenity_parsing():
    """Verify '2 beds', '2 baths', '1218 sq ft' are parsed into ints."""
    html = (FIXTURES / "zoopla_search.html").read_text()
    listings = _parse_search_html(html)
    # Find a listing with sqft set
    has_sqft = [l for l in listings if l.floor_area_sqft is not None]
    assert has_sqft, "expected at least one listing with floor_area_sqft"
    sample = has_sqft[0]
    assert sample.bedrooms is not None
    assert sample.floor_area_sqft and sample.floor_area_sqft > 200
    assert any("bed" in a.lower() for a in sample.amenities)


@pytest.mark.anyio
async def test_zoopla_search_live() -> None:
    if os.getenv("RUN_LIVE_TESTS") != "1":
        pytest.skip("Set RUN_LIVE_TESTS=1 to run live network tests")

    import anyio
    from functools import partial
    from property_core.zoopla_scraper import ZooplaError, fetch_listings

    postcode = os.getenv("ZOOPLA_TEST_POSTCODE", "SW1A 1AA")
    url = ZooplaLocationAPI().build_search_url(postcode)

    try:
        listings = await anyio.to_thread.run_sync(
            partial(fetch_listings, url, max_pages=1)
        )
    except ImportError:
        pytest.skip("Playwright not installed (install property-shared[planning])")
    except ZooplaError as exc:
        # Cloudflare may gate this IP/UA combination — treat as
        # environment-level skip, not a code failure.
        pytest.skip(f"Zoopla blocked (likely Cloudflare): {exc}")

    assert isinstance(listings, list)
    print(f"Zoopla live fetched {len(listings)} listings from {url}")
    if listings:
        sample = listings[0]
        print(f"  sample: id={sample.id} price={sample.price} addr={sample.address!r}")
