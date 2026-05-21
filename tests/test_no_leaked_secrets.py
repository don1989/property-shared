"""Repository-wide guard against accidentally committed secrets.

Scrapers in this repo save real upstream HTML as test fixtures. Those
pages often embed third-party API keys (e.g. Google Maps) that should
be redacted before committing.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Google Cloud / Maps / Firebase API key pattern.
GOOGLE_API_KEY = re.compile(r"AIza[0-9A-Za-z_-]{35}")

SCAN_DIRS = ["tests/fixtures", "property_core", "app", "property_app", "property_cli"]
SCAN_SUFFIXES = {".html", ".py", ".json", ".md", ".txt", ".yml", ".yaml"}


def test_no_google_api_keys_in_repo() -> None:
    offenders: list[str] = []
    for sub in SCAN_DIRS:
        root = REPO_ROOT / sub
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.suffix not in SCAN_SUFFIXES or not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for match in GOOGLE_API_KEY.finditer(text):
                offenders.append(f"{path.relative_to(REPO_ROOT)}: {match.group()}")
    assert not offenders, (
        "Found Google API keys in tracked files. Redact them (replace with "
        "'REDACTED_GOOGLE_MAPS_KEY' or similar) before committing:\n  "
        + "\n  ".join(offenders)
    )
