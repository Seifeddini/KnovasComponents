"""MOCK_RECORD_PARTS: an end-to-end check reads back what an uploader sent.

With the env variable `MOCK_RECORD_PARTS=1` (read at call time, like
`MOCK_DOC_FIELDS`) the mock keeps every init's title, path, identifier and
fields and every part's number, text and page/sentence numbers, per
transmission; `GET /_mock/parts` lists them. Without it the route answers
404 and nothing is kept.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

MOCK_DIR = Path(__file__).resolve().parents[1]
TESTING = MOCK_DIR / "testing.py"

if not TESTING.is_file():  # pragma: no cover - a checkout without the mock
    pytest.skip("mock_knovas_api/testing.py is not in this checkout", allow_module_level=True)

_spec = importlib.util.spec_from_file_location("knovas_mock_testing", TESTING)
testing = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(testing)


def _upload(client, title="Rechnung", fields=None):
    init = client.post("/secured/init_document_transmission",
                       json={"identifier": f"rc-sync/a/{title}.pdf", "title": title,
                             "path": f"a/{title}.pdf", "fields": fields})
    key = init.get_json()["transmission_key_id"]
    client.post("/secured/transmit_document_part",
                json={"key": key, "part_number": 0, "snippet": "Seite eins", "page_number": 1,
                      "sentence_number": 1})
    client.post("/secured/transmit_document_part",
                json={"key": key, "part_number": 1, "snippet": "Seite zwei", "page_number": 2})
    return key


def test_parts_are_recorded_per_document(monkeypatch):
    monkeypatch.setenv("MOCK_RECORD_PARTS", "1")
    client = testing.load_mock_app(doc_fields="filters").test_client()
    _upload(client, fields={"doc_type": "invoice"})

    docs = client.get("/_mock/parts").get_json()["documents"]

    assert len(docs) == 1
    assert docs[0]["title"] == "Rechnung"
    assert docs[0]["path"] == "a/Rechnung.pdf"
    assert docs[0]["identifier"] == "rc-sync/a/Rechnung.pdf"
    assert docs[0]["fields"] == {"doc_type": "invoice"}
    assert docs[0]["parts"] == [
        {"part_number": 0, "snippet": "Seite eins", "page_number": 1, "sentence_number": 1},
        {"part_number": 1, "snippet": "Seite zwei", "page_number": 2, "sentence_number": None},
    ]


def test_route_is_off_and_nothing_is_kept_without_the_switch(monkeypatch):
    monkeypatch.delenv("MOCK_RECORD_PARTS", raising=False)
    app = testing.load_mock_app()
    client = app.test_client()
    _upload(client)

    assert client.get("/_mock/parts").status_code == 404
    monkeypatch.setenv("MOCK_RECORD_PARTS", "1")
    assert client.get("/_mock/parts").get_json() == {"documents": []}
