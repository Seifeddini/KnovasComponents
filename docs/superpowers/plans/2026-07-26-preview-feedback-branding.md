# Multi-Format-Preview, Feedback-Entfernung, Knovas-Branding — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dokument-Vorschau für PDF, DOCX, TXT und MSG in einem Seitenpanel; das Relevanz-/Bewertungs-Feedback samt Endpunkten entfernen; die UI auf das offizielle Knovas-Branding umstellen.

**Architecture:** Der Server extrahiert DOCX/TXT/MSG über `knovas_extract` nach sanitisiertem **Markdown** und liefert es als JSON; PDF wird nicht konvertiert, sondern als `<iframe>` auf den bestehenden `/preview`-Endpunkt eingebettet. Der Client rendert Markdown mit einem escape-first Subset-Renderer in ein Seitenpanel. Kein Build-Schritt, keine neuen JS-Dependencies.

**Tech Stack:** Python 3.11, Flask 3.0, `knovas-extract` 0.2, pytest 7.4, Vanilla JS (ES2020), handgeschriebenes CSS, nginx, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-07-26-preview-feedback-branding-design.md`

## Global Constraints

- Arbeitsverzeichnis für alle Tests und Builds: `KnovasPlatform/components/docbridge_integration`. Tests laufen mit blankem `pytest` (Konfiguration in `pyproject.toml`, `pythonpath = ["src"]`).
- **Keine neuen Laufzeit-Dependencies.** Weder npm noch zusätzliche Python-Pakete über das hinaus, was `knovas-extract`s Extras bereits mitbringen.
- **Der Server liefert niemals HTML aus Dokumentinhalten.** Immer Markdown, das der Client escape-first rendert. Ein `innerHTML` mit unescaptem Extraktions-Output ist ein Fehler, kein Stilproblem.
- Pfad-Auflösung ausschließlich über das vorhandene `_confine_to_autodoc`. Kein neuer Traversal-Code.
- UI-Texte auf Deutsch, passend zum Bestand (`Öffnen`, `Vorschau`, `Abmelden`).
- Nach jeder Änderung an `requirements.txt` oder `src/` muss das Image neu gebaut werden — der Code liegt per `COPY src/` im Image, ein `docker compose restart` übernimmt ihn nicht.
- Farbwerte und Schriften ausschließlich aus der Tabelle in Task 11. Keine erfundenen Zwischentöne.

## File Structure

**Neu:**

| Datei | Verantwortung |
| --- | --- |
| `src/web_interface/preview.py` | Format-Erkennung und Extraktion nach Markdown. Kennt Flask nicht. |
| `src/web_interface/static/js/markdown.js` | Escape-first Markdown-Subset-Renderer. Kennt die App nicht. |
| `tests/fixtures/make_msg.py` | Generator für die `.msg`-Fixture |
| `tests/fixtures/__init__.py` | macht `fixtures` importierbar |
| `tests/test_preview_extract.py` | Tests für `preview.py` |
| `tests/test_preview_endpoint.py` | Tests für die Route |
| `src/web_interface/static/fonts/` | selbst gehostete IBM-Plex-woff2 |

**Geändert:**

| Datei | Änderung |
| --- | --- |
| `requirements.txt:33` | Extra `html` ergänzen |
| `src/web_interface/app.py` | neue Route; drei Feedback-Routen entfernen; `draft_theme` entfernen |
| `src/knovas_client.py` | drei Analytics-Methoden entfernen |
| `src/web_interface/static/js/app.js` | Panel; Toasts; Feedback-/Hover-Code entfernen |
| `src/web_interface/templates/index.html` | Panel-Markup, Toast-Container, Logo, `markdown.js` einbinden, `draft_theme` entfernen |
| `src/web_interface/templates/login.html` | Logo, Schriften |
| `src/web_interface/static/css/style.css` | Panel-Styles, Knovas-Palette, Schriften |
| `nginx/docbridge-web-local.conf` | Framing same-origin erlauben |
| `KnovasPlatform/deploy/host-nginx/` | dieselbe Header-Änderung |
| `tests/test_csrf_enforcement.py` | drei entfallende Endpunkte streichen |

**Gelöscht:** `src/web_interface/static/css/drafts/` (4 Dateien), `tests/test_engagement.py`

Die Aufteilung hält `preview.py` und `markdown.js` frei von Framework- bzw. App-Wissen, damit beide isoliert testbar bleiben. `app.py` ist mit 2.194 Zeilen bereits groß; deshalb kommt die Extraktionslogik bewusst in ein eigenes Modul statt obendrauf.

---

# Phase 1 — Multi-Format-Preview

## Task 1: Dependency-Fix und MSG-Fixture

**Files:**
- Modify: `requirements.txt:33`
- Create: `tests/fixtures/__init__.py`
- Create: `tests/fixtures/make_msg.py`
- Test: `tests/test_preview_extract.py`

**Interfaces:**
- Consumes: nichts
- Produces: `build_sample_msg(path: str) -> None` schreibt eine gültige `.msg` an `path`.
  Import in Tests: `from fixtures.make_msg import build_sample_msg` — **nicht** `tests.fixtures...`.
  `tests/` hat kein `__init__.py`, deshalb legt pytest `tests/` selbst auf `sys.path[0]`;
  `import tests.fixtures...` scheitert mit `No module named 'tests'`.

- [ ] **Step 1: `requirements.txt` korrigieren**

Zeile 33 lautet aktuell:

```
knovas-extract[pdf,docx,msg,markdown,sentences]>=0.2
```

Ersetzen durch:

```
knovas-extract[pdf,docx,msg,markdown,html,sentences]>=0.2
```

Grund: `selectolax` hängt am Extra `html`, nicht an `markdown`. DOCX geht den Weg DOCX → mammoth → HTML → Markdown und wirft ohne den HTML-Parser zur Laufzeit `DependencyMissingError`.

- [ ] **Step 2: Image neu bauen**

```bash
cd KnovasPlatform && docker compose build docbridge-web
```

- [ ] **Step 3: Nachweisen, dass selectolax jetzt vorhanden ist**

```bash
docker run --rm docbridge-web-clientbundle:latest python -c "import selectolax; print(selectolax.__name__)"
```

Erwartet: `selectolax`

- [ ] **Step 4: Fixture-Paket anlegen**

`tests/fixtures/__init__.py` — leere Datei.

- [ ] **Step 5: MSG-Generator schreiben**

`tests/fixtures/make_msg.py`:

```python
"""Erzeugt eine minimale, aber echte Outlook-.msg (OLE/CFB) als Testfixture.

Layout nach MS-OXMSG:
  __properties_version1.0        Property-Stream der Nachricht (32-Byte-Kopf)
  __substg1.0_<TAG>              je ein Stream pro variabel langer Property
  __recip_version1.0_#00000000/  Empfaenger-Storage (8-Byte-Property-Kopf)
  __nameid_version1.0/           Named-Property-Map (drei leere Streams)
"""

import datetime
import struct

from extract_msg import OleWriter

PT_LONG = 0x0003
PT_SYSTIME = 0x0040
PT_UNICODE = 0x001F

READABLE_WRITABLE = 0x06

# Festes Datum haelt die Fixture byteweise reproduzierbar.
SENT_AT = datetime.datetime(2026, 3, 15, 10, 0, 0, tzinfo=datetime.timezone.utc)
EPOCH_1601 = datetime.datetime(1601, 1, 1, tzinfo=datetime.timezone.utc)
FILETIME = int((SENT_AT - EPOCH_1601).total_seconds() * 10_000_000)

SUBJECT = "Rückfrage zum Kaufvertrag 2024-001"
BODY = (
    "Guten Tag,\r\n\r\n"
    "der Kaufpreis von EUR 485.000,00 ist bis zum Übergabetermin zu bezahlen.\r\n"
    "Bitte bestätigen Sie den Termin.\r\n\r\n"
    "Freundliche Grüsse\r\nA. Muster"
)
SENDER_NAME = "Anna Muster"
RECIPIENT_EMAIL = "beat.beispiel@example.com"


def _tag_name(propid: int, proptype: int) -> str:
    return f"__substg1.0_{propid:04X}{proptype:04X}"


class _PropStream:
    """Sammelt 16-Byte-Property-Eintraege und die Streams, auf die sie zeigen."""

    def __init__(self, header: bytes):
        self.header = header
        self.entries: list[bytes] = []
        self.streams: dict[str, bytes] = {}

    def add_unicode(self, propid: int, value: str) -> None:
        raw = value.encode("utf-16-le")
        # Das Groessenfeld zaehlt den abschliessenden Null-Terminator mit (2 Byte).
        self.entries.append(
            struct.pack(
                "<IIII", (propid << 16) | PT_UNICODE, READABLE_WRITABLE, len(raw) + 2, 0
            )
        )
        self.streams[_tag_name(propid, PT_UNICODE)] = raw

    def add_long(self, propid: int, value: int) -> None:
        self.entries.append(
            struct.pack("<IIQ", (propid << 16) | PT_LONG, READABLE_WRITABLE, value)
        )

    def add_time(self, propid: int, filetime: int) -> None:
        self.entries.append(
            struct.pack("<IIQ", (propid << 16) | PT_SYSTIME, READABLE_WRITABLE, filetime)
        )

    def properties_bytes(self) -> bytes:
        return self.header + b"".join(self.entries)


