# Search UI — offene Punkte

Stand: 2026-07-27, nach Abschluss von `feat/preview-feedback-branding`.

Was hier steht, ist bewusst nicht umgesetzt worden — entweder weil es eine eigene
Entscheidung braucht, weil es Messung an echten Daten voraussetzt, oder weil es
nach dem aktuellen Zweig kommt. Jeder Punkt trägt die Begründung mit, damit
niemand sie neu herleiten muss.

## 1. Klick auf den Treffer öffnet direkt das ganze Dokument

**Priorität: als Nächstes.**

Heute öffnet ein Klick auf die Trefferkarte die Vorschau als Seitenpanel rechts;
von dort kann man über den Aufklapp-Schalter auf Vollbild gehen. Künftig soll der
Klick **direkt** das Vollbild-Popup öffnen — dieselbe Darstellung wie heute nach
dem Aufklappen, nur ohne den Zwischenschritt über das Panel.

Der Grund, warum das jetzt richtig ist und vorher nicht: die Karte zeigt seit
`5cb39df` die **gerenderte erste Seite** des Dokuments. Damit erfüllt sie selbst
die Rolle, für die das Seitenpanel gedacht war — einen schnellen Blick, ohne den
Kontext der Liste zu verlieren. Ein Panel, das dieselbe Information noch einmal
zeigt, ist ein Klick zu viel. Wer klickt, will lesen, nicht nochmal schauen.

Zu klären beim Umsetzen:

- **Bleibt das Seitenpanel überhaupt?** Wenn der Klick ins Vollbild geht, hat es
  keinen Auslöser mehr. Entweder ersatzlos entfernen (dann fällt viel CSS und der
  `is-fullscreen`-Sonderfall weg, das Panel *ist* dann der Normalzustand), oder
  als Option behalten und über eine zweite Geste erreichbar machen. Ersteres ist
  ehrlicher — ein Modus, den niemand aufruft, ist toter Code.
- **Navigation im Popup.** Sobald die Liste verdeckt ist, braucht man Vor/Zurück
  zwischen den Treffern, sonst ist jeder Wechsel Schließen-und-neu-Klicken. Das
  war schon beim ersten Vollbild-Vorschlag der Einwand und gilt jetzt umso mehr.
- **Semantik.** Ein Vollbild-Overlay ist faktisch ein Modal. Aktuell ist das Panel
  ein `<aside>` ohne Fokusfalle. Der saubere Weg ist das native `<dialog>`: bringt
  Backdrop, Escape, Fokusfalle und `inert`-Hintergrund mit, statt dass wir jedes
  Stück selbst bauen. Realistisch ein halber Tag.

## 2. Caching für die Vorschau

**Gemessen, vertagt.** Zahlen vom 2026-07-26 auf lokaler Platte, kleine Dateien:

| | |
| --- | --- |
| Server, TXT / MSG | 7–9 ms |
| Server, PDF | 2 ms |
| Server, DOCX | 103–231 ms |
| Client, Klick bis sichtbar | TXT 17–35 ms · MSG 14–21 ms · PDF-iframe 31–50 ms · DOCX 128–168 ms |

DOCX-Extraktion skaliert mit dem Dokument: 171 ms bei 10 Absätzen, 232 ms bei
1.000, **387 ms bei 3.000**. Und dieser Aufwand fällt bei **jedem** Öffnen erneut
an — es wird nichts zwischengespeichert. Dreimal dasselbe Dokument öffnen erzeugt
sechs Requests.

Der Vorschaubild-Endpunkt aus `5cb39df` hat als einziger inzwischen ein ETag. Für
`preview-content` fehlt das Äquivalent: ein Cache auf dem extrahierten Markdown,
geschlüsselt auf Pfad + mtime + Größe.

**Vor der Umsetzung messen**, nicht danach: alle Zahlen oben stammen von winzigen
Testdateien auf lokaler Platte. In der Realität liegen die Dokumente auf
`/mnt/autodoc` über SMB, wo womöglich schon das reine Dateilesen alles andere
dominiert. Ein Cache, der das falsche Problem löst, ist verschenkte Arbeit.

## 3. Eigener PDF-Viewer statt des Browser-Viewers

Der eingebaute Viewer bringt seine eigene Oberfläche mit — dunkler Balken, eigene
Sidebar, Download- und Druckknopf, in jedem Browser anders. Mitten in der hellen
Knovas-Oberfläche wirkt er wie ein Fremdkörper.

