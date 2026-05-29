"""Unit tests for the OnTheMarket scraper / URL builder."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from property_core.onthemarket_location import OnTheMarketLocationAPI
from property_core.onthemarket_scraper import (
    _clean_image_urls,
    _extract_data_layer,
    _extract_detail_images,
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


def test_url_builder_new_build_uses_dedicated_path():
    """new_build=True must switch to /new-homes/property/{slug}/."""
    url = OnTheMarketLocationAPI().build_search_url(
        "Hitchin Station",
        new_build=True,
        travel_duration=15,
    )
    assert "/new-homes/property/hitchin-station/" in url
    assert "/for-sale/property/" not in url
    assert "travel-duration=15" in url


def test_url_builder_rejects_new_build_with_rent():
    with pytest.raises(ValueError, match="new_build=True is only valid"):
        OnTheMarketLocationAPI().build_search_url(
            "London", property_type="rent", new_build=True
        )


def test_url_builder_station_slug_with_travel_duration():
    """Station-anchored commute search: '15 min walk from Hitchin Station'."""
    url = OnTheMarketLocationAPI().build_search_url(
        "Hitchin Station",
        min_bedrooms=3,
        max_bedrooms=3,
        building_type="T",
        travel_duration=15,
    )
    assert url.startswith(
        "https://www.onthemarket.com/for-sale/property/hitchin-station/?"
    )
    assert "travel-duration=15" in url
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
            "Hitchin Station", travel_duration=15, travel_type="teleport"
        )


def test_unknown_location_raises_typed_error():
    """OTM returns 404 + an HTML interstitial when the slug doesn't exist;
    the scraper must surface that as OnTheMarketLocationNotFound rather
    than a generic 404 (or worse, an empty listing list)."""
    from unittest.mock import MagicMock, patch

    from property_core.onthemarket_scraper import (
        OnTheMarketLocationNotFound,
        fetch_listings,
    )

    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_response.text = (
        "<html><body>"
        "<h1>Location 'watford-metropolitan-station' not recognised</h1>"
        "</body></html>"
    )
    with patch(
        "property_core.onthemarket_scraper.Session.get",
        return_value=mock_response,
    ):
        with pytest.raises(OnTheMarketLocationNotFound) as exc_info:
            fetch_listings(
                "https://www.onthemarket.com/for-sale/property/watford-metropolitan-station/",
                retry_attempts=1,
            )
    assert exc_info.value.slug == "watford-metropolitan-station"
    assert "watford-metropolitan-station" in str(exc_info.value)


def test_url_builder_rejects_unsupported_travel_duration():
    """OnTheMarket's filter only accepts 15/30/45/60 — anything else
    silently returns zero results upstream, so the builder must reject it."""
    with pytest.raises(ValueError, match="travel_duration must be one of"):
        OnTheMarketLocationAPI().build_search_url(
            "Hitchin Station", travel_duration=10
        )
    with pytest.raises(ValueError, match="travel_duration must be one of"):
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


def test_parse_search_html_every_card_has_images():
    """Regression: search cards must yield photo URLs (these become
    ``photo_urls`` downstream). A markup change that broke the
    ``itemprop=contentUrl`` / spotlight-swiper extraction would leave cards
    with empty galleries — assert every card in the fixture has at least one
    media.onthemarket.com image."""
    html = (FIXTURES / "otm_search.html").read_text()
    listings = _parse_search_html(html)
    assert listings
    cards_without_images = [l.id for l in listings if not l.images]
    assert not cards_without_images, (
        f"cards missing images: {cards_without_images}"
    )
    assert all(
        "media.onthemarket.com" in img
        for l in listings
        for img in l.images
    )


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


def test_detail_images_prefer_full_gallery():
    """The detail parser should return the full ``__NEXT_DATA__`` gallery,
    not just the smaller visible hero subset."""
    html = (FIXTURES / "otm_listing.html").read_text()
    soup = BeautifulSoup(html, "html.parser")
    images = _extract_detail_images(html, soup)
    # All absolute https photo URLs, deduped, no EPC graphs / floor plans.
    assert images, "expected a non-empty gallery"
    assert all(u.startswith("https://") for u in images)
    assert len(set(images)) == len(images)
    assert not any("epc-graph" in u or "floor-plan" in u for u in images)
    # __NEXT_DATA__ holds more photos than the hero DOM subset (5), so the
    # gallery must be preferred over the hero fallback.
    assert len(images) > 5


def test_clean_image_urls_filters_and_dedupes():
    raw = [
        "https://media.onthemarket.com/properties/1/x/image-0-1024x1024.jpg",
        "https://media.onthemarket.com/properties/1/x/image-0-1024x1024.jpg",  # dup
        "https://media.onthemarket.com/properties/1/x/floor-plan-0-1024x1024.jpg",
        "https://media.onthemarket.com/properties/1/x/epc-graph-0-1024x1024.gif",
        "http://media.onthemarket.com/properties/1/x/image-1.jpg",  # not https
        "",
        None,  # type: ignore[list-item]
        "https://media.onthemarket.com/properties/1/x/image-1-1024x1024.jpg",
    ]
    cleaned = _clean_image_urls(raw)
    assert cleaned == [
        "https://media.onthemarket.com/properties/1/x/image-0-1024x1024.jpg",
        "https://media.onthemarket.com/properties/1/x/image-1-1024x1024.jpg",
    ]


def test_detail_images_falls_back_to_og_image():
    """With no ``__NEXT_DATA__`` and no hero section, the og:image hero is used."""
    html = (
        '<html><head>'
        '<meta property="og:image" '
        'content="https://media.onthemarket.com/properties/9/x/image-0-1024x1024.jpg">'
        '</head><body></body></html>'
    )
    soup = BeautifulSoup(html, "html.parser")
    images = _extract_detail_images(html, soup)
    assert images == [
        "https://media.onthemarket.com/properties/9/x/image-0-1024x1024.jpg"
    ]


def test_detail_images_empty_on_garbage():
    html = "<html><body><p>no images here</p></body></html>"
    soup = BeautifulSoup(html, "html.parser")
    assert _extract_detail_images(html, soup) == []


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
