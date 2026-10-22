"""Tests fuer GET /api/document/<id>/preview-content.

Konventionen folgen tests/test_csrf_enforcement.py und tests/conftest.py.
"""

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from fixtures.make_msg import build_sample_msg  # noqa: E402


@pytest.fixture
def logged_in_client(docbridge_app, tmp_path, monkeypatch):
    """Angemeldeter Testclient, dessen AutoDoc-Wurzel auf tmp_path zeigt."""
    from web_interface import app as web_app

    monkeypatch.setattr(
        web_app.AutoDocFileHandler, "autodoc_path", str(tmp_path), raising=False
    )
    client = docbridge_app.test_client()
    with client.session_transaction() as session:
        session["company_login_ok"] = True
    return client


def test_preview_content_requires_login(docbridge_app, tmp_path):
    client = docbridge_app.test_client()
    response = client.get("/api/document/x/preview-content?path=a.txt")
    assert response.status_code == 401


def test_preview_content_requires_path(logged_in_client):
    response = logged_in_client.get("/api/document/x/preview-content")
    assert response.status_code == 400


def test_preview_content_rejects_traversal(logged_in_client):
    response = logged_in_client.get(
        "/api/document/x/preview-content?path=../../etc/passwd"
    )
    assert response.status_code == 400


def test_preview_content_missing_file(logged_in_client):
    response = logged_in_client.get("/api/document/x/preview-content?path=weg.txt")
    assert response.status_code == 404


def test_preview_content_unsupported_format(logged_in_client, tmp_path):
    (tmp_path / "bild.png").write_bytes(b"\x89PNG\r\n")
    response = logged_in_client.get("/api/document/x/preview-content?path=bild.png")
    assert response.status_code == 415


def test_preview_content_rejects_pdf(logged_in_client, tmp_path):
    (tmp_path / "a.pdf").write_bytes(b"%PDF-1.4\n")
    response = logged_in_client.get("/api/document/x/preview-content?path=a.pdf")
    assert response.status_code == 415


def test_preview_content_returns_txt_markdown(logged_in_client, tmp_path):
    (tmp_path / "notiz.txt").write_text("Hallo Welt.\n", encoding="utf-8")
    response = logged_in_client.get("/api/document/x/preview-content?path=notiz.txt")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["kind"] == "txt"
    assert "Hallo Welt." in payload["markdown"]


def test_preview_content_returns_msg_metadata(logged_in_client, tmp_path):
    build_sample_msg(str(tmp_path / "mail.msg"))
    response = logged_in_client.get("/api/document/x/preview-content?path=mail.msg")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["kind"] == "msg"
    assert payload["meta"]["msg:from"] == "Anna Muster"


# --- EML und MD ------------------------------------------------------------
#
# Knovas Connector nimmt beide standardmaessig auf (_DEFAULT_INCLUDE_GLOBS), die
# Suche findet sie also -- und die Vorschau antwortete 415, weil sie nicht in
# PREVIEW_KIND_BY_SUFFIX standen. Bei einer Kanzlei sind E-Mails der groesste
# Teil des Bestands, das war damit der haeufigste Fehlerfall ueberhaupt.

SAMPLE_EML = (
    "From: meierhans@example.ch\r\n"
    "To: kanzlei@example.ch\r\n"
    "Subject: Schaffhauserstrasse 12\r\n"
    "Date: Fri, 15 Mar 2024 09:00:00 +0100\r\n"
    "Content-Type: text/plain; charset=utf-8\r\n"
    "\r\n"
    "Sehr geehrte Damen und Herren, betreffend Akte 2024-017.\r\n"
)


def test_preview_content_returns_eml_markdown(logged_in_client, tmp_path):
    (tmp_path / "anfrage.eml").write_text(SAMPLE_EML, encoding="utf-8")
    response = logged_in_client.get("/api/document/x/preview-content?path=anfrage.eml")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["kind"] == "eml"
    assert "Akte 2024-017" in payload["markdown"]


