"""SSRF guard tests (audit H5)."""
from unittest import mock

import pytest

from property_core.url_guard import UrlNotAllowedError, validate_listing_url


def _public_addrinfo(*_a, **_k):
    # Pretend the host resolves to a public address.
    return [(2, 1, 6, "", ("93.184.216.34", 443))]


def test_allows_a_portal_url_resolving_to_a_public_ip():
    with mock.patch("socket.getaddrinfo", _public_addrinfo):
        url = "https://www.rightmove.co.uk/properties/12345"
        assert validate_listing_url(url, allowed_suffixes=("rightmove.co.uk",)) == url


def test_rejects_off_allowlist_host():
    with pytest.raises(UrlNotAllowedError):
        validate_listing_url(
            "https://evil.example.com/x", allowed_suffixes=("rightmove.co.uk",)
        )


def test_rejects_non_https():
    with pytest.raises(UrlNotAllowedError):
        validate_listing_url(
            "http://www.rightmove.co.uk/x", allowed_suffixes=("rightmove.co.uk",)
        )


def test_rejects_ip_literal_metadata_host():
    # The classic cloud-metadata SSRF target.
    with pytest.raises(UrlNotAllowedError):
        validate_listing_url("https://169.254.169.254/latest/meta-data/")


def test_rejects_host_resolving_to_private_address():
    def _private(*_a, **_k):
        return [(2, 1, 6, "", ("10.0.0.5", 443))]

    with mock.patch("socket.getaddrinfo", _private):
        with pytest.raises(UrlNotAllowedError):
            validate_listing_url(
                "https://www.rightmove.co.uk/x", allowed_suffixes=("rightmove.co.uk",)
            )


def test_subdomain_of_allowed_host_is_accepted():
    with mock.patch("socket.getaddrinfo", _public_addrinfo):
        url = "https://media.rightmove.co.uk/x"
        assert validate_listing_url(url, allowed_suffixes=("rightmove.co.uk",)) == url