Der Weg wäre **pdf.js**, selbst gehostet als zwei Dateien (`pdf.mjs`,
`pdf.worker.mjs`) — kein npm, kein Build-Schritt, kein CDN, analog zu den
IBM-Plex-Schriften.

Das eigentliche Argument ist nicht die Optik: **wir kennen die Trefferseite.**
Jeder Treffer trägt `page_number` und `sentence_number`. Mit pdf.js springt die
Vorschau direkt auf die Fundstelle und markiert sie. Mit dem nativen Viewer bleibt
nur `#page=N` und die Hoffnung, dass der Browser es beachtet.

Kosten: rund 1 MB ausgeliefertes JavaScript, das selbst aktuell gehalten werden
muss (pdf.js hatte CVEs), `worker-src 'self'` in der CSP, und Toolbar, Zoom und
Seitennavigation baut man selbst. Zwei bis drei Tage für etwas, das sich fertig
anfühlt.

**Vorher erheben**, wie oft Nutzer PDFs öffnen gegenüber DOCX und MSG — bei den
anderen drei Formaten rendern wir bereits selbst, und dort sieht es aus wie Knovas.

## 3a. Aktengruppierung hängt still an der Anreicherung

Die Trefferliste gruppiert nach `akten_id` (siehe
`docs/superpowers/specs/2026-07-30-trefferliste-design.md`, Abschnitt 2). Dieses
Feld kommt **nicht** von der Knovas-API, sondern aus der lokalen
`.search_enrichment.jsonl`, die der RemoteController schreibt.

Ist sie nicht konfiguriert oder nicht vorhanden — wie in der lokalen Demo, wo
`/mnt/autodoc/.search_enrichment.jsonl` schlicht fehlt —, tragen die Treffer kein
`akten_id`, und die Gruppierung erscheint nie. Es gibt dabei **keinen Fehler und
keinen Hinweis**: die Liste bleibt einfach flach, was vom normalen Fall „alle
Treffer aus einer Akte" nicht zu unterscheiden ist.

Zu entscheiden: ob das reicht, oder ob der Systemstatus ausweisen soll, ob die
Anreicherung geladen wurde. Das Feld `onedrive_enrichment_loaded` liegt in jeder
Suchantwort bereits vor, es müsste nur angezeigt werden.

## 4. Kleinere Punkte aus dem Review

- **Format-Badge auf der Karte.** Man sieht einem Treffer nicht an, ob er eine
  E-Mail, ein Vertrag oder eine Notiz ist. Billig, spart bei jeder Suche einen
  Klick, und das PRD fordert es unter FR-6.
- **Pfeiltasten zwischen den Treffern.** Enter öffnet inzwischen, aber von Treffer
  zu Treffer kommt man nur per Tab durch alle Buttons.
- **`escapeJsString` escaped keine Anführungszeichen** (`app.js`). Wird in
  `onclick="app.openDocument('…','…')"` innerhalb eines doppelt gequoteten
  Attributs verwendet. Über SMB-/Windows-Dateinamen nicht ausnutzbar, da `"` dort
  unzulässig ist, und der Code ist älter als dieser Zweig — aber eine Zeile Härtung
  oder der Wechsel auf `addEventListener` + `data-`-Attribute wäre sauberer.
- **woff2 wird als `application/octet-stream` ausgeliefert.** Ursache ist nicht
  nginx, sondern die Python-Stdlib: `.woff2` fehlt in `mimetypes.types_map`, und
  das schlanke Container-Image hat keine `/etc/mime.types`. Fix wäre
  `mimetypes.add_type('font/woff2', '.woff2')` in `app.py`. Kein Nutzereffekt,
  Browser erzwingen `nosniff` bei Schriften nicht.
- **Seitenvorschau nur für PDF.** DOCX, TXT und MSG zeigen weiterhin einen
  Textauszug statt einer gerenderten Seite. Für eine echte Seite bräuchte es einen
  Konverter im Serving-Pfad — bewusst nicht Teil dieser Anwendung, siehe die
  verworfene LibreOffice-Variante in der Design-Spec.

## 5. Der große Block: Facetten, Sortierung, Pagination

Unverändert gültig aus der PRD-Analyse vom 2026-07-26: `POST /secured/query` nimmt
**ausschliesslich** `Input` entgegen — kein Filter, kein Sort, kein Offset, kein
Suggest-Endpunkt, und die Antwort enthält keinen Volltext. Facetten mit Zählern,
Sortierung und Pagination sind deshalb **keine Frontend-Aufgaben**. Wer sie im
Frontend plant, plant an der API vorbei.

Reihenfolge bleibt: erst die API, dann die UI.