def test_preview_content_carries_eml_mail_headers(logged_in_client, tmp_path):
    """Ohne diese Felder ist eine E-Mail in der Vorschau blosser Fliesstext:
    der Dialog zeigt Von und An nur, wenn sie in den Metadaten stehen."""
    (tmp_path / "anfrage.eml").write_text(SAMPLE_EML, encoding="utf-8")
    response = logged_in_client.get("/api/document/x/preview-content?path=anfrage.eml")
    payload = response.get_json()
    assert payload["meta"]["eml:from"] == "meierhans@example.ch"
    assert payload["meta"]["eml:to"] == "kanzlei@example.ch"


def test_preview_content_returns_md_markdown(logged_in_client, tmp_path):
    (tmp_path / "notiz.md").write_text(
        "# Aktennotiz\n\nBesprechung mit **Meierhans**.\n", encoding="utf-8"
    )
    response = logged_in_client.get("/api/document/x/preview-content?path=notiz.md")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["kind"] == "md"
    assert "Aktennotiz" in payload["markdown"]


# --- Lesefassung aus dem Suchindex ------------------------------------------
#
# "Datei nicht verfügbar": der Eintrag steht im Index, die Datei liegt nicht
# mehr auf dem Dokumentenspeicher -- umbenannt, verschoben, oder ein neu
# erzeugtes Korpus mit neuen Dateinamen, dessen alte Einträge noch da sind.
# Der Kontext-Sidecar hält jeden Satz, der bei der Aufnahme gelesen wurde. Ein
# Treffer, den man nur anschauen und nicht lesen kann, ist keiner.

import hashlib  # noqa: E402
import json  # noqa: E402


def _seed_sidecar(store_dir, pointer, sentences):
    store_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(pointer.encode("utf-8")).hexdigest()
    (store_dir / f"{digest}.json").write_text(
        json.dumps({"version": 1, "sentences": sentences}), encoding="utf-8"
    )


@pytest.fixture
def with_index(logged_in_client, tmp_path, monkeypatch):
    store = tmp_path / "index"
    monkeypatch.setenv("SEARCH_CONTEXT_STORE_PATH", str(store))
    return logged_in_client, store


