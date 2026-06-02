"""SSRF guard for caller-supplied listing URLs.

Several scraper entrypoints accept a full ``http(s)`` URL and fetch it. Because
the MCP server is reachable by external callers, an un-validated URL is an SSRF
sink (cloud metadata at 169.254.169.254, internal service URLs, ...). This
restricts fetches to the property portals we actually scrape and blocks
private / loopback / link-local targets.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


class UrlNotAllowedError(ValueError):
    """Raised when a caller-supplied URL fails the SSRF allowlist checks."""


# Portal hosts (and their subdomains) we are willing to fetch listing detail
# from. Each scraper narrows this to its own portal.
ALLOWED_SUFFIXES: tuple[str, ...] = (
    "rightmove.co.uk",
    "zoopla.co.uk",
    "onthemarket.com",
    "newhomesforsale.co.uk",
    "primelocation.com",
)


def _host_matches(host: str, suffixes: tuple[str, ...]) -> bool:
    h = host.lower().rstrip(".")
    return any(h == s or h.endswith("." + s) for s in suffixes)


def _is_blocked_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


def validate_listing_url(
    url: str,
    *,
    allowed_suffixes: tuple[str, ...] | None = None,
) -> str:
    """Return the URL unchanged if it targets an allowed portal host and does
    not resolve to a private/internal address; otherwise raise
    :class:`UrlNotAllowedError`.

    Checks, in order: https-only, host on the allowlist, host is not an IP
    literal, and the host does not resolve to any private/link-local/loopback
    address.
    """
    parsed = urlparse(url.strip())
    if parsed.scheme != "https":
        raise UrlNotAllowedError("Only https URLs are allowed")

    host = parsed.hostname
    if not host:
        raise UrlNotAllowedError("URL has no host")

    suffixes = allowed_suffixes or ALLOWED_SUFFIXES
    if not _host_matches(host, suffixes):
        raise UrlNotAllowedError(f"Host not allowed: {host}")

    # IP-literal hosts are never legitimate portal URLs and bypass DNS checks.
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise UrlNotAllowedError("IP-literal hosts are not allowed")

    # Resolve and block private targets, so an allowed-looking name pointed at
    # an internal address (DNS) is still rejected before we connect.
    try:
        infos = socket.getaddrinfo(host, parsed.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UrlNotAllowedError(f"Cannot resolve host: {host}") from exc
    for info in infos:
        ip = info[4][0]
        if _is_blocked_ip(ip):
            raise UrlNotAllowedError(f"Host resolves to a blocked address: {ip}")

    return url.strip()
