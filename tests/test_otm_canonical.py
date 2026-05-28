"""Unit tests for OnTheMarket canonical-shape helpers + listing tool."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from property_core.onthemarket_scraper import (
    _parse_detail_html,
    normalise_tenure,
    to_canonical_listing,
)


FIXTURES = Path(__file__).parent / "fixtures"


def _detail():
    html = (FIXTURES / "otm_listing.html").read_text()
    return _parse_detail_html(
        html,
        listing_id="19100332",
        url="https://www.onthemarket.com/details/19100332/",
    )


# ---------------------------------------------------------------------------
# normalise_tenure
# ---------------------------------------------------------------------------


def test_normalise_tenure_freehold() -> None:
    assert normalise_tenure("Freehold") == "freehold"


def test_normalise_tenure_leasehold_with_years() -> None:
    assert normalise_tenure("Leasehold  |  108 yrs left") == "leasehold"


def test_normalise_tenure_share_of_freehold() -> None:
    assert normalise_tenure("Share of Freehold") == "share_of_freehold"


def test_normalise_tenure_commonhold_collapses_to_leasehold() -> None:
    # OTM treats commonhold like leasehold for buyer purposes; the
    # canonical four-value enum has no commonhold slot.
    assert normalise_tenure("Commonhold") == "leasehold"


def test_normalise_tenure_non_traditional_is_unknown() -> None:
    assert normalise_tenure("Non traditional") == "unknown"


def test_normalise_tenure_none_is_unknown() -> None:
    assert normalise_tenure(None) == "unknown"


# ---------------------------------------------------------------------------
# to_canonical_listing
# ---------------------------------------------------------------------------


def test_to_canonical_listing_includes_all_required_fields() -> None:
    detail = _detail()
    payload = to_canonical_listing(detail)
    assert payload["id"] == "19100332"
    assert payload["url"] == "https://www.onthemarket.com/details/19100332/"
    assert payload["price"] == 1_200_000
    assert payload["currency"] == "GBP"
    # Bedrooms / bathrooms / floor area from NEXT_DATA
    assert payload["bedrooms"] == 2
    assert payload["bathrooms"] == 2
    assert payload["floor_area_sqft"] == 1062
    assert payload["floor_area_sqm"] == 99
    # Tenure normalised to lowercase enum
    assert payload["tenure"] == "leasehold"
    # Lease economics
    assert payload["ground_rent"] == 250
    assert payload["service_charge"] == 10_000
    assert payload["lease_years_remaining"] == 108
    assert payload["council_tax_band"] == "F"
    # EPC rating from the property.epc.rating block
    assert payload["epc_rating"] == "C"
    # Agent contact
    assert payload["agent_name"] == "John D Wood & Co"
    assert payload["agent_telephone"] == "020 3007 7116"
    # Photos non-empty, absolute, hosted at media.onthemarket.com
    assert payload["photo_urls"]
    assert all(u.startswith("https://media.onthemarket.com") for u in payload["photo_urls"])
    assert len(payload["photo_urls"]) >= 10
    # Floorplan
    assert payload["floorplan_url"]
    assert "floor-plan" in payload["floorplan_url"]
    # Nearest stations with distance_miles
    assert payload["nearest_stations"]
    first = payload["nearest_stations"][0]
    assert "name" in first and "distance_miles" in first
    assert isinstance(first["distance_miles"], float)
    # Location
    assert payload["latitude"] == 51.498742
    assert payload["longitude"] == -0.143732


def test_lookup_onthemarket_listing_uses_canonical_shape() -> None:
    from property_app.tools import lookup_onthemarket_listing

    detail = _detail()
    with patch("property_core.fetch_onthemarket_listing", return_value=detail):
        payload = lookup_onthemarket_listing("19100332")
    assert payload["tenure"] == "leasehold"
    assert payload["photo_urls"] and len(payload["photo_urls"]) >= 10
    assert payload["floorplan_url"] is not None
    assert payload["nearest_stations"]


def test_lookup_onthemarket_listing_handles_scraper_error() -> None:
    from property_app.tools import lookup_onthemarket_listing
    from property_core.onthemarket_scraper import OnTheMarketError

    with patch(
        "property_core.fetch_onthemarket_listing",
        side_effect=OnTheMarketError("upstream gone"),
    ):
        result = lookup_onthemarket_listing("19100332")
    assert "error" in result
    assert "upstream gone" in result["error"]
