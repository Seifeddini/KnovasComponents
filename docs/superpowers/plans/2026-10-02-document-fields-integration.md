# Document Fields integration spec for Knovas Connector and KnovasPlatform

- **Date:** 2026-10-02. **Status:** implemented on branch `claude/document-fields-integration` (WP-C, RC1, P1, RC2, P2, P3, P4, I). This is the spec the work packages were built against; **§10 lists every deviation found during implementation** and wins where the two differ. Appendix A lists the review dispositions.
- **Server:** KnowledgeBase Document Fields P1a+P1b. All flags are off in every overlay (`knovas-software/app/config/defaults.toml:161-205`).
- **Components:** knovascomponents `main` @ f9d872c.
- **Contract digest:** a summary of the server contract written for this work (request/response shapes, echo fields, error codes, feature-off behaviour). The server source (`KB:api/doc_fields_api.py`, `secure_api.py`, `services/query_pipeline.py`) and the Developer Kit (`KBD:Knovas_Developer_Kit/api/Secure_API.md`, `Knowledge_Graph_API.md`) are authoritative.

**Path prefixes used below**

| Prefix | Path |
|---|---|
| `KB:` | `KnowledgeBase/knovas-software/app/src` |
| `KBD:` | `KnowledgeBase/docs` |
| `KC:` | `KnovasComponents` (this repository) |
| `RC:` | `KC:/KnovasConnector` |
| `PL:` | `KC:/KnovasPlatform/components/docbridge_integration` |
| `MOCK:` | `KC:/KnovasPlatform/mock_knovas_api` |

---

## 0. Design decisions (every section below depends on these)

**D1. Each component learns what the server supports; nobody configures it on.**
- The Platform sends a probe. The Knovas Connector (RC) reads the echo the server returns on init.
- `DOC_FIELDS_UI=off` and `RC_DOC_FIELDS=off` can only turn the feature off. They can never turn a gated feature on.

**D2. A filter is either honest or absent.**
- The Platform sends `where` only when the capability is `filters`.
- Results are shown as filtered only when `where.applied === true`. Otherwise they are withheld.
- A request that carries `where` never falls back to unfiltered results and is never silently retried without `where`.
- A request without `where` may be retried once without `return_fields` (H3). Nothing was filtered, so nothing is misrepresented.

**D3. Which layer a value goes to.**
- Sub-folder defaults are server folder rules: the `rule` layer, applied retroactively and editable through `PUT /secured/graph/doc-field-rules`.
  - A rule re-applies in PostgreSQL without re-uploading anything.
  - So any value that does not depend on the source belongs in a rule.
- These go into RC upload `fields` (the `upload` layer):
  - constants for a whole source;
  - path-template captures;
  - extractor mappings the admin opted into.
- Changing any of these re-uploads the whole source (§3.7).
- Per-source values cannot be rules, because pointers leave out the source folder (`RC:src/sync/sync_executor.py:572-577, 597-603`; `RC:src/sync/knovas_uploader.py:181`).
- The Platform warns when one key is configured both ways.

**D4. The RC sends entity values as plain-string names, never node ids.**
- RC uploads carry no assertion. A node id for a walled node would be dropped as `invalid_value`.
- A name is kept, linked or unlinked, and `where` matches it by name.

**D5. Fields never block indexing.**
- If the server refuses an init because of its fields, the RC retries once without `fields`. The previous upload-layer values survive.
- A refusal counts as caused by the fields only if that retry succeeds (§3.6).

**D6. Values never leave the data path.**
- None of these appears in logs, metric labels, audit detail, support JSON or **any URL the new code builds**, whether a Knovas request or a Platform route:
  - a field value or a template capture;
  - a title or description;
  - a pointer or `rel`;
  - an entity name or a typed name prefix;
  - query text.
- Only keys, codes, counts and versions appear there.
- New Platform routes that carry a pointer or a name are POST routes with a JSON body.
- Platform nginx stops logging request URIs (§4.9).
- The audit `target_id` is the `document_uuid` for value edits and the rule `id` for folder rules.

**D7. Every Platform call to Knovas carries the person's assertion.**
- All calls go through `_make_request`, `_request_no_retry` or the new `_request_quiet`. Each attaches `principal_assertion` through `_with_principal` (`PL:src/knovas_client.py:1373-1399`).
- Doc-fields writes are sent once, through `_request_no_retry` (`:1486`). A tenacity replay of a PATCH that already committed would come back 409.
- The capability probe goes through `_request_quiet`: one attempt, and no ERROR log of the response body (§2.2).

**D8. New wire keys are sent only when they are not empty.**
- An unchanged profile, upload or query stays byte-identical to today.
- New sync-body keys go only to an RC that advertises the matching capability.

**D9. The RC never calls `GET /secured/graph/doc-fields`.**
- That call is billed `graph_read`, and the first listing installs `core` (`KB:api/doc_fields_api.py:615-630`).
- The Platform validates keys when a profile is saved instead.

**D10. Platform entity filters send `{"name": …}`, never `{node_id}`, and typed names never reach Knovas.**
- A name matches both linked and unlinked values. `{node_id}` would miss names the RC stored unlinked (D4).
- Entity suggestions come from the node-type's node list, fetched **without `q`** and filtered inside the Platform.
  - `GET /secured/graph/nodes` reads `q` only from the query string (`KB:api/graph_api.py:1374-1376`).
  - The prod gateway logs `"$request"` (`infra/kubernetes/overlays/knovas-aks-prod/mtls-gateway-prod-configmap.yaml:23-31`).
- `resolved_nodes` in the echo is a count (`KB:services/knowledge_graph/doc_fields/planner.py:238-240`). It is shown as "n verknüpfte Einträge".

**D11. New UI appears only for the capability that backs it (H6).**
- `values`: detail panel and admin tab.
- `listing_only`: adds typed values on cards, the listing and the Feldfilter.
- `filters`: adds the search filter rail.

**D12. Only the configured roles edit values, and edits keep what the person typed.**
- Value, title and description edits are allowed only for roles in `web.doc_fields.edit_roles` (default `admin`).
  - The built-in `member` role is documented as "Searches and reads, within their access groups." (`PL:src/identity/migrations/0001_identity.sql:65-66`).
  - In a tenant without walls, the server treats the caller as non-enforcing (`KB:services/knowledge_graph/doc_fields/authority.py:107-108`). There a member could change every visible document.
- Only `admin` may edit `sensitivity: special` fields.
- Knovas still decides through `authorize_change` (`authority.py:115-129`).
- Edits are sent with **`fields_strict: false`**:
  - The server keeps every value it can normalise and returns `warnings[]` per key, such as `unresolved_entity` or `ambiguous_date` (`KB:services/knowledge_graph/doc_fields/values_service.py:633-638, 660-664, 906-914`). The panel shows them next to the field.
  - With `true`, every name typed into a field without a target node type would be refused with 422, because the index is None and the result is `unresolved_entity` (`KB:services/knowledge_graph/doc_fields/entity_resolver.py:424-425`). Those fields are `core.party` and `legal_ch.counterparty` (`knovas-software/app/config/doc_field_packs/core.yaml:135-139`, `legal_ch.yaml:72-75`).
  - Input the server cannot normalise is refused as a whole (400) and shown inline. Nothing is dropped silently.

**D13. Doc-fields UI exists only in secured mode.**
- The capability is `off` unless `use_secured_api and mtls_enabled` (`PL:src/knovas_client.py:1009-1014`).
- The legacy `GET /api/search` path has nowhere to carry `where` (`:1697-1705`). `search_documents` raises `ValueError` when `where` or `return_fields` reaches it.

---

## 1. Value map (end to end, with the capability each step needs)

**Capabilities:**

| Capability | Requires | Adds |
|---|---|---|
| **values** | `KNOWLEDGE_GRAPH_ENABLED`, `DOC_FIELDS_ENABLED`, tenant on the allowlist | Init `fields`, GET/PATCH doc-values, registry, packs, settings, folder rules, the admin tab, the detail panel |
| **listing_only** | values, `DOC_FIELDS_WHERE_ENABLED`; relevance calibration missing | Typed values on cards (`return_fields` needs no calibration), the listing (`find`), the admin Feldfilter |
| **filters** | listing_only, plus relevance calibration | Search filters |

How the Platform learns `listing_only`:
- The probe cannot tell it apart from `filters`, because `find` never checks calibration (`KB:api/doc_fields_api.py:577-607`).
- The Platform learns it from the first 503 `where_requires_calibration` (`KB:services/query_pipeline.py:246-254`).

Example names are placeholders only ("Muster AG", "Beispiel GmbH").

### Fiduciary (Treuhand)

1. **Setup (Dokumentfelder tab, values).** `core` is installed automatically. The admin creates `mandant` as an `entity_ref` field targeting node type "Mandant", with `display` and `facet` on.
2. **Ingestion profile (values, plus an RC with `source_fields_v1`).**
   - Source `Mandate`: template `{mandant}/{period}/**`.
   - Source `Kreditoren`: static `{"doc_type": "invoice"}`.
   - Source `Postfach-Export`: metadata items `email_date` and `email_doc_type`. There is no `language` item: knovas-extract sets no language on e-mail `Metadata`.
3. **Sync.** The RC uploads `Muster AG/GJ 2024/Rechnung_17.pdf`.
   - The path is relative to source `Mandate`; the pointer is `rc-sync/Muster AG/GJ 2024/Rechnung_17.pdf`.
   - The upload carries `"fields": {"mandant": "Muster AG", "period": "GJ 2024"}`.
   - The server turns the period into an interval. It links the Mandant name to a visible node, or keeps it as an unlinked name.
4. **Search (filters).** The query "Kündigungsfrist" with Mandant = Muster AG, Zeitraum = GJ 2024 and Dokumentart = Rechnung sends `where: {"mandant": {"name": "Muster AG"}, "period": "GJ 2024", "doc_type": "invoice"}`.
   - The "Verstanden als" chips are built from the person's own input, plus the linked count from `where.resolved`.
5. **Browse (listing_only).** "Alle Rechnungen Muster AG GJ 2024", sorted by `document_date` descending.
   - Pages are fetched until `next_after` is null.
   - "Liste unvollständig – Filter eingrenzen" appears only when the walk ended with `next_after` null **and** `complete: false` (H5).
6. **Edit (values, role in `edit_roles`).** A mis-filed period is corrected in the detail panel. The manual value beats the template value, and the layer badge changes from "Upload" to "Manuell" with the date of the change.
7. **Extras.** `amount` is shown as `CHF 1'234.50`. `reference` auto-detects QR references, IBANs and CHE-UIDs by checksum.

### Lawyer (legal_ch)

1. **Setup.** Install `legal_ch`, which adds client, matter, court, counterparty, case_number, legal_class, filed_on, decision_date, deadline, legal_area and privileged.
2. **Templates and rules.**
   - Template `{client}/{matter}/**` on source `Akten`.
   - Folder rule `rc-sync/Muster AG/` sets `legal_area: ["corporate"]`.
     - This is the prefix `rc_pointer_prefix` builds. It leaves out the source folder `Akten`, so the rule applies to every source with a top folder `Muster AG`. The admin confirms this.
   - Mail source with `email_date` and `email_doc_type`.
3. **Search.** "Fristerstreckung" with Gericht, Rechtsgebiet = tenancy and Dokumentklasse = judgment.
4. **Browse.**
   - All documents of a matter, sorted by `filed_on`.
   - "Dokumente nach Frist sortiert": `where: {"deadline": {"gte": "01.10.2026"}}`, sorted by `deadline` ascending.
     - It always carries the banner "Nur Dokumente mit erfasstem Fristfeld, die für Sie sichtbar sind – keine Fristenkontrolle".
     - It is never called "Fristenliste". It is shown as complete only when the last page says `complete: true` (H9).
5. **Edit.**
   - `case_number` `4A 123/2024` is normalised to `4A_123/2024`.
   - `privileged` is set to Ja. The panel notes "Kennzeichnung, keine Zugriffsbeschränkung".
6. **Honesty note.** `counterparty` and `core` `party` have no target node type.
   - Names typed into them are kept unlinked: the edit is non-strict (D12).
   - They show "nicht verknüpft" as information, not as an error.

### Doctor (Arztpraxis)

1. **Setup.**
   - No medical pack exists (see non-goals).
   - The admin creates `patient` as an `entity_ref` field targeting node type "Patient": `sensitivity: special`, `facet: true`, `display: false`.
   - Keys that look personal are refused by the server (`key_looks_personal`, 422). `patient` passes.
2. **Template.** `{patient}/**` on source `Patienten`, because `rel` leaves out the source folder.
   - Captures are never logged and never put in support JSON or URLs.
   - Special fields never appear on cards, because they are not in `return_fields`.
   - They get no autocomplete suggestions (free text only).
   - Only `admin` may edit them.
3. **Search and browse.** "Medikation" with Patient = … and Dokumentart = report. List every report for a patient, sorted by date.
4. **Walls.** Walls stay exactly as today, through per-source `access_groups`.
   - Values never widen visibility: query and find filter by ACL first.
   - Held values show as quarantined.

### Generic office (core only)

- Real titles on cards: the anchor title (e.g. an email subject) when it is a real title, meaning at most 100 characters and not just the file name. Otherwise the file-name stem, as today (§4.1).
- `doc_type` and `document_date` chips.
- `language` (from document properties) and the email date through the metadata mapping.
- Honest empty states per `no_results_reason`, and an "unsicherer Treffer" badge.
- A detail panel showing where each value came from and when a manual value was last changed.
- Admin folder defaults.

**Never presented as available:** facet counts, bulk edit, the extracted layer, content-derived proposals, packs other than `core` and `legal_ch`, a deadline control, searchable manual titles, and ACL effects of `privileged`.

---

## 2. Capability detection and backward compatibility

### 2.1 Server states

| State | `fields` on init | `where` / `return_fields` on query | Doc-fields routes |
|---|---|---|---|
| **off or old** (flags off, tenant not on the allowlist, knowledge graph off, server older than the feature) | Ignored, no `fields` echo (`KB:api/secure_api.py:1808-1817`) | Ignored, no echo (`KB:services/query_pipeline.py:213-227`) | 404 `{"status":"error","error_code":"HTTP_404"}` for every method (`KB:api/doc_fields_api.py:317-349`) |
| **values** | Staged, echo at the top level (`secure_api.py:1912-1913`) | 400 `where_unsupported` for **either** key (`query_pipeline.py:228-229`) | Work; `find` returns 400 `where_unsupported` (`doc_fields_api.py:584-587`) |
| **where on, no calibration** | Staged, with echo | `where`: 503 `where_requires_calibration` (`query_pipeline.py:246-254`). `return_fields` alone works | All work, `find` included |
| **filters** | Staged, with echo | Echo `where.applied: true`, plus `return_fields.applied` | All work |

### 2.2 Platform detection (`PL:src/doc_fields_capability.py`, new)

**Preconditions.** The capability is `off` without probing in two cases:
- `web.doc_fields.ui: off`;
- the client is not in secured mode (D13).

**Probe:** `POST /secured/graph/doc-values/find`, body `{}` plus the assertion, sent through `_request_quiet`.
- `_make_request` logs every response of 400 or higher at ERROR with its body (`PL:src/knovas_client.py:1474-1483`). An off server would otherwise produce an ERROR line per worker per TTL.
- It is never billed, because billing happens only after success (`doc_fields_api.py:606-607`).
- It has no side effects. Rate-limit class: METADATA.

| Probe answer | Capability |
|---|---|
| 404 with any `error_code` other than `NOT_FOUND` or `pack_not_found`, or a 404 body that is not JSON | `off` |
| 400 `where_unsupported` | `values` |
| 400 `invalid_value` with `path: "where"` | `filters` (may later be lowered to `listing_only`) |
| 401, 403, 429, 5xx, network error | `unknown` (shown as off; cached 30 s) |

**Cache:** `shared_cache()`, a process-wide singleton (§4.2), since there is one tenant per deployment. TTL comes from `web.doc_fields.capability_ttl_seconds` (default 300).

**Signals from other calls (`observe`):**

| Signal | Raised when | Effect |
|---|---|---|
| `where_unsupported` | A query or find answers 400 `where_unsupported` | `values` |
| `feature_off` | Any doc-fields call answers 404 `HTTP_404` | `off` |
| `echo_missing` | A 2xx answer to a `where` request lacks `where.applied === true` | `unknown`; the next call probes again |
| `needs_calibration` | A query answers 503 `where_requires_calibration` | `listing_only`, held for `calibration_recheck_seconds` (default 3600). The probe does not lift it in the meantime |

