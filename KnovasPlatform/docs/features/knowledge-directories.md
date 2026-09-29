# Knowledge types, directories, cards and the Wissensnetz

The Platform shows the tenant's Knowledge Graph as a working surface for the
firm: every entry of a type in a searchable list (a **Verzeichnis**), each entry
as a **card** with its fields, documents, history and **Wissensnetz**, and a
console where administrators define what a type consists of.

Nothing in the code names a type. Tables, cards and forms are generated from
the fields an administrator defines, so a new type ("Vertrag", "Gutachten") is
data entry in the console, not a release.

Requires `ONTOLOGY_SOURCE=graph` and per-user accounts (`identity.enabled`).
In fixture mode every page says *Wissensnetz-Modus erforderlich* and every
`/api/graph/*` route answers 409; nothing is invented.

Design: the "Wissenstypen & Verzeichnisse" prototype, built on
`docs/superpowers/specs/2026-09-02-typed-node-workbench-design.md`.

## Where things live

| What | Stored in | Why there |
|------|-----------|-----------|
| Types, fields, entries, values, edges, documents | Knowledge Graph at Knovas (`/secured/graph/*`) | The firm's knowledge; read access is the Knovas ACL |
| Directories: title, address, columns, order, on/off | Platform DB, `node_type_views` | Presentation of *this* installation |
| Card layout per type: header, rail, sections | Platform DB, `node_type_cards` | A type without a directory still has cards |
| Who may edit an entry | Platform DB, `node_grants` | Knovas has no concept of a Platform user |
| Dismissed suggestions | Platform DB, `graph_suggestion_dismissals` | So they never come back |

Migration `0003_directories` creates the three new tables at start-up.

## Screens

**Sidebar → Verzeichnisse.** One entry per active directory, in the order set in
the console. A directory lists every entry of its type the signed-in person may
see, with the chosen fields as columns (the first four when none are chosen),
the number of connections, a search over the *name* (not over field values; the
API cannot search those), and *nur mit Lücke* for entries with an empty
required field. *Neuer Eintrag* opens a form built from the type's fields; a
required field left empty never blocks the save — it shows as *fehlt*.

**The card** (`/verzeichnis/<address>/<id>`, or `/eintrag/<id>` for a type
without a directory): header chips, sections and rail as laid out in the
console; fields no one placed appear under *Nicht zugeordnet*, values of a
retired field under *Weitere Angaben*. Fields are changed in place. Dates keep
their precision — *März 2026* is never shown as a day. The tabs are:

- *Wissensnetz*: the entry and its neighbours up to three steps, edges named
  after the connection field that made them, a search over every entry the
  person may see.
- *Dokumente*: the assigned documents.
- *Verlauf*: fact history, where the API provides it.

**Verwaltung → Wissenstypen.** Types with their field and entry counts. A
type's page lists its fields with how often each is filled, reorders them by
dragging, edits names, *Pflicht*, descriptions and choice values, and retires a
field (*stilllegen*: existing values stay readable) or takes it back. *Karte
gestalten* places fields by dragging or by choosing a zone on the field, with a
preview that uses the card's own renderer on a real entry.

**Verwaltung → Verzeichnisse.** Create one directory per type, set title,
subtitle, address and columns, reorder the navigation, switch a directory off
(settings kept) or remove it (settings deleted; entries and card layout stay).

### Suggestions

The console computes proposals from the inventory: retire a field no entry
fills, drop choice values no entry uses, add a date field where a text field
holds dates, count entries with gaps, place unplaced fields, move surplus
header chips to the rail, give a type with entries a directory, drop an empty
column. **Nothing happens by itself.** A suggestion is recomputed on the server
before it is applied, and one that is dismissed never returns. Suggestions that
need the whole inventory (retire, trim, gaps, empty column) are only made when
all values could be read.

## Who may do what

| Action | Who |
|--------|-----|
| See an entry, its fields, documents, network | Decided by the Knovas ACL (the entry's access groups) |
| Create an entry | Any signed-in person; they become its owner |
| Change an entry's fields or name | Its owner, its editors, any administrator |
| Grant or revoke editors | The owner or an administrator — never an editor |
| Types, fields, card layouts, directories, suggestions | Administrators |

An entry created outside the Platform has no owner; administrators can edit it
and grant editors. Grants are enforced by the Platform's routes — anything
holding the tenant certificate can bypass them, as with every Platform
permission.

## Limits and fallbacks

- **About one request a second** reaches Knovas. Names, types and edges come
  from the topology export the Cortex already caches per person (60 s,
  `ONTOLOGY_CACHE_TTL`); the values of a whole type from one paged
  `GET /secured/graph/facts?node_type_id=`. An API without that route answers
  404 — the directory then shows names at once and fills the columns in batches,
  saying so; the console computes fill rates from a sample and says that too.
- **Edges of the network** come with `GET /nodes/<id>/neighbors?include_edges=true`.
  An API without `include_edges` answers without edges; the visible edges of the
  tenant are then induced on the same visible node set. An edge is never drawn to
  an entry the person cannot see.
- **History** needs `GET /secured/graph/facts/<id>/history`; without it the tab
  says the API does not provide one yet. Up to 25 fields are read.
- **Taking a retired field back** sends `deprecated_at: null` and reads the field
  again; an API that ignores it is reported as such, not as success.
- **Colours** follow the order in which types were created: the first four types
  get four distinct colours.

## API

All routes under `/api/graph`, JSON, CSRF header on every write:
`node-types[/<id>/schema[/<aid>[/move]]]`, `nodes[/<id>[/fields/<aid>|/network|/history|/grants[/<user>]]]`,
`people`, `directories/<address>[/rows]`, `views[/<type>[/move]]`, `cards/<type>`,
`admin/types[/<type>[/card]]`, `admin/directories`, `suggestions/apply|dismiss`.
