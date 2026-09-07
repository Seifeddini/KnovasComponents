"""Screen every generated company name — including the firm's — against Zefix."""
from __future__ import annotations

from typing import Protocol

import requests

ZEFIX_SEARCH_URL = "https://www.zefix.ch/ZefixREST/api/v1/firm/search.json"


class NameCollision(RuntimeError):
    """A generated name matches an active Zefix entry."""


class SearchClient(Protocol):
    def search_exact(self, name: str) -> list[str]:
        ...


class ZefixClient:
    def __init__(self, session: requests.Session | None = None, timeout: float = 20.0):
        self.session = session or requests.Session()
        self.timeout = timeout
        self.session.headers.setdefault("User-Agent", "knovas-demo-kanzlei/1.0")
        self.session.headers.setdefault("Accept", "application/json")

    def search_exact(self, name: str) -> list[str]:
        payload = {
            "languageKey": "de",
            "maxEntries": 30,
            "offset": 0,
            "name": name,
            "searchType": "exact",
            "activeOnly": True,
        }
        resp = self.session.post(ZEFIX_SEARCH_URL, json=payload, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        rows = data.get("list") if isinstance(data, dict) else data
        if not rows:
            return []
        names = []
        for row in rows:
            if isinstance(row, dict):
                value = row.get("name") or row.get("firmName")
                if value:
                    names.append(str(value))
        return names


def _norm(value: str) -> str:
    return " ".join(value.casefold().split())


def screen_names(names: list[str], client: SearchClient | None = None) -> None:
    """Raise NameCollision on the first exact (case-insensitive) active hit."""
    searcher = client or ZefixClient()
    seen: set[str] = set()
    for name in names:
        key = _norm(name)
        if not name.strip() or key in seen:
            continue
        seen.add(key)
        hits = searcher.search_exact(name)
        for hit in hits:
            if _norm(hit) == key:
                raise NameCollision(
                    f"Zefix collision: generated name {name!r} matches active firm {hit!r}"
                )