def test_a_missing_file_is_served_from_the_index(with_index):
    client, store = with_index
    _seed_sidecar(store, "2019-012_Aktennotiz.txt", [
        {"i": 1, "t": "AKTENNOTIZ intern — nicht an die Mandantschaft."},
        {"i": 2, "t": "Der Mitgesellschafter blockiert die Dividende."},
    ])
    response = client.get(
        "/api/document/2019-012_Aktennotiz.txt/preview-content"
        "?path=2019-012_Aktennotiz.txt"
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["from_index"] is True
    assert "Dividende" in payload["markdown"]


def test_a_missing_pdf_is_readable_too(with_index):
    """Für ein PDF gibt es dann keine Seiten mehr -- lesen kann man es."""
    client, store = with_index
    _seed_sidecar(store, "vertrag.pdf", [{"i": 1, "t": "Massgeblich ist Art. 368 OR."}])
    payload = client.get(
        "/api/document/vertrag.pdf/preview-content?path=vertrag.pdf"
    ).get_json()
    assert payload["from_index"] is True
    assert "368" in payload["markdown"]


def test_a_document_with_neither_file_nor_index_is_still_404(with_index):
    client, _store = with_index
    response = client.get(
        "/api/document/spurlos.txt/preview-content?path=spurlos.txt"
    )
    assert response.status_code == 404


def test_a_file_that_is_present_is_not_served_from_the_index(with_index, tmp_path):
    """Die Datei ist die Wahrheit, solange es sie gibt."""
    client, store = with_index
    (tmp_path / "notiz.txt").write_text("Der echte Dateiinhalt.\n", encoding="utf-8")
    _seed_sidecar(store, "notiz.txt", [{"i": 1, "t": "Veralteter Indextext."}])
    payload = client.get(
        "/api/document/notiz.txt/preview-content?path=notiz.txt"
    ).get_json()
    assert not payload.get("from_index")
    assert "echte Dateiinhalt" in payload["markdown"]


# --- Fundstellen im Dokument -------------------------------------------------
#
# Der browsereigene PDF-Viewer hebt nichts hervor, was man ihm sagt -- aber er
# zeigt Anmerkungen an, die im Dokument stehen. Die schreibt der Server in den
# ausgelieferten Datenstrom. Die Datei auf dem Dokumentenspeicher bleibt
# unberührt, und /download liefert weiter das Original: wer ein Dokument aus der
# Akte holt, soll nicht unsere Markierungen darin haben.

def _write_pdf(path, lines):
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    page = doc.new_page()
    for i, line in enumerate(lines):
        page.insert_text((60, 100 + i * 24), line, fontsize=11)
    doc.save(str(path))
    doc.close()


def _annotation_count(data):
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open(stream=data, filetype="pdf")
    try:
        return sum(len(list(page.annots() or [])) for page in doc)
    finally:
        doc.close()


def test_the_preview_pdf_carries_the_hits_as_annotations(logged_in_client, tmp_path):
    _write_pdf(tmp_path / "akte.pdf", ["Sophie Keller hat die Akte gefuehrt."])
    response = logged_in_client.get(
        "/api/document/akte.pdf/preview?path=akte.pdf&q=Sophie%20Keller"
    )
    assert response.status_code == 200
    assert _annotation_count(response.data) >= 2


def test_without_a_query_the_pdf_is_untouched(logged_in_client, tmp_path):
    _write_pdf(tmp_path / "akte.pdf", ["Sophie Keller hat die Akte gefuehrt."])
    response = logged_in_client.get("/api/document/akte.pdf/preview?path=akte.pdf")
    assert response.status_code == 200
    assert _annotation_count(response.data) == 0


def test_a_query_that_does_not_occur_leaves_it_untouched(logged_in_client, tmp_path):
    _write_pdf(tmp_path / "akte.pdf", ["Sophie Keller hat die Akte gefuehrt."])
    response = logged_in_client.get(
        "/api/document/akte.pdf/preview?path=akte.pdf&q=Reaktionszeit"
    )
    assert response.status_code == 200
    assert _annotation_count(response.data) == 0


def test_the_download_never_carries_them(logged_in_client, tmp_path):
    """Wer ein Dokument aus der Akte holt, bekommt das Dokument."""
    _write_pdf(tmp_path / "akte.pdf", ["Sophie Keller hat die Akte gefuehrt."])
    logged_in_client.get("/api/document/akte.pdf/preview?path=akte.pdf&q=Sophie")
    response = logged_in_client.get("/api/document/akte.pdf/download?path=akte.pdf")
    assert response.status_code == 200
    assert _annotation_count(response.data) == 0


def test_the_file_on_disk_is_never_modified(logged_in_client, tmp_path):
    target = tmp_path / "akte.pdf"
    _write_pdf(target, ["Sophie Keller hat die Akte gefuehrt."])
    before = target.read_bytes()
    logged_in_client.get("/api/document/akte.pdf/preview?path=akte.pdf&q=Sophie%20Keller")
    assert target.read_bytes() == before


def test_a_semantic_hit_is_marked_even_without_the_words(logged_in_client, tmp_path, monkeypatch):
    """Der Fall, für den es die semantische Suche gibt: die Trefferstelle
    enthält die gesuchten Wörter nicht. Ohne s= stünde sie in der Liste und
    wäre im Dokument nicht zu sehen."""
    import hashlib
    import json

    sentence = "Beim Pachtende fehlt Inventar im Wert der Kaution."
    _write_pdf(tmp_path / "vertrag.pdf", [sentence])
    store = tmp_path / "idx"
    store.mkdir()
    digest = hashlib.sha256(b"vertrag.pdf").hexdigest()
    (store / f"{digest}.json").write_text(
        json.dumps({"version": 1, "sentences": [{"i": 1, "t": sentence}]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("SEARCH_CONTEXT_STORE_PATH", str(store))

    response = logged_in_client.get(
        "/api/document/vertrag.pdf/preview?path=vertrag.pdf&q=Mietzins&s=1"
    )
    assert response.status_code == 200
    # "Mietzins" kommt im Dokument nicht vor; markiert ist trotzdem die Stelle.
    assert _annotation_count(response.data) >= 1


# --- Wie schnell das PDF im Dialog steht ---------------------------------------
#
# Das Vorschaubild kam in Millisekunden, das PDF daneben gar nicht: der Dialog
# fragte vorab mit Range: bytes=0-0, und der Server markierte dafuer das ganze
# Dokument -- vor dem iframe, das es gleich noch einmal tat. Je Rechteck eine
# Anmerkung machte ein langes PDF zweimal 17 s lang.

def test_the_probe_answers_without_reading_the_pdf(logged_in_client, tmp_path, monkeypatch):
    from web_interface import app as web_app

    _write_pdf(tmp_path / "akte.pdf", ["Sophie Keller hat die Akte gefuehrt."])

    def no_highlighting(*args, **kwargs):
        raise AssertionError("the probe must not mark the PDF")

    monkeypatch.setattr(web_app, "highlight_pdf", no_highlighting)
    response = logged_in_client.get(
        "/api/document/akte.pdf/preview?path=akte.pdf&q=Sophie&probe=1"
    )
    assert response.status_code == 204
    assert response.data == b""


def test_the_probe_still_says_when_the_file_is_missing(logged_in_client):
    response = logged_in_client.get(
        "/api/document/fehlt.pdf/preview?path=fehlt.pdf&q=Sophie&probe=1"
    )
    assert response.status_code == 404


def test_one_annotation_per_term_and_page(logged_in_client, tmp_path):
    """Zwanzig Treffer eines Wortes auf einer Seite sind eine Anmerkung mit
    zwanzig Rechtecken, nicht zwanzig Anmerkungen."""
    _write_pdf(tmp_path / "akte.pdf", ["Keller"] * 20)
    response = logged_in_client.get(
        "/api/document/akte.pdf/preview?path=akte.pdf&q=Keller"
    )
    assert response.status_code == 200
    assert _annotation_count(response.data) == 1
    pymupdf = pytest.importorskip("pymupdf")
    with pymupdf.open(stream=response.data, filetype="pdf") as doc:
        page = doc[0]
        annot = next(page.annots())
        assert len(annot.vertices) // 4 == 20


def test_the_time_budget_still_serves_the_whole_pdf(tmp_path, monkeypatch):
    """Ueber dem Zeitbudget werden keine weiteren Seiten markiert; das PDF
    kommt trotzdem ganz, mit dem, was bis dahin markiert war."""
    pymupdf = pytest.importorskip("pymupdf")
    from web_interface import preview

    path = tmp_path / "lang.pdf"
    doc = pymupdf.open()
    for _ in range(5):
        doc.new_page().insert_text((60, 100), "Keller", fontsize=11)
    doc.save(str(path))
    doc.close()

    clock = iter([0.0, 0.0, 0.0, 99.0, 99.0, 99.0, 99.0])
    monkeypatch.setattr(preview.time, "monotonic", lambda: next(clock, 99.0))
    data = preview.highlight_pdf(str(path), ["keller"])
    with pymupdf.open(stream=data, filetype="pdf") as served:
        assert served.page_count == 5
        marked = [len(list(page.annots() or [])) for page in served]
    assert marked == [1, 1, 0, 0, 0]
