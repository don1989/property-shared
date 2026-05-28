"""Unit tests for property_app rightmove_listing tool + nhfs county resolution."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


def _stub_detail(**overrides):
    base = dict(
        id=88722216,
        url="https://www.rightmove.co.uk/properties/88722216",
        price=750000,
        currency="GBP",
        bedrooms=2,
        bathrooms=1,
        address="1 High Street, Berkhamsted, HP4 2AB",
        postcode="HP4 2AB",
        latitude=51.7596,
        longitude=-0.5631,
        property_type="Flat",
        property_sub_type="Flat",
        description="A lovely flat in the heart of Berkhamsted.",
        floor_area_sqm=72.5,
        floor_area_sqft=780.0,
        tenure_type="LEASEHOLD",
        years_remaining_on_lease=125,
        annual_service_charge=2400,
        annual_ground_rent=250,
        council_tax_band="D",
        agent_name="Demo Estates",
        agent_branch="Demo Estates - Berkhamsted",
        first_visible_date="2026-01-10",
        images=["https://media.rightmove.co.uk/1.jpg", "https://media.rightmove.co.uk/2.jpg"],
        floorplans=["https://media.rightmove.co.uk/floorplan.gif"],
        key_features=["Lift", "Allocated parking"],
        listing_status="UNDER_OFFER",
        nearest_stations=[
            {"name": "Berkhamsted Station", "distance": 0.4, "unit": "miles", "types": []},
            {"name": "Tring Station", "distance": 1.6, "unit": "kilometres", "types": []},
        ],
        raw={
            "contactInfo": {
                "telephoneNumbers": {"localNumber": "+44 1442 000000"}
            },
            "epcRatings": {"currentRating": "C"},
        },
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_lookup_rightmove_listing_maps_fields() -> None:
    from property_app.tools import lookup_rightmove_listing

    with patch("property_core.fetch_listing", return_value=_stub_detail()):
        result = lookup_rightmove_listing("88722216")

    assert result["id"] == 88722216
    assert result["url"] == "https://www.rightmove.co.uk/properties/88722216"
    assert result["price"] == 750000
    assert result["currency"] == "GBP"
    assert result["address"] == "1 High Street, Berkhamsted, HP4 2AB"
    assert result["postcode"] == "HP4 2AB"
    assert result["latitude"] == 51.7596
    assert result["tenure"] == "leasehold"
    assert result["ground_rent"] == 250
    assert result["service_charge"] == 2400
    assert result["lease_years_remaining"] == 125
    assert result["council_tax_band"] == "D"
    assert result["epc_rating"] == "C"
    assert result["agent_telephone"] == "+44 1442 000000"
    assert result["photo_urls"] == [
        "https://media.rightmove.co.uk/1.jpg",
        "https://media.rightmove.co.uk/2.jpg",
    ]
    assert result["floorplan_url"] == "https://media.rightmove.co.uk/floorplan.gif"
    assert result["floor_area_sqm"] == 72.5
    assert result["listing_status"] == "UNDER_OFFER"
    # Nearest stations: miles untouched, km converted, all rounded
    assert result["nearest_stations"][0] == {
        "name": "Berkhamsted Station",
        "distance_miles": 0.4,
    }
    km_station = result["nearest_stations"][1]
    assert km_station["name"] == "Tring Station"
    assert km_station["distance_miles"] == pytest.approx(0.99, rel=0.05)


def test_lookup_rightmove_listing_freehold_normalises() -> None:
    from property_app.tools import lookup_rightmove_listing

    with patch("property_core.fetch_listing", return_value=_stub_detail(tenure_type="FREEHOLD")):
        result = lookup_rightmove_listing("88722216")
    assert result["tenure"] == "freehold"


def test_lookup_rightmove_listing_unknown_tenure() -> None:
    from property_app.tools import lookup_rightmove_listing

    with patch("property_core.fetch_listing", return_value=_stub_detail(tenure_type=None)):
        result = lookup_rightmove_listing("88722216")
    assert result["tenure"] == "unknown"


def test_lookup_rightmove_listing_floorplan_url_is_none_when_absent() -> None:
    from property_app.tools import lookup_rightmove_listing

    with patch("property_core.fetch_listing", return_value=_stub_detail(floorplans=[])):
        result = lookup_rightmove_listing("88722216")
    assert result["floorplan_url"] is None


def test_lookup_rightmove_listing_handles_scraper_error() -> None:
    from property_core.rightmove_scraper import RightmoveError
    from property_app.tools import lookup_rightmove_listing

    with patch(
        "property_core.fetch_listing",
        side_effect=RightmoveError("Cloudflare challenge"),
    ):
        result = lookup_rightmove_listing("88722216")
    assert "error" in result
    assert "Cloudflare" in result["error"]


def test_search_newhomesforsale_resolves_county_from_postcode() -> None:
    import anyio

    from property_app.tools import search_newhomesforsale

    fake_listings: list = []

    with (
        patch("property_core.postcode_to_county", return_value="Hertfordshire") as mock_pc,
        patch("property_core.town_to_county") as mock_town,
        patch(
            "property_core.NewHomesForSaleLocationAPI"
        ) as mock_loc,
        patch("property_core.fetch_nhfs_listings", return_value=fake_listings),
        patch(
            "property_core.filter_developments_by_distance",
            new=MagicMock(return_value=[]),
        ) as mock_filter,
    ):
        mock_loc.return_value.build_search_url.return_value = (
            "https://www.newhomesforsale.co.uk/new-homes/hertfordshire/"
        )
        # Make filter awaitable
        async def _async_filter(*args, **kwargs):
            return []
        mock_filter.side_effect = _async_filter

        result = anyio.run(search_newhomesforsale, None, None, "HP4 2AB")

    assert result["county"] == "Hertfordshire"
    assert result["resolved_via"] == "postcode:HP4 2AB"
    mock_pc.assert_called_once_with("HP4 2AB")
    mock_town.assert_not_called()


def test_search_newhomesforsale_resolves_county_from_town() -> None:
    import anyio

    from property_app.tools import search_newhomesforsale

    with (
        patch("property_core.postcode_to_county") as mock_pc,
        patch("property_core.town_to_county", return_value="Hertfordshire") as mock_town,
        patch("property_core.NewHomesForSaleLocationAPI") as mock_loc,
        patch("property_core.fetch_nhfs_listings", return_value=[]),
    ):
        mock_loc.return_value.build_search_url.return_value = (
            "https://www.newhomesforsale.co.uk/new-homes/hertfordshire/berkhamsted/"
        )
        result = anyio.run(search_newhomesforsale, None, "Berkhamsted")

    assert result["county"] == "Hertfordshire"
    assert result["resolved_via"] == "town:Berkhamsted"
    mock_pc.assert_not_called()
    mock_town.assert_called_once_with("Berkhamsted")


def test_search_newhomesforsale_uses_explicit_county_when_provided() -> None:
    import anyio

    from property_app.tools import search_newhomesforsale

    with (
        patch("property_core.postcode_to_county") as mock_pc,
        patch("property_core.town_to_county") as mock_town,
        patch("property_core.NewHomesForSaleLocationAPI") as mock_loc,
        patch("property_core.fetch_nhfs_listings", return_value=[]),
    ):
        mock_loc.return_value.build_search_url.return_value = (
            "https://www.newhomesforsale.co.uk/new-homes/hertfordshire/"
        )
        result = anyio.run(search_newhomesforsale, "Hertfordshire")

    assert result["county"] == "Hertfordshire"
    assert "resolved_via" not in result
    mock_pc.assert_not_called()
    mock_town.assert_not_called()


def test_search_newhomesforsale_returns_error_when_all_missing() -> None:
    import anyio

    from property_app.tools import search_newhomesforsale

    result = anyio.run(search_newhomesforsale)
    assert "error" in result
    assert "county" in result["error"].lower()


def test_search_newhomesforsale_returns_error_when_resolution_fails() -> None:
    import anyio

    from property_app.tools import search_newhomesforsale

    with (
        patch("property_core.postcode_to_county", return_value=None),
        patch("property_core.town_to_county", return_value=None),
    ):
        result = anyio.run(
            search_newhomesforsale, None, "Mysteryville", "ZZ1 1ZZ"
        )

    assert "error" in result
    assert "resolve" in result["error"].lower()