**Constraints:**
- Never probe with `GET /doc-fields`; it installs `core` and is billed.
- The probe runs lazily inside a request. With a broker configured, `_with_principal` refuses to send anything without a signed-in user (`knovas_client.py:1381-1386`).

### 2.3 RC detection

- The RC sends no probe.
- For each upload that carries `fields`:
  - 2xx with a `fields` echo: `staged`.
  - 2xx without an echo: `not_accepted` (server off or old).
  - Refused: one retry without fields; if that retry succeeds, `refused:<code>` (§3.6).
- `/sync/status` derives `server: accepted | not_accepted | unknown` from the stored outcomes (§3.8).
- **Turning the feature on later:**
  - When a `staged` echo arrives while `not_accepted` rows exist, those rows are queued for re-upload, bounded per cycle (§3.7).
  - `POST /sync/doc-fields/requeue` does the same on request.

### 2.4 Honesty rules (each one is pinned by a pytest)

- **H1.** The Platform sends `where` only when the capability is `filters`. It sends `return_fields` only when the capability is `filters` or `listing_only`.
- **H2.** `filter_state` is computed as follows:
  - `applied` when `response.where.applied is True`;
  - `partial` when, in addition, `may_be_partial` is true;
  - otherwise `not_applied`: the browser gets 409 `filter_not_applied` with **no results**.
- **H3.** A request carrying `where` is never retried without `where`.
  - When `where` was sent, the Platform skips both of these:
    - the filename supplement (`PL:src/web_interface/app.py:1810-1813`, `_supplement_results_from_enrichment_filenames` `:3095`);
    - the client score thresholds (`_apply_search_refinement`, `:1808`, `:2994`).
  - Requests **without** `where` keep today's refinement, even when the response says `relevance_gate_applied: true`.
    - That flag follows `RELEVANCE_GATE_ENABLED`, independently of doc fields. It is already `"true"` in dev (`infra/kubernetes/overlays/knovas-aks-dev/app-config-dev.yaml:132`).
  - A request without `where` that the server refuses with a doc-fields code because of `return_fields` is retried **exactly once** without `return_fields`, and the response carries `fields_unavailable: true`.
- **H4.** A result shows `fields` only when the server returned them.
  - A missing enum label falls back to the code.
  - Entity values `{hidden:true}` are shown as "verborgen".
- **H5.** The listing shows "Liste unvollständig – Filter eingrenzen" only when `next_after is None and complete is False`.
  - `complete` is false on every page that has a following page (`KB:services/knowledge_graph/doc_fields/listing.py:405-406`).
  - `total_count` is shown only when it is an integer. It is null on overflow (`:407-408`) and present on the first page only.
- **H6.** Gated UI is not rendered unless its capability holds (D11). The System tab is the only place that names a reduced state.
- **H7.** RC and Platform status say "nicht übernommen" when no echo came back, never "gespeichert".
- **H8.** Empty-state texts say "in den für Sie sichtbaren Dokumenten". They never claim the corpus lacks something.
- **H9.** Some things are never presented:
  - a listing as a deadline control, or as complete while `complete` is false;
  - `privileged` as an access restriction;
  - a manual title edit as searchable. The panel says "Titel wird angezeigt, nicht durchsucht", because the PATCH writes PostgreSQL only (`KB:api/doc_fields_api.py:525-526`);
  - the 503 `where_requires_calibration` as temporary ("vorübergehend").

### 2.5 Compatibility matrix

| Combination | Behaviour |
|---|---|
| New Platform + old RC | `rc.capabilities()` is empty, so saving or pushing a profile that uses fields is refused ("Knovas Connector aktualisieren"). Profiles without fields push byte-identical bodies, because the new keys are left out. |
| Old Platform + new RC | The new keys are never sent, so the RC behaves exactly as today. |
| New RC + server off or old | `fields` is sent only when configured. It is ignored, the status says `not_accepted`, and documents are indexed as today. |
| New Platform + server off | No new UI and no new keys. `/api/search` only adds keys: `document_fields: {"capability":"off","filter_state":"none"}` and a `honesty` block, whose values are null on old servers. |
| New Platform + values server | Detail panel and admin tab (registry, packs, settings, rules). No filters, listing or typed values on cards. |
| New Platform in legacy (unsecured) mode, including today's docker-compose `mock` demo (`KC:KnovasPlatform/docs/demo.md:24-25`) | Capability `off`, no new UI, and no new keys on the legacy GET. |
| Platform downgrade | New profile data lives only inside `sources[]` entries, which an older `profile_from_json` parses field by field and so drops silently (`PL:src/identity/ingestion_profiles.py:31-38`). **No new top-level profile keys**, because an older Platform would crash on them through `IngestionProfile(**fields)`. If an older Platform re-pushes the profile, the RC clears its upload-layer values with `{}` on the next re-upload of each affected document, bounded per cycle. This is documented in WP-I. |
| RC downgrade | An old RC answers 400 to a sync body with new keys, and the Platform rolls back the config push (`PL:src/knovas_connector_client.py:137-170`). The new state-DB columns are additive and an old RC ignores them. |

---

## 3. Knovas Connector changes

### 3.1 Contract (WP-C)

`RC:contracts/sync_request.schema.json:11-25`: the source items, closed with `additionalProperties:false` at `:14`, gain three optional properties and a `$defs` block:

```json
"fields": {
  "type": "object", "maxProperties": 64,
  "propertyNames": {"pattern": "^[a-z][a-z0-9_]{0,63}$",
                    "not": {"enum": ["title","description","path","ingested_at","pointer"]}},
  "additionalProperties": {"$ref": "#/$defs/fieldValue"},
  "description": "Static Knovas field values for every document of this source, keyed by registry key; sent as init `fields` (upload layer). Entity values are names. Omit when empty."
},
"field_templates": {
  "type": "array", "maxItems": 8,
  "items": {"type": "string", "minLength": 1, "maxLength": 512},
  "description": "Source-relative path templates, e.g. \"{client}/{period}/**\". First match wins; captures become field values. Golden vectors: contracts/vectors/field_templates.json."
},
"metadata_fields": {
  "type": "array", "uniqueItems": true,
  "items": {"enum": ["language","email_date","email_doc_type","email_author","document_author"]}
}
```

- `$defs.fieldScalar` is one of: a string of 1–256 characters, a number, or a boolean.
- `$defs.fieldValue` is a `fieldScalar`, or an array of 1–32 `fieldScalar`s.
- No objects are allowed:
  - money goes as a string, e.g. `"CHF 1234.50"`;
  - entities go as name strings.
- `ingestion` (`:41-51`) is **unchanged**. `metadata_fields` sits per source (see the downgrade row in §2.5).
- `RC:contracts/sync_response.schema.json`:
  - The top level is closed (`:14`) and gains an optional `"doc_fields": {"type":"object"}`.
  - `document_sync` is closed too (`:49-59`) and gains `"fields_changed": {"type":"integer","minimum":0}`. Without it, `/sync` returns 500 "Internal schema validation failed" (`RC:src/routes/sync.py:114-118`).
  - Transmission items are already open (`:28`), so the per-transmission `fields` is only documented in the description.
- Copy `sync_request.schema.json` byte-identically to `PL:src/identity/rc_contracts/sync_request.schema.json`. The existing check at `PL:tests/test_ingestion_compiler.py:279-285` enforces this.
- The template golden vectors live at `RC:contracts/vectors/field_templates.json`, **not** in `contracts/` itself.
  - CI runs `Draft202012Validator.check_schema` on every `contracts/*.json` (`KC:.github/workflows/ci.yml:150-153`), and a JSON array is not a schema.
  - The glob is not recursive, so the subfolder is safe.

### 3.2 Carrying per-source settings through the sync (WP-RC2)

- **`SourceSpec`.** A frozen dataclass, defined by WP-RC1 in `RC:src/sync/doc_fields_payload.py`: `access_groups: tuple[str,...]`, `fields: Mapping[str, FieldValue]`, `templates: tuple[CompiledTemplate,...]`, `metadata_fields: frozenset[str]`.
- **Walk targets.**
  - `_WalkTarget` (`sync_executor.py:108-116`) gets `spec: SourceSpec`.
  - Both branches of `build_walk_targets` (`:556-604`, constructors at `:573` and `:598`) build it from the source dict.
  - `_iter_candidate_files` (`:281-325`) yields `(*item, target.spec)` instead of `target.access_groups` (`:323`).
  - `_iter_m365_candidates` (`:328-374`) builds the spec per source next to `groups` (`:354`).
- **Upload queue.** The fifth element of each queue tuple becomes the `SourceSpec`. That covers the type at `:77`, `_ScanPlan.upload_queue` (`:513-517`), the append in `plan_sync_cycle` (`:720-724`) and the upload loop (`:905-918`).
- **Uploader signature.**
  - New signature: `upload_file(file_path, relative_path, sync_body, access_groups=(), *, source: SourceSpec | None = None, previous_fields_sent: bool = False)` (`knovas_uploader.py:171-177`).
  - When `source` is given, `source.access_groups` wins.
  - `previous_fields_sent` comes from the state row (§3.7) and drives the `{}` clear in §3.6.
- **Relative-path collisions.**
  - The executor does not deduplicate `rel` across sources (`sync_executor.py:677-691`), and state is keyed by `rel`.
  - When several sources yield the same `rel` in one cycle, the spec of the **first** source that yielded it governs the fields and the digest for every later duplicate in that cycle. Content handling is unchanged from today.
  - This keeps per-source digests from alternating, which would re-upload both copies every cycle.
  - Collisions are counted as `rel_collisions` in the status.
- **Backfill.** `RC:scripts/backfill_partial_ocr.py:63-82, 175` passes the spec of the first matching source, the same rule.
- **Bad template.** A template that does not compile skips that source for the cycle and sets the status error `field_template_invalid`. It never crashes the cycle.

### 3.3 Building the field payload (WP-RC1 writes the pure functions, WP-RC2 calls them)

`assemble(rel, spec, source_metadata, ext) -> Payload(values: dict, dropped: Counter[str])`:

- **Precedence per key:** template capture first, then static per-source value, then metadata mapping.
- **Dropped with a counted reason:**
  - system keys (`system_key`);
  - strings over 256 characters (`value_too_long`);
  - more than 32 items in a list (`cap_exceeded`).
- **More than 64 keys:** drop the lowest precedence first (`cap_exceeded`).
- **Size limit:** compact UTF-8 JSON over 16384 bytes drops metadata keys first, then static ones, then captures (`too_large`).
- **Nothing configured or nothing applicable:** returns `{}`. Examples: a PDF in a source whose only metadata items are e-mail items; a path no template matches.

`config_digest(rel, spec) -> str`:
- sha256 of the canonical JSON `{"v":1,"static":…,"captures":captures(rel),"metadata":sorted(...),"mapping_version":METADATA_MAPPING_VERSION}`.
- Returns `""` when static, captures and metadata are all empty.
- Extractor values are not part of the digest, because they are unknown before extraction.
- **The digest is stored for every recorded outcome (§3.7),** including when nothing was sent. A non-empty digest with an empty payload therefore never looks "changed" again.

### 3.4 Path templates (WP-RC1; the Platform preview reimplements them in WP-P4)

The grammar uses the same tokens as the server's Phase 2 templates (`KBD:ModernDocs/plans/2026-10-01-document-fields.md:856-860`):

```
template := segment ("/" segment)* ["/**"]
segment  := "*" | "{" key "}" | literal
key      := [a-z][a-z0-9_]{0,63}, not title|description|path|ingested_at|pointer, at most once per template
literal  := 1..255 chars, none of "/" "{" "}" "*"
```

