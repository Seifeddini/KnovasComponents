# Document fields (Dokumentfelder)

Typed values per document — Mandant, Zeitraum, Dokumentart, Gericht, Frist —
that Knovas keeps next to the text: shown on result cards, used as search
filters, listed, and corrected by hand. Knovas holds the values; the Platform
shows only what Knovas confirms, and the Knovas Connector can send values with
each upload.

**On for every account since Knovas 1.5.0.** Document fields, including
filtering by fields in search and in lists, is on for every Knovas account.
Knovas can still switch it off for an account, and an older server does not
have it, so the Platform and the Knovas Connector check every answer: the
Platform asks Knovas what it serves and shows only that, and nothing in
`knovas.env` switches it on. Against an account with the feature off, or an
older server, the Platform behaves exactly as before: no new UI, and no new
keys in its requests.

In a BROKERED tenant, entity values sent by the Knovas Connector (for example
`client`) also need change S1; before it, Knovas refuses such an upload with
`401 assertion_rejected` and the Knovas Connector indexes the document without
its fields (`refused:assertion_rejected`).

Design and decisions: [`docs/superpowers/plans/2026-10-02-document-fields-integration.md`](../../../docs/superpowers/plans/2026-10-02-document-fields-integration.md).
Knovas Connector side: [`RemoteController/docs/configuration.md`](../../../RemoteController/docs/configuration.md#per-source-document-fields-dokumentfelder).
Customer one-pager: [`docs/client/document-fields.md`](../../../docs/client/document-fields.md).

## What the tenant gets, by state

The Platform probes Knovas (`POST /secured/graph/doc-values/find` with an
empty body — never billed, no side effects) and keeps the answer for
`capability_ttl_seconds`. Errors of later calls adjust it. **System → Dokumentfelder**
names the state; nothing else in the UI mentions a reduced one.

| State (System tab) | Knovas has | The Platform shows |
|---|---|---|
| `aus` | feature switched off for the account, knowledge graph off, older server — or the Platform runs without mTLS (legacy mode), or `DOC_FIELDS_UI=off` | nothing new |
| `Werte (ohne Filter)` | values | the field panel in the preview, the **Dokumentfelder** admin tab, the field drawer in **Dokumente** |
| `Werte + Liste (Filter in der Suche: Kalibrierung bei Knovas fehlt)` | values and listing; search filters wait for a relevance calibration at Knovas | additionally: typed values on result cards, the **Liste anzeigen** listing, the admin **Feldfilter** |
| `Werte + Filter` | everything | additionally: the filter rail in the search |

A probe that fails (401, 403, 429, 5xx, network) counts as "unknown" and is
shown as `aus` for 30 seconds. The "listing without filters" state is learned
from the first filtered search Knovas refuses for a missing calibration and is
kept for `calibration_recheck_seconds` (1 hour); the first filtered search
after that may meet the same answer once more.

## Honesty rules

These are pinned by tests (the decisions live in Python; there is no JS test
runner):

- **A filter is applied or absent.** A search carries filters only in the
  `Werte + Filter` state, and results count as filtered only when Knovas
  confirms it (`where.applied`). Without that confirmation the search answers
  "Der Filter konnte nicht angewendet werden" with **no results**, and
  *Ohne Filter suchen* is an explicit button — a filtered search is never
  quietly retried without its filters, and the client-side score thresholds
  and filename supplement are skipped while a filter is sent.
- **"Verstanden als"** chips repeat the person's own input (enum labels for
  codes) plus "n verknüpfte Einträge" when Knovas linked names to entries.
- **Lists say when they are incomplete.** "Liste unvollständig – Filter
  eingrenzen" appears only when the walk reached its last page and Knovas says
  it is not complete; a total appears only when Knovas returns one.
- **A listing sorted by a deadline field is not a deadline control.** It
  carries "Nur Dokumente mit erfasstem Fristfeld, die für Sie sichtbar sind –
  keine Fristenkontrolle".