def build_sample_msg(path: str) -> None:
    """Schreibt eine gueltige .msg nach ``path``."""
    top = _PropStream(
        struct.pack(
            "<8sIIII8s",
            b"\x00" * 8,   # reserviert
            1,             # naechste Empfaenger-ID
            0,             # naechste Anhang-ID
            1,             # Empfaengeranzahl
            0,             # Anhanganzahl
            b"\x00" * 8,   # reserviert
        )
    )
    top.add_unicode(0x0037, SUBJECT)                     # PR_SUBJECT_W
    top.add_unicode(0x0E1D, SUBJECT)                     # PR_NORMALIZED_SUBJECT_W
    top.add_unicode(0x1000, BODY)                        # PR_BODY_W
    top.add_unicode(0x0C1A, SENDER_NAME)                 # PR_SENDER_NAME_W
    top.add_unicode(0x0C1F, "anna.muster@example.com")   # PR_SENDER_EMAIL_ADDRESS_W
    top.add_unicode(0x001A, "IPM.Note")                  # PR_MESSAGE_CLASS_W
    # extract_msg liest msg.date aus PR_CLIENT_SUBMIT_TIME, nicht aus Delivery-
    # oder Creation-Time -- und nur, wenn die Nachricht als gesendet gilt.
    top.add_time(0x0039, FILETIME)                       # PR_CLIENT_SUBMIT_TIME
    top.add_time(0x0E06, FILETIME)                       # PR_MESSAGE_DELIVERY_TIME
    top.add_time(0x3007, FILETIME)                       # PR_CREATION_TIME
    # isSent prueft PR_MESSAGE_FLAGS auf das MSGFLAG_UNSENT-Bit (0x08).
    top.add_long(0x0E07, 0x01)                           # PR_MESSAGE_FLAGS = READ

    recip = _PropStream(b"\x00" * 8)
    recip.add_unicode(0x3001, "Beat Beispiel")           # PR_DISPLAY_NAME_W
    recip.add_unicode(0x3003, RECIPIENT_EMAIL)           # PR_EMAIL_ADDRESS_W
    recip.add_long(0x0C15, 1)                            # PR_RECIPIENT_TYPE = To

    writer = OleWriter()
    writer.addEntry("__properties_version1.0", top.properties_bytes())
    for name, data in top.streams.items():
        writer.addEntry(name, data)

    writer.addEntry("__recip_version1.0_#00000000", storage=True)
    writer.addEntry(
        "__recip_version1.0_#00000000/__properties_version1.0", recip.properties_bytes()
    )
    for name, data in recip.streams.items():
        writer.addEntry(f"__recip_version1.0_#00000000/{name}", data)

    writer.addEntry("__nameid_version1.0", storage=True)
    for tag in ("00020102", "00030102", "00040102"):
        writer.addEntry(f"__nameid_version1.0/__substg1.0_{tag}", b"")

    writer.write(path)
```

- [ ] **Step 6: Test schreiben, der die Fixture prüft**

`tests/test_preview_extract.py`:

```python
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from fixtures.make_msg import build_sample_msg  # noqa: E402


def test_generated_msg_parses_with_extract_msg(tmp_path):
    import extract_msg

    target = tmp_path / "sample.msg"
    build_sample_msg(str(target))

    msg = extract_msg.openMsg(str(target))
    assert msg.subject == "Rückfrage zum Kaufvertrag 2024-001"
    assert msg.sender == "Anna Muster"
    assert msg.isSent is True
    assert msg.date is not None
    assert msg.date.year == 2026 and msg.date.month == 3 and msg.date.day == 15
    assert [r.email for r in msg.recipients] == ["beat.beispiel@example.com"]
    assert "Kaufpreis von EUR 485.000,00" in msg.body
```

- [ ] **Step 7: Test laufen lassen**

```bash
cd KnovasPlatform/components/docbridge_integration && pytest tests/test_preview_extract.py -v
```

Erwartet: PASS. Schlägt `msg.date is not None` fehl, fehlt `PR_CLIENT_SUBMIT_TIME` oder das `MSGFLAG_UNSENT`-Bit ist gesetzt.

- [ ] **Step 8: Commit**

```bash
git add requirements.txt tests/fixtures/ tests/test_preview_extract.py
git commit -m "fix: add html extra for knovas-extract, add reproducible .msg test fixture"
```

---

## Task 2: Extraktionsmodul `preview.py`

**Files:**
- Create: `src/web_interface/preview.py`
- Test: `tests/test_preview_extract.py` (erweitern)

**Interfaces:**
- Consumes: `build_sample_msg` aus Task 1, importiert als `from fixtures.make_msg import build_sample_msg`
- Produces:
  - `PREVIEW_KIND_BY_SUFFIX: dict[str, str]` — `{".pdf": "pdf", ".docx": "docx", ".txt": "txt", ".msg": "msg"}`
  - `preview_kind(path: str) -> str | None`
  - `extract_markdown(path: str) -> dict` — liefert `{"kind": str, "markdown": str, "meta": dict, "warnings": list[str]}`
  - `PreviewUnsupported(Exception)`, `PreviewFailed(Exception)`

- [ ] **Step 1: Failing Test für die Format-Erkennung schreiben**

An `tests/test_preview_extract.py` anhängen:

```python
from web_interface.preview import preview_kind  # noqa: E402


def test_preview_kind_recognises_supported_suffixes():
    assert preview_kind("a/b/report.pdf") == "pdf"
    assert preview_kind("Vertrag.DOCX") == "docx"
    assert preview_kind("notiz.txt") == "txt"
    assert preview_kind("mail.msg") == "msg"


def test_preview_kind_rejects_everything_else():
    assert preview_kind("bild.png") is None
    assert preview_kind("archiv.zip") is None
    assert preview_kind("ohne_endung") is None
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag bestätigen**

```bash
pytest tests/test_preview_extract.py::test_preview_kind_recognises_supported_suffixes -v
```

Erwartet: FAIL mit `ModuleNotFoundError: No module named 'web_interface.preview'`

- [ ] **Step 3: `preview.py` schreiben**

```python
"""Dokument-Extraktion fuer die Vorschau.

Traegt bewusst kein Flask-Wissen, damit die Extraktion isoliert testbar bleibt.

Sicherheitsposition: dieses Modul gibt **Markdown** heraus, niemals HTML.
``knovas_extract._markdown`` ist die Trust Boundary gegenueber feindlichen
Dokumenten -- es entfernt Markup, Event-Handler und gefaehrliche URL-Schemata.
Es escaped jedoch keinen *Textinhalt*: ein Dokument, das woertlich
``<script>`` enthaelt, liefert diese Zeichen unveraendert zurueck. Der Client
muss deshalb zuerst escapen und erst danach formatieren.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

PREVIEW_KIND_BY_SUFFIX: Dict[str, str] = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".txt": "txt",
    ".msg": "msg",
}

# Obergrenzen fuer die Extraktion. Bewusst konservativ: die Vorschau ist eine
# Entscheidungshilfe in der Trefferliste, kein vollwertiger Dokumentbetrachter.
MAX_INPUT_BYTES = 25 * 1024 * 1024
MAX_TEXT_BYTES = 2 * 1024 * 1024


class PreviewUnsupported(Exception):
    """Das Format hat keinen Vorschaupfad."""


class PreviewFailed(Exception):
    """Die Datei ist beschaedigt, verschluesselt oder zu gross."""


def preview_kind(path: str) -> Optional[str]:
    """Vorschau-Art anhand der Dateiendung, oder None."""
    _, suffix = os.path.splitext(path or "")
    return PREVIEW_KIND_BY_SUFFIX.get(suffix.lower())


def extract_markdown(path: str) -> Dict[str, Any]:
    """Extrahiert ``path`` nach sanitisiertem Markdown.

    PDF gehoert nicht hierher -- es wird im Browser nativ dargestellt.
    """
    kind = preview_kind(path)
    if kind is None or kind == "pdf":
        raise PreviewUnsupported(path)

    import knovas_extract
    from knovas_extract.errors import ExtractError
    from knovas_extract.result import Limits

    limits = Limits(max_input_bytes=MAX_INPUT_BYTES, max_text_bytes=MAX_TEXT_BYTES)
    try:
        result = knovas_extract.extract(path, limits=limits, emit_markdown=True)
    except ExtractError as exc:
        raise PreviewFailed(str(exc)) from exc

    markdown = result.content.markdown or ""
    metadata = result.metadata
    meta: Dict[str, Any] = {
        "title": metadata.title,
        "page_count": metadata.page_count,
        "word_count": metadata.word_count,
        "created": metadata.created,
        "modified": metadata.modified,
    }
    # MSG legt Absender, Empfaenger und Body-Quelle unter msg:* in extra ab.
    for key, value in (metadata.extra or {}).items():
        if key.startswith("msg:"):
            meta[key] = value

    return {
        "kind": kind,
        "markdown": markdown,
        "meta": meta,
        "warnings": list(result.warnings or []),
    }
```

