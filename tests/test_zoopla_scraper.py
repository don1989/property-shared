"""Unit tests for the Zoopla scraper / URL builder.

Live tests are gated behind ``RUN_LIVE_TESTS=1`` since they require
Playwright + Chromium and a residential network egress.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from property_core.zoopla_location import ZooplaLocationAPI
from property_core.zoopla_scraper import _parse_listing_html, _parse_search_html

FIXTURES = Path(__file__).parent / "fixtures"


def test_url_builder_basic_sale():
    url = ZooplaLocationAPI().build_search_url("SW1A 1AA")
    assert url == "https://www.zoopla.co.uk/for-sale/property/sw1a-1aa/"


def test_url_builder_basic_rent():
    url = ZooplaLocationAPI().build_search_url("SW1A 1AA", property_type="rent")
    assert url == "https://www.zoopla.co.uk/to-rent/property/sw1a-1aa/"


def test_url_builder_area_slug():
    url = ZooplaLocationAPI().build_search_url("Greater London")
    assert url == "https://www.zoopla.co.uk/for-sale/property/greater-london/"


def test_url_builder_filters():
    url = ZooplaLocationAPI().build_search_url(
        "SW1A 1AA",
        building_type="F",
        min_price=300_000,
        max_price=600_000,
        min_bedrooms=2,
        radius=0.5,
        page=2,
    )
    assert url.startswith("https://www.zoopla.co.uk/for-sale/property/sw1a-1aa/?")
    assert "price_min=300000" in url
    assert "price_max=600000" in url
    assert "beds_min=2" in url
    assert "radius=0.5" in url
    assert "property_sub_type=flats" in url
    assert "pn=2" in url


def test_url_builder_new_build_uses_dedicated_path():
    """new_build=True must use /new-homes/for-sale/{slug}/."""
    url = ZooplaLocationAPI().build_search_url("SW1A 1AA", new_build=True)
    assert url == "https://www.zoopla.co.uk/new-homes/for-sale/sw1a-1aa/"


def test_url_builder_rejects_new_build_with_rent():
    with pytest.raises(ValueError, match="new_build=True is only valid"):
        ZooplaLocationAPI().build_search_url(
            "SW1A 1AA", property_type="rent", new_build=True
        )


def test_scraper_parses_new_homes_details_urls():
    """Zoopla serves new-home listings from /new-homes/details/{id}/ not
    /for-sale/details/{id}/ — the scraper's card parser must accept both."""
    # Minimal HTML mimicking the new-homes search card structure
    html = """
    <html><body>
      <a data-testid="listing-card-content" href="/new-homes/details/72615642/">
        <span data-testid="component-property-price">£450,000</span>
        <address data-testid="component-property-address">5 Example Way, Nottingham, NG1 1AA</address>
      </a>
    </body></html>
    """
    listings = _parse_search_html(html)
    assert len(listings) == 1
    assert listings[0].id == "72615642"
    assert "/new-homes/details/72615642/" in listings[0].url


def test_url_builder_invalid_property_type():
    with pytest.raises(ValueError):
        ZooplaLocationAPI().build_search_url("SW1A 1AA", property_type="lease")


def test_url_builder_empty_postcode():
    with pytest.raises(ValueError):
        ZooplaLocationAPI().build_search_url("")


def test_url_builder_url_encoded_postcode_is_decoded():
    """Reviewer fix: URL-encoded inputs must not survive into the slug."""
    url = ZooplaLocationAPI().build_search_url("SW1A%201AA")
    assert url == "https://www.zoopla.co.uk/for-sale/property/sw1a-1aa/"


def test_starting_page_honours_existing_pn():
    """Reviewer fix: caller-supplied ?pn= must be the starting page, not overwritten."""
    from property_core.zoopla_scraper import _next_page_url, _starting_page

    url = "https://www.zoopla.co.uk/for-sale/property/sw1a-1aa/?pn=3"
    assert _starting_page(url) == 3
    assert "pn=4" in _next_page_url(url, _starting_page(url) + 1)
    # Default to 1 when not present
    assert _starting_page("https://www.zoopla.co.uk/for-sale/property/sw1a-1aa/") == 1


def test_search_card_populates_raw_html():
    """Reviewer fix: transport models must populate `raw` per house rules."""
    html = (FIXTURES / "zoopla_search.html").read_text()
    listings = _parse_search_html(html)
    assert listings[0].raw is not None
    assert "html" in listings[0].raw
    assert "listing-card-content" in listings[0].raw["html"]


