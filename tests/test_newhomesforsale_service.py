"""Tests for the NHFS post-filter (``filter_developments_by_distance``)."""

from __future__ import annotations

import os
from typing import Dict, Iterable, Optional

import pytest

from property_core.models.newhomesforsale import NewHomesForSaleDevelopment
from property_core.models.postcode import PostcodeResult
from property_core.newhomesforsale_service import filter_developments_by_distance


def _result(postcode: str, lat: float, lon: float) -> PostcodeResult:
    return PostcodeResult(
        postcode=postcode,
        latitude=lat,
        longitude=lon,
    )


def _dev(
    id_: str,
    name: str,
    postcode: Optional[str],
) -> NewHomesForSaleDevelopment:
    return NewHomesForSaleDevelopment(
        id=id_,
        name=name,
        url=f"https://www.newhomesforsale.co.uk/new-homes/dev/{id_}/",
        postcode=postcode,
    )


class _StubPostcodeClient:
    """Test double for ``PostcodeClient``."""

    def __init__(
        self,
        single: Dict[str, Optional[PostcodeResult]],
        bulk: Optional[Dict[str, Optional[PostcodeResult]]] = None,
    ):
        self._single = single
        self._bulk = bulk if bulk is not None else single

    def lookup(self, postcode: str) -> Optional[PostcodeResult]:
        return self._single.get(postcode.replace(" ", "").upper())

    def bulk_lookup(
        self, postcodes: Iterable[str]
    ) -> Dict[str, Optional[PostcodeResult]]:
        return {
            p.replace(" ", "").upper(): self._bulk.get(p.replace(" ", "").upper())
            for p in postcodes
        }


@pytest.mark.anyio
async def test_filter_keeps_near_drops_far() -> None:
    # Anchor: Hitchin station ~ SG5 1AG
    anchor = _result("SG5 1AG", 51.9499, -0.2731)
    # Within ~0.3mi
    near = _result("SG5 1AB", 51.9520, -0.2750)
    # Stevenage town centre ~ 3mi from Hitchin
    far = _result("SG1 1XX", 51.9038, -0.2017)

    client = _StubPostcodeClient(
        single={"SG51AG": anchor},
        bulk={"SG51AB": near, "SG11XX": far},
    )
    devs = [
        _dev("1", "Hitchin Mews", "SG5 1AB"),
        _dev("2", "Stevenage Heights", "SG1 1XX"),
    ]

    kept = await filter_developments_by_distance(
        devs, anchor_postcode="SG5 1AG", max_miles=1.0, client=client
    )

    assert [d.id for d in kept] == ["1"]
    assert kept[0].distance_to_anchor_miles is not None
    assert kept[0].distance_to_anchor_miles < 1.0


@pytest.mark.anyio
async def test_filter_sorts_by_distance_ascending() -> None:
    anchor = _result("SG5 1AG", 51.9499, -0.2731)
    near = _result("SG5 1AB", 51.9520, -0.2750)  # ~0.2mi
    mid = _result("SG5 2AA", 51.9580, -0.2900)  # ~0.9mi

    client = _StubPostcodeClient(
        single={"SG51AG": anchor},
        bulk={"SG51AB": near, "SG52AA": mid},
    )
    devs = [
        _dev("mid", "Mid", "SG5 2AA"),
        _dev("near", "Near", "SG5 1AB"),
    ]
    kept = await filter_developments_by_distance(
        devs, anchor_postcode="SG5 1AG", max_miles=5.0, client=client
    )
    assert [d.id for d in kept] == ["near", "mid"]


@pytest.mark.anyio
async def test_filter_drops_dev_without_postcode() -> None:
    anchor = _result("SG5 1AG", 51.9499, -0.2731)
    client = _StubPostcodeClient(single={"SG51AG": anchor}, bulk={})
    devs = [_dev("1", "Mystery", postcode=None)]
    kept = await filter_developments_by_distance(
        devs, anchor_postcode="SG5 1AG", max_miles=10.0, client=client
    )
    assert kept == []


