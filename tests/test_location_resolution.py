"""Unit tests for postcode_to_county and town_to_county resolvers."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx

from property_core import location_resolution
from property_core.location_resolution import postcode_to_county, town_to_county
from property_core.models.postcode import PostcodeResult


def _clear_caches() -> None:
    location_resolution._postcode_to_county_cache.clear()
    location_resolution._town_to_county_cache.clear()
    location_resolution._outcode_latlon_cache.clear()


def test_postcode_to_county_prefers_admin_county() -> None:
    _clear_caches()
    result = PostcodeResult(
        postcode="HP4 2AB",
        admin_county="Hertfordshire",
        admin_district="Dacorum",
        region="East of England",
    )
    with patch("property_core.location_resolution.PostcodeClient") as mock_cls:
        mock_cls.return_value.lookup.return_value = result
        county = postcode_to_county("HP4 2AB")
    assert county == "Hertfordshire"


def test_postcode_to_county_falls_back_to_admin_district() -> None:
    _clear_caches()
    result = PostcodeResult(
        postcode="SW1A 1AA",
        admin_county=None,
        admin_district="Westminster",
        region="London",
    )
    with patch("property_core.location_resolution.PostcodeClient") as mock_cls:
        mock_cls.return_value.lookup.return_value = result
        county = postcode_to_county("SW1A 1AA")
    assert county == "Westminster"


def test_postcode_to_county_falls_back_to_region() -> None:
    _clear_caches()
    result = PostcodeResult(
        postcode="AB1 2CD",
        admin_county=None,
        admin_district=None,
        region="North East",
    )
    with patch("property_core.location_resolution.PostcodeClient") as mock_cls:
        mock_cls.return_value.lookup.return_value = result
        county = postcode_to_county("AB1 2CD")
    assert county == "North East"


def test_postcode_to_county_uses_outcode_endpoint_for_partial_postcode() -> None:
    _clear_caches()
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "result": {
            "outcode": "HP4",
            "admin_county": ["Hertfordshire"],
            "admin_district": ["Dacorum"],
            "region": "East of England",
        }
    }
    mock_resp.raise_for_status.return_value = None

    with patch("property_core.location_resolution.httpx.Client") as mock_client_cls:
        ctx = MagicMock()
        ctx.get.return_value = mock_resp
        mock_client_cls.return_value.__enter__.return_value = ctx
        county = postcode_to_county("HP4")

    assert county == "Hertfordshire"
    ctx.get.assert_called_once()
    assert "/outcodes/HP4" in ctx.get.call_args[0][0]


def test_postcode_to_county_returns_none_for_unknown_postcode() -> None:
    _clear_caches()
    with patch("property_core.location_resolution.PostcodeClient") as mock_cls:
        mock_cls.return_value.lookup.return_value = None
        county = postcode_to_county("XX1 1XX")
    assert county is None


def test_postcode_to_county_caches_result() -> None:
    _clear_caches()
    result = PostcodeResult(
        postcode="HP4 2AB",
        admin_county="Hertfordshire",
    )
    with patch("property_core.location_resolution.PostcodeClient") as mock_cls:
        mock_cls.return_value.lookup.return_value = result
        assert postcode_to_county("HP4 2AB") == "Hertfordshire"
        assert postcode_to_county("HP4 2AB") == "Hertfordshire"
        # Second call should be cached — only one client construction
        assert mock_cls.return_value.lookup.call_count == 1


def test_town_to_county_picks_first_gb_result() -> None:
    _clear_caches()
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.json.return_value = [
        {"address": {"country_code": "fr", "county": "Paris"}},
        {
            "address": {
                "country_code": "gb",
                "town": "Berkhamsted",
                "county": "Hertfordshire",
                "state": "England",
            }
        },
    ]
    mock_resp.raise_for_status.return_value = None

    with patch("property_core.location_resolution.httpx.Client") as mock_client_cls:
        ctx = MagicMock()
        ctx.get.return_value = mock_resp
        mock_client_cls.return_value.__enter__.return_value = ctx
        county = town_to_county("Berkhamsted")

    assert county == "Hertfordshire"
    args, kwargs = ctx.get.call_args
    assert kwargs["params"]["q"] == "Berkhamsted"
    assert kwargs["params"]["countrycodes"] == "gb"
    assert "User-Agent" in kwargs["headers"]


def test_town_to_county_falls_back_to_state_district() -> None:
    _clear_caches()
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.json.return_value = [
        {
            "address": {
                "country_code": "gb",
                "state_district": "Greater London",
                "state": "England",
            }
        }
    ]
    mock_resp.raise_for_status.return_value = None

    with patch("property_core.location_resolution.httpx.Client") as mock_client_cls:
        ctx = MagicMock()
        ctx.get.return_value = mock_resp
        mock_client_cls.return_value.__enter__.return_value = ctx
        county = town_to_county("Some London Town")

    assert county == "Greater London"


def test_town_to_county_returns_none_on_no_gb_match() -> None:
    _clear_caches()
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.json.return_value = [
        {"address": {"country_code": "us"}},
    ]
    mock_resp.raise_for_status.return_value = None

    with patch("property_core.location_resolution.httpx.Client") as mock_client_cls:
        ctx = MagicMock()
        ctx.get.return_value = mock_resp
        mock_client_cls.return_value.__enter__.return_value = ctx
        county = town_to_county("Nowhere")

    assert county is None


def test_outcode_latlon_returns_centroid_for_outcode() -> None:
    _clear_caches()
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "result": {
            "outcode": "HP4",
            "latitude": 51.7596,
            "longitude": -0.5631,
        }
    }
    mock_resp.raise_for_status.return_value = None
    with patch("property_core.location_resolution.httpx.Client") as mock_client_cls:
        ctx = MagicMock()
        ctx.get.return_value = mock_resp
        mock_client_cls.return_value.__enter__.return_value = ctx
        latlon = location_resolution.outcode_latlon("HP4")
    assert latlon == (51.7596, -0.5631)


def test_outcode_latlon_strips_inward_code_from_full_postcode() -> None:
    _clear_caches()
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "result": {"latitude": 51.5, "longitude": -0.1}
    }
    mock_resp.raise_for_status.return_value = None
    with patch("property_core.location_resolution.httpx.Client") as mock_client_cls:
        ctx = MagicMock()
        ctx.get.return_value = mock_resp
        mock_client_cls.return_value.__enter__.return_value = ctx
        latlon = location_resolution.outcode_latlon("SW1A 1AA")
    assert latlon == (51.5, -0.1)
    assert "/outcodes/SW1A" in ctx.get.call_args[0][0]


def test_outcode_latlon_returns_none_for_unknown_outcode() -> None:
    _clear_caches()
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 404
    with patch("property_core.location_resolution.httpx.Client") as mock_client_cls:
        ctx = MagicMock()
        ctx.get.return_value = mock_resp
        mock_client_cls.return_value.__enter__.return_value = ctx
        latlon = location_resolution.outcode_latlon("ZZ99")
    assert latlon is None


def test_town_to_county_returns_none_on_http_error() -> None:
    _clear_caches()
    with patch("property_core.location_resolution.httpx.Client") as mock_client_cls:
        ctx = MagicMock()
        ctx.get.side_effect = httpx.HTTPError("boom")
        mock_client_cls.return_value.__enter__.return_value = ctx
        county = town_to_county("Berkhamsted")
    assert county is None