def test_parse_search_html_extracts_cards():
    html = (FIXTURES / "zoopla_search.html").read_text()
    listings = _parse_search_html(html)
    assert len(listings) > 0, "expected at least one card from the saved fixture"

    first = listings[0]
    assert first.id.isdigit()
    assert first.url.startswith("https://www.zoopla.co.uk/for-sale/details/")
    assert first.price is not None and first.price > 0
    assert first.display_price and first.display_price.startswith("£")
    assert first.address  # non-empty
    assert first.bedrooms is not None
    assert first.amenities  # at least one amenity row
    # Most cards expose Leasehold/Freehold or a status as a badge
    assert any(b in {"Leasehold", "Freehold", "Reduced", "New", "Featured"} for b in first.badges) or first.badges == []


def test_parse_search_html_amenity_parsing():
    """Verify '2 beds', '2 baths', '1218 sq ft' are parsed into ints."""
    html = (FIXTURES / "zoopla_search.html").read_text()
    listings = _parse_search_html(html)
    # Find a listing with sqft set
    has_sqft = [l for l in listings if l.floor_area_sqft is not None]
    assert has_sqft, "expected at least one listing with floor_area_sqft"
    sample = has_sqft[0]
    assert sample.bedrooms is not None
    assert sample.floor_area_sqft and sample.floor_area_sqft > 200
    assert any("bed" in a.lower() for a in sample.amenities)


def test_profiles_to_try_orders_initial_first():
    from property_core.zoopla_scraper import _profiles_to_try
    out = _profiles_to_try("safari17_2_ios", ("chrome120", "safari17_2_ios", "firefox133"))
    assert out[0] == "safari17_2_ios"
    assert "chrome120" in out
    assert "firefox133" in out
    # No duplicate of initial profile
    assert out.count("safari17_2_ios") == 1


def test_profiles_to_try_disabled_with_empty_fallbacks():
    from property_core.zoopla_scraper import _profiles_to_try
    assert _profiles_to_try("chrome120", ()) == ["chrome120"]


def test_supported_profiles_drops_unknown_names():
    """Profile names the installed curl_cffi doesn't know are filtered out,
    while order of the recognised ones is preserved."""
    from property_core.zoopla_scraper import _supported_profiles
    out = _supported_profiles(["chrome120", "not_a_real_profile", "firefox133"])
    assert out == ["chrome120", "firefox133"]


def test_supported_profiles_falls_back_when_all_unknown():
    """If filtering would drop everything, the input is returned unchanged so
    the request layer (not the filter) surfaces the failure."""
    from property_core.zoopla_scraper import _supported_profiles
    bogus = ["nope1", "nope2"]
    assert _supported_profiles(bogus) == bogus


def test_default_profiles_are_supported_by_installed_curl_cffi():
    """Guard against pinning a default/fallback profile that the locked
    curl_cffi build doesn't actually ship."""
    from property_core.zoopla_scraper import (
        _DEFAULT_IMPERSONATE,
        _FALLBACK_PROFILES,
        _supported_profiles,
    )
    wanted = [_DEFAULT_IMPERSONATE, *_FALLBACK_PROFILES]
    assert _supported_profiles(wanted) == wanted


def test_fetch_with_profile_rotation_falls_through_to_working_profile(monkeypatch):
    """When the first profile is Cloudflare-blocked, rotation must try the
    next profile and return its (session, html) on success."""
    from property_core import zoopla_scraper as zs

    attempts: list[str] = []

    class _FakeSession:
        def __init__(self, profile: str):
            self.profile = profile

    def fake_new_session(*, impersonate: str, proxy):
        return _FakeSession(impersonate)

    def fake_get(session, url, *, timeout):
        attempts.append(session.profile)
        if session.profile == "chrome120":
            raise zs.ZooplaError(f"Cloudflare blocked profile {session.profile}")
        return f"<html>OK from {session.profile}</html>"

    monkeypatch.setattr(zs, "_new_session", fake_new_session)
    monkeypatch.setattr(zs, "_get", fake_get)

    session, html = zs._fetch_with_profile_rotation(
        url="https://www.zoopla.co.uk/for-sale/details/1/",
        impersonate="chrome120",
        fallback_profiles=("chrome120", "safari17_2_ios", "firefox133"),
        proxy=None,
        timeout=10.0,
    )
    assert attempts == ["chrome120", "safari17_2_ios"]
    assert session.profile == "safari17_2_ios"
    assert "OK from safari17_2_ios" in html


def test_fetch_with_profile_rotation_raises_when_all_blocked(monkeypatch):
    from property_core import zoopla_scraper as zs

    class _FakeSession:
        def __init__(self, profile: str):
            self.profile = profile

    monkeypatch.setattr(zs, "_new_session", lambda *, impersonate, proxy: _FakeSession(impersonate))
    monkeypatch.setattr(
        zs, "_get",
        lambda session, url, *, timeout: (_ for _ in ()).throw(
            zs.ZooplaError(f"blocked {session.profile}")
        ),
    )

    with pytest.raises(zs.ZooplaError) as exc_info:
        zs._fetch_with_profile_rotation(
            url="https://www.zoopla.co.uk/for-sale/details/1/",
            impersonate="chrome120",
            fallback_profiles=("chrome120", "safari17_2_ios"),
            proxy=None,
            timeout=10.0,
        )
    msg = str(exc_info.value)
    assert "All 2 curl_cffi profiles were blocked" in msg
    assert "chrome120" in msg and "safari17_2_ios" in msg


