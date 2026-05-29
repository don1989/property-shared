"""Regression: a 200 search page missing __NEXT_DATA__ is a transient
bot-challenge / interstitial and must be refetched, not surfaced as a hard
failure that drops Rightmove from a whole scan."""

from __future__ import annotations

import json

from property_core import rightmove_scraper as rm


class _FakeResponse:
    def __init__(self, text: str, status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code


class _FakeSession:
    """Returns the queued pages in order, one per .get() call."""

    def __init__(self, pages: list[str]) -> None:
        self._pages = list(pages)
        self.calls = 0

    def get(self, url, headers=None, timeout=None):  # noqa: D401 - mimic requests
        self.calls += 1
        text = self._pages.pop(0) if self._pages else self._pages_last
        self._pages_last = text
        return _FakeResponse(text)


_SOFT_BLOCK_PAGE = "<html><body>Just a moment...</body></html>"


def _valid_search_page() -> str:
    payload = {"props": {"pageProps": {"searchResults": {"properties": [{"id": 1}]}}}}
    return f'<html><body><script id="__NEXT_DATA__">{json.dumps(payload)}</script></body></html>'


def test_missing_next_data_is_retried_then_succeeds() -> None:
    session = _FakeSession([_SOFT_BLOCK_PAGE, _valid_search_page()])

    result = rm._get_search_results(
        session=session,
        url="https://www.rightmove.co.uk/property-for-sale/find.html",
        timeout=5.0,
        retry_attempts=3,
        retry_backoff=0.0,  # no real backoff in the test
    )

    assert result == {"properties": [{"id": 1}]}
    assert session.calls == 2  # first soft-block, then the real page


def test_persistent_soft_block_raises_after_retries() -> None:
    session = _FakeSession([_SOFT_BLOCK_PAGE, _SOFT_BLOCK_PAGE, _SOFT_BLOCK_PAGE])

    try:
        rm._get_search_results(
            session=session,
            url="https://www.rightmove.co.uk/property-for-sale/find.html",
            timeout=5.0,
            retry_attempts=3,
            retry_backoff=0.0,
        )
    except rm.RightmoveError as exc:
        assert "embedded search data" in str(exc)
    else:  # pragma: no cover - the call must raise
        raise AssertionError("expected RightmoveError after exhausting retries")

    assert session.calls == 3
