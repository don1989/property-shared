"""Unit tests for the Rightmove location lookup + URL builder.

Network calls are mocked via ``unittest.mock``; the live end-to-end
check lives in ``test_rightmove_service_live.py``.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from property_core.rightmove_location import (
    LocationLookupError,
    RightmoveLocationAPI,
)


def _mock_response(payload):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = payload
    return resp


def test_lookup_station_returns_first_station_match():
    api = RightmoveLocationAPI(rate_limit_delay=0)
    payload = {
        "matches": [
            # Typeahead often ranks STREET above STATION when the query
            # contains "Station" as a word — lookup_station must skip past.
            {"type": "STREET", "id": "abc", "displayName": "Station Approach"},
            {"type": "STATION", "id": "4646", "displayName": "Hitchin Station"},
        ]
    }
    with patch("property_core.rightmove_location.requests.get", return_value=_mock_response(payload)):
        assert api.lookup_station("Hitchin Station") == "STATION^4646"


def test_lookup_station_returns_none_when_no_station_match():
    api = RightmoveLocationAPI(rate_limit_delay=0)
    payload = {"matches": [{"type": "STREET", "id": "abc"}, {"type": "REGION", "id": "1"}]}
    with patch("property_core.rightmove_location.requests.get", return_value=_mock_response(payload)):
        assert api.lookup_station("Nowhere") is None


def test_lookup_station_is_cached():
    api = RightmoveLocationAPI(rate_limit_delay=0)
    payload = {"matches": [{"type": "STATION", "id": "4646"}]}
    mock_get = MagicMock(return_value=_mock_response(payload))
    with patch("property_core.rightmove_location.requests.get", mock_get):
        api.lookup_station("Hitchin Station")
        api.lookup_station("hitchin station")  # case-insensitive cache hit
        api.lookup_station("  Hitchin Station  ")  # whitespace ignored
    assert mock_get.call_count == 1


def test_build_search_url_with_station():
    api = RightmoveLocationAPI(rate_limit_delay=0)
    payload = {"matches": [{"type": "STATION", "id": "4646"}]}
    with patch("property_core.rightmove_location.requests.get", return_value=_mock_response(payload)):
        url = api.build_search_url(
            station="Hitchin Station",
            min_bedrooms=3,
            max_bedrooms=3,
            radius=0.5,
            building_type="T",
        )
    assert "locationIdentifier=STATION%5E4646" in url
    assert "radius=0.5" in url
    assert "propertyTypes=terraced" in url
    assert "minBedrooms=3" in url


def test_build_search_url_requires_exactly_one_anchor():
    api = RightmoveLocationAPI(rate_limit_delay=0)
    with pytest.raises(ValueError, match="exactly one"):
        api.build_search_url()  # neither
    with pytest.raises(ValueError, match="exactly one"):
        api.build_search_url(postcode="SG4", station="Hitchin Station")  # both


def test_build_search_url_raises_when_station_unknown():
    api = RightmoveLocationAPI(rate_limit_delay=0)
    payload = {"matches": []}
    with patch("property_core.rightmove_location.requests.get", return_value=_mock_response(payload)):
        with pytest.raises(LocationLookupError, match="station"):
            api.build_search_url(station="Atlantis Central")


@pytest.mark.skipif(
    os.getenv("RUN_LIVE_TESTS") != "1", reason="Set RUN_LIVE_TESTS=1"
)
def test_lookup_station_live():
    """Verify Rightmove's typeahead still returns STATION matches."""
    import requests

    api = RightmoveLocationAPI(rate_limit_delay=0)
    try:
        ident = api.lookup_station("Hitchin Station")
    except requests.RequestException as exc:
        pytest.skip(f"Rightmove typeahead unavailable: {exc}")
    assert ident is not None and ident.startswith("STATION^")
