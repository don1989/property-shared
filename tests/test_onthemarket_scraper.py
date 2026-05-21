"""Unit tests for the OnTheMarket scraper / URL builder."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from property_core.onthemarket_location import OnTheMarketLocationAPI
from property_core.onthemarket_scraper import (
    _extract_data_layer,
    _extract_key_information,
    _parse_detail_html,
    _parse_search_html,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_url_builder_basic_sale():
    url = OnTheMarketLocationAPI().build_search_url("SW1A 1AA")
    assert url == "https://www.onthemarket.com/for-sale/property/sw1a-1aa/"


def test_url_builder_basic_rent():
    url = OnTheMarketLocationAPI().build_search_url("SW1A 1AA", property_type="rent")
    assert url == "https://www.onthemarket.com/to-rent/property/sw1a-1aa/"


def test_url_builder_filters():
    url = OnTheMarketLocationAPI().build_search_url(
        "SW1A 1AA",
        building_type="F",
        min_price=300_000,
        max_price=600_000,
        min_bedrooms=2,
        radius=0.5,
        page=3,
    )
    assert url.startswith("https://www.onthemarket.com/for-sale/property/sw1a-1aa/?")
    assert "min-price=300000" in url
    assert "max-price=600000" in url
    assert "min-bedrooms=2" in url
    assert "radius=0.5" in url
    assert "prop-types=flats-apartments" in url
    assert "page=3" in url


def test_url_builder_invalid_property_type():
    with pytest.raises(ValueError):
        OnTheMarketLocationAPI().build_search_url("SW1A 1AA", property_type="lease")


def test_url_builder_station_slug_with_travel_duration():
    """Station-anchored commute search: '10 min walk from Hitchin Station'."""
    url = OnTheMarketLocationAPI().build_search_url(
        "Hitchin Station",
        min_bedrooms=3,
        max_bedrooms=3,
        building_type="T",
        travel_duration=10,
    )
    assert url.startswith(
        "https://www.onthemarket.com/for-sale/property/hitchin-station/?"
    )
    assert "travel-duration=10" in url
    assert "travel-type=walking" in url  # defaulted
    assert "min-bedrooms=3" in url
    assert "prop-types=terraced" in url


def test_url_builder_travel_type_without_duration():
    """travel_type alone is allowed (rare but valid)."""
    url = OnTheMarketLocationAPI().build_search_url(
        "Hitchin Station", travel_type="cycling"
    )
    assert "travel-type=cycling" in url
    assert "travel-duration" not in url


def test_url_builder_rejects_unknown_travel_type():
    with pytest.raises(ValueError, match="travel_type must be one of"):
        OnTheMarketLocationAPI().build_search_url(
            "Hitchin Station", travel_duration=10, travel_type="teleport"
        )


def test_url_builder_rejects_non_positive_travel_duration():
    with pytest.raises(ValueError, match="positive number"):
        OnTheMarketLocationAPI().build_search_url(
            "Hitchin Station", travel_duration=0
        )


def test_url_builder_empty_postcode():
    with pytest.raises(ValueError):
        OnTheMarketLocationAPI().build_search_url("")


def test_url_builder_url_encoded_postcode_is_decoded():
    """Reviewer fix: URL-encoded inputs must not survive into the slug."""
    url = OnTheMarketLocationAPI().build_search_url("SW1A%201AA")
    assert url == "https://www.onthemarket.com/for-sale/property/sw1a-1aa/"


def test_starting_page_honours_existing_page_param():
    """Reviewer fix: caller-supplied ?page= must be the starting page."""
    from property_core.onthemarket_scraper import _next_page_url, _starting_page

    url = "https://www.onthemarket.com/for-sale/property/sw1a-1aa/?page=3"
    assert _starting_page(url) == 3
    assert "page=4" in _next_page_url(url, _starting_page(url) + 1)
    assert _starting_page("https://www.onthemarket.com/for-sale/property/sw1a-1aa/") == 1


def test_detail_coerces_int_data_layer_values():
    """Reviewer fix: OnTheMarketListingDetail must not crash when the
    upstream dataLayer ships an int where we expected a str."""
    from property_core.models.onthemarket import OnTheMarketListingDetail

    detail = OnTheMarketListingDetail.build(
        listing_id="42",
        url="https://www.onthemarket.com/details/42/",
        data_layer={
            "price": 500_000,
            "channel": 1,           # int instead of "sale"/"rent"
            "status": 2,
            "addressline_2": 7,
            "postcode": 99,
            "property-type": 0,
            "trans-type-id": 5,
            "branch-id": 8,
            "parent-locations": ["uk", 1],
        },
        title=None,
        description=None,
        meta_description=None,
        display_price=None,
        images=[],
        key_information={},
    )
    assert detail.channel == "1"
    assert detail.postcode == "99"
    assert detail.parent_locations == ["uk", "1"]


def test_search_card_populates_raw_html():
    """Reviewer fix: transport models must populate `raw` per house rules."""
    html = (FIXTURES / "otm_search.html").read_text()
    listings = _parse_search_html(html)
    assert listings[0].raw is not None
    assert "html" in listings[0].raw
    assert "search-result-property-card" in listings[0].raw["html"]


def test_parse_search_html_extracts_cards():
    html = (FIXTURES / "otm_search.html").read_text()
    listings = _parse_search_html(html)
    assert len(listings) >= 20, f"expected ~30 cards, got {len(listings)}"

    first = listings[0]
    assert first.id.isdigit()
    assert first.url.startswith("https://www.onthemarket.com/details/")
    assert first.price is not None and first.price > 0
    assert first.display_price and first.display_price.startswith("£")
    assert first.address
    assert first.bedrooms is not None
    assert first.bathrooms is not None
    assert first.summary  # 'X bedroom Y for sale - ...' from itemprop=description
    # agent panel should be populated
    assert first.agent_name
    # at least one image from media.onthemarket.com
    assert first.images and "media.onthemarket.com" in first.images[0]


def test_parse_search_html_status_pill():
    """Status comes from data-component='pill' (Spotlight, Featured, Reduced, etc.)."""
    html = (FIXTURES / "otm_search.html").read_text()
    listings = _parse_search_html(html)
    statuses = {l.status for l in listings if l.status}
    # should observe at least one of the known pill values in our SW1A fixture
    assert any(
        s in {"Spotlight Property", "Featured Property", "Reduced", "Premium Listing"}
        for s in statuses
    ), f"unexpected statuses: {statuses}"


def test_extract_data_layer():
    html = (FIXTURES / "otm_listing.html").read_text()
    dl = _extract_data_layer(html)
    assert dl["property-id"] == 19100332
    assert dl["price"] == "1,200,000"
    assert dl["postcode"] == "SW1W 0PP"
    assert dl["channel"] == "sale"
    assert dl["property-type"] == "homes"
    assert dl["status"] == "live"
    assert dl["branch-id"] == 73949
    assert dl["addressline_2"] == "Buckingham Palace Road"
    assert "uk" in dl["parent-locations"]


def test_extract_key_information():
    from bs4 import BeautifulSoup

    html = (FIXTURES / "otm_listing.html").read_text()
    soup = BeautifulSoup(html, "html.parser")
    ki = _extract_key_information(soup)
    assert ki["Tenure"].startswith("Leasehold")
    assert "108" in ki["Tenure"]
    assert "£250" in ki["Ground rent"]
    assert "£10,000" in ki["Service charge"]
    assert ki["Council tax"] == "Band F"


def test_parse_detail_html_full():
    html = (FIXTURES / "otm_listing.html").read_text()
    detail = _parse_detail_html(
        html,
        listing_id="19100332",
        url="https://www.onthemarket.com/details/19100332/",
    )
    assert detail.id == "19100332"
    assert detail.price == 1_200_000
    assert detail.display_price == "£1,200,000"
    assert detail.title == "2 bedroom flat for sale"
    assert detail.postcode == "SW1W 0PP"
    assert detail.addressline_2 == "Buckingham Palace Road"
    assert detail.channel == "sale"
    assert detail.status == "live"
    assert detail.property_type == "homes"
    assert detail.trans_type_id == "resale"
    assert detail.branch_id == 73949
    assert detail.tenure == "Leasehold"
    assert detail.years_remaining_on_lease == 108
    assert detail.annual_ground_rent == 250
    assert detail.annual_service_charge == 10_000
    assert detail.council_tax_band == "F"
    assert detail.images and len(detail.images) >= 3
    assert detail.parent_locations == ["uk", "england", "south-east"]
    assert detail.description and "two bedroom" in detail.description.lower()


# ---------------------------------------------------------------------------
# Live tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_onthemarket_search_live() -> None:
    if os.getenv("RUN_LIVE_TESTS") != "1":
        pytest.skip("Set RUN_LIVE_TESTS=1 to run live network tests")

    import anyio
    from functools import partial
    from property_core.onthemarket_scraper import fetch_listings

    postcode = os.getenv("OTM_TEST_POSTCODE", "SW1A 1AA")
    url = OnTheMarketLocationAPI().build_search_url(postcode)
    listings = await anyio.to_thread.run_sync(
        partial(fetch_listings, url, max_pages=1)
    )
    print(f"OnTheMarket live fetched {len(listings)} listings")
    assert listings, "expected at least one listing on a live SW1A 1AA search"


@pytest.mark.anyio
async def test_onthemarket_listing_detail_live() -> None:
    if os.getenv("RUN_LIVE_TESTS") != "1":
        pytest.skip("Set RUN_LIVE_TESTS=1 to run live network tests")

    import anyio
    from functools import partial
    from property_core.onthemarket_scraper import fetch_listing, fetch_listings

    postcode = os.getenv("OTM_TEST_POSTCODE", "SW1A 1AA")
    url = OnTheMarketLocationAPI().build_search_url(postcode)
    listings = await anyio.to_thread.run_sync(
        partial(fetch_listings, url, max_pages=1)
    )
    if not listings:
        pytest.skip("no listings to drill into")
    sample_id = listings[0].id
    detail = await anyio.to_thread.run_sync(partial(fetch_listing, sample_id))
    assert detail.id == sample_id
    assert detail.price is not None
    assert detail.url.endswith(f"/details/{sample_id}/")
    print(
        f"OnTheMarket detail: id={detail.id} price={detail.price} "
        f"channel={detail.channel} tenure={detail.tenure!r}"
    )