- [ ] **Step 4: Test laufen lassen**

```bash
pytest tests/test_preview_extract.py -v
```

Erwartet: PASS

- [ ] **Step 5: Failing Tests für die Extraktion aller drei Formate schreiben**

An `tests/test_preview_extract.py` anhängen:

```python
import pytest  # noqa: E402

from web_interface.preview import (  # noqa: E402
    PreviewUnsupported,
    extract_markdown,
)


def test_extract_txt(tmp_path):
    target = tmp_path / "notiz.txt"
    target.write_text("Zeile eins.\nZeile zwei mit Umlauten: äöü.\n", encoding="utf-8")

    result = extract_markdown(str(target))
    assert result["kind"] == "txt"
    assert "Zeile eins." in result["markdown"]
    assert "äöü" in result["markdown"]


def test_extract_docx(tmp_path):
    import docx

    target = tmp_path / "vertrag.docx"
    document = docx.Document()
    document.add_heading("Kaufvertrag", 1)
    document.add_paragraph("Der Kaufpreis betraegt EUR 485.000.")
    document.save(str(target))

    result = extract_markdown(str(target))
    assert result["kind"] == "docx"
    assert "# Kaufvertrag" in result["markdown"]
    assert "EUR 485.000" in result["markdown"]
    assert result["meta"]["word_count"] > 0


def test_extract_msg(tmp_path):
    target = tmp_path / "mail.msg"
    build_sample_msg(str(target))

    result = extract_markdown(str(target))
    assert result["kind"] == "msg"
    assert "Kaufpreis von EUR 485.000,00" in result["markdown"]
    assert result["meta"]["title"] == "Rückfrage zum Kaufvertrag 2024-001"
    assert result["meta"]["msg:from"] == "Anna Muster"
    assert result["meta"]["msg:to"] == "beat.beispiel@example.com"


def test_extract_rejects_pdf_and_unknown(tmp_path):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    with pytest.raises(PreviewUnsupported):
        extract_markdown(str(pdf))
    with pytest.raises(PreviewUnsupported):
        extract_markdown(str(tmp_path / "bild.png"))


def test_extraction_does_not_escape_document_text(tmp_path):
    """Haelt die Annahme fest, auf der der Client-Renderer beruht.

    knovas_extract entfernt *Markup* aus feindlichen Dokumenten, escaped aber
    keinen *Textinhalt*: steht "<script>" woertlich im Fliesstext, kommt es
    unveraendert zurueck. static/js/markdown.js escaped deshalb zuerst und
    formatiert erst danach. Schlaegt dieser Test fehl, weil die Bibliothek
    inzwischen selbst escaped, ist das kein Fehler -- aber der Renderer und
    dieser Kommentar gehoeren dann ueberprueft.
    """
    import docx

    target = tmp_path / "hostile.docx"
    document = docx.Document()
    document.add_paragraph("<script>alert(1)</script>")
    document.save(str(target))

    markdown = extract_markdown(str(target))["markdown"]
    assert "<script>" in markdown
```

- [ ] **Step 6: Tests laufen lassen**

```bash
pytest tests/test_preview_extract.py -v
```

Erwartet: PASS. Scheitert `test_extract_docx` mit `DependencyMissingError: selectolax`, wurde Task 1 Step 1 nicht angewendet oder das Image nicht neu gebaut.

- [ ] **Step 7: Commit**

```bash
git add src/web_interface/preview.py tests/test_preview_extract.py
git commit -m "feat: add preview extraction module for docx, txt and msg"
```

---

## Task 3: Vorschau-Endpunkt

**Files:**
- Modify: `src/web_interface/app.py` (neue Route direkt nach `preview_document`, aktuell endend bei Zeile 1130)
- Test: `tests/test_preview_endpoint.py`

**Interfaces:**
- Consumes: `web_interface.preview.extract_markdown`, `preview_kind`, `PreviewUnsupported`, `PreviewFailed` aus Task 2
- Produces: `GET /api/document/<doc_id>/preview-content?path=<pfad>` mit Endpunktnamen `preview_content`

- [ ] **Step 1: Failing Tests schreiben**

`tests/test_preview_endpoint.py`:

```python
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
```

- [ ] **Step 2: Tests laufen lassen, Fehlschlag bestätigen**

```bash
pytest tests/test_preview_endpoint.py -v
```

Erwartet: alle FAIL mit `404 != 400` bzw. `404 != 200` — die Route existiert noch nicht.

- [ ] **Step 3: Route implementieren**

In `src/web_interface/app.py` direkt nach der bestehenden Funktion `preview_document` einfügen:

```python
    @app.route('/api/document/<path:doc_id>/preview-content', methods=['GET'])
    def preview_content(doc_id: str):
        """Sanitisiertes Markdown fuer DOCX, TXT und MSG.

        PDF laeuft bewusst nicht hierueber: der Client bettet /preview ein und
        laesst den Browser rendern. Antwort enthaelt niemals HTML -- der Client
        escaped zuerst und formatiert danach (siehe static/js/markdown.js).
        """
        file_path = str(request.args.get('path') or '').strip()
        if not file_path:
            return jsonify({'success': False, 'error': 'Document path required'}), 400

        full_path = _resolve_autodoc_path(file_path)
        if not full_path:
            return jsonify({'success': False, 'error': 'Document path not allowed'}), 400

        kind = preview_kind(file_path)
        if kind is None or kind == 'pdf':
            return jsonify({'success': False, 'error': 'Preview not supported for this format'}), 415

        if not os.path.exists(full_path):
            return jsonify({'success': False, 'error': 'Document file not found'}), 404

        try:
            extracted = extract_markdown(full_path)
        except PreviewUnsupported:
            return jsonify({'success': False, 'error': 'Preview not supported for this format'}), 415
        except PreviewFailed as exc:
            logger.warning("Preview extraction failed for %s: %s", file_path, exc)
            return jsonify({'success': False, 'error': 'Vorschau konnte nicht erzeugt werden'}), 422
        except Exception:
            logger.error("Preview error for %s", file_path, exc_info=True)
            return jsonify({'success': False, 'error': _GENERIC_ERROR_MESSAGE}), 500

        return jsonify({
            'success': True,
            'doc_id': doc_id,
            'kind': extracted['kind'],
            'markdown': extracted['markdown'],
            'meta': extracted['meta'],
            'warnings': extracted['warnings'],
        })
```

Reihenfolge beachten: der Pfad-Guard läuft **vor** der Formatprüfung, damit ein Traversal-Versuch mit erlaubter Endung 400 liefert und nicht 415. Die Existenzprüfung läuft **nach** der Formatprüfung, damit eine nicht unterstützte Endung 415 liefert, ohne die Existenz der Datei zu verraten.

- [ ] **Step 4: Import ergänzen**

Bei den übrigen `web_interface`-Importen am Kopf von `app.py`:

```python
from web_interface.preview import (
    PreviewFailed,
    PreviewUnsupported,
    extract_markdown,
    preview_kind,
)
```

- [ ] **Step 5: Tests laufen lassen**

```bash
pytest tests/test_preview_endpoint.py -v
```

Erwartet: PASS

- [ ] **Step 6: Gesamte Suite laufen lassen**

```bash
pytest
```

Erwartet: PASS

- [ ] **Step 7: Commit**

```bash
git add src/web_interface/app.py tests/test_preview_endpoint.py
git commit -m "feat: add preview-content endpoint for docx, txt and msg"
```

---

## Task 4: nginx erlaubt Same-Origin-Framing

**Files:**
- Modify: `KnovasPlatform/components/docbridge_integration/nginx/docbridge-web-local.conf:11-12`
- Modify: `KnovasPlatform/deploy/host-nginx/` (Template mit denselben beiden Headern)

- [ ] **Step 1: Blockade reproduzieren**

Stack starten und im Browser auf der Suchseite ausführen:

```javascript
const f = document.createElement('iframe'); f.src = '/login'; document.body.appendChild(f);
```

Erwartet in der Konsole:

```
Framing '…' violates the following Content Security Policy directive:
"frame-ancestors 'none'". The request has been blocked.
```

- [ ] **Step 2: Lokale nginx-Conf anpassen**

In `nginx/docbridge-web-local.conf` diese beiden Zeilen:

```nginx
    add_header X-Frame-Options "DENY" always;
    add_header Content-Security-Policy "frame-ancestors 'none'" always;
```

ersetzen durch:

```nginx
    # SAMEORIGIN statt DENY: die App bettet eigene Dokumentvorschauen als
    # <iframe> ein. Framing durch fremde Herkuenfte bleibt verboten.
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header Content-Security-Policy "frame-ancestors 'self'" always;
```

- [ ] **Step 3: Produktions-Template gleichziehen**

```bash
grep -rn "frame-ancestors\|X-Frame-Options" KnovasPlatform/deploy/host-nginx/
```

Jede Fundstelle auf `SAMEORIGIN` bzw. `'self'` setzen, mit demselben Kommentar. Ein zurückbleibendes `DENY` im Produktions-Template lässt die Vorschau nur in der Entwicklung funktionieren.

- [ ] **Step 4: Stack neu starten und Blockade als aufgehoben nachweisen**

```bash
cd KnovasPlatform && docker compose up -d --force-recreate docbridge-web-nginx
curl -sI http://localhost:8081/login | grep -iE "x-frame-options|content-security-policy"
```

Erwartet: `X-Frame-Options: SAMEORIGIN` und `Content-Security-Policy: frame-ancestors 'self'`

Danach das Snippet aus Step 1 erneut ausführen: kein Konsolenfehler mehr, das iframe lädt.

- [ ] **Step 5: Commit**

```bash
git add KnovasPlatform/components/docbridge_integration/nginx/docbridge-web-local.conf KnovasPlatform/deploy/host-nginx/
git commit -m "fix: allow same-origin framing so document previews can render"
```

---

## Task 5: Markdown-Renderer im Client

**Files:**
- Create: `src/web_interface/static/js/markdown.js`
- Modify: `src/web_interface/templates/index.html` (Script vor `app.js` einbinden)

**Interfaces:**
- Consumes: nichts
- Produces: globales `window.KnovasMarkdown.render(markdown: string) -> string` (HTML-String)

- [ ] **Step 1: Renderer schreiben**

`src/web_interface/static/js/markdown.js`:

```javascript
// Minimaler Markdown-Renderer fuer die Dokumentvorschau.
//
// SICHERHEIT: Der Eingabetext stammt aus fremden Dokumenten. knovas_extract
// entfernt serverseitig Markup, escaped aber keinen Textinhalt -- ein DOCX mit
// dem woertlichen Text "<script>" liefert genau diese Zeichen zurueck.
// Deshalb wird hier IMMER zuerst escaped und erst danach formatiert.
// Diese Reihenfolge umzudrehen oeffnet XSS.
(function () {
    'use strict';

    var ALLOWED_LINK_SCHEMES = /^(https?:|mailto:)/i;

    function escapeHtml(text) {
        return String(text)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    /** Inline-Auszeichnung auf bereits escaptem Text. */
    function renderInline(escaped) {
        return escaped
            .replace(/`([^`]+)`/g, '<code>$1</code>')
            .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
            .replace(/(^|[^*])\*([^*]+)\*/g, '$1<em>$2</em>')
            // Die URL-Gruppe erlaubt eine Ebene balancierter Klammern. Ohne das
            // bricht [x](https://de.wikipedia.org/wiki/Foo_(bar)) das href an der
            // ersten Klammer ab und verlinkt still auf eine andere Adresse.
            .replace(/\[([^\]]+)\]\(((?:[^()\s]|\([^()\s]*\))*)\)/g, function (match, label, href) {
                // Zweite Verteidigungslinie: der Server filtert Schemata bereits.
                if (!ALLOWED_LINK_SCHEMES.test(href)) {
                    return label;
                }
                return '<a href="' + href + '" target="_blank" rel="noopener noreferrer">' + label + '</a>';
            });
    }

    function render(markdown) {
        var escaped = escapeHtml(markdown == null ? '' : markdown);
        var lines = escaped.split(/\r?\n/);
        var html = [];
        var listOpen = false;

        function closeList() {
            if (listOpen) {
                html.push('</ul>');
                listOpen = false;
            }
        }

        for (var i = 0; i < lines.length; i++) {
            var line = lines[i];
            var heading = /^(#{1,6})\s+(.*)$/.exec(line);
            var bullet = /^\s*[-*]\s+(.*)$/.exec(line);

            if (heading) {
                closeList();
                var level = Math.min(heading[1].length + 2, 6);
                html.push('<h' + level + '>' + renderInline(heading[2]) + '</h' + level + '>');
            } else if (bullet) {
                if (!listOpen) {
                    html.push('<ul>');
                    listOpen = true;
                }
                html.push('<li>' + renderInline(bullet[1]) + '</li>');
            } else if (line.trim() === '') {
                closeList();
            } else {
                closeList();
                html.push('<p>' + renderInline(line) + '</p>');
            }
        }
        closeList();
        return html.join('');
    }

    window.KnovasMarkdown = { render: render, escapeHtml: escapeHtml };
})();
```

Anmerkung zur Überschriften-Ebene: `#` wird auf `<h3>` abgebildet, weil `<h1>` und `<h2>` bereits der Seite gehören. Ein Dokument darf die Dokumentgliederung der Anwendung nicht überschreiben.

- [ ] **Step 2: Script einbinden**

In `src/web_interface/templates/index.html` vor der bestehenden `app.js`-Zeile:

```html
    <script src="{{ url_for('static', filename='js/markdown.js') }}?v={{ asset_version }}"></script>
```

- [ ] **Step 3: Sicherheitsverhalten im Browser nachweisen**

Stack starten, einloggen, in der Konsole:

```javascript
KnovasMarkdown.render('<script>alert(1)</script>')
// erwartet: "<p>&lt;script&gt;alert(1)&lt;/script&gt;</p>"

KnovasMarkdown.render('[klick](javascript:alert(1))')
// erwartet: "<p>klick</p>" -- kein <a>, kein href

KnovasMarkdown.render('# Titel\n\n- eins\n- zwei\n\n**fett** und `code`')
// erwartet: "<h3>Titel</h3><ul><li>eins</li><li>zwei</li></ul><p><strong>fett</strong> und <code>code</code></p>"
```

Jede Ausgabe einzeln prüfen. Erscheint irgendwo ein echtes `<script>`-Element oder ein `href="javascript:`, ist die Escape-Reihenfolge verletzt.

Diese Prüfung ist bewusst manuell: das Repo hat kein JS-Test-Runner und soll laut Global Constraints keinen npm-Baum bekommen. Die serverseitige Hälfte der Annahme — dass die Extraktion Dokumenttext *nicht* escaped — ist dafür automatisiert in `test_extraction_does_not_escape_document_text` (Task 2) festgehalten. Wer diesen Schritt überspringt, verlässt sich darauf, dass jemand anders die einzige XSS-Barriere geprüft hat.

- [ ] **Step 4: Commit**

```bash
git add src/web_interface/static/js/markdown.js src/web_interface/templates/index.html
git commit -m "feat: add escape-first markdown renderer for document previews"
```

---

## Task 6: Seitenpanel-Markup und Styles

**Files:**
- Modify: `src/web_interface/templates/index.html` (`<main>`, aktuell Zeilen 26-69)
- Modify: `src/web_interface/static/css/style.css`

**Interfaces:**
- Produces: DOM-Knoten `#previewPanel`, `#previewTitle`, `#previewMeta`, `#previewBody`, `#previewClose`, `#previewActions`; Body-Klasse `preview-open`

- [ ] **Step 1: Panel-Markup ergänzen**

In `index.html` die `<main>`-Sektion so umbauen, dass Ergebnisse und Panel nebeneinander liegen. `<main>` bekommt die Klasse `main-layout`; direkt nach `</section>` der Ergebnisse einfügen:

```html
            <aside class="preview-panel" id="previewPanel" hidden aria-label="Dokumentvorschau">
                <header class="preview-panel-header">
                    <div class="preview-panel-heading">
                        <h2 class="preview-title" id="previewTitle"></h2>
                        <p class="preview-meta" id="previewMeta"></p>
                    </div>
                    <button type="button" class="btn-text preview-close" id="previewClose" aria-label="Vorschau schliessen">Schliessen</button>
                </header>
                <div class="preview-actions" id="previewActions"></div>
                <div class="preview-body" id="previewBody" tabindex="0"></div>
            </aside>
```

- [ ] **Step 2: Styles ergänzen**

Ans Ende von `style.css`:

