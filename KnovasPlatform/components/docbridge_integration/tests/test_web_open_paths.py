"""Getting the document to the person, on both document backends.

"Öffnen" starts the file on the **user's** PC, so it needs a path that PC can
reach — a UNC share, or a mount it makes itself. A deployment whose documents
live only on the server has neither, and the endpoint answered 503 on every
click for every document, with nothing offered in its place. That is the state
a plain LAN install starts in, because knovas.env sets no share.

So the rule these tests pin: there is always a way to the document. A client
path where one is configured; the file itself where none is. The operator can
still refuse the download, but it has to be their decision and not a default
nobody chose.

The two backends differ only in the last step. A fileshare corpus is opened from
the share or downloaded; a OneDrive corpus is mirrored onto the same mount (the
RemoteController mirror downloads it), so preview and download work identically
and it additionally carries a webUrl to open in Office.
"""

from __future__ import annotations

import pytest

pytest.importorskip("flask")


@pytest.fixture
def logged_in(docbridge_app, tmp_path, monkeypatch):
    from web_interface import app as web_app

    monkeypatch.setattr(
        web_app.AutoDocFileHandler, "autodoc_path", str(tmp_path), raising=False
    )
    client = docbridge_app.test_client()
    with client.session_transaction() as session:
        session["company_login_ok"] = True
    return client


class TestThereIsAlwaysAWayToTheDocument:
    def test_without_a_share_the_answer_names_the_download(self, logged_in, tmp_path):
        """503 alone left the browser with an error and the person with nothing.
        The body now says what to do instead, and the UI follows it."""
        (tmp_path / "brief.pdf").write_bytes(b"%PDF-1.4\n")
        response = logged_in.get("/api/document/brief.pdf/client-path?path=brief.pdf")
        assert response.status_code == 503
        assert response.get_json()["fallback"] == "download"

    def test_the_refusal_is_in_the_users_language(self, logged_in, tmp_path):
        """It is shown verbatim in a German UI; "Open mapping not configured
        (OPEN_UNC_ROOT / OPEN_CLIENT_LOCAL_ROOT)" is not an answer to a lawyer."""
        (tmp_path / "brief.pdf").write_bytes(b"%PDF-1.4\n")
        body = logged_in.get(
            "/api/document/brief.pdf/client-path?path=brief.pdf"
        ).get_json()
        assert "OPEN_UNC_ROOT" not in body["error"]
        assert "Freigabe" in body["error"]

    def test_an_operator_who_refuses_the_download_is_obeyed(
        self, docbridge_app, tmp_path, monkeypatch
    ):
        """A default false is nobody's decision; an explicit one is."""
        monkeypatch.setenv("OPEN_ALLOW_DEGRADED_DOWNLOAD_OPEN", "false")
        from web_interface import app as web_app

        monkeypatch.setattr(
            web_app.AutoDocFileHandler, "autodoc_path", str(tmp_path), raising=False
        )
        (tmp_path / "brief.pdf").write_bytes(b"%PDF-1.4\n")
        client = docbridge_app.test_client()
        with client.session_transaction() as session:
            session["company_login_ok"] = True
        body = client.get(
            "/api/document/brief.pdf/client-path?path=brief.pdf"
        ).get_json()
        assert body["fallback"] is None

    def test_the_file_itself_is_served(self, logged_in, tmp_path):
        """Whatever else is configured, the bytes are on this server."""
        (tmp_path / "brief.pdf").write_bytes(b"%PDF-1.4\ncontent")
        response = logged_in.get("/api/document/brief.pdf/download?path=brief.pdf")
        assert response.status_code == 200
        assert response.data.startswith(b"%PDF")


class TestTheSearchPageKnowsWhichWayIsOpen:
    def test_the_download_is_offered_when_there_is_no_client_path(self, logged_in):
        page = logged_in.get("/").data.decode("utf-8")
        assert "openMappingConfigured: false" in page
        assert "allowDegradedDownloadOpen: true" in page


class TestOneDriveIsAPropertyOfTheDocument:
    def test_a_document_without_a_weburl_is_not_marked_openable_there(
        self, logged_in, monkeypatch
    ):
        """A mirrored corpus has an enrichment file and still holds documents
        with no link. Marking those "In OneDrive öffnen" put a button on them
        that 404s, and hid the local Öffnen that would have worked."""
        from web_interface import app as web_app

        monkeypatch.setattr(web_app, "_load_search_enrichment", lambda config=None: {})
        monkeypatch.setattr(
            web_app, "_unique_enrichment_records", lambda: [{"doc_id": "andere.pdf"}]
        )
        from conftest import DummyKnovasClient

        DummyKnovasClient.last_instance.search_results = [
            {"doc_id": "brief.pdf", "path": "brief.pdf", "title": "Brief"}
        ]
        response = logged_in.post(
            "/api/search",
            json={"query": "brief", "limit": 20},
            headers={"X-CSRF-Token": _csrf(logged_in)},
        )
        assert response.status_code == 200
        row = response.get_json()["results"][0]
        assert row.get("onedrive_open_available") is False
        assert not row.get("external_url")


def _csrf(client):
    page = client.get("/").data.decode("utf-8")
    marker = 'csrfToken: "'
    start = page.index(marker) + len(marker)
    return page[start:page.index('"', start)]