**Matching:**
- The template is matched against the **directory** segments of the source-relative `rel`, with `\` replaced by `/`. The file name is excluded.
- `rel` is the same path that becomes the pointer (`sync_executor.py:572-577, 597-603`; M365 `:349-362`). It never contains the source folder itself.
- Literals are compared after NFC and casefold. `*` matches exactly one segment.
- Without `/**`, the template must match the directory depth exactly. With `/**`, zero or more deeper directories are allowed.

**Captures:**
- A capture is the whole segment, NFC-normalised and stripped. An empty segment gives no value.
- **There is no type conversion on the client.** "GJ 2024" and "Q1 2024" are sent as strings, and the server's normaliser decides; its warnings come back in the echo.
- The first template that matches wins.

**API:**
- `compile_template(s) -> CompiledTemplate` raises `TemplateError(code)` with code `syntax`, `duplicate_key`, `system_key` or `too_long`.
- `captures(rel, templates) -> dict[str,str]`.

**Golden vectors** (`RC:contracts/vectors/field_templates.json`):
- Shape: `[{"template","path","captures"|null,"error"|null}]`.
- At least 25 cases: `\` separators, NFC/NFD umlauts, `*`, `**` with zero and with several deeper levels, depth mismatch, duplicate key, system key, empty segment, case-insensitive literal.

### 3.5 Extractor metadata mapping (WP-RC1)

**Carrying the metadata through extraction:**
- `ExtractedDocument` (`RC:src/sync/document_text.py:193-227`) gains `source_metadata: dict[str,str]`.
- It holds author, language, created and modified from the knovas-extract `Metadata`, plus `extra["eml:content_language"]` when present.
- It is filled in `_extract_bytes` (`:730-817`) and stays picklable, since it crosses the forked child through the queue (`:907-1021`).
- `_scalar_extra` (`:530-540`) is unchanged.

**Mapping.** `RC:src/sync/metadata_fields.py`: `map_metadata(md, ext, enabled) -> dict`, with `METADATA_MAPPING_VERSION = 1`.

| Item | Target key | Rule |
|---|---|---|
| `language` | `language` | pdf, docx and md: `Metadata.language`, the document-properties `dc:language`. That is often the authoring tool's locale, so the admin UI labels it "aus Dokumenteigenschaften, oft Programmsprache". `.eml`: the `Content-Language` header (`extra["eml:content_language"]`). `.msg`: none. The value must match `^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$`; skip `x-default` and `und` |
| `email_date` | `document_date` | **Only `.eml`/`.msg`.** `created` (the Date header) is sent verbatim; the server uses its date part (`KB:services/knowledge_graph/doc_fields/typed_values.py:542-545`) |
| `email_doc_type` | `doc_type` = `correspondence.email` | `.eml`/`.msg` |
| `email_author` | `author` | `.eml`/`.msg` From: the display name, else the address |
| `document_author` | `author` | pdf, docx or md author. Skip junk, case-insensitive: `administrator`, `admin`, `user`, `owner`, `unknown`, `author`, `microsoft office user`, empty |

**Never:**
- File mtime, the M365 `lastModifiedDateTime`, or PDF/DOCX/MD `created`/`modified` never become `document_date`. A test pins each one.
- No Message-ID mapping. It would flood the human-facing `reference` code field (`core.yaml:141-147`), which fiduciaries use for QR, IBAN and invoice references.
- No `sender` or `recipients` mapping: those keys are in no pack, and mapped keys are never auto-registered.
- No `source_metadata` key on the wire; that is server Phase 2.

### 3.6 Init, echo and fallback (WP-RC2, in `knovas_uploader.py:228-262`)

```python
payload = assemble(relative_path, spec, doc.source_metadata, ext)   # after access_groups (:239-243)
clear   = previous_fields_sent and not payload.values               # values were staged before; config now empty
if doc_fields_enabled() and (payload.values or clear):              # RC_DOC_FIELDS kill switch
    init_body["fields"] = payload.values                            # {} only to clear
```

**Body rules:**
- Never send `fields_mode` (the default is `replace`) or `fields_strict`.
- Cap the title at 500 characters (`title[:500]`). The server refuses longer titles (`KB:api/secure_api.py:1632-1634`), and today such a file loops forever.

**In-call retries:** `_request` (`:118-169`) gains `retry_status: Callable[[Response], bool] | None`. When an init carries `fields`, a 503 whose `error_code` starts with `doc_fields_` is returned at once instead of after five backoff retries.

**2xx answers:**
- `parse_init_echo(init_json)` (WP-RC1) returns `FieldsEcho(staged, unknown_keys, warning_codes: Counter, suggest)`, or None.
- The echo sits at the top level, next to `transmission_key_id`.
- Outcome:
  - `staged` when there is an echo;
  - `not_accepted` when fields were sent and no echo came back;
  - `cleared` when `{}` was sent;
  - `none` when no `fields` key was sent.

**Non-2xx answers when fields were sent:** `classify_init_refusal(status, body)` (WP-RC1) returns a code in any of these cases.

| Status | Condition |
|---|---|
| 400 or 422 | `error_code` is `invalid_fields`, `fields_too_large`, `ambiguous_field` or `unknown_field`, **or** `path` starts with `fields` |
| 503 | `doc_fields_ingest_unavailable` or `doc_fields_unavailable` (transient) |
| 401 | `assertion_rejected`: a brokered tenant on a server without S1, see §5 |

- With a code, the RC re-posts the init **once without `fields`**.
  - **If that retry succeeds:** outcome `refused:<code>`. The document is indexed, and its previous upload-layer values survive.
  - **If the retry fails:** the refusal was not caused by the fields. For example, in a BROKERED tenant an `access_groups` body 401s on its own (`KB:api/secure_api.py:1686-1712`). The existing path applies: error `init failed: <retry status>`, no fields record, and §3.7 attempt counting.
- Without a code, the existing path is unchanged: the error string is `init failed: <status>`.

**Result:** `UploadResult` (`:49-59`) gains `fields: FieldsOutcome | None` = `{outcome, staged, warning_codes, unknown_keys, suggest, digest, transient: bool}`.

### 3.7 State, digest and re-upload (WP-RC2)

**New columns.**
- `documents` (`RC:src/sync/sync_state_db.py:14-21`) gains them through `ALTER TABLE ADD COLUMN`, guarded by `PRAGMA table_info` in `_connect` (`:83-96`):
  - `fields_digest TEXT`;
  - `fields_sent INTEGER NOT NULL DEFAULT 0`;
  - `fields_outcome TEXT`;
  - `fields_warning_codes TEXT` (a JSON list of codes);
  - `fields_attempts INTEGER NOT NULL DEFAULT 0`.

**Writing without wiping.**
- Today `record_upload` is `INSERT OR REPLACE … (relative_path, mtime_iso, size_bytes, last_uploaded_at, transmission_key_id)` (`sync_state_db.py:175-189`). It resets every column it does not name.
- It becomes an UPSERT: `INSERT … ON CONFLICT(relative_path) DO UPDATE SET …`, naming each column explicitly.
- It gains `fields: FieldsRecord | None = None`:
  - `None` leaves the fields columns untouched.
  - A `FieldsRecord(digest, outcome, sent, warning_codes)` writes them and resets `fields_attempts` to 0.
- `SyncStateStore.record_upload`, `record_partial` and `record_skip` (`RC:src/sync/sync_state.py:106-163`) pass it through. `record_skip` and `record_partial` reach the DB through `record_upload` today.

**What is written per outcome.** `record_upload_outcome` (`sync_executor.py:443-508`) takes the governing digest and the `FieldsOutcome`, and stores a `FieldsRecord` on **every outcome it records**:

| Outcome | When | Digest stored | `fields_sent` |
|---|---|---|---|
| `staged` | Echo present | New | 1 |
| `cleared` | `{}` sent | New | 0 |
| `not_accepted` | Fields sent, no echo | New | Unchanged |
| `refused:<code>`, permanent (400, 422, 401) | Retry without fields succeeded | New | Unchanged |
| `refused:<code>`, transient (503) | Retry without fields succeeded | **Old digest kept**; `fields_attempts` += 1 | Unchanged |
| `none` | Nothing sent: empty payload, kill switch off, `skip:unconvertible`, partial after exhausted extraction retries | New | Unchanged |

- A `retry` outcome records nothing, as today. For a `fields_changed` document, it increments `fields_attempts`.
- **Full mode** (`mode != "incremental"`) records nothing today (`:472, :489`). In full mode, the fields columns of rows that already exist are updated (UPDATE only). A full run is therefore not followed by a second, fields-only pass.

**Detecting a change.**
- `DocumentSyncStatus` (`sync_state.py:15`) gains `fields_changed`.
- `_classify_status` (`sync_executor.py:136-151`): a fingerprint-synced file whose governing `config_digest` differs from the stored digest is `fields_changed`.
- **A stored NULL digest equals `""`**, so upgrading does not cause a mass re-upload.
- When `RC_DOC_FIELDS=off`, no digest is computed and nothing is `fields_changed`.
- `SyncStateStore.document_status` and `summarize` (`sync_state.py:86, 195-224`) stay fingerprint-only. They never return `fields_changed`, so their else branch (counted as modified) is not reached by it.
- `plan_sync_cycle` (`sync_executor.py:607-735`) gets an explicit `elif status == "fields_changed"` branch. Today's else counts `excluded_max_age` (`:707-708`).
- `DocumentSyncSummary` (`sync_state.py:27-50`) gains `fields_changed` in the dataclass and in `as_dict`.
- `/sync/status` reads the last summary key by key (`RC:src/routes/sync_control.py:92-96`) and adds it.
- `_needs_upload` (`:416-419`) treats `fields_changed` as work in incremental mode.

**Queue (re-uploads never displace new work).** `plan_sync_cycle` builds two lists:
- **Primary:** `pending` and `modified` up to `max_upload_files`, today's single-pass cap (`:720-724`).
- **Side:** `fields_changed` up to `RC_FIELDS_REUPLOAD_PER_CYCLE`, appended only while `max_upload_files` (when > 0) leaves room.
- `budget.note_file` counts `fields_changed` as no work, so re-uploads never truncate a scan.
- `_ordered_upload_queue` (`:77-81`):
  - `small_first` sorts by `(priority, size)`, so the side list goes last.
  - The default order keeps the primary list before the side list, in scan order.

**Sequential subfolders.** `maybe_advance` receives `modified = ds.modified + ds.fields_changed` (`:1010-1016`; `RC:src/sync/subfolder_queue.py:198-230`). A subfolder therefore completes only after its re-uploads are done.

**Liveness.**
- A `fields_changed` re-upload whose outcome is `retry` increments `fields_attempts`. Causes include a server error (e.g. 403 `group_not_dominated` after an admin narrowed a document's ACL, `KB:api/secure_api.py:544-572`), a transient fields refusal, or an extraction error below the extraction retry cap.
- Rate-limit pauses do not count.
- At `RC_FIELDS_REUPLOAD_MAX_ATTEMPTS` (default 3) the digest is stored with outcome `reupload_failed:<class>`, and the document leaves the queue.
  - `<class>` comes from a closed set: `init_401`, `init_403`, `init_4xx`, `init_5xx`, `fields_unavailable`, `extract`, `other`.
  - It is reported in the status.
  - Requeue `reupload_failed` or `all` resets it.

**Cost (documented in WP-I and shown before saving, §4.8).**
- Every re-upload costs a full extraction, OCR included, a full transmission, and one billed init: `@billable("request")` plus `ingestion_init` (`KB:api/secure_api.py:1588-1589, 1901-1904`).
- The content short-circuit skips GPU embedding only when the document's chunks carry the current embedding-space stamp (`KB:services/weaviate_service.py:1077-1103`).
- A change to a source's static values, templates or metadata items re-sends every document of that source.
- At 100 per cycle and the `nightly` preset, 20,000 documents take about 200 nights.
- A `one_time` run (preset `manual`, `PL:src/identity/ingestion_presets.py:38-44`) re-sends at most one bound per press of Start.
- Folder rules avoid all of this for values that do not depend on the source (D3).

**When the server starts accepting.**
- On a `staged` outcome, if `not_accepted` rows exist, run `UPDATE documents SET fields_digest=NULL WHERE fields_outcome='not_accepted'`.
- Those rows now count as `fields_changed` and are re-uploaded within the bound.
- `POST /sync/doc-fields/requeue {"outcome":"not_accepted"|"refused"|"reupload_failed"|"all"}` does the same on request and returns `{"requeued": n}`. It uses the same auth decorators as `/sync/start` (`sync_control.py:27-35`).

### 3.8 Status, sync response, schema errors and metrics (WP-RC2)

`/sync/status` (`RC:src/routes/sync_control.py:61-100`) adds:

```json
"capabilities": ["source_fields_v1","field_templates_v1","metadata_fields_v1","fields_requeue_v1"],
"doc_fields": {
  "enabled": true,
  "server": "accepted|not_accepted|unknown",
  "per_cycle": 100,
  "documents": {"with_fields": 1234, "pending_reupload": 56, "refused": 3, "not_accepted": 0, "reupload_failed": 1},
  "last_cycle": {"staged": 40, "not_accepted": 0, "cleared": 0, "none": 12, "refused": {"unknown_field": 2}, "rel_collisions": 0},
  "warnings": {"unresolved_entity": 12, "ambiguous_date": 1},
  "unknown_keys": ["mandat"],
  "suggest": {"mandat": ["mandant"]},
  "template_errors": {"field_template_invalid": 0}
}
```

- `unknown_keys` and `suggest` contain registry or config keys only, from the last cycle, at most 20 entries, held in scheduler memory.
- `/health` stays unchanged, because it is unauthenticated.
- **Sync response:**
  - `document_sync.fields_changed` (schema change in §3.1).
  - `tx_entry["fields"] = {"outcome","staged","warning_codes"}`, only when fields were sent (`sync_executor.py:943-977`).
  - `SyncRunResult` (`:89-104`) gains a top-level `doc_fields` summary with the same shape as the `last_cycle` and `warnings` blocks.
- **Schema error messages.**
  - `RC:src/util/schema.py:22-25` returns `e.message`, which embeds the offending instance. `/sync` and `/sync/body` return `errors[0]` (`RC:src/routes/sync.py:60-62, 100-103`), and the Platform shows it as a `KnovasConnectorError` (`PL:src/knovas_connector_client.py:88-90`).
  - For errors whose path lies under `sources[*].fields`, `field_templates` or `metadata_fields`, the RC returns `<json_path>: <validator keyword>` only.
- **Metrics** in `RC:src/sync/doc_fields_metrics.py`:
  - `rc_doc_fields_uploads_total{outcome}`;
  - `rc_doc_fields_refusals_total{code}`;
  - `rc_doc_fields_warnings_total{code}`;
  - `rc_doc_fields_client_dropped_total{reason}`.
  - Every label value comes from a closed set, with `other` for anything else, because `/metrics` is unauthenticated (`RC:docs/operations.md:14`).

### 3.9 Configuration

Set in `RC:src/config.py:62-182`:
- `RC_DOC_FIELDS` = `on`|`off`, default `on`.
  - `off` never sends `fields`, keeps today's body, computes no digest and leaves the fields columns untouched.
  - Turning it on later re-sends each configured source once, bounded per cycle.
- `RC_FIELDS_REUPLOAD_PER_CYCLE`: default `100`, range 1–10000. There is no "0 = manual only", because requeue relies on the same bound.
- `RC_FIELDS_REUPLOAD_MAX_ATTEMPTS`: default `3`.

### 3.10 Logging

New code logs only codes and counts, e.g. `doc_fields outcome=staged staged=3 warnings=2`. It never logs values, captures, `rel` or pointers. A `caplog` test enforces this.

---

## 4. KnovasPlatform changes

### 4.1 Client (WP-P1): `PL:src/knovas_client.py`

**Errors:**
- `GraphError` (`:890-904`) gains a keyword argument `details: dict | None = None`, filtered to the whitelist `path`, `suggest`, `candidates`, `current_version` and `errors[{path, code}]`.
- It reads `failure.get('message') or failure.get('error')`; today it reads `message` only (`:1764-1770`).
- New exceptions:
  - `DocFieldsUnavailable(RuntimeError)`;
  - `DocFieldsError(GraphError)`;
  - `QueryRejected(Exception)` with `status`, `error_code` and `details`.

**New `_request_quiet(method, endpoint, data)`:**
- One attempt with the assertion attached.
- No tenacity and no ERROR log of the body.
- Returns the `Response` without raising.
- Used only by the probe.

**New helper `_doc_fields_call(method, path, *, data=None, write=False)`:**
- Reads go through `_make_request`; writes go through `_request_no_retry`.
- Path segments are quoted with `quote(x, safe="")`.

| Answer | Result |
|---|---|
| 404 `NOT_FOUND` | None |
| 404 `pack_not_found` | `DocFieldsError` |
| Any other 404 | `DocFieldsUnavailable` |
| Other ≥ 400 | `DocFieldsError` with `details` |

**Methods.** Every body carries the assertion through `_with_principal`. **Pointers always travel in the JSON body**, never in `params` (§5 S2).

| Method | Request |
|---|---|
| `doc_fields_probe() -> "off"\|"values"\|"filters"\|"unknown"` | `POST /secured/graph/doc-values/find {}` via `_request_quiet` |
| `doc_fields()` | `GET /secured/graph/doc-fields` |
| `create_doc_field(defn)` | `POST /secured/graph/doc-fields` (write) |
| `update_doc_field(field_id, changes)` | `PATCH …/doc-fields/<id>` (write) |
| `deprecate_doc_field(field_id)` | `POST …/doc-fields/<id>/deprecate` (write) |
| `doc_field_packs()` | `GET …/doc-fields/packs` |
| `install_doc_field_pack(pack)` | `POST …/doc-fields/packs/<pack>/install` (write) |
| `doc_field_settings()` / `set_doc_field_settings(unknown_keys=None, date_order=None)` | `GET` / `PUT …/doc-fields/settings` |
| `doc_field_rules()` / `put_doc_field_rule(prefix, values)` / `retire_doc_field_rule(prefix)` | `GET` / `PUT` / `DELETE /secured/graph/doc-field-rules`. All three need the tenant-admin group or full clearance (`KB:api/doc_fields_api.py:802-814`). DELETE carries a JSON body; a missing rule returns None |
| `doc_values(pointer) -> dict \| None` | `GET /secured/graph/doc-values`, body `{"pointer"}` |
| `patch_doc_values(pointer, if_version, *, set=None, unset=None, add=None, remove=None, fields_strict=False, actor_ref=None)` | `PATCH /secured/graph/doc-values` (write) |
| `find_doc_values(where, *, sort=None, limit=50, after=None, return_fields=None)` | `POST /secured/graph/doc-values/find` |

- The routes are listed at `KB:api/doc_fields_api.py:109-117`.
- `graph_nodes` (`:1829-1844`) is unchanged. Doc-fields code never passes `q` (D10); a test asserts that no Knovas request URL built by doc-fields code contains `q=`.

**Search:**
- New signature: `search_documents(self, query, limit=20, filters=None, *, where=None, return_fields=None)` (`:1670-1709`).
  - In legacy mode it raises `ValueError` when `where` or `return_fields` is given (D13).
- `_secured_query_request_body` (`:1092-1126`):
  - adds `where` and `return_fields` only when they are not None;
  - **clamps `limit` to 1..50**, because the server answers 422 above 50 (`KB:services/query_pipeline.py:91-103`).
- `_unwrap_secured_query_response` (`:233-269`) and the `semantix_meta` built at `:2444-2450` also keep: `no_strong_matches`, `no_results_reason`, `relevance_gate_applied`, `meta.degraded_to_bm25`, `where` (`applied`, `clauses`, `resolved`, `may_be_partial`) and `return_fields.applied`.
- `_secured_query_hit_to_row` (`:832-879`) copies `fields`, `relevance_tier` and `score_mode`.
- **Title rule** (`doc_fields_view.display_title`): `fields.title` becomes the row `title`, with `title_from_values=True`, only when all of these hold:
  - it is a string of at most 100 characters;
  - it is neither the pointer's basename nor its stem (case-insensitive).
  - Otherwise today's `_display_title_for_hit` (`:285-300`) runs on the hit title as before.
  - Reason: the anchor title falls back to the path basename (`KB:services/knowledge_graph/doc_fields/return_fields.py:102-106`). The RC sends `file_path.name` when no title was extracted (`RC:src/sync/knovas_uploader.py:228`). Extracted titles can be run-on text.
- An `HTTPError` on a request that carried `where` or `return_fields`, whose body has a doc-fields `error_code`, raises `QueryRejected`. All other errors are unchanged, so unfiltered queries behave exactly as today.

**Not built:** the Platform-upload `fields` pass-through.
- `sync_single_document` (`:1634`) has no caller in `src/`, and no Platform upload form exists.
- `knovas_extract_upload.py` and `_sync_single_document_secured` stay unchanged (§8).

### 4.2 Shared modules (WP-P1)

**`PL:src/doc_fields_capability.py`** holds the process-wide runtime hooks. Wave-2 packages import these and change no other package's signatures.

| Name | Behaviour |
|---|---|
| `Capability` | Enum: `off`, `values`, `listing_only`, `filters`, `unknown` |
| `CapabilityCache(ttl, unknown_ttl, calibration_recheck, clock)` | `get(client)` and `observe(signal)`, with the signals from §2.2 |
| `shared_cache(config) -> CapabilityCache` | The process singleton |
| `capability_for(client) -> Capability` | `off` when `ui: off` or the client is not in secured mode; otherwise `shared_cache().get(client)` |
| `registry_for(client, user_key) -> list` | Per-user cache of `sanitize_registry(client.doc_fields())` for `registry_cache_seconds`, because the registry depends on who asks (`target_type_hidden`). Also `invalidate(user_key=None)` |
| `entity_names_for(client, user_key, field) -> list[str] \| None` | Per-user cache of the names from `client.graph_nodes(node_type_id=<target>)`, fetched **without `q`**, for `registry_cache_seconds`. Returns None (free text only) when more than 5000 nodes come back, when the field has no visible target type, or when the field is `sensitivity: special` |

**`PL:src/doc_fields_view.py`** (pure functions):

| Function | Purpose |
|---|---|
| `sanitize_registry(fields, lang="de")` | Returns `[{key, label, datatype, cardinality, display, facet, sensitivity, enum:[{code,label}], has_target, status}]`. Label preference: de, fr, it, en, then the key |
| `card_return_fields(registry)` | Active fields with `display` set, excluding `sensitivity == "special"`, plus `"title"`; at most 64 (`KB:services/knowledge_graph/doc_fields/planner.py:114`) |
| `format_value(field, value)` | Date by precision (`15.03.2024`, `März 2024`, `Q1 2024`, `2024`); period label; `CHF 1'234.50`; enum label or the code; `Ja`/`Nein`; entity name or `verborgen`; several values joined with `; ` |
| `fields_display(registry, fields)` | `[{key, label, text}]`, without the title |
| `layer_label(layer)` | `Manuell`, `Upload`, `Ordnervorgabe` or `Extrahiert`. Template captures and static values both land in the upload layer with `source_ref = transmission:<id>` (`KB:services/knowledge_graph/doc_fields/commit.py:739`), so there is no "Pfadvorlage" badge |
| `display_title(pointer, hit_title, fields_title)` | The title rule from §4.1 |
| `filter_state(where_sent, meta)` | `none`, `applied`, `partial` or `not_applied` |
| `listing_notice(page)` | The H5 truth table: notice only when `next_after is None and complete is False`; `total_count` only when it is an int |
| `resolved_chips(where_sent, echo, registry)` | Chip text from the person's own submitted values (enum labels for codes), plus "n verknüpfte Einträge" from `resolved_nodes` |
| `validate_where(obj)` | At most 8 keys, keys matching `^[a-z0-9_.\- ]{1,64}$`, depth ≤ 3, JSON ≤ 8 KiB. Raises `ValueError` |
| `can_edit(roles, edit_roles, sensitivity, held, identity_on)` | D12: identity on, not held, a role in `edit_roles`, and `admin` for special fields |
| `METADATA_TARGETS` | `{item: target_key}` as in §3.5 |
| `profile_field_keys(profile_json) -> set[str]` | Static keys, template capture keys (the §3.4 key syntax) and metadata targets of a stored profile, read from the JSON shape §4.8 fixes. Used by WP-P3 and WP-P4 |
| `error_message(code, details, registry)` | German text; names a field by label or key, never echoes a value |
| `no_results_message(reason)` | Per `no_results_reason` |

`.py` files must stay ASCII-only (the `scripts/check_ascii_py.py` convention), so umlauts go in templates or as `\u` escapes.

**`PL:config/config.yaml`**, a new block under `web:` (`:141`):

```yaml
  doc_fields:
    ui: "${DOC_FIELDS_UI:-auto}"            # auto | off
    capability_ttl_seconds: 300
    calibration_recheck_seconds: 3600
    registry_cache_seconds: 300
    edit_roles: "${DOC_FIELDS_EDIT_ROLES:-admin}"   # comma list of admin, approver, ingestion_manager, member
    find_page_size: 50
```

### 4.3 Search (WP-P2): `/api/search` (`PL:src/web_interface/app.py:1724-1915`)

**Request:** `{"query", "limit", "filters", "where"?}`. `where` is validated with `validate_where`, and `limit` is clamped to 1..50.

**Before calling Knovas:**
- `where` is set but the capability is not `filters`: answer without calling Knovas.
  - 409 `filters_need_calibration` when the capability is `listing_only`.
  - Otherwise 409 `filters_unavailable`.
- The capability is `filters` or `listing_only`: always send `return_fields = card_return_fields(registry_for(...))`.
  - A query with `return_fields` alone is not relevance-gated, because the field plan is built only when `where` was sent (`query_pipeline.py:398, 439`).
  - Attach failures come back as `return_fields.applied: false` without an error (`:1409-1430`).

**Errors on a request that carried `where`.** A `QueryRejected` maps as follows. Search is never retried without `where`.

| Server answer | Browser answer |
|---|---|
| 400 `where_unsupported` | 409 `filters_unavailable`; `observe("where_unsupported")` |
| 400 `unknown_field` (with `suggest`), `ambiguous_field` (with `candidates`), `invalid_value`, `type_mismatch`, `where_too_complex` or `restricted_identifier` | 400 `filter_invalid` `{field_label, code, suggest_labels}` |
| 503 `where_requires_calibration` | 409 `filters_need_calibration` and `observe("needs_calibration")`. Message: "Filter in der Suche sind bei Knovas noch nicht eingerichtet (Kalibrierung fehlt)", with "Ohne Filter suchen" as an explicit user action |
| 503 `where_unavailable` | 503 `filter_temporarily_unavailable` |

**Errors on a request without `where`.**
- These codes caused by `return_fields` lead to **one** retry without `return_fields`: `where_unsupported`, `unknown_field`, `ambiguous_field`, `invalid_value`, `where_too_complex`, `where_unavailable`, `doc_fields_*`.
- The response carries `document_fields.fields_unavailable: true`.
- `where_unsupported` also calls `observe("where_unsupported")`. `unknown_field` invalidates the registry cache.
- Nothing was filtered, so the results are honest.
- A test pins that a plain search on a values-mode server, or one whose registry fails, still returns results.

**Response additions:**

```json
"document_fields": {"capability":"filters","filter_state":"applied|partial|none","fields_unavailable":false,
                    "resolved":[{"field":"doc_type","label":"Dokumentart","op":"eq","text":"Rechnung"},
                                {"field":"mandant","label":"Mandant","op":"eq","text":"Muster AG","linked_count":1}]},
"honesty": {"no_strong_matches":false,"no_results_reason":null,"relevance_gate_applied":true,"degraded_to_bm25":false}
```

Each result also gains `fields_display`, `title_from_values` and `relevance_tier`.

- **`not_applied`:** 409 `filter_not_applied` with no results (H2), then `observe("echo_missing")`.
- **Refinement:** H3. Refinement and the supplement are skipped only when `where` was sent.
- **Grants:** the grant block at `:1834-1848` is refactored into `_grant_for_current_user(rows)`, which search and listing both use.
- **Grant check helper:** the check inside `require_readable_document` (`:1326-1395`) is extracted into `_readable_for_current_user(doc_id) -> bool`. That hook and the new POST routes both use it.
- **Title override:** the enrichment title override (`:3510-3511`) does not replace a title for which `title_from_values` is true.
- **Logging:** `:1749` logs `query_len` and `where_keys_count` only. `_log_search_similarity_debug` (`:168-193`) logs counts only.

### 4.4 Listing, registry view, entity names, detail and edit (WP-P2): `PL:src/web_interface/doc_fields_routes.py` (new)

`app.py` registers the module with `attach(app, *, config, client_factory, identity_gate, grant, grant_check, enhance)`. The capability and registry come from `doc_fields_capability` (§4.2).

**Routes.** Every route carrying a pointer or a name is a POST with a JSON body (D6). Every POST route goes through the header CSRF gate (`app.py:1297-1320`).

| Route | Behaviour |
|---|---|
| `GET /api/doc-fields` | Returns `{"capability", "fields": registry_for(...), "editable_keys": [...]}`. When the capability is off, returns `{"capability":"off","fields":[]}` without a Knovas call |
| `POST /api/doc-fields/entities` | Body `{"field", "q"}`, with `q` of at least 2 characters. Only for fields that have a target type and are not special. Names come from `entity_names_for(...)`, filtered in the Platform by casefold prefix, then substring; at most 10. Returns `{"items":[{"name"}]}`. **No Knovas request carries the typed text** |
| `POST /api/documents/find` | Body `{"where", "sort"?: {"field", "order"}, "after"?}`. Requires `filters` or `listing_only`, else 409. Calls `find_doc_values` with `limit = find_page_size` and `return_fields = card_return_fields`. A missing `where.applied` gives 409 `filter_not_applied`. Rows are mapped to search-row shape (`doc_id = path = pointer`, `title`, `fields_display`), passed through `enhance({"results": rows})` (a closure over `_enhance_search_results(results, file_handler, config, query='')`, `app.py:3470-3475`), then through `grant(rows)`. Response `{"success":true, "documents", "next_after", "complete", "total_count"?, "notice", "document_fields": {"filter_state", "resolved"}}`, with `notice` from `listing_notice` |
| `POST /api/document-fields/read` | Body `{"doc_id"}`. The pointer is checked with `grant_check(doc_id)` (§4.3), then sent to Knovas verbatim (`knovas_pointer_for`, below). Returns `{"pointer", "document_uuid", "version", "title", "title_source", "description", "held", "fields": [{key, label, text, layer, layer_label, verified, changed_at?}], "warnings": [codes], "editable_keys"}`. `changed_at` is the manual layer's `created_at` (`KB:services/knowledge_graph/doc_fields/values_service.py:330-350`) |
| `POST /api/document-fields/edit` | Requires identity and a role in `edit_roles`; keys of special fields require `admin`. Grant check as above. Body `{"doc_id", "if_version", "set"?, "unset"?, "add"?, "remove"?}`; title and description go inside `set`. Calls `patch_doc_values(..., fields_strict=False, actor_ref=f"platform-user:{user.id}")` and returns the server's `warnings[]` per key. Then reads again, because a PATCH that names no typed key returns `fields:{}`. Writes the audit row `document.values_edited` (§4.6) |

**`knovas_pointer_for(doc_id)`:**
- The browser always sends the row's `doc_id`. For search and listing rows that is the Knovas pointer (`PL:src/knovas_client.py:845-849`).
- Search also grants the mount-relative spelling (`app.py:1838-1847`). Such a spelling is answered 404 rather than guessed into a pointer.
- `document_grants.normalize_pointer` (`PL:src/document_grants.py:59-66`) is used only for the grant check.

**Error mapping:**

| Server answer | Browser answer |
|---|---|
| 409 `version_conflict` | 409 with `current_version`; the UI reloads and says "inzwischen geändert", never overwriting automatically |
| 403 `change_not_authorized` | Panel turns read-only |
| 409 `anchor_quarantined` | "Werte zurückgehalten" |
| 400 with a field `path` | Inline message at that field; nothing saved |
| 400 `invalid_value` with `path: "pointer"` on read | "Knovas-Update nötig". A server without S2 reads the pointer only from the query string. There is no fallback to the query string |

The `document_grants.py` docstring and the comment at `app.py:1835-1837` change to "search or listing". Both list only what Knovas returned under the person's own assertion.

### 4.5 Frontend (WP-P2)

**`static/js/app.js`:**
- `performSearch` (`:1075-1129`) sends `where`.
- `loadMore` (`:1137-1142`) clamps to 50. Under a filter it offers "Liste anzeigen" instead.
- `createDocumentCard` (`:1361-1430`) renders `fields_display` chips with `textContent` and adds a borderline badge ("unsicherer Treffer").
- `displayTitle` (`:1259-1268`) uses the server row's `title` as is when `title_from_values` is true.
- `showEmptyState` (`:1702-1716`) shows a text per `no_results_reason`:
  - `no_candidates`: "Nichts in den für Sie sichtbaren Dokumenten erwähnt das."
  - `below_relevance_floor`: "Nichts beantwortet das gut genug."
  - `empty_where` / `empty_scope`: "Kein für Sie sichtbares Dokument erfüllt diese Filter." with "Filter entfernen".
  - Any other reason: the generic text.
  - When `degraded_to_bm25` is true, it adds "eingeschränkte Suchqualität".
- `_renderDocData` (`:736-754`) lazily loads the fields panel when the sidebar opens, through `POST /api/document-fields/read`.

**New `static/js/doc_fields.js`:**
- **Filter rail** (capability `filters` only), built from the `facet` fields:
  - enum: a select, sending a code or a list of codes;
  - entity: an autocomplete via `POST /api/doc-fields/entities`, 300 ms debounce, sending `{"name"}`. Special fields and fields without suggestions get free text;
  - date or period: a text input sending `eq`; the server parses "GJ 2024" or "Q1 2024". An advanced from/to sends `{"gte","lte"}`;
  - bool: a select.
- **Display:** "Verstanden als" chips from `resolved`; the `partial` hint.
- **Listing view** (`filters` or `listing_only`):
  - An empty question with filters set goes to `/api/documents/find`.
  - Paging uses `next_after`. An empty page with a non-null cursor keeps paging.
  - The notice comes from the response; it is never computed in JS.
  - A sort on a deadline-like field shows the H9 banner.
- **Edit panel:**
  - Fields from `editable_keys` only.
  - Warnings per key: "nicht verknüpft", "Datum mehrdeutig – bitte prüfen".
  - Hints "Titel wird angezeigt, nicht durchsucht" and, for `privileged`, "Kennzeichnung, keine Zugriffsbeschränkung".
  - "zuletzt manuell geändert am …".
- No `innerHTML` with server data.

**`templates/index.html`:** a container for the filter rail at `:24-57` next to `resultsNotice`, a fields section at `:135-138`, and the script tag.

**`static/css/style.css`:** the styles for the above.

**Cortex link (stretch):**
- `PL:src/ontology_graph.py:316` (`entity_detail`) adds `doc_field_links: [{key, label}]` for registry fields whose target type equals the node's type, only when the capability is `filters` or `listing_only`.
- `static/js/ontology.js:1055-1101` renders "Dokumente mit <Feld> = <Name>".
  - The link writes `{field, name}` to `sessionStorage["knovas.docFieldsHandoff"]` and opens `/?list=1`, so no name goes into the URL.
  - `doc_fields.js` reads and deletes the entry.
- Cortex titles are left alone, because `ontology.js:1677` uses the title as the doc_id.

### 4.6 Admin tab "Dokumentfelder" (WP-P3)

**Placement and visibility:**
- New module `PL:src/web_interface/admin_doc_fields.py`, attached in `admin.py` after `:277-286`, the same way as `attach_document_routes`.
- The tab goes in `templates/_admin_tabs.html` (`:10-35`). It is visible to `admin` when the capability is not off.
- **No change to `app.py`.** Admin pages build their context from `page_context()` (`PL:src/web_interface/app.py:1698-1704`, owned by WP-P2).
  - `admin.py` instead registers a blueprint `context_processor` that injects `doc_fields_capability` lazily from `capability_for(client_factory())`.
  - The signatures of `create_admin_blueprint`, `attach_document_routes` and `attach_ingestion_routes` stay as they are (`admin.py:36-37, 277-286, 309-327`).

**Routes.** All are behind `require_admin` and an in-handler `csrf_valid(form csrf_token)` check, since admin routes are exempt from the header gate (`app.py:1314`):

| Route | Purpose |
|---|---|
| `GET /admin/doc-fields` | Page: registry table (labels DE/FR/IT, aliases, status, origin, warnings), packs (`key`, `version`, `installed`), settings, folder rules |
| `POST /admin/doc-fields/create` | New field. Datatype, cardinality, enum codes with labels, entity target chosen from `graph_node_types`, display, facet, sensitivity |
| `POST /admin/doc-fields/<field_id>/update` | Change a field |
| `POST /admin/doc-fields/<field_id>/deprecate` | Deprecate a field |
| `POST /admin/doc-fields/packs/<pack>/install` | Install a pack; shows `skipped` and `target_type_missing:<key>` |
| `POST /admin/doc-fields/settings` | `unknown_keys` and `date_order`. Setting `unknown_keys=reject` shows the keys of the current ingestion profile that the registry would not know (`profile_field_keys`) |
| `POST /admin/doc-fields/rules/save` | Save a folder rule |
| `POST /admin/doc-fields/rules/delete` | Retire a folder rule |

**Predicting the authority check:**
- A write needs the tenant-admin group (`is_admin` from `client.access_groups()`, `knovas_client.py:2152`, intersected with the person's groups) or full clearance.
- If the prediction fails, the forms are read-only with "Nur Mitglieder der Knovas-Administratorgruppe".
- The rules **list** needs the same clearance (`KB:api/doc_fields_api.py:814`). On 403 the rules section says why instead of failing.
- The server decides: 403 `registry_write_requires_full_clearance` gets an explaining message.
- Other server errors also get German texts: 422 `key_looks_personal`, 409 `field_key_exists`, `field_type_locked`, `field_cap_reached`.

**Fields in use:**
- Update and deprecate warn when the key appears in `profile_field_keys(IngestionProfileRepository(conn).current())`. The warning reads "wird von der Ingestion-Konfiguration verwendet; Uploads mit diesem Schlüssel werden danach nicht mehr übernommen", and the action requires a confirmation checkbox.
  - Deprecated keys still match queries by exact key (`KB:services/knowledge_graph/doc_fields/planner.py:365-376`). Uploads go through `resolve_key`, so they are refused.
- After any registry write, when an RC is configured, the page offers "Abgelehnte Uploads erneut senden": `rc_client.requeue_doc_fields("refused")`, called through `getattr` because WP-P4 adds the method.

**Folder rules:**
- **Picking the folder:**
  - With an RC configured, the folder is picked from the RC tree through `/admin/ingestion/folders` (`admin_ingestion.py:374-382`). Those routes exist only when `rc_client_factory` is set (`admin.py:309-327`).
  - Without an RC, a text input takes the pointer prefix, with the same validation.
- **New helper** `PL:src/identity/rc_pointers.py`: `rc_pointer_prefix(identifier_prefix, source_path, folder_path) -> str`.
  - It returns `identifier_prefix.strip() + "/" + relpath(folder, source).replace("\\","/").strip("/") + "/"`.
  - A source root gives `identifier_prefix + "/"`.
  - A folder outside every source is refused.
- A trailing `/` is mandatory, because matching is a raw `startswith` (`KB:services/knowledge_graph/doc_fields/rules.py:93`).
- A profile with more than one source requires the confirmation checkbox "gilt in allen Quellen mit diesem Unterordner", because the source folder is not in the pointer.
- Values come from registry-typed inputs; entity values are names.
- The response shows `reapply_job_id` as "wird angewendet (meist Minuten)" and is never reported as done.
- `retire` returning None is shown as "keine aktive Vorgabe".

**Audit:** `identity/audit.py:23-62` `record(...)` is reused with no change to `audit.py`.

| Action | Target | Detail recorded (never values) |
|---|---|---|
| `doc_field.created`, `doc_field.updated`, `doc_field.deprecated` | `doc_field`, field id | `{key, datatype}` |
| `doc_field_pack.installed` | `doc_field_pack`, pack key | `{pack, installed_count, skipped_count}` |
| `doc_field_settings.changed` | `doc_field_settings`, `-` | `{unknown_keys, date_order}` |
| `doc_field_rule.saved`, `doc_field_rule.retired` | `doc_field_rule`, the rule `id` from the response (`rules.py:250-256`), **never the prefix** | `{prefix_depth, keys}` |
| `document.values_edited` (also used by WP-P2) | `document`, `document_uuid` | `{"keys", "ops":{set,unset,add,remove}, "title_changed", "description_changed", "version_from", "version_to", "warning_codes", "code"?}`; `outcome` = `ok`, or `denied` with Knovas's refusal code in `code` (`version_conflict`, `change_not_authorized`, `anchor_quarantined`) — see §10 |

### 4.7 Documents admin and System tab (WP-P3)

**Fields drawer on each row** (`admin_documents.py:205-263`, `templates/admin_documents.html:87-131`, `static/js/admin_documents.js:63-80`):
- Opened on click only, so there is no `graph_read` per row.
- Uses the JSON routes `POST /admin/documents/fields/read` and `POST /admin/documents/fields/edit`. Both take the pointer in the body and check the `X-CSRF-Token` header themselves.
- Shows layers with badges and `changed_at`, warnings by code, "zurück zum Upload-/Ordnerwert" (`set: null`), and a pointer picker when `pointers[]` has more than one entry.
- Edits use `fields_strict: false` and show the returned warnings.
- A held document is read-only with an explanation.

**Feldfilter mode** (filters `:72-79`):
- Field and value pairs build `where`, plus a sort on a date field.
- Uses `find_doc_values`, with keyset paging.
- Checks `where.applied`.
- The notice and `total_count` follow `listing_notice` (H5).
- Visible when the capability is `filters` or `listing_only`.

**System check** (`admin_system.py:54-235`):
- "Dokumentfelder" shows one of four states, plus the number of fields and the installed packs:
  - `aus`;
  - `Werte (ohne Filter)`;
  - `Werte + Liste (Filter in der Suche: Kalibrierung bei Knovas fehlt)`;
  - `Werte + Filter`.
- The RC check looks up `health` or `get_health` by name (`:229-234`). It starts working once WP-P4 adds `KnovasConnectorClient.health`.
- Reports `rc.capabilities()` (with `getattr`; interface from WP-P4).

**People tab** (`admin.py:78-97`, `templates/admin_people.html`): an "Administratorgruppe bei Knovas" badge, as a hint only.

### 4.8 Ingestion profiles → RC (WP-P4)

**Data model** (`PL:src/identity/ingestion_compiler.py:78-91`):
- `SourceFolder` gains three fields:
  - `fields: tuple[tuple[str, Any], ...] = ()`: sorted pairs. A dict default raises `ValueError` on a dataclass and is unhashable on a frozen one;
  - `field_templates: tuple[str, ...] = ()`;
  - `metadata_fields: tuple[str, ...] = ()`.
- `profile_to_json` and `profile_from_json` (`ingestion_profiles.py:21-38`) handle them:
  - `profile_to_json` writes `fields` as a JSON object;
  - `profile_from_json` reads the object back into sorted pairs, with defaults;
  - the round trip is lossless.

**Compilation:**
- `_compile_sync_request` (`ingestion_compiler.py:222-281`) emits `sources[].fields`, `field_templates` and `metadata_fields` **only when not empty**, so profiles without fields compile byte-identically to today.
- `compile_profile` (`:302-320`) validates against the shipped schema as today.

**Validation at save** (`validate_profile_fields(profile, registry)`, with the registry from `registry_for`):
- Keys must exist and be active.
- Enum values are given as code or label and stored as the code.
- Values are at most 256 characters; at most 64 keys and 8 templates.
- Templates are compiled with `PL:src/identity/field_templates.py`, the Platform's implementation of the §3.4 grammar, checked against `PL:src/identity/rc_contracts/vectors/field_templates.json`.
- Capture keys must be registered.
- Metadata targets must be registered and active.
- Key conflicts with folder rules (the same key as a static value or capture and in a rule under the profile prefix) give a warning.
  - This needs `doc_field_rules()`. On 403 the check is skipped with the note "Ordnervorgaben nicht prüfbar (nur Knovas-Administratorgruppe)".

**Gating:**
- The inputs show only when the capability is not off.
- Save and push (including the guarded executor `execute_ingestion_change`) require that `rc.capabilities()` contains `source_fields_v1`, plus `field_templates_v1` or `metadata_fields_v1` when those keys are used. Otherwise the error is "Knovas Connector zu alt – bitte aktualisieren".

**Re-upload confirmation.** When a save changes any source's static values, templates or metadata items, the form shows the cost and requires a confirmation checkbox:
- The text: "Alle Dokumente der Quelle(n) <Pfad> werden erneut gesendet – je ein verrechneter Upload mit erneuter Texterkennung. Höchstens <document_sync.total> Dokumente; bei <per_cycle> pro Durchlauf und Zeitplan <Preset> ca. <ETA>."
- The ETA is computed as `ceil(total / per_cycle)` × the preset cadence.
- The form recommends folder rules for values that do not depend on the source.

**Preview:** template captures over the paths from `/admin/ingestion/preview` (`admin_ingestion.py:384-412`). Shown only in the authenticated admin page.

**Redaction:**
- `redact_for_support` (`ingestion_compiler.py:322-345`) and the approvals `_summary` (`admin_approvals.py:109-135`) show only counts ("3 Ordner mit Feldern, 1 Pfadvorlage").
- The `ingestion.profile_pushed` audit (`admin_ingestion.py:278-281`) records counts only.

**Status bar** (`admin_ingestion.py:331-363`):
- Renders the RC `doc_fields` block, e.g.:
  - "Felder bei 140 Uploads nicht übernommen: Funktion bei Knovas aus"
  - "doc_type: 12× invalid_value"
- Shows `document_sync.fields_changed` and `pending_reupload` with an ETA of `ceil(pending_reupload / per_cycle)` × cadence, plus `rel_collisions` and `reupload_failed`.
- Offers "Erneut senden" (`rc.requeue_doc_fields(outcome)`) in three cases:
  - for `not_accepted` once the capability is at least `values`;
  - for `refused`;
  - for `reupload_failed`.

**`PL:src/knovas_connector_client.py`:**
- `health()` is a one-line alias of `status()` (`:105-106`), because `admin_system` looks up `health` by name.
- `capabilities() -> frozenset[str]`: read from `status()["capabilities"]`; empty on an error or an old RC.
- `requeue_doc_fields(outcome) -> int`.

**Labels:**
- The profile "Beschreibung" label becomes "wird jedem Dokument als Beschreibung mitgegeben" (`templates/admin_ingestion.html:81-83`); the behaviour is unchanged.
- A profile with more than one source warns that identical relative paths share a pointer, and that the first source governs its fields.

### 4.9 Platform access logs (WP-C)

- `PL:nginx/docbridge-web-local.conf`, which Compose mounts at `KC:docker-compose.yml:177`, and `PL:nginx/docbridge-web.conf` have no `access_log` directive. nginx:alpine therefore logs the full request line, URI and query string included.
- Both files gain:
  - `log_format knovas_privacy '$time_iso8601 $request_method $status $body_bytes_sent $request_time';`
  - `access_log /dev/stdout knovas_privacy;`
- This keeps pointers out of the access log, including those of existing preview routes.
- App-level pointer logging that predates this work stays out of scope (§8).

---

## 5. Server follow-ups (KnowledgeBase): minimal, inside the existing doc-fields gates

**S1 (needed for RC entity fields in BROKERED tenants).**
- **Today:**
  - The RC sends no assertion. In a BROKERED tenant, `principal_of()` raises inside init field processing in two places: when an entity value is resolved (`KB:api/secure_api.py:1401`), and when the tenant registers unknown keys (`:1469`).
  - The result is 401 `assertion_rejected` and no transmission (`KB:services/rbac/principal_resolver.py:160-167`, caught at `secure_api.py:1831-1834`).
- **Change, in `_parse_doc_fields_upload` (`:1281-1530`):**
  - When the posture is BROKERED and the body has no `principal_assertion`, do **not** resolve a principal.
  - Entity items become **unlinked name rows** with warning `unresolved_entity`.
  - A `{node_id}` item is dropped with `invalid_value`.
  - Register mode is treated as `ignore`.
  - With an assertion present, behaviour is unchanged.
- **Why it is safe:** this is strictly narrower than the existing unasserted path. Nothing is linked or registered, and no assertion is consumed.
- **Scope:** the `access_groups` 401 on the same posture (`secure_api.py:1686-1712`) exists today and is **out of scope** (§8). The RC classifies it correctly (§3.6).
- **Test:** `KB:../tests/test_init_doc_fields_brokered_unasserted.py`.
- **Process:** follow `KBD:Docs/01_SYSTEM/Feature_Design_Workflow.md`. If an Alloy fact in `models/alloy/**` covers the init principal, add a precondition test.

**S2. `GET /secured/graph/doc-values` reads `pointer` from the JSON body first, then from the query string** (`KB:api/doc_fields_api.py:498`).
- Why: the prod gateway logs `$request`, query string included (`infra/kubernetes/overlays/knovas-aks-prod/mtls-gateway-prod-configmap.yaml:23-31`), and pointers carry client names.
- The Platform sends the pointer only in the body; the assertion is already read from the body first (`KB:api/graph_api.py:571-573`).
- Every flag is off everywhere today, so S2 must ship before any tenant is enabled.
- **Test:** `KB:../tests/test_doc_values_pointer_in_body.py`.

**S3. Developer Kit corrections** in `KBD:Knovas_Developer_Kit/api/Secure_API.md` and `Knowledge_Graph_API.md`. No route changes; `scripts/check_api_doc_coverage.py` stays green.
1. Replace the `where` example (`Secure_API.md:512-523`) with keys from `core` and `legal_ch`.
2. `return_fields` alone also returns `where_unsupported`.
3. The brokered behaviour of init `fields` (after S1).
4. The PATCH response has `fields:{}` when no typed key was named; GET again.
5. The feature-off answer is 404 `HTTP_404`, and the `find {}` probe recipe. `find` does not reveal missing calibration; a `where` query answers 503 `where_requires_calibration`.
6. `DELETE /doc-field-rules` takes a JSON body. All three rule methods need the tenant-admin group or full clearance.
7. The pack listing returns `{key, version, installed, installed_version}`.
8. The `find` cursor does not bind `where`; keep `where` constant.
9. The S2 pointer-in-body option.
10. `complete` is false on every page with a successor; `total_count` is first-page only and null on overflow.
11. `fields_strict: false` keeps unresolved names unlinked with warnings; `true` refuses them with 422.

**S4. Wire goldens.**
- `KB:../tests/test_doc_fields_wire_goldens.py` asserts real route answers against JSON files in `KBD:Knovas_Developer_Kit/api/examples/doc_fields/*.json`. It covers:
  - the probe in off, values and filters;
  - the init echo;
  - the query `where` echo with `return_fields`;
  - `where_unsupported`, `where_requires_calibration`, `version_conflict` and `HTTP_404`;
  - a find page with `next_after`, `complete` and `total_count`.
- These files are what the mock is checked against (WP-I copies them).

**Also:** point `KBD:ModernDocs/plans/2026-10-01-document-fields.md:877` to this spec.

**Deliberately not requested from the server:**
- a `may_edit` hint, a capability endpoint, or pack labels;
- effective `fields` in the PATCH response;
- a body-borne `q` for `GET /secured/graph/nodes`: the Platform filters names itself (D10);
- `title_source` in `return_fields`: the title rule in §4.1 replaces it.

---

## 6. Security and privacy checklist

- [ ] No field value, template capture, title, description, pointer, `rel`, entity name or query text in any log line, metric label, audit `detail`, support JSON, approvals summary **or URL** of the new code. Keys, codes, counts and versions only. A `caplog` test per component asserts this with sentinel values.
- [ ] No Knovas request URL built by doc-fields code contains `q=` or `pointer=`. Asserted on the `FakeSession`.
- [ ] Every new Platform route that carries a pointer or a name is a POST with a JSON body. Platform nginx logs no URIs (§4.9). The Cortex handoff uses `sessionStorage`.
- [ ] RC metric labels come only from closed sets (`/metrics` is unauthenticated).
- [ ] `/sync/status` (authenticated) shows keys and codes, never values. `/health` is unchanged. RC schema errors under the new keys carry no instance values.
- [ ] Every new Platform write route checks CSRF:
  - Non-admin routes go through the header gate (`app.py:1297-1320`).
  - Admin routes check in the handler (`csrf_valid`). JSON admin routes check `X-CSRF-Token`. A test runs per route.
- [ ] Platform authorization before any call to a Knovas write route:
  - Registry, pack, settings and rule writes: `require_admin`.
  - Value edits: identity enabled, a signed-in user, a role in `edit_roles` (`admin` for special fields), and a live grant through `_readable_for_current_user`.
  - Knovas remains the authority (403 is handled).
- [ ] Every Platform→Knovas call carries `principal_assertion` (`_with_principal`). The browser can never supply `access_groups` (`app.py:1233-1247`). Pointers travel only in the body.
- [ ] Writes are sent once (`_request_no_retry`). The probe is quiet (`_request_quiet`). `actor_ref` is `platform-user:<opaque id>`, never an e-mail address.
- [ ] Templates are autoescaped. New JS uses `textContent` and never `innerHTML` with server data; a static test greps for this.
- [ ] `where` from the browser is bounded with `validate_where`. The registry and entity-name caches are per user, never shared across users.
- [ ] Special-sensitivity fields: never in `card_return_fields`, no autocomplete, `admin`-only edits.
- [ ] Config and doc examples use only placeholder names ("Muster AG", "Beispiel GmbH"), never real people or patients.
- [ ] No `node_id` from the RC (D4). No auto-registration through mapped metadata keys.
- [ ] Filters are honest (H1–H9). Gated features are never shown as available.
- [ ] New `.py` files are ASCII-only.
- [ ] Tests run with `RC_SYNC_STATE_PATH` set to a temporary path, so the git-tracked `RC:.rc-sync-state.db` is never rewritten.

---

## 7. Work packages (each file has exactly one owner)

**Conventions:**
- Branch `cl/document-fields-integration-<rand>` in knovascomponents, with a PR to `main`. Expect rebases against open PRs #21 and #22, which touch `knovas_client.py`, `app.py` and `tests/conftest.py`.
- KnowledgeBase work goes on its own branch.
- Commit prefixes: `rc:`, `platform:`, `rc+platform:`, `docs:`, `ci:`, with the trailers from CLAUDE.md.
- WPs in the same wave do not depend on each other.
  - Wave 2 consumes only the wave-1 interfaces listed here: `doc_fields_capability`, `doc_fields_view`, client methods, the schemas, the mock and the RC payload library.
  - **No wave-2 WP changes a signature defined in a file another WP owns.** In particular, `create_admin_blueprint`, `attach_document_routes`, `attach_ingestion_routes` and the `page_context` lambda stay unchanged.

### Wave 1 (four in parallel)

#### WP-C: Contracts, mock Knovas API, CI baseline, Platform access logs

**Goal:** the shared wire contracts, a mock that emulates every server state, a green CI baseline, and access logs without URIs.

**Files owned:**
- `RC:contracts/sync_request.schema.json`
- `RC:contracts/sync_response.schema.json` (top-level `doc_fields`, `document_sync.fields_changed`)
- `PL:src/identity/rc_contracts/sync_request.schema.json`
- `MOCK:app.py`
- `MOCK:testing.py` (new)
- `MOCK:goldens/*.json` (new, provisional; replaced in WP-I)
- `MOCK:tests/test_mock_doc_fields.py` (new)
- `KC:docker-compose.yml`: the `knovas-mock` service env `MOCK_DOC_FIELDS: ${MOCK_DOC_FIELDS:-off}` (`:194-212`)
- `KC:.github/workflows/ci.yml`
- `RC:Dockerfile`: only the `KNOVAS_EXTRACT_REF` default
- `RC:tests/test_sync_source_fields_schema.py` (new)
- `PL:nginx/docbridge-web-local.conf`, `PL:nginx/docbridge-web.conf`
- `PL:tests/test_nginx_privacy_log.py` (new)

**Mock (`MOCK:app.py`):**
- `create_app(doc_fields=os.environ.get("MOCK_DOC_FIELDS","off"), calibrated=True, refuse_init_fields=None, brokered=False)`. The module keeps `app = create_app()`.
- **All state lives per app instance.** Today `DOCUMENTS` and `_STORED_POINTERS` are module globals (`MOCK:app.py:9-36, 96`).
- A JSON 404 handler `{"status":"error","error":"Not Found","error_code":"HTTP_404"}` for unknown routes.
- New routes for the new UI:
  - `GET /secured/graph/nodes`, filtered by `node_type_id`;
  - `GET /secured/graph/node-types`;
  - `GET /secured/access_groups` (with `is_admin`).
- `/secured/query` with `limit` > 50 answers 422.

| Mode | Behaviour |
|---|---|
| `off` | Today's behaviour. Every `/secured/graph/doc-*` route answers `HTTP_404`. `fields`, `where` and `return_fields` are ignored without an echo. |
| `values` | The init echo `{staged, mapped_keys, unknown_keys, warnings, suggest?}`. GET/PATCH doc-values with `if_version` and 409 `version_conflict` (`current_version`). PATCH returns `warnings[]`. Registry (core seeded), packs, settings and rules routes. Query with `where` or `return_fields`: 400 `where_unsupported`. `find`: 400 `where_unsupported`. |
| `filters` | Query `where` matches stored document `fields` by **exact equality only**: codes and strings as stored, entity names casefolded. The mock does not emulate the normaliser (dates, periods, fiscal years). Echo per §3 of the contract digest. Each hit gets `fields`. `no_strong_matches`, `relevance_gate_applied` and `no_results_reason` are set. `find` pages with `next_after`, `complete` (false on non-last pages) and `total_count` (first page only). Probe `{}` returns 400 `invalid_value` with path `where`. With `calibrated=False`, a query with `where` answers 503 `where_requires_calibration`, while `find` and `return_fields` work. |

- `refuse_init_fields="<status>:<code>"` forces that refusal when `fields` is present.
- `brokered=True` returns 401 `assertion_rejected` for `access_groups` without an assertion (and, before S1, for entity keys sent without one; see §10).
- GET doc-values reads the pointer from the body; with the pointer only in the query string it answers 400 `invalid_value` `path: "pointer"`.

**Test helper (`MOCK:testing.py`):**
- `load_mock_app(**kw)`.
- `WsgiSession(app)`: a `requests.Session` with an adapter that dispatches to `app.test_client()`. Platform tests construct the client with an `https://` base URL and inject this session, so the mTLS-https rule (`PL:src/knovas_client.py:1015-1023`) is never relaxed.
- `wsgi_request(app)`: a drop-in replacement for `requests.request`.
- Component tests load it with `importlib.util.spec_from_file_location` and call `pytest.skip` when it is absent, following `test_ingestion_compiler.py:279-285`.

**CI (`ci.yml`):**
- Install knovas-extract in the RC job from a pinned sha, the current knovas-extract-python `main` (`:119-128`), and use the same sha as the `RC:Dockerfile` default. This fixes the red main build (run 36973032452).
- Add `RC_SYNC_STATE_PATH: ${{ runner.temp }}/rc-state/.rc-sync-state.json` to the RC test env (`:130-148`).
- Add a step `pytest mock_knovas_api/tests` to the Platform job. The job's default working directory is `KnovasPlatform` (`:32-34`).
- Keep the contract-schema step (`:150-153`) as it is; the vectors live in `contracts/vectors/`.

**Tests:**
- The schema accepts valid `fields`, `field_templates` and `metadata_fields`.
- The schema rejects system keys, more than 64 keys, values over 256 characters, more than 32 items, objects and unknown metadata items (including `email_message_id`).
- Bodies without the new keys still validate. A response with `document_sync.fields_changed` validates.
- The mock answers per mode, including the probe, the echo shapes, calibration, the JSON 404, `limit` > 50, and per-app isolation (two `create_app()` share nothing).
- Both nginx confs define `knovas_privacy` without `$request`, `$request_uri`, `$uri` or `$args`.

**Acceptance:**
- Both schema copies are byte-identical, and the existing Platform suite is green.
- The RC suite is green in CI with the pinned extractor.
- The mock in `off` mode answers `/secured/query` and init exactly as before.

#### WP-RC1: Knovas Connector field-payload library (pure functions, no I/O)

**Goal:** everything in §3.3–3.6 that can be tested without the network.

**Files owned:**
- `RC:src/sync/doc_fields_payload.py` (new): `SourceSpec`, `assemble`, `config_digest`, `parse_init_echo`, `classify_init_refusal`, `FieldsRecord`, `FIELD_REFUSAL_CODES`, `TRANSIENT_REFUSAL_CODES`, `FIELDS_PAYLOAD_VERSION`
- `RC:src/sync/field_templates.py` (new)
- `RC:src/sync/metadata_fields.py` (new)
- `RC:src/sync/document_text.py`: `source_metadata`
- `RC:contracts/vectors/field_templates.json` (new)
- `RC:tests/unit/test_field_templates.py`, `test_metadata_fields.py`, `test_doc_fields_payload.py`, `test_document_text_source_metadata.py` (all new)

**Tests:**
- Template vectors, including `/**` at depth zero.
- Precedence: capture over static over metadata.
- Caps: 64 keys, 32 items, 256 characters, 16 KiB; system keys dropped.
- Digest: stable, `""` when empty, changes when config changes.
- `assemble` returns `{}` for a PDF in an e-mail-metadata-only source.
- Metadata:
  - A test per item.
  - `document_date` only for eml and msg, never from mtime, from M365 or from PDF/DOCX `created`.
  - `language` from `Content-Language` for eml; none for msg.
- Junk-author filter.
- Echo parsing: present, absent, malformed.
- Refusal classification per code and per `path`; transient vs permanent.
- `source_metadata` survives the fork and the queue.

**Acceptance:** `pytest` passes in the CI env with `RC_SYNC_STATE_PATH` on a temporary path. No module imports `requests`.

#### WP-P1: Platform client, capability and view layer

**Goal:** §4.1–4.2, plus test fakes for wave 2.

**Files owned:**
- `PL:src/knovas_client.py`
- `PL:src/doc_fields_capability.py` (new)
- `PL:src/doc_fields_view.py` (new)
- `PL:config/config.yaml`
- `PL:tests/conftest.py`: `DummyKnovasClient` (`:176-244`) gains `where`/`return_fields` keyword arguments and doc-fields methods that default to off
- `PL:tests/doc_fields_fakes.py` (new): `FakeDocFieldsApi(mode)` with a version counter, `find` paging, calibration and the error modes 403, 409 and 422
- `PL:tests/test_knovas_client_doc_fields.py`, `test_doc_fields_capability.py`, `test_doc_fields_view.py` (new)

**Tests (FakeSession, `test_knovas_client_hardening.py:60-128`):**
- `where` and `return_fields` appear only when given; the legacy path raises on either.
- `limit` is at most 50.
- The assertion is in every body, GET included. The pointer is in the body, never in the params. No `q=` from doc-fields code.
- Writes are never retried. The probe makes one request and logs no ERROR.
- The 404 mapping (`NOT_FOUND`, `pack_not_found`, `HTTP_404`, non-JSON).
- `GraphError` details, read from `error`.
- `QueryRejected` only for requests that carried `where` or `return_fields`.
- Row mapping: `fields`, `relevance_tier`, and the title rule. "Rechnung_17.pdf" gives the stem; a 120-character run-on title gives the stem; "Vertrag mit Muster AG" is kept.
- Probe classification, including 401, 403 and 429 → unknown.
- Capability state:
  - TTL and the effect of each signal;
  - `needs_calibration` held past a probe;
  - legacy mode → off without a request.
- `registry_for` and `entity_names_for` are cached per user. `entity_names_for` returns None for special fields and above 5000 names.
- Formatter vectors: date precision, money with an apostrophe, enum label or code fallback, hidden entity, multi-value.
- `filter_state` truth table.
- `listing_notice` truth table (first, middle and last page; `total_count` null or int).
- `validate_where` limits.
- `can_edit` matrix.
- `profile_field_keys` over a sample profile JSON.

**Acceptance:**
- The whole Platform suite is green (PostgreSQL mode).
- `test_documents_view_only_passes_arguments_the_real_client_accepts` (`test_web_admin_documents.py:388-399`) still passes.

#### WP-S: Server follow-ups (KnowledgeBase)

**Goal:** §5 S1–S4.

**Files owned:**
- `KB:api/secure_api.py`: only `_parse_doc_fields_upload` and its helpers
- `KB:services/knowledge_graph/doc_fields/entity_resolver.py`, only if it needs an unlinked-row helper
- `KB:api/doc_fields_api.py`: only `get_doc_values`
- `KB:../tests/test_init_doc_fields_brokered_unasserted.py` (new)
- `KB:../tests/test_doc_values_pointer_in_body.py` (new)
- `KB:../tests/test_doc_fields_wire_goldens.py` (new)
- `KBD:Knovas_Developer_Kit/api/examples/doc_fields/*.json` (new)
- `KBD:Knovas_Developer_Kit/api/Secure_API.md`
- `KBD:Knovas_Developer_Kit/api/Knowledge_Graph_API.md`
- `KBD:ModernDocs/plans/2026-10-01-document-fields.md`: a pointer line only

**Tests:**
- Brokered without an assertion: 201 with `unresolved_entity`, register treated as ignore, the resolver never called.
- With an assertion: unchanged.
- Non-brokered: unchanged.
- The body pointer wins over the query pointer.
- Feature-off: still a byte-identical 404.
- The goldens match.

**Acceptance:**
- `TESTING=true python run_tests.py -k "doc_field or doc_values or where or init_doc_fields"` is green.
- `scripts/check_api_doc_coverage.py` passes.
- Docker unit suite (`./scripts/docker-scripts/run-tests.sh --unit`) is green.

### Wave 2 (four in parallel)

#### WP-RC2: Knovas Connector sync integration

**Goal:** §3.2 and §3.6–3.10.

**Depends on:** the WP-C schema and mock, and WP-RC1.

**Files owned:**
- `RC:src/sync/knovas_uploader.py`
- `RC:src/sync/sync_executor.py`
- `RC:src/sync/sync_state_db.py`
- `RC:src/sync/sync_state.py`
- `RC:src/sync/subfolder_queue.py`: only if `maybe_advance` needs a docstring update; the call-site change is in `sync_executor.py`
- `RC:src/sync/sync_scheduler.py`
- `RC:src/sync/doc_fields_metrics.py` (new)
- `RC:src/routes/sync_control.py`
- `RC:src/routes/sync.py`
- `RC:src/util/schema.py`
- `RC:src/config.py`
- `RC:scripts/backfill_partial_ocr.py`
- `RC:tests/conftest.py`: `RC_SYNC_STATE_PATH` defaults to `tmp_path`
- `RC:tests/unit/test_uploader_doc_fields.py`, `test_sync_executor_doc_fields.py`, `test_sync_state_db_doc_fields_migration.py` (new)
- `RC:tests/contract/test_doc_fields_against_mock.py` (new)
- `RC:tests/test_doc_fields_no_values_in_logs.py` (new)

**Tests:**
- No `fields` key without configuration. This is the backward-compatibility test, modelled on `tests/test_sync_access_groups.py`.
- `{}` only to clear values that were staged before (`previous_fields_sent`).
- Fallback per refusal code: exactly one retry, document indexed, outcome recorded.
  - A 401 with `access_groups` whose retry also 401s is recorded as `init failed: 401`, not `refused`, and stores no digest.
- A 503 `doc_fields_*` answer is not retried five times inside the call.
- Title capped at 500.
- Migration from an old database file. The UPSERT keeps the fields columns when `fields=None`.
- **Stable on the second cycle** (no re-upload) in each of these cases:
  - a metadata-only source with a PDF;
  - a partial upload;
  - a `skip:unconvertible` row;
  - a template that matches no path;
  - two sources yielding the same `rel` with different fields.
- `fields_changed`:
  - bounded per cycle and queued after new and modified work;
  - never truncates a scan;
  - NULL digest equals `""`.
- `reupload_failed` after `RC_FIELDS_REUPLOAD_MAX_ATTEMPTS`, e.g. on a forced 403; requeue resets it.
- Full mode updates the digest of existing rows.
- In sequential mode, `maybe_advance` waits for `fields_changed`.
- `/sync` in one_time mode with a `fields_changed` document still validates `sync_response.schema.json`, and `tx_entry["fields"]` validates.
- Requeue when the server starts accepting, and through the endpoint.
- `/sync/status` capabilities and the `doc_fields` block.
- Schema errors under the new keys carry no instance values.
- Metric labels come from closed sets.
- Logging: sentinel values never appear.
- Against the mock, in all three modes plus `calibrated=False`, a forced refusal and `brokered`: `wsgi_request` is patched over `sync.knovas_uploader.requests.request`.

**Acceptance:**
- The RC suite is green with the CI env.
- Against the mock in `off` mode, the init body is byte-identical to today's for a source without fields.

#### WP-P2: Platform search, listing, cards, detail and edit (plus Cortex link as a stretch)

**Goal:** §4.3–4.5.

**Depends on:** WP-P1 and the WP-C mock.

**Files owned:**
- `PL:src/web_interface/app.py`
- `PL:src/web_interface/doc_fields_routes.py` (new)
- `PL:src/document_grants.py`
- `PL:src/web_interface/static/js/app.js`
- `PL:src/web_interface/static/js/doc_fields.js` (new)
- `PL:src/web_interface/templates/index.html`
- `PL:src/web_interface/static/css/style.css`
- `PL:src/ontology_graph.py`, `PL:src/web_interface/static/js/ontology.js` (stretch)
- `PL:tests/test_frontend_static.py`
- `PL:tests/test_web_search_doc_fields.py`, `test_web_documents_find.py`, `test_web_document_fields.py`, `test_doc_fields_mock_contract.py` (new)

**Tests:**
- Feature-off parity: identical Knovas request bodies; the response has only additive keys.
- H1–H9.
- A 409 when `where` is sent but not supported, and `filters_need_calibration` on `listing_only`.
- The echo-missing case withholds results.
- Error-code mapping, with no unfiltered retry.
- Supplement and thresholds skipped under `where`. Both unchanged when only `relevance_gate_applied: true`.
- A `return_fields`-only failure is retried once without it; a plain search still returns results on a values-mode server.
- Limit clamp.
- The listing:
  - records grants, and a preview of a listed row is then allowed (pattern from `test_web_content_wall.py:44-55`);
  - shows the notice only on the last page.
- Fields read and edit:
  - refused without CSRF, without identity, and without a role in `edit_roles`;
  - a special-field edit by a non-admin is refused;
  - `set: {"counterparty": "Beispiel GmbH"}` succeeds non-strict and shows "nicht verknüpft";
  - 409 `version_conflict`, 403 and `anchor_quarantined` are each handled;
  - read again after the edit;
  - 400 `path: "pointer"` gives "Knovas-Update nötig";
  - a mount-relative `doc_id` gives 404;
  - the audit row contains keys and no values.
- Registry cache keyed per user.
- The entities route returns names only, refuses special fields, and makes no Knovas call carrying the typed text.
- `doc_fields.js` and `ontology.js` contain no `innerHTML` with server data and build no URL with a name.
- Client against the mock in three modes plus `calibrated=False` (`WsgiSession`).

**Acceptance:**
- The Platform suite is green in PostgreSQL mode.
- With the mock in `off` mode, today's search UI behaviour is unchanged, except that the limit clamp turns the "Mehr laden" 422 into a working page.

#### WP-P3: Platform admin: Dokumentfelder, documents drawer and Feldfilter, System and People

**Goal:** §4.6–4.7.

**Depends on:** WP-P1.

**Files owned:**
- `PL:src/web_interface/admin_doc_fields.py` (new)
- `PL:src/web_interface/admin.py`: the attach call and the blueprint context processor; no signature changes
- `PL:src/web_interface/templates/_admin_tabs.html`
- `PL:src/web_interface/templates/admin_doc_fields.html` (new)
- `PL:src/web_interface/static/js/admin_doc_fields.js` (new)
- `PL:src/web_interface/admin_documents.py`
- `PL:src/web_interface/templates/admin_documents.html`
- `PL:src/web_interface/static/js/admin_documents.js`
- `PL:src/web_interface/admin_system.py`
- `PL:src/web_interface/templates/admin_people.html`
- `PL:src/web_interface/static/css/admin.css`
- `PL:src/identity/rc_pointers.py` (new)
- `PL:tests/test_web_admin_doc_fields.py` (new)
- `PL:tests/test_rc_pointers.py` (new)
- `PL:tests/test_web_admin_documents.py`: the tab strip `:148-152` and the drawer and Feldfilter cases
- `PL:tests/test_web_admin_system_doc_fields.py` (new)

**Tests:**
- `require_admin` on every route.
- CSRF checked before any write, form and JSON.
- Feature off: no forms, "bei Knovas nicht freigeschaltet".
- 403 `registry_write_requires_full_clearance` gets the German explanation. A rules list 403 gives the read-only note.
- Pack install shows skipped fields and warnings.
- The `reject` setting warns with the profile keys it would refuse.
- Deprecating a key used by the profile requires confirmation.
- The requeue offer appears after a registry write when an RC is configured.
- Rule prefixes:
  - prefix vectors: Windows `\`, trailing `/`, source root, a folder outside every source refused;
  - manual entry without an RC;
  - the multi-source confirmation.
- Audit holds keys and counts only. The rule audit `target_id` is the rule id, never the prefix.
- Drawer:
  - POST routes with the pointer in the body;
  - re-reads on 409;
  - read-only when held;
  - non-strict warnings shown.
- Feldfilter: requires `where.applied`; the notice only on the last page.
- System check states, all four.

**Acceptance:** the Platform suite is green in PostgreSQL mode, and the tab is hidden when the capability is off.

#### WP-P4: Platform ingestion profiles → RC

**Goal:** §4.8.

**Depends on:** WP-P1, the WP-C schema, and the WP-RC1 vectors and grammar.

**Files owned:**
- `PL:src/identity/ingestion_compiler.py`
- `PL:src/identity/ingestion_profiles.py`
- `PL:src/identity/field_templates.py` (new)
- `PL:src/identity/rc_contracts/vectors/field_templates.json` (new, byte copy)
- `PL:src/web_interface/admin_ingestion.py`
- `PL:src/web_interface/templates/admin_ingestion.html`
- `PL:src/web_interface/static/js/admin_ingestion.js`
- `PL:src/web_interface/admin_approvals.py`
- `PL:src/knovas_connector_client.py`
- `PL:tests/test_ingestion_compiler.py`
- `PL:tests/test_identity_ingestion_profiles.py`
- `PL:tests/test_web_admin_ingestion.py`
- `PL:tests/test_field_templates_platform.py` (new)
- `PL:tests/test_rc_contract_copies.py` (new; vectors byte-identical, skipped without the RC checkout)

**Tests:**
- A profile without fields compiles byte-identically to a frozen fixture of today's output.
- Compiled fields validate against the shipped schema.
- Empty keys are omitted.
- `SourceFolder` imports, stays hashable, and the round trip is lossless; an old row still loads.
- Registry validation errors.
- The folder-rule conflict check is skipped with a note on 403.
- Push refused when the RC lacks the capability (`FakeKnovasConnectorClient` from `test_web_admin_ingestion.py:17-80` gains `status()` capabilities).
- A field-config change requires the re-upload confirmation; the ETA is shown.
- Preview matches the vectors.
- Support JSON and approvals summary contain no values or templates.
- The status bar renders the RC `doc_fields` block, `fields_changed`, the ETA, `rel_collisions` and `reupload_failed`.
- Requeue buttons per outcome.
- `health()` and `capabilities()` handle an error and an old RC.

**Acceptance:**
- The Platform suite is green in PostgreSQL mode.
- The Platform's template preview gives the same captures as the RC for every golden vector.

### Final: WP-I, integration, docs and release (sequential, after wave 2)

**Files owned:**
- `RC:docs/configuration.md`: covers these points:
  - the new source keys and env;
  - the re-upload bound and attempts;
  - precedence vs rules;
  - re-upload cost (billing, OCR, about 200 nights for 20,000 documents at 100 per night);
  - the one_time limit;
  - rel collisions;
  - the downgrade clearing.
- `RC:docs/operations.md`: status, metrics, logging, requeue
- `RC:CHANGELOG.md`: `### 0.3.0`
- `RC:pyproject.toml`: version 0.3.0
- `RC:.env.example`
- `RC:README.md`
- `PL:README.md`: env table `:27-41`
- `KC:KnovasPlatform/docs/features/document-fields.md` (new): covers these points:
  - capabilities, `listing_only` included;
  - edit roles;
  - non-strict edits;
  - titles not searchable;
  - the access-log change;
  - a rename prunes manual values;
  - Platform downgrade clears RC upload values.
- `KC:KnovasPlatform/docs/README.md`
- `KC:KnovasPlatform/docs/demo.md`: the compose `mock` demo stays in legacy mode and therefore shows no doc-fields UI; `MOCK_DOC_FIELDS` serves the tests
- `KC:docs/client/README.md`: the switches table `:100-107`
- `KC:docs/specifications.md`: §1.6 `:161`, §2.5 `:348`
- `KC:docs/KnovasAPI/*`: re-copy `Secure_API.md`, `Client_Integration_Guide.md` and `Analytics_Integration_Guide.md`, and add `Knowledge_Graph_API.md`, all from the KnowledgeBase Dev Kit after WP-S. Front matter `canonical:false`, `source_commit:<sha>`. The README names the canonical source
- `KC:scripts/sync_knovas_api_docs.sh` (new): copies the docs and `api/examples/doc_fields/*.json` from a given KnowledgeBase checkout. The goldens go to `MOCK:goldens/`
- `MOCK:goldens/*.json`: replaced by the KnowledgeBase copies. Any mock drift is fixed in `MOCK:app.py` in this WP
- `KC:RELEASE_NOTES.md`: German `## Dokumentwerte` under `# Unreleased`. It states:
  - the tenant prerequisite;
  - what the RC sends and what re-uploads cost;
  - what the Platform shows;
  - that filters apply only when the server confirms them;
  - that access logs no longer contain URIs.
- `KC:knovas.env.example`: commented passthrough block `:51-99` with `DOC_FIELDS_UI`, `DOC_FIELDS_EDIT_ROLES`, `RC_DOC_FIELDS`, `RC_FIELDS_REUPLOAD_PER_CYCLE`, `RC_FIELDS_REUPLOAD_MAX_ATTEMPTS`
- `KC:scripts/doctor.sh`: a probe next to `:600-616`
  - It sends `POST /secured/graph/doc-values/find {}` and reports off, values or filters, printing the status and `error_code` only.
  - doctor.sh sends no assertion. A 401 therefore means the where gate already passed, so it reports "Filter an (BROKERED-Mandant)", because `find` checks `where` before `_caller()` (`KB:api/doc_fields_api.py:584-590`).
  - It notes that calibration cannot be probed.
- `KC:docs/superpowers/plans/2026-10-02-document-fields-integration.md`: this spec, plus the deviations found during implementation

**Verification:**
1. Both full suites in CI mode: RC with the env from `ci.yml:131-147` plus `RC_SYNC_STATE_PATH`; Platform with `PLATFORM_DB_REQUIRED=true`.
2. The mock suite, against the copied goldens.
3. `bash scripts/lib/test_expand_knovas_env.sh`.
4. `pytest -k doc_fields_render` renders the search page, the listing, the detail panel and the admin tab through the Flask test client against the mock (`WsgiSession`). It covers each `MOCK_DOC_FIELDS` mode plus `calibrated=False` and writes the HTML to a temp directory for review. This replaces a manual compose run, because the compose demo is legacy-mode.
5. A `git status` check that `RC:.rc-sync-state.db` is unchanged.

**Acceptance:** CI is green, the docs describe the gated states and the re-upload cost honestly, and no doc or example contains a real personal name.

---

## 8. Non-goals (not built now, and why)

**Not shipped by the server, so never presented as available:**
- facet counts (P1c);
- bulk edit;
- field merge;
- `where` on `/secured/fulfill-evidence`;
- the `extracted` layer and `source_metadata` forwarding (Phase 2);
- category-backed `doc_type`;
- push-down;
- packs other than `core` and `legal_ch` (no medical or fiduciary pack);
- a deadline control.

**Server-side path templates:** these are Phase 2. The RC templates cover the need now with the same grammar.

**`moved_from` and M365 move detection by item id:** these are Phase 2 on the server. Until then, a rename prunes the old pointer, and with it any manual values. This is documented in WP-I.

**SharePoint column mapping:** it needs extra Graph calls and a permission review.

**Changing the pointer scheme to include the source folder:** it would rewrite every existing pointer and need a re-ingest. Collisions are handled by "first source governs fields" and warned about.

**Changing which source's content wins on a rel collision:** today's content behaviour stays. Only the fields are made deterministic.

**New four-eyes guarded kinds and a dedicated `editor` role:** they need migration `0003`, which collides with PRs #21 and #22. Field operations are audited, and edit rights use `edit_roles` over the existing roles.

**Platform upload `fields` pass-through and field inputs on an upload form:** there is no caller and no Platform upload form (`sync_single_document`, `PL:src/knovas_client.py:1634`, is uncalled).

**A separate re-upload bound for one_time runs:** the bound and its cost are documented instead.

**Old behaviours that stay as they are:**
- **Profile "Beschreibung":** it is still stamped on every document. Only its label is made honest; see §9.
- **Titles:** no junk-title filtering in the RC and no `pdf:subject` as description. Today's title and description derivation stays, apart from the 500-character cap. The Platform's display rule (§4.1) handles run-on titles.
- **Init errors:** a general cap on `init 4xx` retries is out of scope. Only fields-caused refusals, `fields_changed` attempts and long titles are fixed.

**Server additions not requested:**
- RC calls to `GET /doc-fields` (D9).
- RC entity node ids (D4).
- A server `may_edit` hint, capability endpoint or pack labels.
- Effective `fields` in the PATCH response. The components GET again instead, and the docs explain it.
- `q` in the body of `GET /secured/graph/nodes`.
- `title_source` in `return_fields`.

**Refusal policy:** a "hold" policy on field refusals is not built. The RC always uploads without fields and reports.

**Logging and authorization outside the touched code:**
- Pre-existing app-level logging of paths and pointers outside the code paths this work touches is left alone. That is a separate privacy pass. It includes `RC:src/sync/sync_executor.py:547, 926-968` and `PL:app.py:1379, 1390, 1929`, plus the legacy search log at `PL:src/knovas_client.py:1704`. The Platform nginx access log is fixed (§4.9).
- The BROKERED `access_groups` 401 on RC uploads (`KB:api/secure_api.py:1686-1712`) exists today and is not addressed here.

**Testing and versioning:**
- No JS test runner: every honesty decision lives in Python and is pinned by pytest.
- No version or changelog for the Platform beyond the README and RELEASE_NOTES.

---

## 9. Defaults this spec chose (owner may override)

1. **A `where` request without `where.applied` withholds results.** The browser gets 409 `filter_not_applied`, with "Ohne Filter suchen" as an explicit user action. The contract would also allow showing them with a banner.
2. **Only `admin` edits values by default** (`DOC_FIELDS_EDIT_ROLES=admin`). A practice may add `member` or `ingestion_manager`. Special-sensitivity fields stay admin-only.
3. **Edits are non-strict** (`fields_strict: false`), so names stay unlinked rather than being refused. Warnings are shown per field.
4. **Per-source values travel in the upload layer**, so they outrank folder rules (D3). The admin is warned on conflicts and about the re-upload cost.
5. **`fields_changed` documents are re-uploaded automatically**, at most 100 per cycle (range 1–10000) and at most 3 failed attempts per document. Only after new and modified work.
6. **Folder rules in a profile with several sources** need a confirmation; they are not blocked.
7. **S1** narrows the BROKERED init path to unlinked names. Without it, RC entity fields in BROKERED tenants always fall back to "without fields".
8. **The profile "Beschreibung" behaviour stays.** Stopping the compilation of it would change future uploads' descriptions and embeddings, so it is left for the owner to decide.
9. **The listing (`/api/documents/find`) becomes a second source of document grants.**
10. **Platform nginx access logs drop the URI** (§4.9). Operators lose per-path access logs in exchange for keeping pointers out of them.
11. **`listing_only` is held for 1 hour** after a 503 `where_requires_calibration`. The first filtered search after that may meet the 503 once more.

---

## 10. Deviations found during implementation

Each work package recorded where the code departs from the sections above.
Where this section and the spec differ, this section describes what shipped.

### Knovas Connector payload library (WP-RC1)

- **Template grammar details:** a backslash inside a literal is a `syntax`
  error; `too_long` applies above 512 characters per template, 255 per
  literal and 64 per key; literals are compared as NFC → casefold → NFC;
  errors are reported left to right (the first one wins).
- **Precedence** is decided per key by presence first, then validation: a
  capture that fails validation does not fall back to the static value.
- An extra drop reason `invalid_value` (a value no field type can carry); a
  list over 32 items is dropped whole (`cap_exceeded`), not truncated.
- `{}` counts as `cleared` only when the server echoed it; without an echo it
  is `not_accepted` and the clear is repeated once the server accepts fields.
- The refusal codes are a closed set (`invalid_fields`, `fields_too_large`,
  `ambiguous_field`, `unknown_field`, `doc_fields_unavailable`,
  `doc_fields_ingest_unavailable`, `assertion_rejected`, `other`).
- `FieldsRecord`: a `digest` or `sent` of None keeps the stored column;
  `count_attempt` adds one to `fields_attempts`, any other record resets it
  to 0.
- `language` also skips `und-*` tags. `email_author` keeps a bare name when
  the `From:` header carries no address.
- `.md` and `.txt` are extracted as `text/plain`, which has no document
  properties: `language` and `document_author` never yield anything for them
  (§3.5 lists md). The Platform's labels say pdf/docx.

### Knovas Connector sync (WP-RC2)

- `POST /sync/doc-fields/requeue` on a row whose values were staged stores the
  digest sentinel `REQUEUE_DIGEST` (`"requeue"`) instead of NULL, because NULL
  equals "" and would never re-send a clear.
- The status block also carries `dropped`, `last_cycle.reupload_failed` and
  `last_cycle.requeued`; `pending_reupload` is an estimate (what the last scan
  found `fields_changed` and the cycle did not finish, or the rows requeued
  since, whichever is larger).
- `RC_DOC_FIELDS` accepts `on/true/1/yes` and `off/false/0/no`; anything else
  stops the boot. `RC_FIELDS_REUPLOAD_MAX_ATTEMPTS` has the range 1–100.
- A requeue that queued anything wakes a running worker (`request_cycle_now`).

### Contracts, mock, CI (WP-C)

- The mock's goldens (`MOCK:goldens/*.json`) use the envelope `{name,
  description, server_state, setup[], request, response{status, body},
  volatile[], absent[]}`. They are now the server's own: KnowledgeBase
  `docs/Knovas_Developer_Kit/api/examples/doc_fields/` (KB 23febc0, pinned by
  the server's `test_doc_fields_wire_goldens`) was copied over unchanged
  (`scripts/sync_knovas_api_docs.sh` was not written; copy them again after a
  server change). To satisfy them the mock now plays the server after S1/S2:
  - a subset of the server's normaliser (`typed_value`): dates `{lo, hi,
    precision}`, periods `{lo, hi, label}`, amounts `{amount, currency}`,
    numbers as decimal text; dates and periods match as intervals;
  - `mapped_keys` lists only keys that differ from the field key;
  - a first stored upload leaves the anchor at version 4; a PATCH answers
    every effective field;
  - a BROKERED init without an assertion keeps entity names unlinked, drops
    `{"node_id"}` and treats `register` as `ignore` (S1) instead of 401.
  The mock stays stricter than the server in one place: GET doc-values
  refuses a pointer in the query string, so a client test catches it.
- `create_app(..., brokered=True)` also answers 401 `assertion_rejected` for a
  `find` without an assertion, so `doctor.sh`'s probe can be tested.

### Platform client and view (WP-P1)

- `_make_request` still logs Knovas's error body at ERROR on doc-fields read
  errors. Knovas's error bodies never echo a value, so this is noise, not a
  leak; the probe uses `_request_quiet`.

### Platform search side (WP-P2)

- `doc_field_links` (Cortex) reads the target ids through
  `doc_fields_capability.registry_targets_for` (added in WP-I) instead of the
  private cache entry.

### Platform admin (WP-P3)

- The System tab's registry read can trigger Knovas's first-use `core`
  install, as any first `GET /doc-fields` does.

### Platform ingestion (WP-P4)

- **ETA.** §3.7's "about 200 nights for 20,000 documents" assumed one cycle a
  night. Knovas Connector runs cycle after cycle while its window is open, so
  the Ingestion tab computes `ceil(documents / bound)` cycles × (preset scan
  interval + the time the throughput preset needs for one bound), counted in
  nights of the window: 20,000 documents, 100 per cycle, *nightly* (19:00–06:00,
  5 min), *Normal* (30 requests/min) → 200 cycles of ~8⅓ min → **ca. 3
  Nächte**. One request per document and no OCR time are counted, so it is a
  lower bound ("ca."). The docs use this computation.
- The bound shown is `min(RC per_cycle, the throughput preset's files per
  cycle)`.
- An unreachable Knovas Connector used to be reported with the same text as
  an old one ("zu alt"). WP-I added `KnovasConnectorClient.reachable_capabilities()`
  (None when it cannot be asked), and a save or push now says "Knovas Connector
  nicht erreichbar" in that case.

### Integration (WP-I)

- **Audit outcome of a refused value edit.** `audit_log.outcome` admits only
  `ok`, `denied` and `error` (0001_identity.sql CHECK), so §4.6's `conflict`
  cannot be stored. WP-P2 recorded `denied` + `detail.reason`, WP-P3 `error` +
  `detail.code`. Unified: every Knovas refusal of an edit
  (`version_conflict`, `change_not_authorized`, `anchor_quarantined`) is
  `denied` with the code in `detail.code` — the backend said no, as designed;
  `error` stays for failures. `version_to` is the version Knovas reported (the
  current one with a conflict, else None). One helper,
  `doc_fields_view.values_edit_audit_detail`, serves both routes.
- **Access logs.** Besides the bundled nginx (§4.9), gunicorn's access log in
  `docker-compose.yml` now uses `--access-logformat='%(t)s %(m)s %(s)s %(b)s
  %(L)s'` (its default logs the request line, path, query string and
  referer), and the host nginx template logs `knovas_privacy` too, with the
  format defined once per host in `knovas-login-limit.conf`.
- **App-level pointer logging.** The file-route gate (`require_readable_document`)
  logs the route's endpoint name and the reason, no longer `request.path`,
  the supplied path or the pointer; the identifier-prefix fallback note no
  longer logs the pointer. Other pre-existing app logs of paths stay (§8).
- **Preview URLs** keep the pointer in the path and `path=` / `q=` in the query
  string. Moving `q` out would need a POST-and-blob preview (the PDF viewer
  re-requests the iframe URL with `a=` and a fragment on every finding click),
  so it is documented as a known limitation, mitigated by the URI-free access
  logs. nginx's error log still prints the request line on upstream errors;
  documented.
- **`docs/KnovasAPI`.** The canonical Developer Kit pages were being edited
  (uncommitted) when WP-I ran, so a copy could not be pinned to a
  `source_commit`. Following the `KnovasPlatform/knovas-docs` precedent,
  `docs/KnovasAPI/Secure_API.md` is retired to a pointer; no
  `Knowledge_Graph_API.md` copy is added; the README names the canonical source
  and marks the two remaining guides as older copies.
- `scripts/doctor.sh` probes the document-fields state (status and
  `error_code` only).
- `tests/test_doc_fields_render.py` (`pytest -k doc_fields_render`) renders the
  search page, the field panel and listing answers, and the admin pages
  through the Flask test client against the mock in each mode.

---

## Appendix A: Review dispositions

All items below were checked against the code at the cited lines before being applied.

### A.1 Applied

| # | Finding (severity) | Verified at | Where applied |
|---|---|---|---|
| 1 | Perpetual re-upload: digest stored only on fields outcomes; `record_upload` is `INSERT OR REPLACE` (blocker) | `RC:src/sync/sync_state_db.py:175-189`; `RC:src/sync/sync_state.py:121-163`; `RC:src/sync/sync_executor.py:443-508` | §3.3, §3.7 (UPSERT, `FieldsRecord` on every recorded outcome, `none` outcome), WP-RC2 stability tests |
| 2 | Typed names and pointers in URLs and access logs (blocker / major) | `KB:api/graph_api.py:1374-1376`; `PL:src/knovas_client.py:1829-1844`; prod gateway `log_format` with `"$request"`; `PL:nginx/*.conf` without `access_log` | D6, D10, §4.2 `entity_names_for`, §4.4 POST routes, §4.5 sessionStorage handoff, §4.7 POST drawer, §4.9, §6 |
| 3 | `fields_strict:true` makes entity and date edits impossible (major) | `values_service.py:633-638, 660-664`; `entity_resolver.py:424-425`; `core.yaml:135-139`; `legal_ch.yaml:72-75` | D12, §4.4, §4.7, tests |
| 4 | Mock and demo cannot reach the doc-fields path; missing mock routes; global state (major) | `KC:KnovasPlatform/docs/demo.md:24-25`; `PL:src/knovas_client.py:1009-1023, 1686-1705`; `MOCK:app.py:9-36, 96` | D13, §2.5, WP-C mock, WP-I verification step 4 |
| 5 | Missing calibration shown as a temporary outage (major) | `KB:services/query_pipeline.py:246-254`; `KB:api/doc_fields_api.py:577-607` | §1, §2.1, §2.2 `needs_calibration`, `listing_only`, §4.3, H9 |
| 6 | `complete:false` notice on every page; `total_count` may be null (major) | `KB:services/knowledge_graph/doc_fields/listing.py:405-408` | H5, `listing_notice`, §4.4, §4.7, tests |
| 7 | Wave-2 ownership coupling through `page_context` and `admin.py` (major) | `PL:src/web_interface/app.py:1690-1705`; `admin.py:36-37, 277-286, 309-327`; `_admin_tabs.html` | §4.2 process-wide hooks, §4.6 context processor, §7 conventions |
| 8 | Vectors file breaks the RC CI schema check (major) | `KC:.github/workflows/ci.yml:150-153` | §3.1, §3.4, WP-RC1 path `contracts/vectors/` |
| 9 | `fields_changed` vs the closed `document_sync` schema, the else branch, `maybe_advance`, the single-pass cap (major ×2) | `contracts/sync_response.schema.json:49-59`; `routes/sync.py:114-118`; `sync_executor.py:416-419, 707-708, 720-724, 1006-1016`; `sync_state.py:15` | §3.1, §3.7 (status, two-list queue, `note_file`, `maybe_advance`), WP-C, WP-RC2 tests |
| 10 | Re-upload liveness and cost (major) | `KB:api/secure_api.py:544-572, 1588-1589, 1901-1904`; `KB:services/weaviate_service.py:1077-1103`; `PL:src/identity/ingestion_presets.py:38-44`; `sync_executor.py:77-81` | §3.7 attempts and `reupload_failed`, cost paragraph, (priority, size) ordering; §4.8 confirmation and ETA; WP-I docs |
| 11 | Relative-path collisions flap digests (major) | `sync_executor.py:301-323, 677-691` | §3.2 (first source governs fields; changed from "skip duplicates", see A.2) |
| 12 | Member edit by default contradicts the read-only role (major) | `0001_identity.sql:65-66`; `authority.py:107-108, 115-129` | D12, `edit_roles` (changed from "reuse ingestion_manager", see A.2) |
| 13 | "Fristenliste" overclaims (major) | `listing.py:39-44`; §8 | §1 Lawyer step 4, H9 |
| 14 | `return_fields` on every search makes plain search fail (major) | `query_pipeline.py:213-229, 255-273`; `planner.py:309-321, 378-397` | D2, H3, §4.3 single retry without `return_fields` |
| 15 | 401 misclassified in BROKERED tenants (minor) | `secure_api.py:1686-1712` (before fields at `:1813`) | D5, §3.6, WP-RC2 test |
| 16 | Interface claims wrong: `_enhance_search_results` signature; pointer spelling; `health`; `previous_fields_sent` (minor) | `app.py:3470-3475`; `app.py:1326-1395`; `document_grants.py:59-66`; `knovas_connector_client.py:105-106`; `admin_system.py:229-234` | §4.4 `enhance` closure and `knovas_pointer_for`, §4.8 `health` alias, §3.2 signature |
| 17 | Inconsistent examples: rule prefix, "Pfadvorlage", upload path (minor) | `knovas_uploader.py:181`; `sync_executor.py:572-577`; `commit.py:739` | §1, `layer_label` |
| 18 | Wrong citations (minor) | `rules.py:93` vs `:228-232`; `ingestion_compiler.py:222-281, 302-320`; `doc_fields_api.py:498`; `sync_executor.py:281-325`; title check `secure_api.py:1632-1634` | §3.2, §3.6, §4.6, §4.8, §5 |
| 19 | CI working directory and the dataclass mutable default (minor) | `ci.yml:32-34`; `ingestion_compiler.py:78-91` | WP-C CI, §4.8 |
| 20 | H3 changes unfiltered search on gate-on servers (minor) | `query_pipeline.py:1458`; dev overlay `:132` | H3, WP-P2 tests |
| 21 | Title regression for run-on or extension titles (minor) | `return_fields.py:102-106`; `knovas_uploader.py:228`; `knovas_client.py:285-300` | §4.1 title rule |
| 22 | Probe logs ERROR; 429/403 missing; doctor.sh 401 inference; admin drawer GET with pointer (minor ×2) | `knovas_client.py:1474-1483`; `doc_fields_api.py:584-590` | D7, §2.2, WP-I doctor.sh, §4.7 |
| 23 | Missing S2 shows as a generic error; rules GET needs clearance; folder picker needs an RC; rule audit target; dead upload pass-through; downgrade clearing (minor) | `doc_fields_api.py:498, 802-814`; `admin.py:309-327`; `rules.py:250-256`; `knovas_client.py:1634` | §4.4 error map, §4.6, §4.8, §8, §2.5, WP-I |
| 24 | Metadata mapping over-built or unreliable (minor) | `core.yaml:141-147`; knovas_extract `eml.py:188, 229-235`; `pdf.py:501`; `docx.py:349` | §1 Fiduciary step 2, §3.1 enum, §3.5 |
| 25 | RC schema errors echo values (minor) | `RC:src/util/schema.py:22-25`; `routes/sync.py:60-62`; `knovas_connector_client.py:88-90` | §3.8, WP-RC2 |
| 26 | Recovery gaps: requeue for refused only; deprecate unwarned (minor) | `planner.py:365-376` | §3.7 requeue outcomes, §4.6, §4.8 |
| 27 | UI overstates title edit, `privileged` and `resolved_nodes` (minor) | `doc_fields_api.py:516-526`; `planner.py:238-240` | H9, §4.2 `resolved_chips`, §4.5 hints |
| 28 | Mock re-implements `where` semantics and will drift (minor) | `MOCK:app.py` (238 lines) | WP-C exact-equality mock, §5 S4 goldens, WP-I copy |

### A.2 Applied in a changed form

| Finding | Suggested fix | Applied instead, and why |
|---|---|---|
| Relative-path collisions | First source wins; skip later duplicates | The first source **governs fields** for all duplicates; content uploads stay as today. Skipping duplicates would change which file's content is indexed, against the backward-compatibility rule. Governing fields alone stops the digest flapping. |
| Member edit | Gate on `ingestion_manager` now | A configurable `edit_roles` list, default `admin`. `ingestion_manager` means "edit and run ingestion profiles" (`0001_identity.sql:63-64`), not document values. A practice can opt in any existing role without a migration. |
| Re-upload count before save | Exact count from `/discover` plus template preview | An upper bound (`document_sync.total`) plus an ETA, with a required confirmation. `/discover` lists folders, not per-file digests, so an exact count would require a second scan. |
| Last editor in the panel | Show the last editor and time from the server's value events | The panel shows the time (`created_at` of the manual layer, `values_service.py:330-350`). The editor's identity is not in the values view; the Platform audit row records it. |
| Entity autocomplete | S4 body-borne `q`, or local filtering | Local filtering only (`entity_names_for`), with no suggestions for special fields or above 5000 nodes. No server change is needed, and no typed text leaves the Platform. |

### A.3 Rejected

| Finding or suggested fix | Reason |
|---|---|
| A secured-mode demo recipe for the mock with HTTP allowed | It would add a path that presents client certificates over plain HTTP, which `PL:src/knovas_client.py:1015-1023` deliberately refuses. The render check runs through `WsgiSession` with an `https://` base URL instead (WP-I step 4). |
| A larger re-upload bound for one_time runs | It adds a second knob without reducing cost. The limit is documented, and the confirmation (§4.8) states it. |
| `RC_FIELDS_REUPLOAD_PER_CYCLE=0` as "manual requeue only" (previous §9.4) | Requeue re-enters through the same bound, so 0 would make requeue a no-op. The range is now 1–10000; `RC_DOC_FIELDS=off` is the full stop. |
| Asking the server for `title_source` in `return_fields` | The Platform title rule (§4.1) achieves the same without a server change. |
| Adding registry-typed field inputs to an `/admin/documents` upload form | No such upload form exists (`admin_documents.py` has no upload route). This is recorded as a non-goal. |