```css
/* --- Dokumentvorschau ---------------------------------------------------- */

.main-layout {
    display: flex;
    flex-wrap: wrap;
    gap: 20px;
    align-items: flex-start;
}

/* Die Suche gehoert ueber beide Spalten, nicht daneben: ohne die eigene
   Zeile wird sie zwischen Ergebnisliste und Panel zerquetscht. */
.main-layout > .search-section {
    flex: 1 1 100%;
}

.main-layout > .results-section {
    flex: 1 1 auto;
    min-width: 0;
}

.preview-panel {
    flex: 0 0 clamp(320px, 40%, 560px);
    position: sticky;
    top: 20px;
    max-height: calc(100vh - 40px);
    display: flex;
    flex-direction: column;
    background: var(--card-bg);
    border: 1px solid var(--border-color);
    border-radius: var(--radius-lg);
    box-shadow: var(--shadow-lg);
    overflow: hidden;
}

.preview-panel[hidden] {
    display: none;
}

.preview-panel-header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 12px;
    padding: 16px 18px 10px;
    border-bottom: 1px solid var(--border-color);
}

.preview-title {
    font-size: 1rem;
    line-height: 1.35;
    overflow-wrap: anywhere;
}

.preview-meta {
    margin-top: 4px;
    font-size: 0.8rem;
    color: var(--text-secondary);
}

.preview-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    padding: 10px 18px;
}

.preview-body {
    flex: 1 1 auto;
    overflow-y: auto;
    padding: 14px 18px 20px;
    font-size: 0.9rem;
}

.preview-body h3,
.preview-body h4,
.preview-body h5,
.preview-body h6 {
    margin: 14px 0 6px;
    font-size: 0.95rem;
}

.preview-body p {
    margin: 0 0 10px;
    overflow-wrap: anywhere;
}

.preview-body ul {
    margin: 0 0 10px 18px;
}

.preview-body code {
    font-family: 'IBM Plex Mono', ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 0.85em;
    background: var(--bg-color);
    padding: 1px 4px;
    border-radius: 4px;
}

.preview-body iframe {
    width: 100%;
    height: 70vh;
    border: 0;
}

.preview-skeleton span {
    display: block;
    height: 12px;
    margin-bottom: 9px;
    border-radius: 4px;
    background: linear-gradient(90deg, var(--bg-color), var(--border-color), var(--bg-color));
    background-size: 200% 100%;
    animation: preview-shimmer 1.2s ease-in-out infinite;
}

.preview-skeleton span:nth-child(2) { width: 92%; }
.preview-skeleton span:nth-child(3) { width: 78%; }
.preview-skeleton span:nth-child(4) { width: 86%; }

@keyframes preview-shimmer {
    from { background-position: 200% 0; }
    to { background-position: -200% 0; }
}

@media (prefers-reduced-motion: reduce) {
    .preview-skeleton span { animation: none; }
}

@media (max-width: 900px) {
    .main-layout {
        display: block;
    }

    .preview-panel {
        position: fixed;
        inset: 0;
        max-height: none;
        border-radius: 0;
        z-index: 40;
    }
}
```

- [ ] **Step 3: Container verbreitern**

`.container` hat `max-width: 920px` (Zeile 36). Für die zweispaltige Ansicht auf `1360px` erhöhen. Ohne das steht das Panel bei jeder realistischen Fensterbreite zu eng.

- [ ] **Step 4: Darstellung prüfen**

Stack neu bauen und starten, einloggen, in der Konsole:

```javascript
document.getElementById('previewPanel').hidden = false;
document.getElementById('previewTitle').textContent = 'Mustervertrag';
document.getElementById('previewBody').innerHTML = '<p>Platzhalter</p>';
```

Erwartet: Panel erscheint rechts neben der Ergebnisliste, Liste bleibt lesbar. Fenster unter 900 px verkleinern: Panel wird zum Vollbild-Overlay.

- [ ] **Step 5: Commit**

```bash
git add src/web_interface/templates/index.html src/web_interface/static/css/style.css
git commit -m "feat: add preview side panel markup and styles"
```

---

## Task 7: Panel-Logik in `app.js`

**Files:**
- Modify: `src/web_interface/static/js/app.js`

**Interfaces:**
- Consumes: `window.KnovasMarkdown.render` aus Task 5; die DOM-Knoten aus Task 6; den Endpunkt aus Task 3
- Produces: Methoden `openPreview(index)`, `closePreview()` auf `DocumentSearchApp`

- [ ] **Step 1: Panel-Referenzen und Zustand im Konstruktor ergänzen**

Nach `this.resultsPerPage = ...` einfügen:

```javascript
        this.previewPanel = document.getElementById('previewPanel');
        this.previewTitle = document.getElementById('previewTitle');
        this.previewMeta = document.getElementById('previewMeta');
        this.previewBody = document.getElementById('previewBody');
        this.previewActions = document.getElementById('previewActions');
        this.previewClose = document.getElementById('previewClose');
        /** @type {AbortController|null} laufende Vorschau-Anfrage */
        this._previewAbort = null;
        /** @type {number|null} Index des aktuell gezeigten Treffers */
        this._previewIndex = null;
```

- [ ] **Step 2: Listener registrieren**

In `initializeEventListeners()` ergänzen:

```javascript
        this.previewClose.addEventListener('click', () => this.closePreview());
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') this.closePreview();
        });
```

- [ ] **Step 3: Klick auf die Karte öffnet das Panel**

In `_onResultsClick(e)` als **erste** Prüfung einfügen, damit Buttons und Links weiterhin Vorrang haben:

```javascript
        // Buttons und Links behalten ihr eigenes Verhalten.
        if (!e.target.closest('a, button')) {
            const openCard = e.target.closest('.document-card');
            if (openCard) {
                const idx = parseInt(openCard.getAttribute('data-index') || '-1', 10);
                if (idx >= 0) {
                    this.openPreview(idx);
                    return;
                }
            }
        }
```

- [ ] **Step 4: Panel-Methoden ergänzen**

Als neue Methoden auf `DocumentSearchApp`:

```javascript
    /** Menschenlesbare Kopfzeile aus den Metadaten der Extraktion. */
    _previewMetaText(kind, meta) {
        const parts = [kind.toUpperCase()];
        if (meta) {
            if (meta['msg:from']) parts.push(`Von ${meta['msg:from']}`);
            if (meta['msg:to']) parts.push(`An ${meta['msg:to']}`);
            if (meta.page_count) parts.push(`${meta.page_count} Seiten`);
            if (meta.word_count) parts.push(`${meta.word_count} Wörter`);
        }
        return parts.join(' · ');
    }

    _previewActionsHtml(doc) {
        const docId = doc.doc_id || '';
        const path = doc.path || '';
        const extRaw = doc.external_url ? String(doc.external_url).trim() : '';
        const externalUrl = /^https?:\/\//i.test(extRaw) ? extRaw : '';
        if (externalUrl) {
            const href = this.externalOpenHref(docId, path || docId);
            return `<a class="btn btn-success" href="${this.escapeAttr(href)}" target="_blank" rel="noopener noreferrer">🔗 In OneDrive öffnen</a>`;
        }
        return `<button type="button" class="btn btn-success" onclick="app.openDocument('${this.escapeJsString(docId)}', '${this.escapeJsString(path)}')">📂 Öffnen</button>`;
    }

    closePreview() {
        if (this._previewAbort) {
            this._previewAbort.abort();
            this._previewAbort = null;
        }
        this._previewIndex = null;
        this.previewPanel.hidden = true;
        document.body.classList.remove('preview-open');
        this.previewBody.innerHTML = '';
        this.previewActions.innerHTML = '';
    }

    async openPreview(index) {
        const doc = this.currentResults[index];
        if (!doc) return;

        // Laufende Anfrage abbrechen, damit ein schneller Kartenwechsel nicht
        // die Antwort des vorherigen Dokuments einblendet.
        if (this._previewAbort) this._previewAbort.abort();
        const controller = new AbortController();
        this._previewAbort = controller;
        this._previewIndex = index;

        const docId = String(doc.doc_id || doc.pointer || '');
        const path = String(doc.path || '');
        const title = this.displayTitle(doc);

        this.previewPanel.hidden = false;
        document.body.classList.add('preview-open');
        this.previewTitle.textContent = title;
        this.previewMeta.textContent = '';
        this.previewActions.innerHTML = this._previewActionsHtml(doc);
        this.previewBody.innerHTML =
            '<div class="preview-skeleton"><span></span><span></span><span></span><span></span></div>';

        if (path.toLowerCase().endsWith('.pdf')) {
            const src = `/api/document/${encodeURIComponent(docId)}/preview?path=${encodeURIComponent(path)}`;
            this.previewMeta.textContent = 'PDF';
            this.previewBody.innerHTML =
                `<iframe src="${this.escapeAttr(src)}" title="PDF-Vorschau"></iframe>`;
            this._previewAbort = null;
            return;
        }

        try {
            const url = `/api/document/${encodeURIComponent(docId)}/preview-content?path=${encodeURIComponent(path)}`;
            const response = await fetch(url, {
                credentials: 'same-origin',
                signal: controller.signal,
            });
            if (this._redirectIfLoginRequired(response)) return;
            const data = await response.json().catch(() => ({}));
            if (!response.ok || !data.success) {
                throw new Error(data.error || `HTTP ${response.status}`);
            }
            // Zwischenzeitlicher Kartenwechsel: Antwort verwerfen.
            if (this._previewIndex !== index) return;

            this.previewMeta.textContent = this._previewMetaText(data.kind, data.meta);
            this.previewBody.innerHTML = window.KnovasMarkdown.render(data.markdown);
        } catch (error) {
            if (error.name === 'AbortError') return;
            console.warn('Preview:', error);
            this.previewBody.innerHTML =
                `<p class="preview-error">Vorschau nicht verfügbar (${this.escapeHtml(error.message)}). Nutzen Sie „Öffnen“.</p>`;
        } finally {
            if (this._previewAbort === controller) this._previewAbort = null;
        }
    }
```