- **Empty states speak of what the person can see** ("in den für Sie
  sichtbaren Dokumenten"), never of the corpus.
- **No echo, no "gespeichert".** The Knovas Connector and the Ingestion tab say
  "nicht übernommen" when Knovas did not confirm.
- **Shown is not searchable.** A manual title is shown, not searched ("Titel
  wird angezeigt, nicht durchsucht"). `privileged` is a marker, not an access
  restriction ("Kennzeichnung, keine Zugriffsbeschränkung").
- **Never presented as available** (Knovas does not ship them): facet counts,
  bulk edit, the extracted layer, content-derived proposals, packs other than
  `core` and `legal_ch`, a deadline control.

## Search, listing and cards

- **Filter rail** (state `Werte + Filter`), built from the fields marked
  *als Filter anbieten*: a select for code lists, a name field with
  suggestions for entity fields, free text for dates and periods ("GJ 2024",
  "Q1 2024", "15.03.2024" — Knovas parses them), Ja/Nein for yes/no fields.
  Suggestions come from the field's node list, fetched **without** the typed
  text and filtered inside the Platform: typed prefixes never reach Knovas.
  Fields marked *besonders schützenswert* get no suggestions.
- **Liste anzeigen** (states with listing): without a question, the chosen
  fields list every matching document visible to the person, sorted by a date
  field or the path, page by page. Experiment documents (*Experimente*) never
  appear in it, as in the search.
- **Cards** show the values of fields marked *auf Trefferkarten zeigen*, except
  *besonders schützenswert* fields. A real title from the values (at most 100
  characters and not just the file name) replaces the file name; an entity the
  person may not see shows as "verborgen"; a weak hit is marked "unsicherer
  Treffer".
- **Cortex**: an entity whose type is the target of an entity field offers
  "Dokumente mit <Feld> = <Name>", which opens that listing (handed over in
  `sessionStorage`, not in the URL).

## The field panel (preview)

Opening a result loads its values on demand: each field with its value, the
layer it comes from (**Manuell**, **Upload**, **Ordnervorgabe**,
**Extrahiert**), "zuletzt manuell geändert am …", and Knovas's warnings.

**Who may edit:** identity on, a role in `DOC_FIELDS_EDIT_ROLES` (default
`admin`; the built-in `member` role reads only), `admin` for *besonders
schützenswert* fields, and — in the search — the document must have come back
in the person's own search or listing (the admin drawer under *Dokumente*
relies on what Knovas shows the administrator). Knovas still decides and may
answer "keine Berechtigung", which turns the panel read-only.

- Edits are **non-strict**: Knovas keeps what it can normalise and warns per
  field — "nicht verknüpft" for a name that matches no entry (kept as text;
  `core.party` and `legal_ch.counterparty` have no target type, so their
  names are always unlinked), "Datum mehrdeutig – bitte prüfen". Input Knovas
  cannot read at all is refused at the field; nothing is dropped silently.
- A version conflict reloads the panel ("inzwischen geändert"); nothing is
  overwritten automatically. Held values ("Werte zurückgehalten") are
  read-only.
- *Zurück zum Upload-/Ordnerwert* removes the manual value.
- **A rename or move prunes manual values.** A renamed file is a new pointer
  at Knovas; the old document and its manual values go with the old pointer.

## Dokumentfelder admin tab

Visible to `admin` in every state except `aus`.

- **Felder**: create, change and retire (*Stilllegen*) fields — key, type
  (Text, Auswahl, Datum, Zeitraum, Betrag, Zahl, Kennung, Ja/Nein, Eintrag
  aus dem Wissensgraph), one or several
  values, labels DE/FR/IT/EN, other names, the entity target, *auf
  Trefferkarten zeigen*, *als Filter anbieten*, *normal* / *besonders
  schützenswert*. Knovas refuses keys that look personal. A field the current
  Ingestion profile uses asks for a confirmation before it is changed or
  retired: uploads with a retired key are no longer accepted.
- **Pakete**: install `core` (installed automatically on first use) or
  `legal_ch`.
- **Einstellungen**: what an upload with an unknown key does, and how
  `03/04/2024` is read. Setting "ablehnen" lists the profile keys Knovas would
  then refuse.
- **Ordnervorgaben** (folder rules): default values for every document under
  a folder, applied at Knovas without re-uploading ("wird angewendet (meist
  Minuten)"). The folder is picked from the Knovas Connector's tree (or typed as
  a pointer prefix ending in `/`). Pointers leave out the source folder, so a
  rule on `rc-sync/Muster AG/` applies in every source with a top folder
  `Muster AG` — a profile with several sources asks for that confirmation.
  **Use rules for every value that does not depend on the source**: they cost
  no upload.
- Writes need Knovas's administrator group or full clearance; without it the
  forms are read-only with an explanation.

**Dokumente** tab: a *Felder* drawer per row (read and edit, same rules as the
panel) and the **Feldfilter** (list documents by field values, states with
listing).

## Ingestion: fields per folder

In **Verwaltung → Ingestion**, each folder has a *Felder* section (shown
unless the state is `aus`):

- **Feste Werte** — one field per line, `schlüssel = Wert`; several values
  separated by `;`:

  ```
  doc_type = invoice
  mandant = Muster AG
  party = Muster AG; Beispiel GmbH
  ```

  Code lists take the code or the label; values are at most 256 characters,
  at most 64 keys.
- **Pfadvorlagen** — one per line, relative to the folder, first match wins:
  `{mandant}/{period}/**` makes `Muster AG/GJ 2024/Rechnung_17.pdf` carry
  `mandant = Muster AG`, `period = GJ 2024`. *Vorlagen testen* and the
  *Vorschau* show the captures for the folder's files.
