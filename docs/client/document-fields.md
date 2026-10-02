# Document fields (Dokumentfelder) — what they do for your office

**Available only once Knovas has enabled Document Fields for your tenant**,
on a Knovas release that reads the document reference of the field panel from
the request body (otherwise the panel and every value edit say *Knovas-Update
nötig*; filters and lists still work).
Until then the Platform shows none of what follows, and search works as
before. Ask Knovas to enable it; afterwards **Verwaltung → System →
Dokumentfelder** says which level your tenant has:

| Level | What you get |
|---|---|
| *Werte (ohne Filter)* | Values per document in the preview, editable by your administrators; the *Dokumentfelder* admin tab |
| *Werte + Liste* | additionally: values on result cards, and lists of documents by field ("all invoices of Muster AG") |
| *Werte + Filter* | additionally: filters in the search ("Kündigungsfrist" restricted to one client and year) |

A document field is a typed value Knovas keeps next to a document's text:
Mandant, Zeitraum, Dokumentart, Gericht, Frist. Values come from three places,
and the most specific wins:

1. **Your edits** in the preview (*Manuell*), by the roles your administrator
   allows (by default administrators only).
2. **Your folders**, sent by RemoteController with each upload (*Upload*): a
   fixed value per folder ("everything in *Kreditoren* is an invoice"), or a
   **path template** that reads the value from the folder name:
   `{mandant}/{period}/**` turns `Muster AG/GJ 2024/Rechnung_17.pdf` into
   Mandant *Muster AG*, Zeitraum *GJ 2024*.
3. **Folder defaults** at Knovas (*Ordnervorgabe*), set in the *Dokumentfelder*
   tab. They apply to documents already indexed, without uploading anything
   again — the cheap way for any value that does not depend on the folder.

## Fiduciary (Treuhand)

- Fields: `mandant` (linked to your Mandanten), `period`, `doc_type`,
  `document_date`, amounts shown as `CHF 1'234.50`, QR/IBAN/UID references.
- Template `{mandant}/{period}/**` on the *Mandate* folder; *Kreditoren* gets
  the fixed value `doc_type = invoice`; the mail export opts into the e-mail
  date and type.
- Search "Kündigungsfrist" with Mandant = Muster AG, Zeitraum = GJ 2024. List
  "all invoices of Muster AG for GJ 2024", newest first.

## Law firm (Kanzlei)

- Install the `legal_ch` pack: client, matter, court, counterparty, case
  number, document class, filing and decision dates, deadline, legal area,
  privileged.
- Template `{client}/{matter}/**`; a folder default gives everything under
  *Muster AG* the legal area "corporate".
- List a matter's documents by filing date. A list sorted by deadline always
  says "keine Fristenkontrolle": it shows only documents whose deadline field
  is filled and that you may see — it does not replace your deadline control.
- *privileged* is a marker, not an access restriction. Access stays with your
  access groups.

## Medical practice (Arztpraxis)

- There is no medical pack. Your administrator creates a `patient` field
  (linked to a node type *Patient*), marked *besonders schützenswert*.
- Template `{patient}/**` on the *Patienten* folder.
- Such fields never appear on result cards, get no name suggestions, and only
  administrators edit them. Values never widen access: every list and search
  shows only documents your access groups allow.

## Good to know

- **Filters are honest.** Results count as filtered only when Knovas confirms
  the filter; otherwise you get no results and a button *Ohne Filter suchen*.
  A list says "Liste unvollständig" when it is.
- **Changing a folder's fields re-sends its documents.** Each one is a billed
  upload with a fresh text recognition, at most 100 per cycle (about three
  nights for 20,000 documents on the nightly schedule). The Ingestion tab shows
  the count and asks before saving. Prefer folder defaults where you can.
- **Renaming or moving a file** makes it a new document at Knovas; manual
  values on the old one are not carried over.
- **A title you edit is shown, not searched.**
- Not available today: counts per filter value, editing many documents at
  once, values read from the document text, packs other than `core` and
  `legal_ch`.
- The document-fields features write no field value into logs, new URLs or
  the audit log of the Platform. Older log lines (opening or previewing a
  document, a failed upload) still name document paths, and folder names can
  be field values: keep the Platform's and RemoteController's logs as
  confidential as the documents.

Details for administrators: [KnovasPlatform/docs/features/document-fields.md](../../KnovasPlatform/docs/features/document-fields.md).