- [ ] **Step 5: Hintergrund-Scroll im Vollbild-Overlay sperren**

`openPreview` und `closePreview` schalten oben bereits `body.preview-open`. Die zugehoerige Regel ans Ende von `style.css`, in denselben `@media (max-width: 900px)`-Block wie das Overlay:

```css
@media (max-width: 900px) {
    body.preview-open {
        overflow: hidden;
    }
}
```

Nur unterhalb des Breakpoints: auf dem Desktop steht das Panel neben der Liste, dort muss die Seite scrollbar bleiben. Im Vollbild-Overlay wuerde ein scrollender Hintergrund dagegen unter der Vorschau wegrutschen.

- [ ] **Step 6: Panel bei neuer Suche schliessen**

In `displayResults(results, total, semantix)` als erste Zeile:

```javascript
        this.closePreview();
```

Ohne das zeigt das Panel nach einer neuen Suche ein Dokument, das in der neuen Trefferliste nicht mehr vorkommt.

- [ ] **Step 7: Alle vier Formate im Browser prüfen**

Testdateien unter das AutoDoc-Mount legen, Stack neu bauen, einloggen und je eine Karte anklicken:

| Datei | Erwartung im Panel |
| --- | --- |
| `.txt` | Text als Absätze |
| `.docx` | Überschrift als `<h3>`, Absätze darunter |
| `.msg` | Kopfzeile mit `Von …` und `An …`, Body als Absätze |
| `.pdf` | eingebetteter Browser-Viewer, blätterbar |

Zusätzlich: zwei Karten schnell hintereinander anklicken. Es muss das zuletzt geklickte Dokument stehenbleiben, nie das vorherige.

- [ ] **Step 8: Commit**

```bash
git add src/web_interface/static/js/app.js src/web_interface/static/css/style.css
git commit -m "feat: render document previews in the side panel"
```

---

## Task 8: Hover-Preview entfernen

**Files:**
- Modify: `src/web_interface/static/js/app.js`
- Modify: `src/web_interface/static/css/style.css`
- Modify: `src/web_interface/app.py` (`hover_preview_enabled`)
- Modify: `config/config.yaml:210`
- Modify: `src/web_interface/templates/index.html`

- [ ] **Step 1: JS-Code entfernen**

Aus `app.js` streichen: die Felder `_hoverPreviewEl`, `_hoverPreviewTimer`, `_hoverPreviewCard`, `_hoverPdfCache` im Konstruktor; die Methoden `_ensureHoverPreview`, `_docForCard`, `_onResultHoverEnter`, `_onResultHoverLeave`, `_showHoverPreview`, `_hideHoverPreview`; sowie in `initializeEventListeners` die Listener für `mouseenter`, `mouseleave`, `focusin`, `focusout` und den `scroll`-Listener auf `window`.

Der `keydown`-Listener für `Escape` bleibt — er gehört jetzt dem Panel (Task 7 Step 2). Es darf am Ende **genau einer** existieren.

- [ ] **Step 2: CSS entfernen**

Alle Regeln zu `.document-hover-preview*` aus `style.css` streichen.

- [ ] **Step 3: Server-Flag entfernen**

In `app.py` die Zeile `hover_preview_enabled = config.get_bool('web.search.hover_preview', True)` und die Übergabe `hover_preview_enabled=hover_preview_enabled` an das Template streichen. In `index.html` das Feld `hoverPreviewEnabled` aus `window.__DOCBRIDGE__` entfernen. In `config/config.yaml` den Schlüssel `hover_preview: true` streichen.

- [ ] **Step 4: Nachweisen, dass nichts zurückbleibt**

```bash
grep -rn "hover_preview\|hoverPreview\|document-hover-preview" src/ config/ templates/ 2>/dev/null
```

Erwartet: keine Treffer.

- [ ] **Step 5: Suite laufen lassen**

```bash
pytest
```

Erwartet: PASS

- [ ] **Step 6: Commit**

```bash
git add src/ config/config.yaml
git commit -m "refactor: drop hover preview in favour of the side panel"
```

---

# Phase 2 — Feedback entfernen

## Task 9: Feedback-UI und Endpunkte entfernen

**Files:**
- Modify: `src/web_interface/static/js/app.js`
- Modify: `src/web_interface/app.py`
- Modify: `src/knovas_client.py`
- Modify: `tests/test_csrf_enforcement.py`
- Delete: `tests/test_engagement.py`

- [ ] **Step 1: JS-Code entfernen**

Aus `app.js` streichen: `_pointerForDoc`, `_queueEngagement`, `_flushEngagementSoon`, `_flushEngagement`, `_setScoreSelection`, `_postRelevanceFeedback`, `_readSelectedScore`, `_savePermanentDocumentRating`, `_loadPermanentDocumentRating`, `_scorePickerHtml`, `_buildRatingsSection`, `_reportEngagementForDocId`; die Felder `querySessionId`, `_engagementQueue`, `_engagementFlushTimer`; in `_onResultsClick` die Zweige für `previewLink`, `externalLink`, `dismissBtn`, `titleEl`, `scoreBtn`, `saveBtn`, `loadBtn`; in `createDocumentCard` den Block `document-feedback-row` und den Aufruf `${pointer ? this._buildRatingsSection(pointer) : ''}`; die Zuweisung von `this.querySessionId` in `performSearch`; die Aufrufe von `_reportEngagementForDocId` in `openDocument` und `downloadDocument`.

`_jsonHeadersWithCsrf` bleibt — `/api/search` und `/api/document/<id>/open` brauchen den Header weiterhin.

- [ ] **Step 2: Routen entfernen**

Aus `app.py` die drei Routen samt Rumpf streichen: `/api/analytics/relevance-feedback`, `/api/analytics/engagement`, `/api/document/rating`.

- [ ] **Step 3: Client-Methoden entfernen**

Aus `src/knovas_client.py` `post_relevance_feedback`, `post_engagement_events` und `post_document_rating` streichen, samt zugehöriger Endpunkt-Konstanten, sofern sie nirgends sonst verwendet werden. Prüfen mit:

```bash
grep -rn "post_relevance_feedback\|post_engagement_events\|post_document_rating" src/ tests/
```

- [ ] **Step 4: CSRF-Test kürzen**

In `tests/test_csrf_enforcement.py` die Testfälle für die drei entfallenen Endpunkte streichen, ebenso die Dummy-Methoden `post_relevance_feedback`, `post_engagement_events`, `post_document_rating` im Test-Client und die entsprechenden Zeilen im Modul-Docstring. `POST /api/search` und `POST /api/document/<id>/open` bleiben und halten die Aussagekraft des Tests aufrecht.

- [ ] **Step 5: Engagement-Test löschen**

```bash
git rm tests/test_engagement.py
```

- [ ] **Step 6: Nachweisen, dass nichts zurückbleibt**

```bash
grep -rn "relevance-feedback\|analytics/engagement\|document/rating\|querySessionId\|_buildRatingsSection" src/ tests/
```

Erwartet: keine Treffer.

- [ ] **Step 7: Suite laufen lassen**

```bash
pytest
```

Erwartet: PASS

- [ ] **Step 8: Im Browser prüfen**

Stack neu bauen, suchen. Erwartet: Karten enden nach der Zusammenfassung. Keine Bewertungsleisten, kein „Nicht relevant". Die Konsole bleibt fehlerfrei, und im Netzwerk-Tab erscheinen keine Aufrufe an `/api/analytics/*`.

- [ ] **Step 9: Commit**

```bash
git add -A src/ tests/
git commit -m "refactor: remove relevance feedback, ratings and engagement telemetry"
```

---

# Phase 3 — Branding

## Task 10: Draft-Themes entfernen

**Files:**
- Delete: `src/web_interface/static/css/drafts/` (atelier, helvetia, horizon, ledger)
- Modify: `src/web_interface/app.py`, `src/web_interface/templates/index.html`
- Modify: `tests/test_ui_theme.py`

- [ ] **Step 1: Umfang der Theme-Logik feststellen**

```bash
grep -rn "draft_theme\|WEB_UI_THEME\|ui_theme\|theme-draft" src/ tests/ ../../docker-compose.yml ../../.env.example
```

Alle Fundstellen bearbeiten. `test_ui_theme.py` testet ausschliesslich diese Logik und entfällt mit ihr; falls die Datei darüber hinaus etwas prüft, nur die Theme-Fälle streichen.

- [ ] **Step 2: Löschen und entfernen**

```bash
git rm -r src/web_interface/static/css/drafts/
```

