"""Regression tests for address_matching.py.

Covers the three bugs fixed in commit 9db9425:
  1. extract_number — flat/apartment prefix stripping
  2. extract_street — 3-word tokens include directional qualifiers
  3. match_epc_address — raised threshold when target has no house number
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# extract_number
# ---------------------------------------------------------------------------


def test_extract_number_plain_address():
    from property_core.address_matching import extract_number

    assert extract_number("10 High Street") == "10"
    assert extract_number("5A Church Lane") == "5a"


def test_extract_number_flat_prefix_stripped():
    """Flat prefix must not cause extract_number to return the flat number."""
    from property_core.address_matching import extract_number

    # Before fix this returned "1" (the flat number, not the building number)
    assert extract_number("FLAT 1, 5 HIGH STREET") == "5"
    assert extract_number("Apartment 8, 20 Newton Street") == "20"
    assert extract_number("Unit 3, 12 Station Road") == "12"


def test_extract_number_flat_only_address():
    """Address with only a flat number and no building number returns None."""
    from property_core.address_matching import extract_number

    assert extract_number("FLAT 1, HIGH STREET") is None


def test_extract_number_no_number():
    from property_core.address_matching import extract_number

    assert extract_number("Cavendish Crescent South") is None
    assert extract_number("The Old Rectory, Church Lane") is None


# ---------------------------------------------------------------------------
# extract_street
# ---------------------------------------------------------------------------


def test_extract_street_basic():
    from property_core.address_matching import extract_street

    assert extract_street("10 High Street London") == "high street london"


def test_extract_street_includes_directional_qualifier():
    """3-word extraction means North/South/East/West are part of the street token."""
    from property_core.address_matching import extract_street

    # Before fix, both mapped to "cavendish crescent" — indistinguishable
    north = extract_street("5 Cavendish Crescent North")
    south = extract_street("5 Cavendish Crescent South")
    assert north == "cavendish crescent north"
    assert south == "cavendish crescent south"
    assert north != south


def test_extract_street_two_word_fallback():
    from property_core.address_matching import extract_street

    assert extract_street("10 High Street") == "high street"


def test_extract_street_one_word_fallback():
    from property_core.address_matching import extract_street

    assert extract_street("10 Broadway") == "broadway"


# ---------------------------------------------------------------------------
# match_score — wrong-street regression
# ---------------------------------------------------------------------------


def test_match_score_wrong_street_directional_scores_low():
    """Cavendish Crescent North vs South must score below the match threshold."""
    from property_core.address_matching import match_score

    # Before fix this scored 36 — above the 30-point threshold
    score = match_score("5 CAVENDISH CRESCENT NORTH", "Cavendish Crescent South")
    assert score < 30, f"wrong-street score too high: {score}"


def test_match_score_correct_address_scores_high():
    from property_core.address_matching import match_score

    score = match_score("5 CAVENDISH CRESCENT SOUTH", "5 Cavendish Crescent South")
    assert score >= 80


def test_match_score_flat_with_building_number_scores_high():
    """FLAT 1, 5 HIGH STREET against target '5 High Street' should score well."""
    from property_core.address_matching import match_score

    # Before fix, extract_number on the cert returned "1" (flat num) → number mismatch → low score
    score = match_score("FLAT 1, 5 HIGH STREET", "5 High Street")
    assert score >= 50, f"flat cert with correct building number scored too low: {score}"


# ---------------------------------------------------------------------------
# match_epc_address — threshold escalation
# ---------------------------------------------------------------------------


def test_match_epc_address_raises_threshold_without_house_number():
    """When target has no house number, min_score is raised to 50."""
    from unittest.mock import MagicMock
    from property_core.address_matching import match_epc_address

    # Two certs on the same street — word overlap alone would score ~15-20
    def make_cert(address):
        m = MagicMock()
        m.address = address
        return m

    certs = [
        make_cert("5 CAVENDISH CRESCENT NORTH"),
        make_cert("10 CAVENDISH CRESCENT NORTH"),
    ]

    # Target has no house number — neither cert should match
    result = match_epc_address(certs, "Cavendish Crescent North")
    assert result is None, "should not match when no house number and scores are word-overlap only"


def test_match_epc_address_matches_with_house_number():
    """When target has a house number, a matching cert is returned."""
    from unittest.mock import MagicMock
    from property_core.address_matching import match_epc_address

    def make_cert(address):
        m = MagicMock()
        m.address = address
        return m

    certs = [
        make_cert("3 CAVENDISH CRESCENT NORTH"),
        make_cert("5 CAVENDISH CRESCENT NORTH"),
    ]

    result = match_epc_address(certs, "5 Cavendish Crescent North")
    assert result is not None
    cert, score = result
    assert cert.address == "5 CAVENDISH CRESCENT NORTH"
    assert score >= 50
