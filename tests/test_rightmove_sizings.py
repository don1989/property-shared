"""Unit tests for _extract_sizings and floor_area fields on RightmoveListingDetail."""
from __future__ import annotations


# ---------------------------------------------------------------------------
# _extract_sizings
# ---------------------------------------------------------------------------


def test_extract_sizings_returns_none_none_when_no_sizings():
    from property_core.models.rightmove import _extract_sizings

    assert _extract_sizings({}) == (None, None)
    assert _extract_sizings({"sizings": None}) == (None, None)
    assert _extract_sizings({"sizings": []}) == (None, None)


def test_extract_sizings_sqm_and_sqft():
    from property_core.models.rightmove import _extract_sizings

    data = {"sizings": [
        {"unit": "sqm", "minimumSize": 55, "maximumSize": 55, "displayUnit": "sq. m"},
        {"unit": "sqft", "minimumSize": 592, "maximumSize": 592, "displayUnit": "sq. ft"},
    ]}
    sqm, sqft = _extract_sizings(data)
    assert sqm == 55.0
    assert sqft == 592.0


def test_extract_sizings_sqft_only():
    from property_core.models.rightmove import _extract_sizings

    data = {"sizings": [
        {"unit": "sqft", "minimumSize": 700, "maximumSize": 700},
    ]}
    sqm, sqft = _extract_sizings(data)
    assert sqm is None
    assert sqft == 700.0


def test_extract_sizings_uses_minimum_size_first():
    """minimumSize takes priority over maximumSize."""
    from property_core.models.rightmove import _extract_sizings

    data = {"sizings": [
        {"unit": "sqm", "minimumSize": 50, "maximumSize": 60},
    ]}
    sqm, _ = _extract_sizings(data)
    assert sqm == 50.0


def test_extract_sizings_falls_back_to_maximum_when_minimum_absent():
    """maximumSize is used when minimumSize key is missing."""
    from property_core.models.rightmove import _extract_sizings

    data = {"sizings": [
        {"unit": "sqm", "maximumSize": 60},
    ]}
    sqm, _ = _extract_sizings(data)
    assert sqm == 60.0


def test_extract_sizings_zero_minimum_size_not_treated_as_absent():
    """minimumSize=0 must NOT fall through to maximumSize (falsy-check regression)."""
    from property_core.models.rightmove import _extract_sizings

    data = {"sizings": [
        {"unit": "sqm", "minimumSize": 0, "maximumSize": 55},
    ]}
    sqm, _ = _extract_sizings(data)
    # 0 is a valid (if unusual) floor area — should not fall back to 55
    assert sqm == 0.0


def test_extract_sizings_takes_first_of_each_unit():
    """Only the first sqm and first sqft entry are used."""
    from property_core.models.rightmove import _extract_sizings

    data = {"sizings": [
        {"unit": "sqm", "minimumSize": 55},
        {"unit": "sqm", "minimumSize": 99},
        {"unit": "sqft", "minimumSize": 592},
        {"unit": "sqft", "minimumSize": 1000},
    ]}
    sqm, sqft = _extract_sizings(data)
    assert sqm == 55.0
    assert sqft == 592.0


def test_extract_sizings_skips_malformed_entries():
    """Non-dict entries and entries with no size are skipped gracefully."""
    from property_core.models.rightmove import _extract_sizings

    data = {"sizings": [
        "not a dict",
        {"unit": "sqm"},  # no minimumSize or maximumSize
        {"unit": "sqm", "minimumSize": 55},
    ]}
    sqm, sqft = _extract_sizings(data)
    assert sqm == 55.0
    assert sqft is None
