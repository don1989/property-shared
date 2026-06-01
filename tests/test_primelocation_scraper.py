"""Unit tests for the PrimeLocation scraper / URL builder.

Live tests are gated behind ``RUN_LIVE_TESTS=1`` since they require
``curl_cffi`` and a residential-ish network egress (primelocation.com is
behind the same Cloudflare bot gate as Zoopla).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from property_core.primelocation_location import PrimeLocationLocationAPI
from property_core.primelocation_scraper import (
    _parse_listing_html,
    _parse_search_html,
)

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# URL builder
# ---------------------------------------------------------------------------


def test_url_builder_basic_sale():
    url = PrimeLocationLocationAPI().build_search_url("SW1A 1AA")
    assert url == "https://www.primelocation.com/for-sale/property/sw1a-1aa/"


def test_url_builder_basic_rent():
    url = PrimeLocationLocationAPI().build_search_url("SW1A 1AA", property_type="rent")
    assert url == "https://www.primelocation.com/to-rent/property/sw1a-1aa/"


def test_url_builder_area_slug():
    url = PrimeLocationLocationAPI().build_search_url("Greater London")
    assert url == "https://www.primelocation.com/for-sale/property/greater-london/"


def test_url_builder_filters():
    url = PrimeLocationLocationAPI().build_search_url(
        "SW1A 1AA",
        building_type="F",
        min_price=300_000,
        max_price=600_000,
        min_bedrooms=2,
        radius=0.5,
        page=2,
    )
    assert url.startswith("https://www.primelocation.com/for-sale/property/sw1a-1aa/?")
    assert "price_min=300000" in url
    assert "price_max=600000" in url
    assert "beds_min=2" in url
    assert "radius=0.5" in url
    assert "property_sub_type=flats" in url
    assert "pn=2" in url


def test_url_builder_new_build_uses_dedicated_path():
    url = PrimeLocationLocationAPI().build_search_url("SW1A 1AA", new_build=True)
    assert url == "https://www.primelocation.com/new-homes/for-sale/sw1a-1aa/"


def test_url_builder_rejects_new_build_with_rent():
    with pytest.raises(ValueError, match="new_build=True is only valid"):
        PrimeLocationLocationAPI().build_search_url(
            "SW1A 1AA", property_type="rent", new_build=True
        )


def test_url_builder_invalid_property_type():
    with pytest.raises(ValueError):
        PrimeLocationLocationAPI().build_search_url("SW1A 1AA", property_type="lease")


def test_url_builder_empty_postcode():
    with pytest.raises(ValueError):
        PrimeLocationLocationAPI().build_search_url("")


def test_url_builder_url_encoded_postcode_is_decoded():
    url = PrimeLocationLocationAPI().build_search_url("SW1A%201AA")
    assert url == "https://www.primelocation.com/for-sale/property/sw1a-1aa/"


def test_starting_page_honours_existing_pn():
    from property_core.primelocation_scraper import _next_page_url, _starting_page

    url = "https://www.primelocation.com/for-sale/property/sw1a-1aa/?pn=3"
    assert _starting_page(url) == 3
    assert "pn=4" in _next_page_url(url, _starting_page(url) + 1)
    assert _starting_page("https://www.primelocation.com/for-sale/property/sw1a-1aa/") == 1


# ---------------------------------------------------------------------------
# Search-card parsing (synthetic + fixture)
# ---------------------------------------------------------------------------


def test_scraper_parses_new_homes_details_urls():
    """PrimeLocation serves new-home listings from /new-homes/details/{id}/ —
    the card parser must accept that URL family (and fall back to the
    href when the id-wrapper is absent)."""
    html = """
    <html><body>
      <div class="ListingsSearchResultsCard_styles_listingRowStyle__x">
        <a class="ListingsSearchResultsCard_styles_galleryLinkStyle__y"
           href="/new-homes/details/72615642/"></a>
        <p class="ListingsSearchResultsCard_styles_priceTextStyle__z"
           data-testid="listing-price">£450,000</p>
        <p class="ListingsSearchResultsCard_styles_addressStyle__a">5 Example Way, Nottingham, NG1 1AA</p>
      </div>
    </body></html>
    """
    listings = _parse_search_html(html)
    assert len(listings) == 1
    assert listings[0].id == "72615642"
    assert "/new-homes/details/72615642/" in listings[0].url
    assert listings[0].price == 450_000


def test_parse_search_html_extracts_cards():
    html = (FIXTURES / "primelocation_search.html").read_text()
    listings = _parse_search_html(html)
    assert len(listings) > 0, "expected at least one card from the saved fixture"

    first = listings[0]
    assert first.id.isdigit()
    assert first.url.startswith("https://www.primelocation.com/for-sale/details/")
    assert first.price is not None and first.price > 0
    assert first.display_price and first.display_price.startswith("£")
    assert first.address  # non-empty
    assert first.bedrooms is not None
    assert first.amenities  # at least one amenity row


def test_parse_search_html_amenity_parsing():
    """Verify '2 beds', '2 baths' are parsed into ints."""
    html = (FIXTURES / "primelocation_search.html").read_text()
    listings = _parse_search_html(html)
    has_beds = [l for l in listings if l.bedrooms is not None]
    assert has_beds, "expected at least one listing with bedrooms parsed"
    sample = has_beds[0]
    assert any("bed" in a.lower() for a in sample.amenities)


def test_search_card_populates_raw_html():
    """Transport models must populate `raw` per house rules."""
    html = (FIXTURES / "primelocation_search.html").read_text()
    listings = _parse_search_html(html)
    assert listings[0].raw is not None
    assert "html" in listings[0].raw
    assert "listingRowStyle" in listings[0].raw["html"]


# ---------------------------------------------------------------------------
# Profile rotation
# ---------------------------------------------------------------------------


def test_profiles_to_try_orders_initial_first():
    from property_core.primelocation_scraper import _profiles_to_try

    out = _profiles_to_try("safari17_2_ios", ("chrome120", "safari17_2_ios", "firefox133"))
    assert out[0] == "safari17_2_ios"
    assert "chrome120" in out
    assert out.count("safari17_2_ios") == 1


def test_profiles_to_try_disabled_with_empty_fallbacks():
    from property_core.primelocation_scraper import _profiles_to_try

    assert _profiles_to_try("chrome120", ()) == ["chrome120"]


def test_fetch_with_profile_rotation_falls_through_to_working_profile(monkeypatch):
    from property_core import primelocation_scraper as ps

    attempts: list[str] = []

    class _FakeSession:
        def __init__(self, profile: str):
            self.profile = profile

    monkeypatch.setattr(
        ps, "_new_session", lambda *, impersonate, proxy: _FakeSession(impersonate)
    )

    def fake_get(session, url, *, timeout):
        attempts.append(session.profile)
        if session.profile == "chrome120":
            raise ps.PrimeLocationError(f"Cloudflare blocked {session.profile}")
        return f"<html>OK from {session.profile}</html>"

    monkeypatch.setattr(ps, "_get", fake_get)

    session, html = ps._fetch_with_profile_rotation(
        url="https://www.primelocation.com/for-sale/details/1/",
        impersonate="chrome120",
        fallback_profiles=("chrome120", "safari17_2_ios", "firefox133"),
        proxy=None,
        timeout=10.0,
    )
    assert attempts == ["chrome120", "safari17_2_ios"]
    assert session.profile == "safari17_2_ios"
    assert "OK from safari17_2_ios" in html


def test_fetch_with_profile_rotation_raises_when_all_blocked(monkeypatch):
    from property_core import primelocation_scraper as ps

    class _FakeSession:
        def __init__(self, profile: str):
            self.profile = profile

    monkeypatch.setattr(
        ps, "_new_session", lambda *, impersonate, proxy: _FakeSession(impersonate)
    )
    monkeypatch.setattr(
        ps, "_get",
        lambda session, url, *, timeout: (_ for _ in ()).throw(
            ps.PrimeLocationError(f"blocked {session.profile}")
        ),
    )

    with pytest.raises(ps.PrimeLocationError) as exc_info:
        ps._fetch_with_profile_rotation(
            url="https://www.primelocation.com/for-sale/details/1/",
            impersonate="chrome120",
            fallback_profiles=("chrome120", "safari17_2_ios"),
            proxy=None,
            timeout=10.0,
        )
    msg = str(exc_info.value)
    assert "All 2 curl_cffi profiles were blocked" in msg


# ---------------------------------------------------------------------------
# Detail parsing (fixture)
# ---------------------------------------------------------------------------


def test_parse_listing_html_full():
    """Smoke test on the captured listing-detail fixture."""
    html = (FIXTURES / "primelocation_listing.html").read_text()
    detail = _parse_listing_html(
        html,
        listing_id="73140870",
        url="https://www.primelocation.com/for-sale/details/73140870/",
    )
    assert detail.id == "73140870"
    assert detail.price == 750_000
    assert detail.currency == "GBP"
    assert detail.display_price == "£750,000"
    assert detail.title and "2 bed flat" in detail.title.lower()
    assert detail.bedrooms == 2
    assert detail.bathrooms == 2
    assert detail.address == "Uxbridge Road, Shepherds Bush, London W12"
    assert detail.postcode == "W12 0NT"
    assert detail.outcode == "W12"
    assert detail.incode == "0NT"
    assert detail.listing_status == "for_sale"
    assert detail.listing_condition == "pre-owned"
    assert detail.tenure == "Leasehold (87 years)"
    assert detail.council_tax_band == "E"
    assert detail.agent_name == "London Property Zone"
    assert detail.branch_id == 12715
    assert detail.date_posted == "2026-05-08T13:01:23"
    assert detail.breadcrumbs[:2] == ["PrimeLocation", "For sale"]
    assert detail.images and detail.images[0].startswith("https://lid.zoocdn.com/")
    assert detail.nts_info["Tenure"] == "Leasehold (87 years)"
    assert detail.nts_info["Council tax band"] == "E"


# ---------------------------------------------------------------------------
# Live network tests (gated)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_primelocation_search_live() -> None:
    if os.getenv("RUN_LIVE_TESTS") != "1":
        pytest.skip("Set RUN_LIVE_TESTS=1 to run live network tests")

    import anyio
    from functools import partial
    from property_core.primelocation_scraper import PrimeLocationError, fetch_listings

    postcode = os.getenv("PRIMELOCATION_TEST_POSTCODE", "London")
    url = PrimeLocationLocationAPI().build_search_url(postcode)

    try:
        listings = await anyio.to_thread.run_sync(partial(fetch_listings, url, max_pages=1))
    except ImportError:
        pytest.skip("curl_cffi not installed")
    except PrimeLocationError as exc:
        pytest.skip(f"PrimeLocation blocked (likely Cloudflare): {exc}")

    assert isinstance(listings, list)
    print(f"PrimeLocation live fetched {len(listings)} listings from {url}")
    if listings:
        sample = listings[0]
        print(f"  sample: id={sample.id} price={sample.price} addr={sample.address!r}")


@pytest.mark.anyio
async def test_primelocation_listing_detail_live() -> None:
    if os.getenv("RUN_LIVE_TESTS") != "1":
        pytest.skip("Set RUN_LIVE_TESTS=1 to run live network tests")

    import anyio
    from functools import partial
    from property_core.primelocation_scraper import (
        PrimeLocationError,
        fetch_listing,
        fetch_listings,
    )

    postcode = os.getenv("PRIMELOCATION_TEST_POSTCODE", "London")
    url = PrimeLocationLocationAPI().build_search_url(postcode)

    try:
        listings = await anyio.to_thread.run_sync(partial(fetch_listings, url, max_pages=1))
    except ImportError:
        pytest.skip("curl_cffi not installed")
    except PrimeLocationError as exc:
        pytest.skip(f"PrimeLocation blocked (likely Cloudflare): {exc}")

    if not listings:
        pytest.skip("no listings to drill into")
    sample_id = listings[0].id

    try:
        detail = await anyio.to_thread.run_sync(partial(fetch_listing, sample_id))
    except PrimeLocationError as exc:
        pytest.skip(f"PrimeLocation detail blocked: {exc}")

    assert detail.id == sample_id
    assert detail.url.endswith(f"/details/{sample_id}/")
    print(
        f"PrimeLocation detail: id={detail.id} price={detail.price} "
        f"tenure={detail.tenure!r} bd={detail.bedrooms} agent={detail.agent_name!r}"
    )