def test_parse_listing_html_full():
    """Reviewer-grade smoke test on the captured listing-detail fixture."""
    html = (FIXTURES / "zoopla_listing.html").read_text()
    detail = _parse_listing_html(
        html,
        listing_id="72192746",
        url="https://www.zoopla.co.uk/for-sale/details/72192746/",
    )
    assert detail.id == "72192746"
    assert detail.price == 2_389_000
    assert detail.currency == "GBP"
    assert detail.display_price == "£2,389,000"
    assert detail.title and "1 bed flat" in detail.title.lower()
    assert detail.bedrooms == 1
    assert detail.bathrooms == 1
    assert detail.floor_area == "7,668 sq. ft"
    assert detail.floor_area_sqft == 7668
    assert detail.address == "31, Knightsbridge, London SW1A"
    assert detail.postcode == "SW1A 1AA"
    assert detail.outcode == "SW1A"
    assert detail.property_type == "flat"
    assert detail.listing_status == "for_sale"
    assert detail.listing_condition == "pre-owned"
    assert detail.furnished_state == "furnished"
    assert detail.chain_free is False
    assert detail.has_epc is False
    assert detail.has_floorplan is False
    assert detail.is_retirement_home is False
    assert detail.is_shared_ownership is False
    assert detail.tenure == "Freehold"
    assert detail.council_tax_band == "G"
    assert detail.agent_name == "UK Sotheby's International Realty"
    assert detail.branch_id == 30027
    assert detail.breadcrumbs[:3] == ["Zoopla", "For sale", "London"]
    assert detail.date_posted == "2026-04-08T08:10:50"
    assert detail.images and detail.images[0].startswith("https://lid.zoocdn.com/")
    assert detail.nts_info["Tenure"] == "Freehold"
    assert detail.nts_info["Council tax band"] == "G"


@pytest.mark.anyio
async def test_zoopla_search_live() -> None:
    if os.getenv("RUN_LIVE_TESTS") != "1":
        pytest.skip("Set RUN_LIVE_TESTS=1 to run live network tests")

    import anyio
    from functools import partial
    from property_core.zoopla_scraper import ZooplaError, fetch_listings

    postcode = os.getenv("ZOOPLA_TEST_POSTCODE", "SW1A 1AA")
    url = ZooplaLocationAPI().build_search_url(postcode)

    try:
        listings = await anyio.to_thread.run_sync(
            partial(fetch_listings, url, max_pages=1)
        )
    except ImportError:
        pytest.skip("curl_cffi not installed")
    except ZooplaError as exc:
        pytest.skip(f"Zoopla blocked (likely Cloudflare): {exc}")

    assert isinstance(listings, list)
    print(f"Zoopla live fetched {len(listings)} listings from {url}")
    if listings:
        sample = listings[0]
        print(f"  sample: id={sample.id} price={sample.price} addr={sample.address!r}")


@pytest.mark.anyio
async def test_zoopla_listing_detail_live() -> None:
    if os.getenv("RUN_LIVE_TESTS") != "1":
        pytest.skip("Set RUN_LIVE_TESTS=1 to run live network tests")

    import anyio
    from functools import partial
    from property_core.zoopla_scraper import ZooplaError, fetch_listing, fetch_listings

    postcode = os.getenv("ZOOPLA_TEST_POSTCODE", "SW1A 1AA")
    url = ZooplaLocationAPI().build_search_url(postcode)

    try:
        listings = await anyio.to_thread.run_sync(
            partial(fetch_listings, url, max_pages=1)
        )
    except ImportError:
        pytest.skip("curl_cffi not installed")
    except ZooplaError as exc:
        pytest.skip(f"Zoopla blocked (likely Cloudflare): {exc}")

    if not listings:
        pytest.skip("no listings to drill into")
    sample_id = listings[0].id

    try:
        detail = await anyio.to_thread.run_sync(partial(fetch_listing, sample_id))
    except ZooplaError as exc:
        pytest.skip(f"Zoopla detail blocked: {exc}")

    assert detail.id == sample_id
    assert detail.price is not None
    assert detail.url.endswith(f"/details/{sample_id}/")
    print(
        f"Zoopla detail: id={detail.id} price={detail.price} "
        f"tenure={detail.tenure!r} bd={detail.bedrooms} agent={detail.agent_name!r}"
    )
