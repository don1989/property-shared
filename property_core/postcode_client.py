"""Postcode lookup client using postcodes.io API.

Free API for UK postcode lookups - no authentication required.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

import httpx

from property_core.models.postcode import PostcodeResult


class PostcodeClient:
    """Client for postcodes.io API."""

    BASE_URL = "https://api.postcodes.io"
    BULK_BATCH_SIZE = 100

    def __init__(self, timeout: float = 10.0):
        self.timeout = timeout

    def lookup(self, postcode: str) -> Optional[PostcodeResult]:
        """Look up a UK postcode.

        Returns:
            PostcodeResult with postcode info including admin_district
            (local authority), or None if not found.
        """
        # Normalize postcode (remove spaces, uppercase)
        postcode_clean = postcode.replace(" ", "").upper()

        with httpx.Client(timeout=self.timeout) as client:
            try:
                resp = client.get(f"{self.BASE_URL}/postcodes/{postcode_clean}")
                if resp.status_code == 404:
                    return None
                resp.raise_for_status()
                data = resp.json()
                result = data.get("result")
                if not result:
                    return None
                return PostcodeResult.from_api_response(result)
            except httpx.HTTPError:
                return None

    def bulk_lookup(
        self, postcodes: Iterable[str]
    ) -> Dict[str, Optional[PostcodeResult]]:
        """Bulk-lookup UK postcodes via postcodes.io's POST /postcodes endpoint.

        Submits up to ``BULK_BATCH_SIZE`` (100) postcodes per request — the
        upstream limit. Larger inputs are split into batches automatically.

        Returns a dict keyed by the *normalised* postcode (spaces removed,
        uppercased), with a ``PostcodeResult`` value when found and ``None``
        when the postcode is unrecognised. Duplicate inputs are de-duplicated.
        Falls back to per-postcode ``lookup()`` for any batch that fails.
        """
        normalised: list[str] = []
        seen: set[str] = set()
        for raw in postcodes:
            if not raw:
                continue
            n = raw.replace(" ", "").upper()
            if n not in seen:
                seen.add(n)
                normalised.append(n)

        results: Dict[str, Optional[PostcodeResult]] = {}
        if not normalised:
            return results

        with httpx.Client(timeout=self.timeout) as client:
            for i in range(0, len(normalised), self.BULK_BATCH_SIZE):
                batch = normalised[i : i + self.BULK_BATCH_SIZE]
                try:
                    resp = client.post(
                        f"{self.BASE_URL}/postcodes",
                        json={"postcodes": batch},
                    )
                    resp.raise_for_status()
                    payload = resp.json()
                    items = payload.get("result") or []
                    matched: set[str] = set()
                    for item in items:
                        query = (item.get("query") or "").replace(" ", "").upper()
                        result = item.get("result")
                        if not query:
                            continue
                        matched.add(query)
                        results[query] = (
                            PostcodeResult.from_api_response(result) if result else None
                        )
                    # postcodes.io echoes every queried postcode; defensively
                    # mark any missing entries as not-found.
                    for q in batch:
                        results.setdefault(q, None)
                except httpx.HTTPError:
                    for q in batch:
                        if q not in results:
                            results[q] = self.lookup(q)

        return results

    def get_local_authority(
        self, postcode: str, *, include_raw: bool = False
    ) -> Optional[Dict[str, Any]]:
        """Get local authority info for a postcode.

        Returns:
            Dict with name, code, region, etc. or None if not found.
            When include_raw=True, includes the full postcodes.io response
            under the 'raw' key.
        """
        result = self.lookup(postcode)
        if not result:
            return None

        data: Dict[str, Any] = {
            "name": result.admin_district,
            "code": result.codes.get("admin_district") if result.codes else None,
            "county": result.admin_county,
            "region": result.region,
            "country": result.country,
            "postcode": result.postcode,
            "latitude": result.latitude,
            "longitude": result.longitude,
            "rural_urban": result.rural_urban,
        }
        if include_raw:
            data["raw"] = result.raw
        return data