Aus `index.html` den `{% if draft_theme %}`-Block im `<head>` und die Body-Klasse `theme-draft-{{ draft_theme }}` entfernen. Aus `app.py` die Theme-Auflösung samt `_normalize_ui_theme_slug` und `_theme_from_query_only` entfernen, sofern nirgends sonst verwendet. Aus `.env.example` und `docker-compose.yml` die `WEB_UI_THEME`-Zeilen streichen.

- [ ] **Step 3: Suite laufen lassen**

```bash
pytest
```

Erwartet: PASS

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "chore: remove draft themes ahead of the Knovas rebrand"
```

---

## Task 11: Palette und Typografie

**Files:**
- Modify: `src/web_interface/static/css/style.css:3-18` und die `body`-Regel
- Create: `src/web_interface/static/fonts/` mit den IBM-Plex-woff2

**Quelle:** `Knovas Branding.pdf`, Seiten 15, 16 und 18.

- [ ] **Step 1: Schriften beschaffen und ablegen**

Quelle: das offizielle IBM-Plex-Release, <https://github.com/IBM/plex/releases> — dort das
Web-Paket herunterladen und daraus die woff2 der benötigten Schnitte entnehmen. Alternativ
liefert `https://fonts.google.com/specimen/IBM+Plex+Sans` dieselben Dateien.

Benötigt werden genau fünf Dateien in `src/web_interface/static/fonts/`:

```
IBMPlexSans-Regular.woff2
IBMPlexSans-Medium.woff2
IBMPlexSans-SemiBold.woff2
IBMPlexMono-Regular.woff2
IBMPlexMono-SemiBold.woff2
```

Die Dateinamen müssen exakt so lauten — die `@font-face`-Regeln in Step 2 verweisen darauf.

Beide Familien stehen unter SIL OFL und werden **selbst gehostet**, keine CDN-URL. Die
Anwendung läuft kundengehostet, teils offline, mit strikten Security-Headern; eine externe
Font-URL bräche dieses Modell. Die Lizenzdatei (`OFL.txt`) gehört mit ins Verzeichnis.

Prüfen:

```bash
ls -la src/web_interface/static/fonts/
```

Erwartet: die fünf woff2 plus `OFL.txt`. Fehlt eine Datei, fällt der betroffene Schnitt
stillschweigend auf die System-Schrift zurück — das fällt visuell kaum auf und ist deshalb
hier explizit zu prüfen.

- [ ] **Step 2: `@font-face` und Variablen setzen**

Am Anfang von `style.css`, vor `:root`:

```css
/* IBM Plex, selbst gehostet (SIL OFL). Kein CDN: die Anwendung laeuft
   kundengehostet und teils offline. */
@font-face {
    font-family: 'IBM Plex Sans';
    src: url('../fonts/IBMPlexSans-Regular.woff2') format('woff2');
    font-weight: 400;
    font-display: swap;
}
@font-face {
    font-family: 'IBM Plex Sans';
    src: url('../fonts/IBMPlexSans-Medium.woff2') format('woff2');
    font-weight: 500;
    font-display: swap;
}
@font-face {
    font-family: 'IBM Plex Sans';
    src: url('../fonts/IBMPlexSans-SemiBold.woff2') format('woff2');
    font-weight: 600;
    font-display: swap;
}
@font-face {
    font-family: 'IBM Plex Mono';
    src: url('../fonts/IBMPlexMono-Regular.woff2') format('woff2');
    font-weight: 400;
    font-display: swap;
}
@font-face {
    font-family: 'IBM Plex Mono';
    src: url('../fonts/IBMPlexMono-SemiBold.woff2') format('woff2');
    font-weight: 600;
    font-display: swap;
}
```

Den `:root`-Block ersetzen durch:

```css
:root {
    /* Knovas Branding, Seiten 15-16 */
    --primary-color: #3B79F2;     /* Azure Blue -- "Knovas Blue" */
    --primary-hover: #1A45C7;     /* Royal Blue */
    --title-color: #07172D;       /* Midnight Blue */
    --accent-soft: #6E88DC;       /* Cornflower Blue */
    --success-color: #1a6b4a;
    --error-color: #c41e1e;
    --bg-color: #F4F6FC;          /* Ice Blue */
    --card-bg: #FDFDFD;           /* Off White */
    --text-primary: #283647;      /* Slate Blue */
    --text-secondary: #73869B;    /* Slate Gray */
    --text-muted: #A3B0C0;
    --border-color: #D1D6DF;      /* Light Ice Blue */
    --shadow: 0 1px 2px rgba(7, 23, 45, 0.06);
    --shadow-lg: 0 8px 24px rgba(7, 23, 45, 0.10);
    --radius: 10px;
    --radius-lg: 14px;
    --font-body: 'IBM Plex Sans', system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
    --font-heading: 'IBM Plex Mono', ui-monospace, SFMono-Regular, Menlo, monospace;
}
```

In der `body`-Regel `font-family` auf `var(--font-body)` setzen. Für `h1`, `h2`, `.site-header h1`, `.results-header h2` und `.preview-title` `font-family: var(--font-heading);` ergänzen — laut Guide ist IBM Plex Mono die Header-Schrift.

- [ ] **Step 3: Kontrast prüfen**

Für jede Kombination das Verhältnis bestimmen (Browser-DevTools oder ein Kontrastrechner):

| Vordergrund | Hintergrund | Mindestens |
| --- | --- | --- |
| `#283647` | `#FDFDFD` | 4.5:1 |
| `#73869B` | `#FDFDFD` | 4.5:1 |
| `#FFFFFF` | `#3B79F2` | 4.5:1 |
| `#07172D` | `#F4F6FC` | 4.5:1 |

Verfehlt eine Kombination den Wert, den **Text** abdunkeln, nicht die Markenfarbe verändern. `--text-muted` ist bewusst kein Guide-Wert, sondern eine abgeleitete, hellere Stufe für Nebeninformationen; wenn sie 4.5:1 verfehlt, nur für nicht-essenzielle Angaben verwenden.

- [ ] **Step 4: Sichtprüfung**

Stack neu bauen, Login- und Suchseite ansehen. Erwartet: blaue Grundstimmung nach Guide, Überschriften in Mono, Fliesstext in Sans, keine Reste des alten `#1e40af`.

```bash
grep -rn "1e40af\|1e3a8a\|f4f6f9\|Segoe UI" src/web_interface/static/css/style.css
```

Erwartet: keine Treffer.

- [ ] **Step 5: Commit**

```bash
git add src/web_interface/static/css/style.css src/web_interface/static/fonts/
git commit -m "feat: apply Knovas brand palette and self-hosted IBM Plex"
```

---

## Task 12: Logo und Favicon

**Files:**
- Create: `src/web_interface/static/img/knovas-logo.svg`, `knovas-mark.svg`
- Create: `src/web_interface/static/favicon.ico` oder `favicon.svg`
- Modify: `src/web_interface/templates/index.html`, `login.html`
- Modify: `src/web_interface/app.py` (Favicon-Route)

**Voraussetzung:** die Original-Logodateien. Ohne sie sind Wordmark und Monogramm als PNG mit mindestens 200 dpi aus `Knovas Branding.pdf` (Seite 12) zu extrahieren; beide liegen dort als Vektoren vor. Ein SVG-Export via PyMuPDF ist **nicht** brauchbar — er erzeugt 3,6 MB, bettet den Folienhintergrund als Rasterbild ein und verliert die Gradienten.

- [ ] **Step 1: Assets ablegen**

Wordmark und Monogramm nach `src/web_interface/static/img/` legen. Das Monogramm zusätzlich als Favicon.

- [ ] **Step 2: Kopfzeilen umbauen**

In `index.html` die Zeile `<h1>{{ app_title }}</h1>` ersetzen durch:

```html
            <h1 class="site-title">
                <img class="site-logo" src="{{ url_for('static', filename='img/knovas-logo.svg') }}" alt="Knovas" height="28">
                <span class="visually-hidden">{{ app_title }}</span>
            </h1>
```

In `login.html` dasselbe Logo über der Überschrift einsetzen.

Dazu in `style.css`:

```css
.site-logo {
    height: 28px;
    width: auto;
}

.visually-hidden {
    position: absolute;
    width: 1px;
    height: 1px;
    margin: -1px;
    padding: 0;
    overflow: hidden;
    clip: rect(0 0 0 0);
    white-space: nowrap;
    border: 0;
}
```

`alt="Knovas"` plus verstecktem Titeltext, damit Screenreader und Browser-Tab den vollständigen Anwendungsnamen behalten.

- [ ] **Step 3: Favicon einbinden**

In `index.html` und `login.html` in den `<head>`:

```html
    <link rel="icon" href="{{ url_for('static', filename='favicon.svg') }}" type="image/svg+xml">
```