- **Aus Dateieigenschaften** — opt-ins: language (document properties of
  pdf/docx, often the program's language; `.eml` Content-Language), e-mail
  date as document date, e-mails as document type "E-Mail", e-mail sender as
  author, author from pdf/docx properties, *Stichwörter aus
  Datei-Eigenschaften* (PDF/Word keywords and Outlook categories, split on
  `,` and `;`, at most 32; knovas-extract 0.4.0a1 does not read Outlook
  categories yet) and *Status aus Word-Dokumentstatus* (sent as written;
  Knovas keeps it only when it matches a choice of `status`, otherwise the
  status panel counts an `invalid_value` under `status`). These two need a
  Knovas Connector that reports `metadata_fields_v2`. `.md` and `.txt` files
  have no properties.

Saving checks every key against the registry (active fields only, code-list
values, template keys, the targets of the file properties) and warns when a
key is also set by a folder rule under the profile's prefix. It refuses when
the Knovas Connector does not report the needed capability ("Der Knovas
Connector ist zu alt – bitte aktualisieren").

**A change re-sends documents, and that costs.** Changing a folder's fixed
values, templates or file properties re-uploads every document of that folder:
each one a billed upload with a fresh text recognition. The form says how many
and how long, and needs a confirmation: "… ca. 3 Nächte" for 20,000 documents
at 100 per cycle on the nightly schedule — `ceil(documents / per cycle)` cycles
× (scan interval + upload time of one cycle), counted in nights of the sync
window. Text recognition is not included, so it is the short side of the
truth. On the *manual* schedule each Start re-sends one cycle's worth.

The status panel shows what the Knovas Connector reports: whether Knovas takes
the fields, counts of refused / not accepted / failed documents, Knovas's
warnings per code and field key ("invalid_value 3× (amount)", at most 50, the
most frequent first, with the meaning of each code below them), unknown keys
with suggestions, pending re-uploads with an ETA,
and identical relative paths in several folders (the first folder governs).
*Erneut senden* re-queues not accepted (once Knovas serves fields), refused or
failed documents — each a billed upload.

**Downgrading the Platform** to a release without document fields: it drops
the per-folder fields when it reads the profile. If it pushes that profile,
the Knovas Connector clears those documents' upload values at Knovas on their next
re-upload (bounded per cycle). Folder rules and manual values stay.

## Configuration

| Setting | Default | |
|---|---|---|
| `DOC_FIELDS_UI` | `auto` | `off` hides every part of it. There is no `on`: what shows follows Knovas. |
| `DOC_FIELDS_EDIT_ROLES` | `admin` | Comma list of `admin`, `approver`, `ingestion_manager`, `member` that may edit values. *Besonders schützenswert* fields stay admin-only. |

Both go into `knovas.env`. The rest of `web.doc_fields` in
`config/config.yaml` (cache times, page size) rarely needs changing.

The compose `mock` demo ([../demo.md](../demo.md)) runs in legacy mode and
therefore shows no document fields; `MOCK_DOC_FIELDS` on the mock serves the
automated tests.

## Privacy and audit

- No field value, template capture, title, description, pointer, entity name
  or query text goes into a log line, an audit entry, a support summary or a
  URL the new code builds. Routes that carry a pointer or a name are POST
  routes with a JSON body.
- **Known limitation (application logs):** application log lines that predate
  document fields still name document paths and pointers — opening,
  downloading and previewing a document, a failed preview or thumbnail, the
  legacy search (its query text), and the Knovas Connector's upload-failure,
  partial-OCR and removal lines. Folder names are where path-template values
  come from, so these lines can carry field values too. Treat the Platform's
  and the Knovas Connector's container logs as confidential, like the documents.
- **Access logs carry no URIs**: the bundled nginx and gunicorn log time,
  method, status, size and duration only (`knovas_privacy`); so does the host
  nginx template (`deploy/host-nginx`). Operators lose per-path access logs in
  exchange.
- **Known limitation:** the existing preview and file routes keep the document
  pointer in the URL path and `path=` / `q=` (the search words, for the
  highlighting in the PDF) in the query string. The URI-free access logs above
  keep them out of the Platform's logs; a proxy of your own in front must be
  configured the same way. nginx's *error* log still prints the request line
  when an upstream request fails (for example a timeout); keep it on the host
  and out of support bundles.
- The audit log (`audit_log` in the Platform database) holds keys, counts,
  codes and versions only: `doc_field.created` / `.updated` / `.deprecated`,
  `doc_field_pack.installed`, `doc_field_settings.changed`,
  `doc_field_rule.saved` / `.retired` (target: the rule id, never the folder),
  `document.values_edited` (target: the document id, never the pointer;
  outcome `ok`, or `denied` with Knovas's code in `detail.code` —
  `version_conflict`, `change_not_authorized`, `anchor_quarantined`), and every
  re-send request, from the Ingestion tab or the *Dokumentfelder* tab, as
  `ingestion.doc_fields_requeued` (target `remote_controller` / `sync`, detail
  `{outcome, requeued}`: each requeued document is a billed upload).