@pytest.mark.anyio
async def test_filter_drops_ungeocodable_dev_postcode() -> None:
    anchor = _result("SG5 1AG", 51.9499, -0.2731)
    client = _StubPostcodeClient(
        single={"SG51AG": anchor},
        bulk={"ZZ99ZZ": None},
    )
    devs = [_dev("1", "Phantom", "ZZ99ZZ")]
    kept = await filter_developments_by_distance(
        devs, anchor_postcode="SG5 1AG", max_miles=10.0, client=client
    )
    assert kept == []


@pytest.mark.anyio
async def test_filter_raises_on_unknown_anchor() -> None:
    from unittest.mock import patch

    client = _StubPostcodeClient(single={}, bulk={})
    devs = [_dev("1", "Anything", "SG5 1AB")]
    with patch(
        "property_core.location_resolution.outcode_latlon", return_value=None
    ):
        with pytest.raises(ValueError, match="[Aa]nchor postcode"):
            await filter_developments_by_distance(
                devs, anchor_postcode="ZZ9 9ZZ", max_miles=1.0, client=client
            )


@pytest.mark.anyio
async def test_filter_falls_back_to_outcode_for_partial_postcode() -> None:
    """Partial postcodes like 'HP4' geocode via the outcode endpoint."""
    from unittest.mock import patch

    dev_result = _result("HP4 2AB", 51.7600, -0.5635)
    client = _StubPostcodeClient(
        single={"HP4": None},
        bulk={"HP42AB": dev_result},
    )
    devs = [_dev("1", "Near Berko", "HP4 2AB")]
    with patch(
        "property_core.location_resolution.outcode_latlon",
        return_value=(51.7596, -0.5631),
    ) as mock_outcode:
        kept = await filter_developments_by_distance(
            devs, anchor_postcode="HP4", max_miles=2.0, client=client
        )
    assert len(kept) == 1
    assert kept[0].id == "1"
    mock_outcode.assert_called_once_with("HP4")


@pytest.mark.anyio
async def test_filter_rejects_non_positive_max_miles() -> None:
    client = _StubPostcodeClient(single={}, bulk={})
    with pytest.raises(ValueError, match="max_miles must be positive"):
        await filter_developments_by_distance(
            [], anchor_postcode="SG5 1AG", max_miles=0, client=client
        )


@pytest.mark.anyio
async def test_filter_empty_input_returns_empty() -> None:
    client = _StubPostcodeClient(single={}, bulk={})
    kept = await filter_developments_by_distance(
        [], anchor_postcode="SG5 1AG", max_miles=5.0, client=client
    )
    assert kept == []


# ---------------------------------------------------------------------------
# Live integration
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_hitchin_within_1mi_is_empty_live() -> None:
    """Real-world finding: nothing on NHFS sits within 1mi of Hitchin centre."""
    if os.getenv("RUN_LIVE_TESTS") != "1":
        pytest.skip("Set RUN_LIVE_TESTS=1 to run live network tests")

    import anyio
    from functools import partial

    from property_core.newhomesforsale_scraper import (
        NewHomesForSaleError,
        fetch_listings,
    )

    url = "https://www.newhomesforsale.co.uk/new-homes/hertfordshire/hitchin/"
    try:
        listings = await anyio.to_thread.run_sync(
            partial(fetch_listings, url, rate_limit_seconds=0)
        )
    except NewHomesForSaleError as exc:
        pytest.skip(f"NHFS unavailable: {exc}")

    kept = await filter_developments_by_distance(
        listings, anchor_postcode="SG5 1AG", max_miles=1.0
    )
    assert kept == [], (
        f"Expected 0 NHFS developments within 1mi of SG5 1AG; got "
        f"{[ (d.name, d.postcode, d.distance_to_anchor_miles) for d in kept ]}"
    )