- [ ] **Step 4: 404 als behoben nachweisen**

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8081/static/favicon.svg
```

Erwartet: `200`. Anschliessend die Seite im Browser laden — die Konsole muss frei von `favicon.ico`-404 sein.

- [ ] **Step 5: Commit**

```bash
git add src/web_interface/static/img/ src/web_interface/static/favicon.svg src/web_interface/templates/
git commit -m "feat: use Knovas logo and favicon"
```

---

## Task 13: Alle Meldungen als Popup

**Files:**
- Modify: `src/web_interface/static/js/app.js`
- Modify: `src/web_interface/templates/index.html`
- Modify: `src/web_interface/static/css/style.css`

**Interfaces:**
- Produces: `showToast(message: string, kind?: 'info'|'success'|'error') -> void` auf `DocumentSearchApp`; DOM-Knoten `#toastContainer`

Heute erscheinen Meldungen an drei verschiedenen Orten: Fehler in einem `div` innerhalb der Ergebnissektion (auto-hide nach 5 s), Erfolge als eingefügtes `div` am Seitenanfang, und der Systemstatus als blockierendes `alert()`. Der Fehler-`div` steckt in `#resultsSection`, die vor der ersten Suche `display: none` trägt — eine Fehlermeldung vor der ersten Suche ist damit unsichtbar. Alles wird auf ein einziges Toast-Popup vereinheitlicht.

- [ ] **Step 1: Container ins Template**

In `index.html` direkt vor `</body>`:

```html
    <div class="toast-container" id="toastContainer" role="status" aria-live="polite" aria-atomic="false"></div>
```

Im selben Zug den bisherigen Fehlerplatzhalter entfernen:

```html
                <div id="errorMessage" class="error-message" style="display: none;"></div>
```

`aria-live="polite"` statt `assertive`: Meldungen sollen vorgelesen werden, ohne den Nutzer beim Tippen zu unterbrechen.

- [ ] **Step 2: Styles ergänzen**

Ans Ende von `style.css`:

```css
/* --- Toasts -------------------------------------------------------------- */

.toast-container {
    position: fixed;
    right: 16px;
    bottom: 16px;
    z-index: 60;
    display: flex;
    flex-direction: column;
    gap: 10px;
    max-width: min(420px, calc(100vw - 32px));
}

.toast {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    padding: 12px 14px;
    border-radius: var(--radius);
    border: 1px solid var(--border-color);
    border-left-width: 4px;
    background: var(--card-bg);
    box-shadow: var(--shadow-lg);
    font-size: 0.88rem;
    animation: toast-in 140ms ease-out;
}

.toast--error { border-left-color: var(--error-color); }
.toast--success { border-left-color: var(--success-color); }
.toast--info { border-left-color: var(--primary-color); }

.toast-text {
    flex: 1 1 auto;
    overflow-wrap: anywhere;
    white-space: pre-line;
}

.toast-close {
    flex: 0 0 auto;
    background: none;
    border: 0;
    cursor: pointer;
    font-size: 1rem;
    line-height: 1;
    padding: 2px 4px;
    color: var(--text-secondary);
}

.toast-close:hover { color: var(--text-primary); }

@keyframes toast-in {
    from { opacity: 0; transform: translateY(8px); }
    to { opacity: 1; transform: none; }
}

@media (prefers-reduced-motion: reduce) {
    .toast { animation: none; }
}
```

`white-space: pre-line` erhält die Zeilenumbrüche der Systemstatus-Meldung, die bisher im `alert()` standen.

- [ ] **Step 3: `showToast` implementieren**

In `app.js` den Konstruktor um die Container-Referenz ergänzen:

```javascript
        this.toastContainer = document.getElementById('toastContainer');
```

`showError`, `hideError` und `showSuccess` durch diese Methoden ersetzen:

```javascript
    /**
     * Einziger Weg, dem Nutzer etwas mitzuteilen. Fehler bleiben stehen, bis
     * sie weggeklickt werden -- eine Fehlermeldung, die sich selbst schliesst,
     * bevor sie gelesen wurde, ist keine Meldung.
     * @param {'info'|'success'|'error'} kind
     */
    showToast(message, kind = 'info') {
        const toast = document.createElement('div');
        toast.className = `toast toast--${kind}`;

        const text = document.createElement('div');
        text.className = 'toast-text';
        text.textContent = message;

        const close = document.createElement('button');
        close.type = 'button';
        close.className = 'toast-close';
        close.setAttribute('aria-label', 'Meldung schliessen');
        close.textContent = '×';
        close.addEventListener('click', () => toast.remove());

        toast.appendChild(text);
        toast.appendChild(close);
        this.toastContainer.appendChild(toast);

        if (kind !== 'error') {
            window.setTimeout(() => toast.remove(), 6000);
        }
    }

    showError(message) {
        this.showToast(message, 'error');
    }

    showSuccess(message) {
        this.showToast(message, 'success');
    }
```

`textContent` statt `innerHTML`: Meldungen tragen Serverfehler und Dateipfade, also fremden Text.

- [ ] **Step 4: Verbleibende Aufrufer umstellen**

`hideError()` existiert nicht mehr. Alle Aufrufe entfernen — sie stehen in `performSearch` und ggf. in `showLoading`. Prüfen mit:

```bash
grep -n "hideError\|errorMessage\|alert(" src/web_interface/static/js/app.js
```

`checkHealth` auf ein Toast umstellen:

```javascript
    async checkHealth() {
        try {
            const response = await fetch('/api/health', { credentials: 'same-origin' });
            const data = await response.json();
            const status = data.semantix_api ? '✅ Online' : '❌ Offline';
            this.showToast(
                `Systemstatus\nWeb-Oberfläche: ✅ Online\nKnovas API: ${status}\nZeitstempel: ${data.timestamp}`,
                data.semantix_api ? 'success' : 'error',
            );
        } catch (error) {
            this.showToast(`Systemstatus konnte nicht geladen werden: ${error.message}`, 'error');
        }
    }
```

Anschliessend darf `grep` weder `alert(` noch `hideError` noch `errorMessage` finden.

- [ ] **Step 5: Alte Styles entfernen**

Die Regeln `.error-message` und `.success-message` aus `style.css` streichen — beide haben keine Verwendung mehr. Prüfen:

```bash
grep -rn "error-message\|success-message" src/
```

Erwartet: keine Treffer.

- [ ] **Step 6: Im Browser prüfen**

Stack neu bauen, einloggen, und der Reihe nach:

1. Leere Suche abschicken → rotes Toast unten rechts, **bleibt stehen**, verschwindet erst per Klick auf ×
2. „System Status" im Fuss anklicken → Toast mit mehrzeiligem Status, kein blockierendes `alert()`
3. Mehrere Meldungen kurz hintereinander auslösen → sie stapeln sich, überlagern sich nicht
4. Fehler **vor** der ersten Suche auslösen → sichtbar (das war im alten Zustand nicht der Fall)

- [ ] **Step 7: Commit**

```bash
git add src/web_interface/static/js/app.js src/web_interface/templates/index.html src/web_interface/static/css/style.css
git commit -m "feat: surface all user messages as dismissible toasts"
```

---

## Task 14: Abschluss

- [ ] **Step 1: Sauber neu bauen**

```bash
cd KnovasPlatform
docker compose build --no-cache docbridge-web
docker compose up -d --force-recreate docbridge-web docbridge-web-nginx
```

`--no-cache` ist hier bewusst: der Dev-Container trug zwischenzeitlich ein manuell nachinstalliertes `selectolax`. Erst dieser Build beweist, dass die `requirements.txt` allein ausreicht.

- [ ] **Step 2: Gesamte Suite laufen lassen**

```bash
cd components/docbridge_integration && pytest
```

Erwartet: PASS

- [ ] **Step 3: Durchgang von Hand**

Einloggen, suchen, und der Reihe nach prüfen:

1. Panel öffnet sich für `.txt`, `.docx`, `.msg` und `.pdf`
2. Esc schliesst das Panel, der Schliessen-Button ebenfalls
3. Zwei Karten schnell hintereinander anklicken — es bleibt das zuletzt geklickte Dokument stehen
4. Keine Bewertungs-UI auf den Karten
5. Knovas-Logo in Kopfzeile und Login, Favicon im Tab
6. Konsole ohne Fehler, Netzwerk-Tab ohne Aufrufe an `/api/analytics/*`
7. Fenster unter 900 px: Panel als Vollbild-Overlay
8. Leere Suche abschicken: Fehler erscheint als Toast unten rechts und bleibt stehen, bis er weggeklickt wird
9. „System Status" im Fuss: Toast statt blockierendem `alert()`

- [ ] **Step 4: Spec-Status nachziehen**

In `docs/superpowers/specs/2026-07-26-preview-feedback-branding-design.md` unter „Offene Punkte" vermerken, was tatsächlich geliefert wurde — insbesondere, ob Original-SVGs verwendet wurden oder PNGs aus dem Brand-PDF.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "docs: record delivery status for preview, feedback removal and rebrand"
```
