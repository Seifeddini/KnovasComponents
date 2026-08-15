# Pflichtenheft D–H, J — KnovasComponents Implementation Plan (Part B)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the backend contracts of Part A into the product a Swiss law firm demanded in sections D–H and J: a search a firm can live in (filters, paging, honest empties, versions, similar documents, jump-to-hit viewer), a party register with dedup and Zefix, an evidentiary conflicts check with lateral-hire import, a Fristen workflow (proposal → adopt → four-eyes confirm → Outlook feed), an inbox fed by the event spine, Cortex on the live graph (ego graph, "why?", trust chips, reports, import wizard, Vorgaben and filters live), Outlook and Word add-ins with two-click filing, an opt-in activity journal, RemoteController connectors and metadata (mailbox, PST, XLSX/PPTX, OCR evidence), and the written declarations (E1, G9, H6, J1, J4) with a capability legend.

**Architecture:** Platform screens are new Flask Blueprints in `KnovasPlatform/components/docbridge_integration/src/web_interface/`, built on the section-C blueprint (`graph_routes.py`, `graph_model.py`, `matter_view.py`, cassette tests) and the section-B identity gate (`src/identity/`, `platform-db`); the client (`knovas_client.py`) grows one method per new backend route; nothing is post-filtered that the API filters. RemoteController grows a metadata builder, RC-local Office extractors registered through knovas-extract's public hook, a Microsoft-Graph mailbox mirror shaped like the OneDrive mirror, a PST exploder, and an OCR benchmark. Office add-ins are a static taskpane app served by the Platform origin plus two manifests. Docs are treated as deliverables with the same "no placeholder" discipline.

**Tech Stack:** Python 3.11, Flask, requests, pytest; vanilla JS (no build step), Cytoscape.js, vendored pdf.js; PostgreSQL (`platform-db`, section B); openpyxl, python-pptx, py3langid, Pillow (RC); Office.js manifests; Microsoft Graph.

**Spec:** [`docs/superpowers/specs/2026-08-15-pflichtenheft-d-j-design.md`](../specs/2026-08-15-pflichtenheft-d-j-design.md).

**Companion plan (Part A — KnowledgeBase):** `KnowledgeBase/docs/superpowers/plans/2026-08-15-pflichtenheft-d-j-knowledgebase.md`. Every backend route this plan calls is defined there; the **Interfaces → Consumes** blocks name them exactly.

**Repository:** `E:\Knovas\KnovasComponents`. Working directories: Platform tasks `KnovasPlatform/components/docbridge_integration`; RemoteController tasks `RemoteController`; add-in tasks `KnovasPlatform/components/knovas_office_addins`; docs tasks the repo root.

## Global Constraints

- **The API first, then the UI.** A screen never post-filters what `/secured/query` filters and never invents metadata the API did not return; API `400 validation_error` responses are surfaced, never swallowed.
- **Graph-first.** New graph screens exist only in `ONTOLOGY_SOURCE=graph`; on the fixture they render "Wissensnetz-Modus erforderlich" — never a 500, never invented data. Fixture Cortex stays frozen.
- **Proposals never commit; rejection is permanent and the confirmation says so.**
- **Trust and scope travel together:** no tier without its `scope` (`_trust_chip.html`), no conflicts result without `withheld_count` and `degraded`.
- **Identity:** the actor sent as `actor_ref` is the section-B user id (`IdentityGate.current_user().id`) — never a typed name; screens that need a person (D2 actor, E3 confirm, J2, H2) are gated on `require_authenticated_user` (section-B KC-B1-2) and are sequenced after `feat/section-b-buildout` merges; their backend halves ship earlier.
- **404 is never widened;** every mutating route enforces CSRF (`X-CSRF-Token`) except the documented Bearer/feed-token endpoints.
- **Multi-worker safety:** nothing stateful lives in process memory; use `platform-db` (section B) or the SQLite pattern from `open_tokens.py`; the events poller elects a leader with a PostgreSQL advisory lock.
- **RemoteController:** `gunicorn -w 1` stays; response schemas are `additionalProperties:false` — every new output field is added to `contracts/*.schema.json` in the same task; the extension allow-list has exactly one source of truth after KC-F-3.
- **UI copy German; code, comments, commits, docs English.** Vendored JS is pinned with a checksum and a licence note.
- **Tests:** Platform `cd KnovasPlatform/components/docbridge_integration && python -m pytest`; RC `cd RemoteController && python -m pytest`; contract cassettes recorded once from the dev tenant (section-C convention) and refreshed when Part A changes a shape.
- **Docs are deliverables:** every screen has a `KnovasPlatform/docs/features/*.md` page with a capability label; every connector is in `RemoteController/docs/connectors.md`; every declaration is in `docs/product-statements.md`; the Developer-Kit mirror under `docs/KnovasAPI/` is re-synced from `KnowledgeBase/docs/Knovas_Developer_Kit/api/` (drift check script).
- **Commits:** `feat(search):`, `feat(cortex):`, `feat(parties):`, `feat(conflicts):`, `feat(deadlines):`, `feat(inbox):`, `feat(addins):`, `feat(rc):`, `docs(...)`, `test(...)`; branch `feat/pflichtenheft-d-j` from `main` (rebase onto the section-C and section-B branches as they merge).

## Part Overview

| Part | Scope | Requirements | Depends on | Independent? |
| --- | --- | --- | --- | --- |
| **KC-A** | Client/model additions; search filter rail, paging, facets, honesty; document dialog (versions, similar, tables, metadata); pdf.js viewer | F3, F6, F7, F8, F9, D5, H4 | Part A KB-A1/KB-A2/KB-B (contracts); C-plan B-tasks for the matter picker | Yes — start with cassettes against dev once Part A phase 0/1 lands |
| **KC-B** | Parteien register + Dubletten/merge; Zefix; Konfliktprüfung + protocol; lateral-hire import | D1, D2, D3, D4 | Part A KB-E; C-plan graph blueprint; section B for actor + approvals | after C-plan B1–B7 |
| **KC-C** | Fristen (proposals, four-eyes, ICS feed); events poller + Posteingang; job status | E3, E4, E5, E6 | Part A KB-C/KB-D; section B for feed tokens + actor | after C-plan B5 |
| **KC-D** | Cortex live: graph default + badge, ego graph, why-panel, trust chip, Berichte, CSV import wizard, Vorgaben live, filters live | G1–G8 | C-plan (Parts A/B/C); Part A KB-F-8..10 | after C-plan |
| **KC-E** | Office add-ins component + `/api/filing/*`; Arbeitstag-Journal | H2, E5-adjacent, J2, J3 | section B (login in taskpane, journal user); Part A metadata contract | after section B |
| **KC-F** | RemoteController: metadata at ingest, matter path rule, single extension list, XLSX/PPTX, OCR (ita + signal + benchmark), mailbox mirror, PST + queue, index status + schema fields | F1, F2, F3, D5, F5, H1, H4 | Part A KB-A1 (metadata contract), KB-C (transmission status), KB-E (identifier search for the path rule) | KC-F-3..6 immediately; KC-F-1/2/7/8/9 as their contracts land |
| **KC-G** | Declarations + capability legend; docs index; Developer-Kit mirror + drift check; specifications/hosting; release notes/changelogs; design copy | E1, G9, H6, J1, J4, F4-doc, F6-doc | — | Yes — start immediately (phase 0) |

Sequencing follows design §10: KC-G and KC-F-3..6 in phase 0; KC-A and the rest of KC-F in phase 1; KC-B/C/D in phase 2 (after the C-plan and section B); KC-E and the mailbox/PST half of KC-F in phase 3.

---

## PART KC-A — KnovasPlatform — client additions, search filters & paging & honesty, document dialog, viewer (F3, F6, F7, F8, F9, D5, H4)

### Task KC-A-1: `knovas_client.py` — F3 query contract, richer hit rows, versions, similar, metadata
**Requirements:** F3, F6, F7, F8, F9, D5
**Files:**
- Modify: `src/knovas_client.py` — module constants after line 41; `_unwrap_secured_query_response` (lines 231–267); `_normalize_top_chunks` (741–753); `_secured_query_hit_to_row` (809–857); `SecuredApiError` next to `KnowledgeGraphDisabled` (859); `_secured_query_request_body` (982–1017); `search_documents` (1463–1499); `_search_documents_secured` (1751–1806); three new methods appended after `_search_documents_secured` (before `_sync_single_document_secured`, line 1808).
- Modify: `tests/test_knovas_client_hardening.py` lines 282–302 (class `TestC5SecuredFilters`) and docstring line 11.
- Create: `tests/test_knovas_client_search_contract.py`, `tests/test_search_contract_live.py`, `tests/cassettes/search_contract.json` (recorded, Step 10).
**Interfaces:**
- Consumes: KB `POST /secured/query`, `GET /secured/document/<uuid>/versions`, `POST /secured/documents/<uuid>/similar`, `PATCH /secured/documents/<uuid>/metadata` (KB parts). Existing `_make_request` (retries only ConnectionError/Timeout), `_request_no_retry`, and `FakeSession/FakeResponse/make_secured_client` from `tests/test_knovas_client_hardening.py`.
- Produces (module `knovas_client`):
  - `API_FILTER_KEYS = ('author','document_type','language','document_status','source_kind','date_from','date_to','pointer_prefix')`, `SORT_VALUES = ('relevance','date_desc','date_asc')`, `FACET_KEYS = ('author','document_type','language','document_status','source_kind')`, `METADATA_KEYS = ('author','document_type','language','document_date','document_status','source_kind','extra')`.
  - `class SecuredApiError(RuntimeError)` with `.status: int`, `.error_code: str`, `.message: str`, `.details: Any`, `.retry_after: Optional[int]`.
  - `KnovasAPIClient.search_documents(query, *, filters=None, limit=None, offset=0, sort='relevance', facets=None, scope=None) -> dict` = `{results: [row], total: int, total_ranked: int, has_more: bool, offset: int, limit: int|None, sort: str, facets: {key: [{value: str, count: int}]}, no_strong_matches: bool, paging_supported: bool, semantix: {status, message, result_count, pointers, query_session_id}}`.
  - Result row (`_secured_query_hit_to_row`) additionally carries — only when the API sent them — `author, document_type, language, document_status, source_kind` (str), `has_versions, is_current` (bool), `version_count` (int), `relevance_tier, score_mode` (str), `fusion_score` (number), `kg_node_ids` (list[str]), `snippet` (str ≤ 300), `primary_chunk_kind` (str), `chunk_uuid` (str); each `top_chunks[]` entry additionally `chunk_uuid, chunk_kind, snippet, sentence_number_end` when present.
  - `KnovasAPIClient.document_versions(document_uuid) -> Optional[dict]` = `{current: dict, versions: [{version_number, content_hash_raw, pointer_at_version, path, timestamp, changed_by, changed_by_kind}]}`; `None` on 404.
  - `KnovasAPIClient.similar_documents(document_uuid, *, limit=None, filters=None, scope=None) -> Optional[dict]` = `{results: [row], total, no_strong_matches, semantix}`; `None` on 404.
  - `KnovasAPIClient.update_document_metadata(document_uuid, metadata: dict) -> Optional[dict]` (parsed API body; `None` on 404; `ValueError` on unknown keys).
  - Every non-404 HTTP error on these four calls raises `SecuredApiError`.

- [ ] **Step 1: Write the failing client tests**

Create `tests/test_knovas_client_search_contract.py`:

```python
"""/secured/query filters, paging, facets and the document endpoints (F3, F6, F8).

Fakes as in test_knovas_client_hardening.py: FakeSession records the request,
FakeResponse answers it. Nothing touches the network.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from knovas_client import API_FILTER_KEYS, SecuredApiError, _secured_query_hit_to_row
from test_knovas_client_hardening import FakeResponse, FakeSession, make_secured_client

CASSETTE = pathlib.Path(__file__).parent / "cassettes" / "search_contract.json"


def _capture(response_json, status=200):
    captured = {}

    def responder(method, url, **kw):
        captured["method"] = method
        captured["url"] = url
        captured.update(kw)
        return FakeResponse(status, response_json)

    return captured, responder


## --- request body ------------------------------------------------------------

def test_query_body_carries_filters_paging_sort_facets_scope():
    client = make_secured_client()
    captured, responder = _capture({"results": []})
    client._session = FakeSession(responder)

    client.search_documents(
        "Kündigungsfrist",
        filters={"author": ["Muster"], "date_from": "2024-01-01"},
        limit=20, offset=40, sort="date_desc",
        facets=["author", "document_type"],
        scope={"node_ids": ["n1"]},
    )

    body = captured["json"]
    assert body["Input"] == "Kündigungsfrist"
    assert body["limit"] == 20 and body["top_k"] == 20
    assert body["offset"] == 40
    assert body["sort"] == "date_desc"
    assert body["facets"] == ["author", "document_type"]
    assert body["filters"] == {"author": ["Muster"], "date_from": "2024-01-01"}
    assert body["scope"] == {"node_ids": ["n1"]}


def test_query_body_defaults_stay_minimal():
    """offset 0, sort relevance and no facets are NOT sent: a tenant without the
    F3 contract keeps receiving the body it always received."""
    client = make_secured_client()
    captured, responder = _capture({"results": []})
    client._session = FakeSession(responder)

    client.search_documents("x", limit=5)

    assert set(captured["json"]) == {"Input", "limit", "top_k"}


def test_ui_only_filter_keys_are_not_forwarded(caplog):
    client = make_secured_client()
    captured, responder = _capture({"results": []})
    client._session = FakeSession(responder)

    client.search_documents("x", filters={"exact_match": True, "author": ["A"]})

    assert captured["json"]["filters"] == {"author": ["A"]}
    assert "exact_match" in caplog.text


def test_api_filter_keys_are_the_documented_eight():
    assert set(API_FILTER_KEYS) == {"author", "document_type", "language",
                                    "document_status", "source_kind",
                                    "date_from", "date_to", "pointer_prefix"}


def test_invalid_sort_and_facet_are_rejected_client_side():
    client = make_secured_client()
    with pytest.raises(ValueError):
        client.search_documents("x", sort="random")
    with pytest.raises(ValueError):
        client.search_documents("x", facets=["colour"])


## --- response ----------------------------------------------------------------

_HIT = {
    "pointer": "corpus/2024-001/Vertrag.pdf",
    "document_uuid": "d-1",
    "title": "Mietvertrag Bahnhofstrasse",
    "author": "Dr. A. Muster",
    "document_type": "Vertrag",
    "language": "de",
    "document_date": "2024-03-15",
    "document_status": "final",
    "source_kind": "share",
    "has_versions": True,
    "version_count": 3,
    "is_current": True,
    "relevance_tier": "strong",
    "score_mode": "hybrid",
    "fusion_score": 0.71,
    "cosine_similarity": 0.83,
    "page_number": 4,
    "sentence_number": 12,
    "top_chunks": [
        {"chunk_uuid": "c-1", "chunk_kind": "body",
         "snippet": "Die Kündigungsfrist beträgt drei Monate.",
         "page_number": 4, "sentence_number": 12, "sentence_number_end": 12,
         "cosine_similarity": 0.83},
        {"chunk_uuid": "c-2", "chunk_kind": "auto_summary",
         "snippet": "Zusammenfassung des Vertrags.",
         "page_number": None, "sentence_number": None, "cosine_similarity": 0.80},
    ],
}


def test_search_response_carries_paging_facets_and_honesty():
    client = make_secured_client()
    _, responder = _capture({
        "status": "success", "results": [_HIT], "result_count": 1,
        "total_ranked": 57, "has_more": True, "offset": 20, "limit": 20,
        "sort": "relevance",
        "facets": {"author": [{"value": "Dr. A. Muster", "count": 12}]},
        "no_strong_matches": False, "query_session_id": "s-1",
    })
    client._session = FakeSession(responder)

    out = client.search_documents("Kündigungsfrist", limit=20, offset=20)

    assert out["total"] == 1
    assert out["total_ranked"] == 57
    assert out["has_more"] is True
    assert out["offset"] == 20 and out["limit"] == 20 and out["sort"] == "relevance"
    assert out["facets"] == {"author": [{"value": "Dr. A. Muster", "count": 12}]}
    assert out["no_strong_matches"] is False
    assert out["paging_supported"] is True
    assert out["semantix"]["query_session_id"] == "s-1"


def test_missing_paging_fields_fall_back_honestly():
    """A tenant without F3 answers the old shape: no facets, has_more False,
    total_ranked equals the returned count, paging_supported False. Nothing is
    invented."""
    client = make_secured_client()
    _, responder = _capture({"results": [_HIT, dict(_HIT, pointer="b.pdf")]})
    client._session = FakeSession(responder)

    out = client.search_documents("x", limit=20)

    assert out["total_ranked"] == 2
    assert out["has_more"] is False
    assert out["facets"] == {}
    assert out["no_strong_matches"] is False
    assert out["paging_supported"] is False


def test_hit_row_maps_metadata_versions_and_chunks():
    row = _secured_query_hit_to_row(dict(_HIT))

    assert row["author"] == "Dr. A. Muster"
    assert row["document_type"] == "Vertrag"
    assert row["language"] == "de"
    assert row["document_status"] == "final"
    assert row["source_kind"] == "share"
    assert row["document_date"] == "2024-03-15"
    assert row["has_versions"] is True
    assert row["version_count"] == 3
    assert row["is_current"] is True
    assert row["relevance_tier"] == "strong"
    assert row["score_mode"] == "hybrid" and row["fusion_score"] == 0.71
    assert row["top_chunks"][0]["chunk_uuid"] == "c-1"
    assert row["top_chunks"][0]["chunk_kind"] == "body"
    assert row["top_chunks"][0]["snippet"] == "Die Kündigungsfrist beträgt drei Monate."
    assert row["top_chunks"][0]["sentence_number_end"] == 12
    assert row["top_chunks"][1]["chunk_kind"] == "auto_summary"
    assert row["snippet"] == "Die Kündigungsfrist beträgt drei Monate."
    assert row["primary_chunk_kind"] == "body"
    assert row["chunk_uuid"] == "c-1"


def test_hit_row_without_metadata_has_no_invented_fields():
    row = _secured_query_hit_to_row({"pointer": "a.pdf", "cosine_similarity": 0.5})

    for key in ("author", "document_type", "language", "document_status",
                "source_kind", "has_versions", "version_count", "is_current",
                "relevance_tier", "snippet", "primary_chunk_kind", "chunk_uuid"):
        assert key not in row, key


def test_auto_summary_primary_chunk_is_labelled_not_hidden():
    hit = dict(_HIT, top_chunks=list(reversed(_HIT["top_chunks"])))

    row = _secured_query_hit_to_row(hit)

    assert row["primary_chunk_kind"] == "auto_summary"
    # The snippet prefers the first real passage: a summary is not a Fundstelle.
    assert row["snippet"] == "Die Kündigungsfrist beträgt drei Monate."


def test_validation_error_becomes_secured_api_error():
    client = make_secured_client()
    _, responder = _capture({"status": "error", "error_code": "validation_error",
                             "message": "filters.language: unknown value 'xx'",
                             "details": {"field": "filters.language"}}, status=400)
    client._session = FakeSession(responder)

    with pytest.raises(SecuredApiError) as caught:
        client.search_documents("x", filters={"language": ["xx"]})

    assert caught.value.status == 400
    assert caught.value.error_code == "validation_error"
    assert "language" in caught.value.message
    assert caught.value.details == {"field": "filters.language"}


## --- versions / similar / metadata -------------------------------------------

def test_document_versions_calls_the_singular_document_route():
    client = make_secured_client()
    captured, responder = _capture({
        "current": {"document_uuid": "d-1", "version_number": 3},
        "versions": [{"version_number": 2, "content_hash_raw": "h2",
                      "pointer_at_version": "a.pdf", "path": "/a.pdf",
                      "timestamp": "2024-01-01T00:00:00Z",
                      "changed_by": "rc-01", "changed_by_kind": "client_ref"}],
    })
    client._session = FakeSession(responder)

    out = client.document_versions("d-1")

    assert captured["method"] == "GET"
    assert captured["url"].endswith("/secured/document/d-1/versions")
    assert out["current"]["version_number"] == 3
    assert out["versions"][0]["changed_by_kind"] == "client_ref"


def test_document_versions_404_is_none():
    client = make_secured_client()
    _, responder = _capture({"status": "error", "error_code": "not_found"}, status=404)
    client._session = FakeSession(responder)

    assert client.document_versions("unknown") is None


def test_similar_documents_posts_limit_filters_scope_and_maps_hits():
    client = make_secured_client()
    captured, responder = _capture({"results": [dict(_HIT, kg_node_ids=["n1", "n2"])],
                                    "no_strong_matches": False})
    client._session = FakeSession(responder)

    out = client.similar_documents("d-1", limit=5,
                                   filters={"language": ["de"], "exact_match": True},
                                   scope={"node_ids": ["n1"]})

    assert captured["method"] == "POST"
    assert captured["url"].endswith("/secured/documents/d-1/similar")
    assert captured["json"] == {"limit": 5, "filters": {"language": ["de"]},
                                "scope": {"node_ids": ["n1"]}}
    assert out["results"][0]["kg_node_ids"] == ["n1", "n2"]
    assert out["results"][0]["author"] == "Dr. A. Muster"
    assert out["no_strong_matches"] is False


def test_similar_documents_404_is_none():
    client = make_secured_client()
    _, responder = _capture({}, status=404)
    client._session = FakeSession(responder)

    assert client.similar_documents("nope") is None


def test_update_document_metadata_patches_only_known_keys():
    client = make_secured_client()
    captured, responder = _capture({"status": "success", "document_uuid": "d-1",
                                    "metadata": {"document_status": "executed"}})
    client._session = FakeSession(responder)

    out = client.update_document_metadata("d-1", {"document_status": "executed"})

    assert captured["method"] == "PATCH"
    assert captured["url"].endswith("/secured/documents/d-1/metadata")
    assert captured["json"] == {"document_status": "executed"}
    assert out["metadata"]["document_status"] == "executed"


def test_update_document_metadata_rejects_unknown_keys_before_the_api():
    client = make_secured_client()
    calls = {"n": 0}

    def responder(method, url, **kw):
        calls["n"] += 1
        return FakeResponse(200, {})

    client._session = FakeSession(responder)

    with pytest.raises(ValueError):
        client.update_document_metadata("d-1", {"title": "nope"})
    assert calls["n"] == 0


def test_update_document_metadata_is_never_retried_on_5xx():
    client = make_secured_client()
    calls = {"n": 0}

    def responder(method, url, **kw):
        calls["n"] += 1
        return FakeResponse(500, {})

    client._session = FakeSession(responder)

    with pytest.raises(SecuredApiError):
        client.update_document_metadata("d-1", {"author": "X"})
    assert calls["n"] == 1


## --- recorded contract -------------------------------------------------------

def test_recorded_query_hit_carries_the_f3_fields():
    """Guards drift against the dev tenant once tests/cassettes/search_contract.json
    has been recorded by tests/test_search_contract_live.py."""
    if not CASSETTE.exists():
        pytest.skip("search contract cassette not recorded (run with --knovas-api)")
    recorded = json.loads(CASSETTE.read_text(encoding="utf-8"))
    query = recorded["POST /secured/query"]

    assert {"total_ranked", "has_more", "offset", "limit", "sort",
            "no_strong_matches"} <= set(query)
    hit = query["results"][0]
    assert {"pointer", "document_uuid", "title", "author", "document_type",
            "language", "has_versions", "version_count", "is_current"} <= set(hit)
    assert {"chunk_uuid", "chunk_kind", "snippet"} <= set(hit["top_chunks"][0])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `py -3.13 -m pytest tests/test_knovas_client_search_contract.py -v`
Expected: FAIL — `ImportError: cannot import name 'API_FILTER_KEYS' from 'knovas_client'`.

- [ ] **Step 3: Constants, `SecuredApiError`, helpers**

In `src/knovas_client.py`, immediately after `_RETRYABLE_REQUEST_EXCEPTIONS` (line 41), add:

```python
## /secured/query filter vocabulary (Developer Kit Secure_API.md, F3). Anything
## else the UI knows -- exact_match -- is a local refinement and never leaves the
## platform. Keys are forwarded verbatim; values are validated by the API.
API_FILTER_KEYS = ('author', 'document_type', 'language', 'document_status',
                   'source_kind', 'date_from', 'date_to', 'pointer_prefix')
SORT_VALUES = ('relevance', 'date_desc', 'date_asc')
FACET_KEYS = ('author', 'document_type', 'language', 'document_status', 'source_kind')
## PATCH /secured/documents/<uuid>/metadata accepts exactly the ingest metadata block.
METADATA_KEYS = ('author', 'document_type', 'language', 'document_date',
                 'document_status', 'source_kind', 'extra')
_HIT_METADATA_KEYS = ('author', 'document_type', 'language', 'document_status', 'source_kind')
_SNIPPET_MAX_CHARS = 300


def _api_filters_only(filters: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Keep the documented filter keys; log and drop the rest.

    Dropping silently would hide a UI bug; forwarding blindly sent exact_match
    to the tenant API on every search (the old behaviour).
    """
    if not filters:
        return {}
    kept = {k: v for k, v in filters.items()
            if k in API_FILTER_KEYS and v not in (None, '', [], {})}
    dropped = sorted(k for k in filters if k not in API_FILTER_KEYS)
    if dropped:
        logger.warning("Secured query: UI-only filter keys not forwarded: %s", dropped)
    return kept


def _int_or(value: Any, default: Any) -> Any:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_facets(raw: Any) -> Dict[str, List[Dict[str, Any]]]:
    """{key: [{value, count}]} - tolerant of a missing or malformed block."""
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, List[Dict[str, Any]]] = {}
    for key, entries in raw.items():
        if not isinstance(entries, list):
            continue
        rows = []
        for entry in entries:
            if isinstance(entry, dict) and entry.get('value') not in (None, ''):
                rows.append({'value': str(entry['value']),
                             'count': _int_or(entry.get('count'), 0)})
        out[str(key)] = rows
    return out
```

Beside `KnowledgeGraphDisabled` (line 859), add:

```python
class SecuredApiError(RuntimeError):
    """A /secured/* call answered 4xx/5xx with a body the caller can act on.

    Bewusst nicht GraphError: die Suche kennt andere Codes (validation_error,
    rate limits, relevance_calibration_missing), und ihre Plattform-Routen
    reichen den Status weiter, statt alles auf 500 zu falten. 404 bleibt bei
    den Dokument-Routen ausserhalb dieses Typs (unbekannt oder fremd -> None).
    """

    def __init__(self, status: int, error_code: str = '', message: str = '',
                 details: Any = None):
        super().__init__(message or error_code or f'HTTP {status}')
        self.status = status
        self.error_code = error_code
        self.message = message
        self.details = details
        self.retry_after: Optional[int] = None


def _secured_api_error_from(exc: requests.exceptions.HTTPError) -> SecuredApiError:
    response = exc.response
    body: Dict[str, Any] = {}
    status = 502
    if response is not None:
        status = int(getattr(response, 'status_code', 502) or 502)
        try:
            body = response.json() or {}
        except ValueError:
            body = {}
    err = SecuredApiError(
        status,
        str(body.get('error_code') or body.get('code') or ''),
        str(body.get('message') or body.get('error') or ''),
        body.get('details') if body.get('details') is not None else body.get('errors'),
    )
    headers = getattr(response, 'headers', None) or {}
    err.retry_after = _int_or(headers.get('Retry-After'), None)
    return err
```

- [ ] **Step 4: Request body**

Replace `_secured_query_request_body` (982–1017) with:

```python
    def _secured_query_request_body(
        self,
        query: Union[str, List[str]],
        limit: Optional[int] = None,
        filters: Optional[Dict[str, Any]] = None,
        *,
        offset: int = 0,
        sort: str = 'relevance',
        facets: Optional[List[str]] = None,
        scope: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if isinstance(query, list):
            inputs = [str(q).strip() for q in query if str(q).strip()]
            if not inputs:
                raise ValueError('Input must be a non-empty string or non-empty list of strings')
            body_input: Union[str, List[str]] = inputs if len(inputs) > 1 else inputs[0]
        else:
            q = str(query).strip()
            if not q:
                raise ValueError('Input must be a non-empty string or non-empty list of strings')
            body_input = q
        if sort not in SORT_VALUES:
            raise ValueError(f'sort must be one of {SORT_VALUES}')
        offset = _int_or(offset, 0)
        if offset < 0:
            raise ValueError('offset must be >= 0')
        unknown_facets = [f for f in (facets or []) if f not in FACET_KEYS]
        if unknown_facets:
            raise ValueError(f'unknown facets: {unknown_facets}')

        body: Dict[str, Any] = {'Input': body_input}
        if limit is not None and limit > 0:
            body['limit'] = int(limit)
            body['top_k'] = int(limit)
        # Only what deviates from the defaults leaves the platform, so a tenant
        # that has not rolled out the F3 contract keeps seeing the old body.
        api_filters = _api_filters_only(filters)
        if api_filters:
            body['filters'] = api_filters
        if offset:
            body['offset'] = offset
        if sort != 'relevance':
            body['sort'] = sort
        if facets:
            body['facets'] = list(facets)
        if scope:
            body['scope'] = scope
        matrix = self._load_encryption_matrix()
        if matrix is not None:
            body['encryption_matrix'] = matrix
        return body
```

- [ ] **Step 5: Hit rows, top chunks, envelope**

In `_unwrap_secured_query_response` (231–267), after the `message` copy (line 266) and before `return out`, add:

```python
    for key in ('total_ranked', 'has_more', 'offset', 'limit', 'sort',
                'facets', 'no_strong_matches'):
        if out.get(key) is None and data.get(key) is not None:
            out[key] = data.get(key)
```

Replace the loop body of `_normalize_top_chunks` (lines 746–752) with:

```python
    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        normalized = _coalesce_secured_query_keys(chunk)
        row = _location_from_mapping(normalized)
        row.update(_score_fields_from_mapping(normalized))
        end = _coerce_location_int(normalized.get('sentence_number_end'))
        if end is not None:
            row['sentence_number_end'] = end
        for key in ('chunk_uuid', 'chunk_kind'):
            val = normalized.get(key)
            if isinstance(val, str) and val.strip():
                row[key] = val.strip()
        snippet = normalized.get('snippet')
        if isinstance(snippet, str) and snippet.strip():
            row['snippet'] = snippet.strip()[:_SNIPPET_MAX_CHARS]
        if row:
            out.append(row)
    return out
```

Add two helpers directly above `_secured_query_hit_to_row`:

```python
def _hit_field(item: Dict[str, Any], key: str) -> Any:
    """Top-level first, then the nested metadata block; None when absent."""
    val = item.get(key)
    if val is None:
        meta = item.get('metadata') if isinstance(item.get('metadata'), dict) else {}
        val = meta.get(key)
    return val


def _primary_snippet(top_chunks: List[Dict[str, Any]]) -> Optional[str]:
    """First passage snippet; an auto_summary chunk only when nothing else has text."""
    for chunk in top_chunks:
        if chunk.get('snippet') and chunk.get('chunk_kind') != 'auto_summary':
            return chunk['snippet']
    for chunk in top_chunks:
        if chunk.get('snippet'):
            return chunk['snippet']
    return None
```

In `_secured_query_hit_to_row`, replace the trailing block

```python
    if top_chunks:
        row["top_chunks"] = top_chunks
    return row
```

with:

```python
    for key in _HIT_METADATA_KEYS:
        val = _hit_field(item, key)
        if isinstance(val, str) and val.strip():
            row[key] = val.strip()
    has_versions = _hit_field(item, 'has_versions')
    if has_versions is not None:
        row['has_versions'] = bool(has_versions)
    version_count = _coerce_location_int(_hit_field(item, 'version_count'))
    if version_count is not None:
        row['version_count'] = version_count
    is_current = _hit_field(item, 'is_current')
    if is_current is not None:
        row['is_current'] = bool(is_current)
    for key in ('relevance_tier', 'score_mode'):
        val = item.get(key)
        if isinstance(val, str) and val.strip():
            row[key] = val.strip()
    if item.get('fusion_score') is not None:
        row['fusion_score'] = item['fusion_score']
    kg_ids = item.get('kg_node_ids')
    if isinstance(kg_ids, list):
        row['kg_node_ids'] = [str(x) for x in kg_ids if x]
    if top_chunks:
        row["top_chunks"] = top_chunks
        if top_chunks[0].get('chunk_kind'):
            row['primary_chunk_kind'] = top_chunks[0]['chunk_kind']
        if top_chunks[0].get('chunk_uuid'):
            row['chunk_uuid'] = top_chunks[0]['chunk_uuid']
        snippet = _primary_snippet(top_chunks)
        if snippet:
            row['snippet'] = snippet
    return row
```

- [ ] **Step 6: `search_documents` and `_search_documents_secured`**

Replace `search_documents` (1463–1499) with:

```python
    def search_documents(
        self,
        query: Union[str, List[str]],
        *,
        filters: Optional[Dict[str, Any]] = None,
        limit: Optional[int] = None,
        offset: int = 0,
        sort: str = 'relevance',
        facets: Optional[List[str]] = None,
        scope: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Search documents in Knovas (POST /secured/query, F3 contract).

        filters: API filter keys only (API_FILTER_KEYS); other keys are logged
        and dropped. offset is a window over ONE ranked, gated set (never a
        corpus offset). facets are counted over the ranked, ACL-filtered pool.
        Raises SecuredApiError on 4xx/5xx, ValueError on a malformed request.
        """
        if self.use_secured_api and self.mtls_enabled:
            return self._search_documents_secured(
                query, filters=filters, limit=limit, offset=offset,
                sort=sort, facets=facets, scope=scope)

        if self.use_secured_api and not self.allow_legacy_api_fallback:
            raise RuntimeError(
                "Secured API mode is enabled but mTLS cert paths are not configured. "
                "Set SEMANTIX_CLIENT_CERT, SEMANTIX_CLIENT_KEY and SEMANTIX_CA_CERT, "
                "or explicitly enable legacy fallback for mock/dev."
            )

        endpoint = self.endpoints['search']
        params: Dict[str, Any] = {'query': query}
        if limit:
            params['limit'] = int(limit)
        if offset:
            params['offset'] = int(offset)
        if sort != 'relevance':
            params['sort'] = sort
        params.update(_api_filters_only(filters))

        try:
            response = self._make_request(method='GET', endpoint=endpoint, params=params)
            result = response.json()
            logger.info(f"Legacy search successful: query='{query}', results={len(result.get('results', []))}")
            return result
        except Exception as e:
            logger.error(f"Error during search: {e}")
            raise
```

Replace `_search_documents_secured` (1751–1806) with:

```python
    def _search_documents_secured(
        self,
        query: Union[str, List[str]],
        *,
        filters: Optional[Dict[str, Any]] = None,
        limit: Optional[int] = None,
        offset: int = 0,
        sort: str = 'relevance',
        facets: Optional[List[str]] = None,
        scope: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        endpoint = self.endpoints.get('query', '/secured/query')
        body = self._secured_query_request_body(
            query, limit=limit, filters=filters, offset=offset, sort=sort,
            facets=facets, scope=scope)
        try:
            response = self._make_request(method='POST', endpoint=endpoint, data=body)
        except requests.exceptions.HTTPError as exc:
            raise _secured_api_error_from(exc) from exc
        result = _unwrap_secured_query_response(response.json())
        # Coerce to [] so a null/absent "results" never crashes None[:limit].
        raw_hits = list(result.get("results") or [])
        if limit is not None and limit > 0:
            raw_hits = raw_hits[:limit]

        normalized_results = []
        for raw in raw_hits:
            if not isinstance(raw, dict):
                continue
            normalized_results.append(_secured_query_hit_to_row(raw))

        if normalized_results and all(
            r.get("page_number") is None
            and r.get("sentence_number") is None
            and not (r.get("top_chunks") or [])
            for r in normalized_results[:3]
        ):
            raw0 = raw_hits[0] if raw_hits and isinstance(raw_hits[0], dict) else {}
            logger.info(
                "Secured query: no page/sentence in first hits; raw[0] keys=%s "
                "page_number=%r sentence_number=%r top_chunks=%r",
                sorted(raw0.keys()) if raw0 else [],
                raw0.get("page_number"),
                raw0.get("sentence_number"),
                raw0.get("top_chunks") if isinstance(raw0.get("top_chunks"), list) else raw0.get("top_chunks"),
            )

        semantix_meta = {
            'status': result.get('status'),
            'message': result.get('message'),
            'result_count': result.get('result_count'),
            'pointers': result.get('pointers'),
            'query_session_id': result.get('query_session_id'),
        }
        # paging_supported: the tenant answered with the F3 envelope. Without it
        # an offset > 0 silently returned page one again -- say so instead.
        paging_supported = ('total_ranked' in result) or ('offset' in result)
        return {
            'results': normalized_results,
            'total': len(normalized_results),
            'total_ranked': _int_or(result.get('total_ranked'), len(normalized_results)),
            'has_more': bool(result.get('has_more') or False),
            'offset': _int_or(result.get('offset'), _int_or(offset, 0)),
            'limit': _int_or(result.get('limit'), limit),
            'sort': str(result.get('sort') or sort),
            'facets': _normalize_facets(result.get('facets')),
            'no_strong_matches': bool(result.get('no_strong_matches') or False),
            'paging_supported': paging_supported,
            'semantix': semantix_meta,
        }
```

- [ ] **Step 7: Versions, similar, metadata**

Append after `_search_documents_secured`, before `_sync_single_document_secured`:

```python
    # ------------------------------------------------------------------
    # Dokument-Endpunkte (F6 Versionen, F8 Aehnliche, Metadaten-PATCH).
    # 404 bedeutet unbekannt oder fremd -> None, dieselbe Regel wie im
    # Graph-Client. Alles andere ausser 2xx wird SecuredApiError.
    # ------------------------------------------------------------------

    def document_versions(self, document_uuid: str) -> Optional[Dict[str, Any]]:
        """GET /secured/document/<uuid>/versions - Versionsliste (F6, Stufe 1)."""
        uuid = str(document_uuid or '').strip()
        if not uuid:
            raise ValueError('document_uuid is required')
        endpoint = f'/secured/document/{quote(uuid, safe="")}/versions'
        try:
            response = self._make_request('GET', endpoint)
        except requests.exceptions.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                logger.info("Document versions 404 (unbekannte oder fremde Id): %s", uuid)
                return None
            raise _secured_api_error_from(exc) from exc
        body = response.json() or {}
        data = body.get('data') if isinstance(body.get('data'), dict) else body
        current = data.get('current') if isinstance(data.get('current'), dict) else {}
        versions = [v for v in (data.get('versions') or []) if isinstance(v, dict)]
        return {'current': current, 'versions': versions}

    def similar_documents(
        self,
        document_uuid: str,
        *,
        limit: Optional[int] = None,
        filters: Optional[Dict[str, Any]] = None,
        scope: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """POST /secured/documents/<uuid>/similar - Treffer in der Query-Form (F8)."""
        uuid = str(document_uuid or '').strip()
        if not uuid:
            raise ValueError('document_uuid is required')
        payload: Dict[str, Any] = {}
        if limit is not None and int(limit) > 0:
            payload['limit'] = int(limit)
        api_filters = _api_filters_only(filters)
        if api_filters:
            payload['filters'] = api_filters
        if scope:
            payload['scope'] = scope
        endpoint = f'/secured/documents/{quote(uuid, safe="")}/similar'
        try:
            response = self._make_request('POST', endpoint, data=payload)
        except requests.exceptions.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                logger.info("Similar documents 404 (unbekannte oder fremde Id): %s", uuid)
                return None
            raise _secured_api_error_from(exc) from exc
        result = _unwrap_secured_query_response(response.json() or {})
        rows = [_secured_query_hit_to_row(h) for h in (result.get('results') or [])
                if isinstance(h, dict)]
        return {
            'results': rows,
            'total': len(rows),
            'no_strong_matches': bool(result.get('no_strong_matches') or False),
            'semantix': {
                'status': result.get('status'),
                'message': result.get('message'),
                'query_session_id': result.get('query_session_id'),
            },
        }

    def update_document_metadata(self, document_uuid: str,
                                 metadata: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """PATCH /secured/documents/<uuid>/metadata - Metadaten ohne Neu-Upload."""
        uuid = str(document_uuid or '').strip()
        if not uuid:
            raise ValueError('document_uuid is required')
        if not isinstance(metadata, dict) or not metadata:
            raise ValueError('metadata must be a non-empty dict')
        unknown = sorted(k for k in metadata if k not in METADATA_KEYS)
        if unknown:
            raise ValueError(f'unknown metadata keys: {unknown}')
        endpoint = f'/secured/documents/{quote(uuid, safe="")}/metadata'
        try:
            # Mutating: single shot, like init/transmit/delete.
            response = self._request_no_retry('PATCH', endpoint, data=dict(metadata))
        except requests.exceptions.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                return None
            raise _secured_api_error_from(exc) from exc
        try:
            return response.json() or {}
        except ValueError:
            return {}
```

- [ ] **Step 8: Update the C5 hardening test to the contract**

In `tests/test_knovas_client_hardening.py` replace lines 285–302 (`class TestC5SecuredFilters`) with:

```python
class TestC5SecuredFilters:
    def test_api_filters_forwarded_into_secured_request_body(self):
        client = make_secured_client()
        captured = {}

        def responder(method, url, **kw):
            captured.update(kw)
            return FakeResponse(200, {"results": []})

        client._session = FakeSession(responder)

        client.search_documents("hello world", limit=5, filters={"author": ["Muster"]})

        body = captured.get("json")
        assert isinstance(body, dict)
        assert body.get("filters") == {"author": ["Muster"]}, (
            "secured search silently dropped an API filter (scoping ignored)"
        )

    def test_ui_only_filter_is_stripped_not_forwarded(self):
        client = make_secured_client()
        captured = {}

        def responder(method, url, **kw):
            captured.update(kw)
            return FakeResponse(200, {"results": []})

        client._session = FakeSession(responder)

        client.search_documents("hello world", limit=5, filters={"exact_match": True})

        assert "filters" not in captured.get("json", {})
```

Change docstring line 11 to: `  C5  API ``filters`` must not be silently dropped in secured search mode; UI-only keys never leave the platform.`

- [ ] **Step 9: Run the client suites**

Run: `py -3.13 -m pytest tests/test_knovas_client_search_contract.py tests/test_knovas_client_hardening.py tests/test_knovas_client_secured_api.py tests/test_knovas_query_parse.py -v`
Expected: PASS (the cassette test reports SKIPPED until Step 10 has run against the dev tenant).

- [ ] **Step 10: Live cassette recorder**

Create `tests/test_search_contract_live.py`:

```python
"""Records the live /secured/query, versions and similar shapes from the dev tenant.

Skipped unless --knovas-api is passed (tests/conftest.py). Re-record
deliberately: tests/test_knovas_client_search_contract.py asserts against the
file, so a changed shape fails a test instead of misreporting a screen.
"""
from __future__ import annotations

import json
import pathlib

import pytest

pytestmark = pytest.mark.knovas_api

CASSETTE = pathlib.Path(__file__).parent / "cassettes" / "search_contract.json"


@pytest.fixture(scope="module")
def live_client():
    from config_loader import get_config
    from knovas_client import KnovasAPIClient
    return KnovasAPIClient(get_config())


def test_record_search_contract(live_client):
    recorded = {}
    query = live_client._make_request(
        "POST", "/secured/query",
        data={"Input": "Vertrag", "limit": 3, "top_k": 3, "offset": 0,
              "sort": "relevance", "facets": ["author", "document_type"]}).json()
    recorded["POST /secured/query"] = query
    hits = query.get("results") or []
    assert hits, "the dev tenant must have at least one indexed document"

    uuid = hits[0]["document_uuid"]
    recorded["GET /secured/document/<uuid>/versions"] = live_client._make_request(
        "GET", f"/secured/document/{uuid}/versions").json()
    recorded["POST /secured/documents/<uuid>/similar"] = live_client._make_request(
        "POST", f"/secured/documents/{uuid}/similar", data={"limit": 3}).json()

    CASSETTE.parent.mkdir(parents=True, exist_ok=True)
    CASSETTE.write_text(json.dumps(recorded, indent=2, ensure_ascii=False),
                        encoding="utf-8")
```

Run once the KB parts are deployed on the dev tenant:
`SEMANTIX_API_URL=<dev url> py -3.13 -m pytest tests/test_search_contract_live.py --knovas-api -v`
Expected: PASS and `tests/cassettes/search_contract.json` written. If `tests/cassettes/README.md` (section-C plan Task B1) exists, append the line `search_contract.json — recorded by tests/test_search_contract_live.py (query / versions / similar).`; otherwise create it with that line under a `# Search contract cassette` heading.

- [ ] **Step 11: Commit**

```bash
git add src/knovas_client.py tests/test_knovas_client_search_contract.py \
        tests/test_search_contract_live.py tests/test_knovas_client_hardening.py tests/cassettes/
git commit -m "feat(search): F3 query contract in the client - filters, paging, facets, versions, similar, metadata"
```

---

---

### Task KC-A-6: Document dialog — versions, similar documents, metadata edit, tables in the preview
**Requirements:** F6, F8, H4 (and the F3 metadata fields the dialog edits)
**Files:**
- Create: `src/web_interface/document_routes.py` (Blueprint `documents_api`, `register_document_routes(app, api_client, *, enhance=None)`).
- Modify: `src/web_interface/app.py` — register the blueprint immediately before `@app.route('/api/ontology/summary'` (line 1717); no other change.
- Modify: `src/web_interface/preview.py` — `extract_markdown` (lines 45–89) also returns `tables`.
- Modify: `src/web_interface/static/js/markdown.js` — table support inside `render` (lines 38–75) plus `hasTable` / `renderTable` on `window.KnovasMarkdown` (line 77).
- Modify: `src/web_interface/templates/index.html` — details block between `#previewActions` (line 95) and `#previewBody` (line 96).
- Modify: `src/web_interface/static/js/app.js` — constructor (lines 24–60), `initializeEventListeners` (78–162), `_afterPreviewClosed` (243–254), `_updatePreviewPosition` (259–264), `openPreview` (281–367) refactored into `_showDocument`, new methods appended after `_previewActionsHtml` (215–234).
- Modify: `src/web_interface/static/css/style.css` — append after `.preview-skeleton` rules (line ~1010).
- Modify: `tests/test_csrf_enforcement.py` — docstring lines 7–8, one new negative test appended.
- Modify: `tests/test_preview_extract.py` — append one test.
- Create: `tests/test_document_routes.py`, `tests/test_js_smoke.py`, `KnovasPlatform/docs/integration/documents-api.md`.
**Interfaces:**
- Consumes: `KnovasAPIClient.document_versions(document_uuid)`, `.similar_documents(document_uuid, *, limit, filters, scope)`, `.update_document_metadata(document_uuid, metadata)`, `SecuredApiError(status, error_code, message, details)` with `.retry_after`, `METADATA_KEYS` (all defined in Task KC-A-1); `_enhance_search_results(results, file_handler, config)` (app.py:2418, unchanged); result-row fields `document_uuid, has_versions, version_count, is_current, author, document_type, language, document_date, document_status, top_chunks[].snippet` (Task KC-A-1); the app-level test pattern of `tests/test_ontology_api.py`.
- Produces:
  - `GET /api/documents/<document_uuid>/versions` → `200 {success: true, document_uuid, current: {...}, versions: [{version_number, content_hash_raw, pointer_at_version, path, timestamp, changed_by, changed_by_kind}], version_count: int}`; `404 {success:false, error:'Dokument nicht gefunden'}` for unknown/foreign/malformed ids; API `400|404|409|422|429|503` passed through as `{success:false, error, error_code[, details]}` (+ `Retry-After`); any other API failure → `502 {error:'Knovas API nicht erreichbar'}`; never 500 with detail.
  - `POST /api/documents/<document_uuid>/similar` body `{limit?: 1..20 (default 5), filters?: {API filter keys}, scope?: {...}}` (CSRF header required) → `200 {success: true, document_uuid, results: [enriched result rows], total, no_strong_matches: bool, similar_matters: [{node_id, hit_count}] (grouped from visible kg_node_ids, descending), semantix: {status, message, query_session_id}}`; same error mapping.
  - `PATCH /api/documents/<document_uuid>/metadata` body: subset of `author, document_type, language, document_date, document_status, source_kind, extra` (CSRF header required) → `200 {success: true, document_uuid, metadata: {...as stored}}`; `400 {success:false, error:'<German validation message>'}` before the API on bad values; API validation errors surfaced verbatim (`error`, `error_code`, `details`), never dropped.
  - `document_routes.validate_metadata_patch(body: Any) -> dict` (raises `ValueError` with the German message), `document_routes.group_similar_matters(rows) -> list[dict]`, constants `DOCUMENT_STATUS_VALUES`, `SOURCE_KIND_VALUES`, `SIMILAR_LIMIT_MAX = 20`.
  - `preview.extract_markdown(path)` result gains `tables: [{client_table_hint, title, headers, rows, page}]`; `GET /api/document/<id>/preview-content` response gains `tables` (same shape).
  - `window.KnovasMarkdown.render(markdown)` renders GFM pipe tables as `<table class="md-table">`; `window.KnovasMarkdown.hasTable(markdown) -> boolean`; `window.KnovasMarkdown.renderTable({title, headers, rows}) -> string` (escaped).
  - `app.openAdhocDocument(doc, originIndex)` (dialog shows a document that is not in the result list); dialog DOM ids `previewDetails, previewVersions, previewVersionsHint, previewVersionsBody, previewSimilar, previewSimilarBody, previewMetadata, previewMetadataBody`.

- [ ] **Step 1: Write the failing route tests**

Create `tests/test_document_routes.py`:

```python
"""/api/documents/<uuid>/{versions,similar,metadata} - the endpoints behind the
Vorschau-Dialog (F6 Versionen, F8 Aehnliche, Metadaten-PATCH).

App-construction and login follow tests/test_ontology_api.py. The Knovas client
is a stub with class-level results so a test can set the answer before the app
is built.
"""
import json
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
WEB_SRC = SRC / "web_interface"
for p in (SRC, WEB_SRC):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from knovas_client import SecuredApiError  # noqa: E402

UUID = "1a2b3c4d-0000-4000-8000-000000000001"


class StubDocumentsClient:
    versions_result = None
    similar_result = None
    metadata_result = None
    raise_exc = None
    calls = []

    def __init__(self, config):
        self.config = config

    def health_check(self):
        return True

    def search_documents(self, query, **kwargs):
        return {"results": [], "total": 0}

    def document_versions(self, document_uuid):
        StubDocumentsClient.calls.append(("versions", document_uuid))
        if StubDocumentsClient.raise_exc:
            raise StubDocumentsClient.raise_exc
        return StubDocumentsClient.versions_result

    def similar_documents(self, document_uuid, *, limit=None, filters=None, scope=None):
        StubDocumentsClient.calls.append(("similar", document_uuid, limit, filters, scope))
        if StubDocumentsClient.raise_exc:
            raise StubDocumentsClient.raise_exc
        return StubDocumentsClient.similar_result

    def update_document_metadata(self, document_uuid, metadata):
        StubDocumentsClient.calls.append(("metadata", document_uuid, metadata))
        if StubDocumentsClient.raise_exc:
            raise StubDocumentsClient.raise_exc
        return StubDocumentsClient.metadata_result


class TmpAutodocHandler:
    def __init__(self, root):
        self.autodoc_path = str(root)


def _build_app(tmp_path, monkeypatch, autodoc_root):
    monkeypatch.setenv("WEB_SECRET_KEY", "test-secret-documents")
    monkeypatch.setenv("COMPANY_LOGIN_ENABLED", "true")
    monkeypatch.setenv("COMPANY_DISPLAY_NAME", "Test Company")
    monkeypatch.setenv("COMPANY_LOGIN_NAME", "office")
    monkeypatch.setenv("COMPANY_LOGIN_PASSWORD", "s3cret")
    monkeypatch.delenv("AUTODOC_IDENTIFIER_PREFIX", raising=False)

    ad_str = str(autodoc_root).replace("\\", "/")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""
web:
  secret_key: "${{WEB_SECRET_KEY}}"
  session_lifetime: 3600
  login:
    enabled: "${{COMPANY_LOGIN_ENABLED:-true}}"
    company_name: "${{COMPANY_DISPLAY_NAME:-Knovas}}"
    username: "${{COMPANY_LOGIN_NAME}}"
    password: "${{COMPANY_LOGIN_PASSWORD}}"
  search:
    results_per_page: 20
    verify_files_on_disk: false
api:
  base_url: "http://example.test"
open:
  companion_enabled: false
  local_root: "{ad_str}"
""",
        encoding="utf-8",
    )

    import web_interface.app as web_app
    monkeypatch.setattr(web_app, "KnovasAPIClient", StubDocumentsClient)
    monkeypatch.setattr(web_app, "AutoDocFileHandler", lambda: TmpAutodocHandler(autodoc_root))
    flask_app = web_app.create_app(str(config_path))
    flask_app.config.update(TESTING=True)
    return flask_app


@pytest.fixture()
def docs_app(tmp_path, monkeypatch):
    for attr in ("versions_result", "similar_result", "metadata_result", "raise_exc"):
        setattr(StubDocumentsClient, attr, None)
    StubDocumentsClient.calls = []
    ad = tmp_path / "autodoc"
    ad.mkdir()
    return _build_app(tmp_path, monkeypatch, ad)


def _login(client):
    client.get("/login")
    with client.session_transaction() as sess:
        token = sess["csrf_token"]
    client.post("/login", data={"login_name": "office", "password": "s3cret",
                                "csrf_token": token})
    with client.session_transaction() as sess:
        return {"X-CSRF-Token": sess["csrf_token"]}


## --- versions (F6) -----------------------------------------------------------

def test_versions_requires_login(docs_app):
    client = docs_app.test_client()
    assert client.get(f"/api/documents/{UUID}/versions").status_code == 401


def test_versions_returns_current_and_versions(docs_app):
    StubDocumentsClient.versions_result = {
        "current": {"document_uuid": UUID, "version_number": 3, "pointer": "a/v3.pdf"},
        "versions": [
            {"version_number": 2, "content_hash_raw": "h2", "pointer_at_version": "a/v2.pdf",
             "path": "/a/v2.pdf", "timestamp": "2026-02-01T10:00:00Z",
             "changed_by": "rc-01", "changed_by_kind": "client_ref"},
            {"version_number": 1, "content_hash_raw": "h1", "pointer_at_version": "a/v1.pdf",
             "path": "/a/v1.pdf", "timestamp": "2026-01-01T10:00:00Z",
             "changed_by": "rc-01", "changed_by_kind": "client_ref"},
        ],
    }
    client = docs_app.test_client()
    _login(client)

    resp = client.get(f"/api/documents/{UUID}/versions")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert data["document_uuid"] == UUID
    assert data["current"]["version_number"] == 3
    assert data["version_count"] == 2
    assert data["versions"][0]["changed_by_kind"] == "client_ref"
    assert ("versions", UUID) in StubDocumentsClient.calls


def test_versions_unknown_or_foreign_uuid_is_404(docs_app):
    client = docs_app.test_client()
    _login(client)
    resp = client.get(f"/api/documents/{UUID}/versions")
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "Dokument nicht gefunden"


def test_versions_malformed_uuid_is_404_without_calling_the_api(docs_app):
    client = docs_app.test_client()
    _login(client)
    resp = client.get("/api/documents/nicht%20eine%20uuid/versions")
    assert resp.status_code == 404
    assert StubDocumentsClient.calls == []


def test_versions_api_503_is_passed_through_with_its_code(docs_app):
    StubDocumentsClient.raise_exc = SecuredApiError(
        503, "relevance_calibration_missing", "calibration missing")
    client = docs_app.test_client()
    _login(client)
    resp = client.get(f"/api/documents/{UUID}/versions")
    assert resp.status_code == 503
    assert resp.get_json()["error_code"] == "relevance_calibration_missing"


def test_versions_api_401_becomes_502_not_a_login_redirect(docs_app):
    """A tenant-API 401 is a certificate problem, not the user's session; app.js
    would send the user to /login on a 401."""
    StubDocumentsClient.raise_exc = SecuredApiError(401, "unauthorized", "bad cert")
    client = docs_app.test_client()
    _login(client)
    resp = client.get(f"/api/documents/{UUID}/versions")
    assert resp.status_code == 502
    assert "bad cert" not in resp.get_data(as_text=True)


## --- similar (F8) ------------------------------------------------------------

def test_similar_requires_csrf(docs_app):
    client = docs_app.test_client()
    _login(client)
    resp = client.post(f"/api/documents/{UUID}/similar", json={"limit": 5})
    assert resp.status_code == 403


def test_similar_forwards_limit_filters_scope_and_groups_matters(docs_app):
    StubDocumentsClient.similar_result = {
        "results": [
            {"doc_id": "a/x.pdf", "path": "a/x.pdf", "score": 0.8, "title": "X",
             "document_uuid": "u-x", "kg_node_ids": ["n1", "n2"]},
            {"doc_id": "a/y.pdf", "path": "a/y.pdf", "score": 0.7, "title": "Y",
             "document_uuid": "u-y", "kg_node_ids": ["n1"]},
        ],
        "total": 2, "no_strong_matches": False,
        "semantix": {"status": "success", "message": None, "query_session_id": "s-9"},
    }
    client = docs_app.test_client()
    headers = _login(client)

    resp = client.post(f"/api/documents/{UUID}/similar", headers=headers,
                       json={"limit": 3, "filters": {"language": ["de"]},
                             "scope": {"node_ids": ["n1"]}})

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert data["total"] == 2
    assert [r["doc_id"] for r in data["results"]] == ["a/x.pdf", "a/y.pdf"]
    # Enrichment ran: the platform-side keys are present.
    assert "autodoc_rel_path" in data["results"][0]
    assert data["similar_matters"] == [{"node_id": "n1", "hit_count": 2},
                                       {"node_id": "n2", "hit_count": 1}]
    assert data["semantix"]["query_session_id"] == "s-9"
    assert ("similar", UUID, 3, {"language": ["de"]}, {"node_ids": ["n1"]}) in StubDocumentsClient.calls


def test_similar_limit_is_clamped_and_defaults_to_five(docs_app):
    StubDocumentsClient.similar_result = {"results": [], "total": 0, "no_strong_matches": True}
    client = docs_app.test_client()
    headers = _login(client)

    client.post(f"/api/documents/{UUID}/similar", headers=headers, json={"limit": 999})
    client.post(f"/api/documents/{UUID}/similar", headers=headers, json={})

    limits = [c[2] for c in StubDocumentsClient.calls if c[0] == "similar"]
    assert limits == [20, 5]


def test_similar_no_strong_matches_flag_survives(docs_app):
    StubDocumentsClient.similar_result = {"results": [], "total": 0, "no_strong_matches": True}
    client = docs_app.test_client()
    headers = _login(client)
    data = client.post(f"/api/documents/{UUID}/similar", headers=headers, json={}).get_json()
    assert data["results"] == []
    assert data["no_strong_matches"] is True
    assert data["similar_matters"] == []


def test_similar_404_when_document_unknown(docs_app):
    client = docs_app.test_client()
    headers = _login(client)
    resp = client.post(f"/api/documents/{UUID}/similar", headers=headers, json={})
    assert resp.status_code == 404


## --- metadata (PATCH) --------------------------------------------------------

def test_metadata_patch_requires_csrf(docs_app):
    client = docs_app.test_client()
    _login(client)
    resp = client.patch(f"/api/documents/{UUID}/metadata", json={"author": "X"})
    assert resp.status_code == 403


def test_metadata_patch_rejects_bad_values_before_the_api(docs_app):
    client = docs_app.test_client()
    headers = _login(client)

    bad = [
        {"document_status": "signed"},
        {"document_date": "01.03.2026"},
        {"language": "deutsch"},
        {"title": "nope"},
        {},
    ]
    for body in bad:
        resp = client.patch(f"/api/documents/{UUID}/metadata", headers=headers, json=body)
        assert resp.status_code == 400, body
        assert resp.get_json()["success"] is False
    assert not [c for c in StubDocumentsClient.calls if c[0] == "metadata"]


def test_metadata_patch_success_echoes_stored_metadata(docs_app):
    StubDocumentsClient.metadata_result = {
        "status": "success", "document_uuid": UUID,
        "metadata": {"document_status": "executed", "language": "de"}}
    client = docs_app.test_client()
    headers = _login(client)

    resp = client.patch(f"/api/documents/{UUID}/metadata", headers=headers,
                        json={"document_status": "executed", "language": "DE"})

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert data["metadata"]["document_status"] == "executed"
    assert ("metadata", UUID, {"document_status": "executed", "language": "de"}) in StubDocumentsClient.calls


def test_metadata_patch_surfaces_api_validation_error(docs_app):
    StubDocumentsClient.raise_exc = SecuredApiError(
        400, "validation_error", "document_date: liegt in der Zukunft",
        {"field": "document_date"})
    client = docs_app.test_client()
    headers = _login(client)

    resp = client.patch(f"/api/documents/{UUID}/metadata", headers=headers,
                        json={"document_date": "2099-01-01"})

    assert resp.status_code == 400
    data = resp.get_json()
    assert data["error"] == "document_date: liegt in der Zukunft"
    assert data["error_code"] == "validation_error"
    assert data["details"] == {"field": "document_date"}


def test_metadata_patch_404_when_document_unknown(docs_app):
    client = docs_app.test_client()
    headers = _login(client)
    resp = client.patch(f"/api/documents/{UUID}/metadata", headers=headers,
                        json={"author": "X"})
    assert resp.status_code == 404


## --- pure helpers ------------------------------------------------------------

def test_validate_metadata_patch_normalises_and_rejects():
    from web_interface.document_routes import validate_metadata_patch

    out = validate_metadata_patch({"language": " FR ", "author": " Dr. Muster ",
                                   "document_date": "2026-03-01", "extra": {"eml:message_id": "<x>"}})
    assert out == {"language": "fr", "author": "Dr. Muster",
                   "document_date": "2026-03-01", "extra": {"eml:message_id": "<x>"}}
    with pytest.raises(ValueError):
        validate_metadata_patch({"source_kind": "fax"})
    with pytest.raises(ValueError):
        validate_metadata_patch({"extra": {str(i): "v" for i in range(17)}})
    with pytest.raises(ValueError):
        validate_metadata_patch({"author": ""})


def test_group_similar_matters_counts_and_sorts():
    from web_interface.document_routes import group_similar_matters

    rows = [{"kg_node_ids": ["b", "a"]}, {"kg_node_ids": ["a"]}, {}, {"kg_node_ids": None}]
    assert group_similar_matters(rows) == [{"node_id": "a", "hit_count": 2},
                                           {"node_id": "b", "hit_count": 1}]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `py -3.13 -m pytest tests/test_document_routes.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'web_interface.document_routes'` on the two helper tests and 404s on every route test.

- [ ] **Step 3: Implement the blueprint**

Create `src/web_interface/document_routes.py`:

```python
"""Dokument-Endpunkte hinter dem Vorschau-Dialog: Versionen (F6),
aehnliche Dokumente (F8) und Metadaten ohne Neu-Upload (PATCH).

Eigener Blueprint statt weiterer Closures in create_app(). Regeln wie bei
den Graph-Routen: ein 404 der API bleibt 404 (unbekannt oder fremd),
bekannte Fehlercodes reichen wir mit ihrem Status weiter, alles andere
ist ein 500 ohne Innenleben.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Callable, Dict, List, Optional

from flask import Blueprint, jsonify, request

from knovas_client import METADATA_KEYS, SecuredApiError

logger = logging.getLogger(__name__)

_GENERIC_ERROR = 'Interner Serverfehler'
_NOT_FOUND = 'Dokument nicht gefunden'
_UUID_RE = re.compile(r'^[0-9a-fA-F-]{8,64}

---

## Appendix: tasks specified but not yet expanded

These task IDs are part of the plan's structure (Part Overview, traceability table, neighbouring **Interfaces** blocks) but their step-by-step bodies are **not yet written**. Each line is the task's brief — precise enough to expand, not licence to improvise. Expand one to the same standard as the tasks above (failing test → run → minimal implementation → run → commit, real code in every step) before starting it.

**KC-A (search UI — KC-A-1, 6, 7, 8 are written)**

| ID | Scope |
| --- | --- |
| KC-A-2 | `src/search_filters.py`: split the UI filter payload into "forward to the API" (`filters`, `sort`, `limit`, `offset`, `facets`, `scope`) and "apply locally" (`exact_match` and the other refinements `_apply_search_refinement` already implements); an allowed-filter list under `web.search.filters` in `config.yaml`; rework `app.py::search()` so UI-only keys are never forwarded (today every unknown key goes to `/secured/query` and logs a warning on every search) and so an API `400 validation_error` is surfaced with its `field`. Tests over the route contract. |
| KC-A-3 | Filter rail in `index.html` + `static/js/filters.js`: Akte (matter picker → `scope.node_ids` via the section-C `/api/graph/nodes?node_type_id=<Mandat>`), Praxisgebiet (matters whose `semantic_role=practice_area` fact matches → `scope`), Dokumenttyp, Autor, Zeitraum, Sprache, Status, Quelle, sort selector, and a real "Weitere Treffer" using `offset` instead of re-running the query with a doubled limit. `app.js` sends the typed payload; facet chips render from the response. |
| KC-A-4 | Hit card and honesty: metaline `Typ · Datum · Autor · Sprache` plus a version badge; `auto_summary` chunk hits labelled "KI-Zusammenfassung"; the empty state renders `no_strong_matches` and `semantix.status` instead of ignoring them; a persistent "Beispieldaten" banner whenever `SEARCH_USE_TEST_RESULTS` is on. |
| KC-A-5 | "Wer kennt sich aus?" (D5): a rail action that re-runs the current query with `facets=["author"]` and renders the author facet as a ranked list, each entry linking to the query filtered to that author. |

**KC-B (Parteien, Zefix, Konfliktprüfung — KC-B-1 is written)**

| ID | Scope |
| --- | --- |
| KC-B-2 | `src/web_interface/parties_routes.py` blueprint: `/parteien` page and `/api/parties`, `/api/parties/search`, `/api/parties/<id>`, `/api/parties/duplicates`, `/api/parties/merge`; party node types from `web.graph.party_node_types`; identifier editor with kinds; Dubletten queue with a merge sheet stating "Quelle bleibt als Verweis erhalten"; merge is a section-B guarded action (`ApprovalService` kind `party_merge`, admin bypass recorded, `audit.record` on execute). Templates + `static/js/parties.js` + tests incl. CSRF and the approval branch. |
| KC-B-3 | `src/zefix_client.py` + `/api/zefix/lookup`: Zefix public REST from the **customer's** network (`ZEFIX_USERNAME`/`ZEFIX_PASSWORD`, disabled when absent, timeouts, no retry on 4xx); "Aus Zefix übernehmen" uploads a generated `Zefix-Auszug <UID> <Datum>` document through the Platform upload path with `metadata` and `graph_assign`, then creates UID / Sitz / Rechtsform / Status facts with that document's chunk as evidence. State in the UI and the docs that signatories and group structure are **not** available from the cantonal extract. |
| KC-B-4 | `src/web_interface/conflicts_routes.py`: `/konfliktpruefung` form (names + role + context), `POST /api/conflict-checks` with `actor_ref` = the section-B user id, result page grouped by Parteien / Akten / Dokumente with `withheld_count` and `degraded` as prominent callouts, decision form, history list, and a printable `templates/conflict_protocol.html` (print CSS; check id, actor, time, queries, hits, decision, result hash). |
| KC-B-5 | Lateral-hire import (D4): `POST /api/conflict-checks/import` accepting CSV/XLSX (`openpyxl`) with columns client / counterparty / matter / period → validation → one check per row under a bundle context `lateral:<uuid>` → summary table with per-row status and protocol links. Sample fixtures in the tests. |
| KC-B-6 | Sidebar entries and `active_nav` values for both screens; fixture-mode state ("Wissensnetz-Modus erforderlich") wherever the graph is required. |
| KC-B-7 | Docs: `KnovasPlatform/docs/features/matters-and-parties.md` (register, identifier kinds, merge semantics, Zefix scope) and `conflicts-check.md` (workflow, what the evidentiary record contains, the wall policy = counted withheld hits, `degraded`, the protocol, the D4 import format); rows in `KnovasPlatform/docs/integration/graph-api.md`; RELEASE_NOTES lines. |

**KC-C (Fristen, Posteingang — KC-C-1 and KC-C-2a are written)**

| ID | Scope |
| --- | --- |
| KC-C-2b | Finish the deadlines routes: the three tabs (Vorschläge / Zur Bestätigung / Bestätigt) wired to `facts_list`, `fact_adopt`, confirm and reject; the confirm button disabled server-side for the user who entered the fact (compare the ledger's last human actor from the fact history); permanent-rejection copy; the per-matter widget include for the section-C `matter.html`. |
| KC-C-3 | `src/ics_feed.py` + `GET /feeds/deadlines.ics?token=`: RFC 5545 output, one VEVENT per confirmed deadline (`DTSTART` honouring `precision` — a month-precision fact is never drawn on a specific day), `ORGANIZER`/`ATTENDEE` from the matter's `responsible`/`deputy` entity_ref facts (Person nodes with an `email` identifier), `VALARM` −P7D and −P1D, `UID` = fact id, `X-KNOVAS-FACT-ID`; `feed_tokens` table (`0005_feed_tokens.sql`) with create/revoke in settings; golden-text test. |
| KC-C-4 | `src/events_poller.py`: leader election via a `platform-db` advisory lock, `events_poll(after=cursor)` every `EVENTS_POLL_SECONDS` (15), rows into `events` + `event_cursor` (`0002_events.sql`), started from `create_app` when `EVENTS_POLL_ENABLED`; safe under two gunicorn workers; tests with a fake client. |
| KC-C-5 | `src/web_interface/inbox_routes.py`: `/posteingang` grouped by kind (sort proposals, deadline proposals, pending confirmations, contradictions, job completions, conflict checks) with deep links, mark-read, and `/api/inbox/unread-count` for the sidebar badge. |
| KC-C-6 | Ingestion/upload screens poll `transmission_status` for pending keys and show indexed/failed; `Ereignisprotokoll` CSV export at `/api/inbox/export`. |
| KC-C-7 | Overdue escalation banner driven by `graph.fact.confirmation_overdue` events. |
| KC-C-8 | Docs: `features/deadlines.md` (E1 cross-link, the proposal → adopt → confirm chain, four-eyes semantics incl. `actor_kind` honesty, Outlook subscription steps, what a PMS integrator consumes instead), `features/reports-and-inbox.md` (inbox half), `integration/events.md`, `.env.example` keys, RELEASE_NOTES lines. |

**KC-D (Cortex live — KC-D-1..3 are written)**

| ID | Scope |
| --- | --- |
| KC-D-4 | G3 "Warum?" drawer in graph mode: facts with tier chips, evidence rows (pointer, page, quote) opening the viewer through the shared `openEvidence(...)` helper from KC-A-7; reachable from the search Trefferliste for a hit's assigned matter. |
| KC-D-5 | G4 `templates/_trust_chip.html` macro (German tier label, scope tag "firmenweit" / "Ihre Sicht", signals popover: independent sources, supporting links, contradiction pressure, curation status, validity elapsed) + CSS, reused by chronology, dossier, facts and the why-panel. |
| KC-D-6 | G5 `src/web_interface/reports_routes.py`: `/berichte` rendering contradictions and completeness with a node-type filter, paging, deep links to node/fact/evidence, and a CSV export. |
| KC-D-7 | G6 `/import` wizard: CSV upload → column mapping (matter number, client, counterparties, responsible lawyer, practice area, status, opened date) → build the `POST /secured/graph/imports` payload (identifiers with kinds, facts by `semantic_role`, Person nodes with email identifiers) → dry-run diff → apply → progress via `graph_job`; cross-link to the section-C file-structure bootstrap. |
| KC-D-8 | G7 `GraphOntologySource.create_type_relation` implemented on `target_node_type_id` (section-C A4/A5/B4) and `summary()` returning declared relations with `count: 0` so a dashed Vorgabe survives a reload; enable the type→type path in `ontology_connect.js` in graph mode. |
| KC-D-9 | G8 `GraphFilterEngine` wired to `filters/evaluate|apply|placements|reject|restore` in graph mode; `503 filter_embedding_model_stale` / `relevance_calibration_missing` rendered as "kann gerade nicht bewerten — bitte später" (never as "keine Treffer"); apply progress via `graph_job`; replace `_locate`'s scan over every node with the server-side node filters. |
| KC-D-10 | Docs: `features/import-and-bootstrap.md`, the reports half of `features/reports-and-inbox.md`, the ego section of `features/matters-and-parties.md`, a "Cortex live vs Demo" section in `KnovasPlatform/docs/README.md`, `docs/specifications.md` §2.5 (`ONTOLOGY_*`) and §2.3 (`/secured/graph/*`). |

**KC-E (add-ins and journal — KC-E-1 is written)**

| ID | Scope |
| --- | --- |
| KC-E-2 | `src/web_interface/filing_routes.py`: `POST /api/filing/email` (`{mime_base64|msg_base64, node_id, include_attachments}`, session auth + CSRF, 25 MB body limit with the matching nginx `client_max_body_size` and gunicorn timeout notes, `audit.record`) and `POST /api/filing/suggest` (`{from, to, subject}` → `identifiers_search` → ranked matters, recent matters from the journal when available). |
| KC-E-3 | `KnovasPlatform/components/knovas_office_addins/`: `manifest.outlook.xml` (Mailbox 1.8, `ReadWriteMailbox`, ribbon button "In Knovas ablegen"), `manifest.word.xml`, `taskpane/` (`index.html`, `common.js` login + CSRF, `outlook.js` — MIME via `makeEwsRequestAsync` `GetItem` `IncludeMimeContent` → `POST /api/filing/email`, matter picker with suggestions, toast; `word.js` — search over `/api/search`, "Öffnen" via `client-path` UNC or the companion token, "Zitat einfügen" via `setSelectedDataAsync`), Knovas design tokens in `styles.css`. |
| KC-E-4 | `src/web_interface/addins_routes.py` serving `/addins/*` over the Platform origin with cache headers and a CSP `frame-ancestors` allowing `outlook.office.com`, `office.live.com`, `*.officeapps.live.com` and localhost for development; a manifest well-formedness test (`xml.etree` parse + required elements) and route tests. |
| KC-E-5 | `src/journal.py`: `record(kind, *, user_id, matter_node_id, pointer, page, format, query_hash)` into `activity_journal` (`0003_journal.sql`), opt-in per user in `settings`, retention purge (`JOURNAL_RETENTION_DAYS`, default 90), `day_view(user_id, day)` splitting blocks on gaps > 20 minutes, `csv_export(user_id, from, to)`; hooks in `app.py::search()`, the document-open routes, the matter page and the viewer. |
| KC-E-6 | `src/web_interface/journal_routes.py`: `/mein-tag`, `/api/journal/day`, `/api/journal/export.csv`, `/api/journal/settings`; a user sees only their own rows and admins have no per-person view (works-council-friendly by construction); `/api/journal/format-stats` returns aggregate open-counts by format, which is the measurement the search backlog's pdf.js precondition asked for. |
| KC-E-7 | Docs: `features/activity-journal.md` (consent text, what is recorded and what is not, retention, export columns, PMS import hint) and the `integration/office-add-ins.md` page (architecture + sequence diagram, hosting on the Platform origin over HTTPS, permissions, central deployment vs sideload, on-prem Exchange note, troubleshooting); rows in `KnovasPlatform/components/README.md`, `docs/specifications.md` §2.8, `hosting-requirements.md`; RELEASE_NOTES section. |

**KC-F (RemoteController — KC-F-1 and KC-F-2 are written)**

| ID | Scope |
| --- | --- |
| KC-F-3 | One source of truth for `SYNCABLE_EXTENSIONS` (`document_text.py:49`) from which `DEFAULT_INCLUDE_GLOBS`, `default_sync_body.py`, the OneDrive `DEFAULT_ALLOWED_EXTENSIONS` and the `sync_request.schema.json` description derive — today the list exists in five places and a partial edit silently half-enables a format. |
| KC-F-4 | `src/sync/office_extractors.py`: `XlsxExtractor` (openpyxl `read_only`, `data_only`; one `Table` per worksheet block, ≤ 64 cols / 5 000 rows, ragged rows padded before `map_extractor_tables` drops them, hidden sheets skipped, sheet name as `title`, `client_table_hint = xlsx_s{i}_t{j}`, plus a flattened text rendering) and `PptxExtractor` (python-pptx; one page per slide, slide title as a section, notes included), registered into `knovas_extract.dispatch.MIME_REGISTRY` at RC import — the documented public hook — with provenance recorded as `remote-controller-office` so nothing is misattributed to the certified extractor. Upstreaming to `knovas-extract` is the named follow-up. |
| KC-F-5 | OCR: `tesseract-ocr-ita` in the Dockerfile, default `RC_TESSERACT_LANG=deu+fra+ita+eng`, `result.warnings` and an `ocr_used` flag kept on `ExtractedDocument`, Prometheus `knovas_rc_documents_extracted_total{ext,ocr}` and `knovas_rc_extract_errors_total{reason}` in `routes/metrics.py`, and `scripts/requeue_skipped.py` for rows parked as `skip:unconvertible` (enabling Italian later does not re-ingest them by itself). |
| KC-F-6 | `benchmarks/ocr/`: `build_corpus.py` renders ground-truth DE/FR/IT legal paragraphs to page images at 200/300 dpi with skew and noise (Pillow) → PDF; `run_ocr_benchmark.py` runs `knovas_extract.extract(use_ocr=True, ocr_language=…)` and reports CER/WER per language and dpi into `results/<ts>/{metrics.json,report.md}`; a README with the on-premise "Nachweis auf eigenen Scans" runbook, because real court scans cannot be published. |
| KC-F-7 | `src/mailbox_mirror/`: `graph_mail.py` (client-credentials auth reusing `onedrive_mirror/graph.py`; `mailFolders`, `messages/delta`, `messages/{id}/$value`, `attachments`), `mirror.py` (mailbox allow-list, folder include/exclude, per-folder delta with full-walk fallback, each message materialised as `.eml` under `<MAILBOX_MIRROR_PATH>/<upn>/<folder>/<sha1(internetMessageId)>.eml` with mtime pinned to `receivedDateTime`, attachments beside it as `<key>.att/<name>`, and the two OneDrive invariants copied verbatim: no cursor advance while downloads fail, no prune on incomplete enumeration), `runner.py`, `MAILBOX_*` env gating so a missing config never fails boot. |
| KC-F-8 | PST: `scripts/explode_pst.py` (`readpst -e -j N -o <staging>`, folder hierarchy preserved, `Message-ID` captured, idempotent, timeout) + `src/sync/pst_queue.py` (one PST per cycle from `RC_PST_INBOX`, resumable, state rows), `pst-utils` in the image, writable `RC_PST_INBOX`/`RC_PST_STAGING` volumes in `docker-compose.yml` and SETUP.md (today `./data` is mounted read-only), tests with a fake `readpst`. |
| KC-F-9 | State DB `content_sha256` + `index_status`/`indexed_at` (additive-migration idiom from `subfolder_queue.py:67-71`); skip or alias an upload whose content hash already exists under another path (a prerequisite for mailbox and PST, where one message appears in several folders); lazy polling of `GET /secured/transmissions/<key>/status` so `/sync/status` can report "N eingereicht, N indexiert"; `sync_response.schema.json` gains `rate_limit` and `subfolder_progress` (both already computed and discarded) and `_build_sync_response` serialises them. |
| KC-F-10 | Docs: `RemoteController/docs/connectors.md` (OneDrive, mailbox, PST, XLSX/PPTX, metadata rules — the OneDrive connector has no prose documentation at all today), `migration.md` (inventory, PST step, throughput settings against the API ceiling, dedup expectations, verification through index status, rollback, the fixed-price rule of thumb), `configuration.md` (format table, OCR languages, metadata env keys, `RC_MATTER_PATH_RULE`), SETUP volumes, CHANGELOG `Unreleased`, `docs/hosting-requirements.md` options C/D + Graph egress, `docs/specifications.md` §1.3/§1.6. |

## Verification

After all parts, on a Platform pointed at the dev tenant with `ONTOLOGY_SOURCE=graph` and section B enabled:

```bash
cd KnovasPlatform/components/docbridge_integration && python -m pytest
python -m pytest tests/test_graph_contract_live.py --knovas-api        # cassette refresh against dev
cd ../../../RemoteController && python -m pytest
python -m benchmarks.ocr.run_ocr_benchmark --dpi 200,300 --languages de,fr,it
```

Then walk the product path once by hand: search with the filter rail → open a hit in the viewer at its page with the snippet highlighted → open the document's versions and similar documents → open *Parteien*, search "Mueller", merge a duplicate → run a *Konfliktprüfung*, print the protocol → open *Fristen*, adopt an extracted deadline as user A, try to confirm as A (disabled), confirm as B, subscribe the ICS feed in Outlook → open *Posteingang* and see the day's events → open a matter's *Akten-Kompass* → open *Berichte* → run the CSV import wizard in dry-run → file an email to a matter from Outlook → read *Mein Tag* → export the journal CSV → in RemoteController, drop a PST into the inbox and watch `/sync/status` report indexed counts.

## Requirement traceability

| Requirement | Tasks |
| --- | --- |
| F3 · filters + pagination (UI half) | KC-A-1..KC-A-4, KC-A-8; RC metadata KC-F-1 |
| F9 · honest empty results (UI) | KC-A-4 |
| D5 · expertise location | KC-A-5; KC-F-1 |
| F6 · version history (UI) | KC-A-6 |
| F8 · similar documents / matters | KC-A-6 (documents), KC-D-3 (matters via ego + `kg_node_ids`) |
| H4 · tables (UI + XLSX) | KC-A-6, KC-F-4 |
| F7 · jump to the hit | KC-A-7 |
| D1 · party register + dedup | KC-B-1, KC-B-2, KC-B-6, KC-B-7 |
| D3 · Zefix/UID enrichment | KC-B-3 |
| D2 · conflicts check as evidence | KC-B-4, KC-B-7 |
| D4 · lateral-hire import | KC-B-5 |
| E3 · four-eyes (UI) | KC-C-1, KC-C-2, KC-C-8 |
| E4 · proposal inbox | KC-C-2, KC-C-5 |
| E5 · deadlines in Outlook with substitutes | KC-C-3, KC-C-8 |
| E6 · eventing consumer (Posteingang, job status) | KC-C-4..KC-C-7 |
| G1 · Cortex on the live graph | KC-D-2 |
| G2 · matter ego graph | KC-D-1, KC-D-3 |
| G3 · every node answers "why?" | KC-D-4 |
| G4 · trust made visible | KC-D-5 |
| G5 · partner's Monday report | KC-D-6 |
| G6 · non-empty graph (import wizard + bootstrap) | KC-D-7 (+ C-plan C11) |
| G7 · draw on the map (Vorgaben live) | KC-D-8 |
| G8 · tireless junior (filters live) | KC-D-9 |
| G9 · honesty labels | KC-G-1, KC-D-2 (badges) |
| H2 · Outlook and Word add-ins | KC-E-1..KC-E-4 |
| J2 · activity hints | KC-E-5, KC-E-6, KC-E-7 |
| J3 · realization reporting (substrate + statement) | KC-E-6, KC-G-1 |
| F1 · OCR accuracy evidence | KC-F-5, KC-F-6 |
| F2 · whole estate (mailbox, XLSX/PPTX, PST) | KC-F-3, KC-F-4, KC-F-7, KC-F-8 |
| H1 · migration incl. PST | KC-F-8, KC-F-9, KC-F-10 |
| F5 · language at ingest | KC-F-1 |
| E1/E2 · deadline strategy declared | KC-G-1 |
| H6 · Justitia 4.0 | KC-G-1 |
| J1/J4 · time capture / invoicing declared | KC-G-1 |
| F4 · throughput statement in customer docs | KC-G-1, KC-G-5 |
| H5 · exit doc + export UI pointers | KC-G-3 (mirror `Export_and_Exit.md`) |
)
## Statuscodes, die der Browser sinnvoll unterscheiden kann. 401/403 der
## Tenant-API (Zertifikat, Rechte) sind KEIN Login-Problem des Nutzers und
## wuerden app.js in die Login-Weiterleitung schicken -> 502.
_PASSTHROUGH_STATUS = frozenset({400, 404, 409, 422, 429, 503})

DOCUMENT_STATUS_VALUES = ('draft', 'final', 'executed', 'unknown')
SOURCE_KIND_VALUES = ('share', 'onedrive', 'mailbox', 'pst', 'upload', 'addin')
SIMILAR_LIMIT_DEFAULT = 5
SIMILAR_LIMIT_MAX = 20
_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}

---

## Appendix: tasks specified but not yet expanded

These task IDs are part of the plan's structure (Part Overview, traceability table, neighbouring **Interfaces** blocks) but their step-by-step bodies are **not yet written**. Each line is the task's brief — precise enough to expand, not licence to improvise. Expand one to the same standard as the tasks above (failing test → run → minimal implementation → run → commit, real code in every step) before starting it.

**KC-A (search UI — KC-A-1, 6, 7, 8 are written)**

| ID | Scope |
| --- | --- |
| KC-A-2 | `src/search_filters.py`: split the UI filter payload into "forward to the API" (`filters`, `sort`, `limit`, `offset`, `facets`, `scope`) and "apply locally" (`exact_match` and the other refinements `_apply_search_refinement` already implements); an allowed-filter list under `web.search.filters` in `config.yaml`; rework `app.py::search()` so UI-only keys are never forwarded (today every unknown key goes to `/secured/query` and logs a warning on every search) and so an API `400 validation_error` is surfaced with its `field`. Tests over the route contract. |
| KC-A-3 | Filter rail in `index.html` + `static/js/filters.js`: Akte (matter picker → `scope.node_ids` via the section-C `/api/graph/nodes?node_type_id=<Mandat>`), Praxisgebiet (matters whose `semantic_role=practice_area` fact matches → `scope`), Dokumenttyp, Autor, Zeitraum, Sprache, Status, Quelle, sort selector, and a real "Weitere Treffer" using `offset` instead of re-running the query with a doubled limit. `app.js` sends the typed payload; facet chips render from the response. |
| KC-A-4 | Hit card and honesty: metaline `Typ · Datum · Autor · Sprache` plus a version badge; `auto_summary` chunk hits labelled "KI-Zusammenfassung"; the empty state renders `no_strong_matches` and `semantix.status` instead of ignoring them; a persistent "Beispieldaten" banner whenever `SEARCH_USE_TEST_RESULTS` is on. |
| KC-A-5 | "Wer kennt sich aus?" (D5): a rail action that re-runs the current query with `facets=["author"]` and renders the author facet as a ranked list, each entry linking to the query filtered to that author. |

**KC-B (Parteien, Zefix, Konfliktprüfung — KC-B-1 is written)**

| ID | Scope |
| --- | --- |
| KC-B-2 | `src/web_interface/parties_routes.py` blueprint: `/parteien` page and `/api/parties`, `/api/parties/search`, `/api/parties/<id>`, `/api/parties/duplicates`, `/api/parties/merge`; party node types from `web.graph.party_node_types`; identifier editor with kinds; Dubletten queue with a merge sheet stating "Quelle bleibt als Verweis erhalten"; merge is a section-B guarded action (`ApprovalService` kind `party_merge`, admin bypass recorded, `audit.record` on execute). Templates + `static/js/parties.js` + tests incl. CSRF and the approval branch. |
| KC-B-3 | `src/zefix_client.py` + `/api/zefix/lookup`: Zefix public REST from the **customer's** network (`ZEFIX_USERNAME`/`ZEFIX_PASSWORD`, disabled when absent, timeouts, no retry on 4xx); "Aus Zefix übernehmen" uploads a generated `Zefix-Auszug <UID> <Datum>` document through the Platform upload path with `metadata` and `graph_assign`, then creates UID / Sitz / Rechtsform / Status facts with that document's chunk as evidence. State in the UI and the docs that signatories and group structure are **not** available from the cantonal extract. |
| KC-B-4 | `src/web_interface/conflicts_routes.py`: `/konfliktpruefung` form (names + role + context), `POST /api/conflict-checks` with `actor_ref` = the section-B user id, result page grouped by Parteien / Akten / Dokumente with `withheld_count` and `degraded` as prominent callouts, decision form, history list, and a printable `templates/conflict_protocol.html` (print CSS; check id, actor, time, queries, hits, decision, result hash). |
| KC-B-5 | Lateral-hire import (D4): `POST /api/conflict-checks/import` accepting CSV/XLSX (`openpyxl`) with columns client / counterparty / matter / period → validation → one check per row under a bundle context `lateral:<uuid>` → summary table with per-row status and protocol links. Sample fixtures in the tests. |
| KC-B-6 | Sidebar entries and `active_nav` values for both screens; fixture-mode state ("Wissensnetz-Modus erforderlich") wherever the graph is required. |
| KC-B-7 | Docs: `KnovasPlatform/docs/features/matters-and-parties.md` (register, identifier kinds, merge semantics, Zefix scope) and `conflicts-check.md` (workflow, what the evidentiary record contains, the wall policy = counted withheld hits, `degraded`, the protocol, the D4 import format); rows in `KnovasPlatform/docs/integration/graph-api.md`; RELEASE_NOTES lines. |

**KC-C (Fristen, Posteingang — KC-C-1 and KC-C-2a are written)**

| ID | Scope |
| --- | --- |
| KC-C-2b | Finish the deadlines routes: the three tabs (Vorschläge / Zur Bestätigung / Bestätigt) wired to `facts_list`, `fact_adopt`, confirm and reject; the confirm button disabled server-side for the user who entered the fact (compare the ledger's last human actor from the fact history); permanent-rejection copy; the per-matter widget include for the section-C `matter.html`. |
| KC-C-3 | `src/ics_feed.py` + `GET /feeds/deadlines.ics?token=`: RFC 5545 output, one VEVENT per confirmed deadline (`DTSTART` honouring `precision` — a month-precision fact is never drawn on a specific day), `ORGANIZER`/`ATTENDEE` from the matter's `responsible`/`deputy` entity_ref facts (Person nodes with an `email` identifier), `VALARM` −P7D and −P1D, `UID` = fact id, `X-KNOVAS-FACT-ID`; `feed_tokens` table (`0005_feed_tokens.sql`) with create/revoke in settings; golden-text test. |
| KC-C-4 | `src/events_poller.py`: leader election via a `platform-db` advisory lock, `events_poll(after=cursor)` every `EVENTS_POLL_SECONDS` (15), rows into `events` + `event_cursor` (`0002_events.sql`), started from `create_app` when `EVENTS_POLL_ENABLED`; safe under two gunicorn workers; tests with a fake client. |
| KC-C-5 | `src/web_interface/inbox_routes.py`: `/posteingang` grouped by kind (sort proposals, deadline proposals, pending confirmations, contradictions, job completions, conflict checks) with deep links, mark-read, and `/api/inbox/unread-count` for the sidebar badge. |
| KC-C-6 | Ingestion/upload screens poll `transmission_status` for pending keys and show indexed/failed; `Ereignisprotokoll` CSV export at `/api/inbox/export`. |
| KC-C-7 | Overdue escalation banner driven by `graph.fact.confirmation_overdue` events. |
| KC-C-8 | Docs: `features/deadlines.md` (E1 cross-link, the proposal → adopt → confirm chain, four-eyes semantics incl. `actor_kind` honesty, Outlook subscription steps, what a PMS integrator consumes instead), `features/reports-and-inbox.md` (inbox half), `integration/events.md`, `.env.example` keys, RELEASE_NOTES lines. |

**KC-D (Cortex live — KC-D-1..3 are written)**

| ID | Scope |
| --- | --- |
| KC-D-4 | G3 "Warum?" drawer in graph mode: facts with tier chips, evidence rows (pointer, page, quote) opening the viewer through the shared `openEvidence(...)` helper from KC-A-7; reachable from the search Trefferliste for a hit's assigned matter. |
| KC-D-5 | G4 `templates/_trust_chip.html` macro (German tier label, scope tag "firmenweit" / "Ihre Sicht", signals popover: independent sources, supporting links, contradiction pressure, curation status, validity elapsed) + CSS, reused by chronology, dossier, facts and the why-panel. |
| KC-D-6 | G5 `src/web_interface/reports_routes.py`: `/berichte` rendering contradictions and completeness with a node-type filter, paging, deep links to node/fact/evidence, and a CSV export. |
| KC-D-7 | G6 `/import` wizard: CSV upload → column mapping (matter number, client, counterparties, responsible lawyer, practice area, status, opened date) → build the `POST /secured/graph/imports` payload (identifiers with kinds, facts by `semantic_role`, Person nodes with email identifiers) → dry-run diff → apply → progress via `graph_job`; cross-link to the section-C file-structure bootstrap. |
| KC-D-8 | G7 `GraphOntologySource.create_type_relation` implemented on `target_node_type_id` (section-C A4/A5/B4) and `summary()` returning declared relations with `count: 0` so a dashed Vorgabe survives a reload; enable the type→type path in `ontology_connect.js` in graph mode. |
| KC-D-9 | G8 `GraphFilterEngine` wired to `filters/evaluate|apply|placements|reject|restore` in graph mode; `503 filter_embedding_model_stale` / `relevance_calibration_missing` rendered as "kann gerade nicht bewerten — bitte später" (never as "keine Treffer"); apply progress via `graph_job`; replace `_locate`'s scan over every node with the server-side node filters. |
| KC-D-10 | Docs: `features/import-and-bootstrap.md`, the reports half of `features/reports-and-inbox.md`, the ego section of `features/matters-and-parties.md`, a "Cortex live vs Demo" section in `KnovasPlatform/docs/README.md`, `docs/specifications.md` §2.5 (`ONTOLOGY_*`) and §2.3 (`/secured/graph/*`). |

**KC-E (add-ins and journal — KC-E-1 is written)**

| ID | Scope |
| --- | --- |
| KC-E-2 | `src/web_interface/filing_routes.py`: `POST /api/filing/email` (`{mime_base64|msg_base64, node_id, include_attachments}`, session auth + CSRF, 25 MB body limit with the matching nginx `client_max_body_size` and gunicorn timeout notes, `audit.record`) and `POST /api/filing/suggest` (`{from, to, subject}` → `identifiers_search` → ranked matters, recent matters from the journal when available). |
| KC-E-3 | `KnovasPlatform/components/knovas_office_addins/`: `manifest.outlook.xml` (Mailbox 1.8, `ReadWriteMailbox`, ribbon button "In Knovas ablegen"), `manifest.word.xml`, `taskpane/` (`index.html`, `common.js` login + CSRF, `outlook.js` — MIME via `makeEwsRequestAsync` `GetItem` `IncludeMimeContent` → `POST /api/filing/email`, matter picker with suggestions, toast; `word.js` — search over `/api/search`, "Öffnen" via `client-path` UNC or the companion token, "Zitat einfügen" via `setSelectedDataAsync`), Knovas design tokens in `styles.css`. |
| KC-E-4 | `src/web_interface/addins_routes.py` serving `/addins/*` over the Platform origin with cache headers and a CSP `frame-ancestors` allowing `outlook.office.com`, `office.live.com`, `*.officeapps.live.com` and localhost for development; a manifest well-formedness test (`xml.etree` parse + required elements) and route tests. |
| KC-E-5 | `src/journal.py`: `record(kind, *, user_id, matter_node_id, pointer, page, format, query_hash)` into `activity_journal` (`0003_journal.sql`), opt-in per user in `settings`, retention purge (`JOURNAL_RETENTION_DAYS`, default 90), `day_view(user_id, day)` splitting blocks on gaps > 20 minutes, `csv_export(user_id, from, to)`; hooks in `app.py::search()`, the document-open routes, the matter page and the viewer. |
| KC-E-6 | `src/web_interface/journal_routes.py`: `/mein-tag`, `/api/journal/day`, `/api/journal/export.csv`, `/api/journal/settings`; a user sees only their own rows and admins have no per-person view (works-council-friendly by construction); `/api/journal/format-stats` returns aggregate open-counts by format, which is the measurement the search backlog's pdf.js precondition asked for. |
| KC-E-7 | Docs: `features/activity-journal.md` (consent text, what is recorded and what is not, retention, export columns, PMS import hint) and the `integration/office-add-ins.md` page (architecture + sequence diagram, hosting on the Platform origin over HTTPS, permissions, central deployment vs sideload, on-prem Exchange note, troubleshooting); rows in `KnovasPlatform/components/README.md`, `docs/specifications.md` §2.8, `hosting-requirements.md`; RELEASE_NOTES section. |

**KC-F (RemoteController — KC-F-1 and KC-F-2 are written)**

| ID | Scope |
| --- | --- |
| KC-F-3 | One source of truth for `SYNCABLE_EXTENSIONS` (`document_text.py:49`) from which `DEFAULT_INCLUDE_GLOBS`, `default_sync_body.py`, the OneDrive `DEFAULT_ALLOWED_EXTENSIONS` and the `sync_request.schema.json` description derive — today the list exists in five places and a partial edit silently half-enables a format. |
| KC-F-4 | `src/sync/office_extractors.py`: `XlsxExtractor` (openpyxl `read_only`, `data_only`; one `Table` per worksheet block, ≤ 64 cols / 5 000 rows, ragged rows padded before `map_extractor_tables` drops them, hidden sheets skipped, sheet name as `title`, `client_table_hint = xlsx_s{i}_t{j}`, plus a flattened text rendering) and `PptxExtractor` (python-pptx; one page per slide, slide title as a section, notes included), registered into `knovas_extract.dispatch.MIME_REGISTRY` at RC import — the documented public hook — with provenance recorded as `remote-controller-office` so nothing is misattributed to the certified extractor. Upstreaming to `knovas-extract` is the named follow-up. |
| KC-F-5 | OCR: `tesseract-ocr-ita` in the Dockerfile, default `RC_TESSERACT_LANG=deu+fra+ita+eng`, `result.warnings` and an `ocr_used` flag kept on `ExtractedDocument`, Prometheus `knovas_rc_documents_extracted_total{ext,ocr}` and `knovas_rc_extract_errors_total{reason}` in `routes/metrics.py`, and `scripts/requeue_skipped.py` for rows parked as `skip:unconvertible` (enabling Italian later does not re-ingest them by itself). |
| KC-F-6 | `benchmarks/ocr/`: `build_corpus.py` renders ground-truth DE/FR/IT legal paragraphs to page images at 200/300 dpi with skew and noise (Pillow) → PDF; `run_ocr_benchmark.py` runs `knovas_extract.extract(use_ocr=True, ocr_language=…)` and reports CER/WER per language and dpi into `results/<ts>/{metrics.json,report.md}`; a README with the on-premise "Nachweis auf eigenen Scans" runbook, because real court scans cannot be published. |
| KC-F-7 | `src/mailbox_mirror/`: `graph_mail.py` (client-credentials auth reusing `onedrive_mirror/graph.py`; `mailFolders`, `messages/delta`, `messages/{id}/$value`, `attachments`), `mirror.py` (mailbox allow-list, folder include/exclude, per-folder delta with full-walk fallback, each message materialised as `.eml` under `<MAILBOX_MIRROR_PATH>/<upn>/<folder>/<sha1(internetMessageId)>.eml` with mtime pinned to `receivedDateTime`, attachments beside it as `<key>.att/<name>`, and the two OneDrive invariants copied verbatim: no cursor advance while downloads fail, no prune on incomplete enumeration), `runner.py`, `MAILBOX_*` env gating so a missing config never fails boot. |
| KC-F-8 | PST: `scripts/explode_pst.py` (`readpst -e -j N -o <staging>`, folder hierarchy preserved, `Message-ID` captured, idempotent, timeout) + `src/sync/pst_queue.py` (one PST per cycle from `RC_PST_INBOX`, resumable, state rows), `pst-utils` in the image, writable `RC_PST_INBOX`/`RC_PST_STAGING` volumes in `docker-compose.yml` and SETUP.md (today `./data` is mounted read-only), tests with a fake `readpst`. |
| KC-F-9 | State DB `content_sha256` + `index_status`/`indexed_at` (additive-migration idiom from `subfolder_queue.py:67-71`); skip or alias an upload whose content hash already exists under another path (a prerequisite for mailbox and PST, where one message appears in several folders); lazy polling of `GET /secured/transmissions/<key>/status` so `/sync/status` can report "N eingereicht, N indexiert"; `sync_response.schema.json` gains `rate_limit` and `subfolder_progress` (both already computed and discarded) and `_build_sync_response` serialises them. |
| KC-F-10 | Docs: `RemoteController/docs/connectors.md` (OneDrive, mailbox, PST, XLSX/PPTX, metadata rules — the OneDrive connector has no prose documentation at all today), `migration.md` (inventory, PST step, throughput settings against the API ceiling, dedup expectations, verification through index status, rollback, the fixed-price rule of thumb), `configuration.md` (format table, OCR languages, metadata env keys, `RC_MATTER_PATH_RULE`), SETUP volumes, CHANGELOG `Unreleased`, `docs/hosting-requirements.md` options C/D + Graph egress, `docs/specifications.md` §1.3/§1.6. |

## Verification

After all parts, on a Platform pointed at the dev tenant with `ONTOLOGY_SOURCE=graph` and section B enabled:

```bash
cd KnovasPlatform/components/docbridge_integration && python -m pytest
python -m pytest tests/test_graph_contract_live.py --knovas-api        # cassette refresh against dev
cd ../../../RemoteController && python -m pytest
python -m benchmarks.ocr.run_ocr_benchmark --dpi 200,300 --languages de,fr,it
```

Then walk the product path once by hand: search with the filter rail → open a hit in the viewer at its page with the snippet highlighted → open the document's versions and similar documents → open *Parteien*, search "Mueller", merge a duplicate → run a *Konfliktprüfung*, print the protocol → open *Fristen*, adopt an extracted deadline as user A, try to confirm as A (disabled), confirm as B, subscribe the ICS feed in Outlook → open *Posteingang* and see the day's events → open a matter's *Akten-Kompass* → open *Berichte* → run the CSV import wizard in dry-run → file an email to a matter from Outlook → read *Mein Tag* → export the journal CSV → in RemoteController, drop a PST into the inbox and watch `/sync/status` report indexed counts.

## Requirement traceability

| Requirement | Tasks |
| --- | --- |
| F3 · filters + pagination (UI half) | KC-A-1..KC-A-4, KC-A-8; RC metadata KC-F-1 |
| F9 · honest empty results (UI) | KC-A-4 |
| D5 · expertise location | KC-A-5; KC-F-1 |
| F6 · version history (UI) | KC-A-6 |
| F8 · similar documents / matters | KC-A-6 (documents), KC-D-3 (matters via ego + `kg_node_ids`) |
| H4 · tables (UI + XLSX) | KC-A-6, KC-F-4 |
| F7 · jump to the hit | KC-A-7 |
| D1 · party register + dedup | KC-B-1, KC-B-2, KC-B-6, KC-B-7 |
| D3 · Zefix/UID enrichment | KC-B-3 |
| D2 · conflicts check as evidence | KC-B-4, KC-B-7 |
| D4 · lateral-hire import | KC-B-5 |
| E3 · four-eyes (UI) | KC-C-1, KC-C-2, KC-C-8 |
| E4 · proposal inbox | KC-C-2, KC-C-5 |
| E5 · deadlines in Outlook with substitutes | KC-C-3, KC-C-8 |
| E6 · eventing consumer (Posteingang, job status) | KC-C-4..KC-C-7 |
| G1 · Cortex on the live graph | KC-D-2 |
| G2 · matter ego graph | KC-D-1, KC-D-3 |
| G3 · every node answers "why?" | KC-D-4 |
| G4 · trust made visible | KC-D-5 |
| G5 · partner's Monday report | KC-D-6 |
| G6 · non-empty graph (import wizard + bootstrap) | KC-D-7 (+ C-plan C11) |
| G7 · draw on the map (Vorgaben live) | KC-D-8 |
| G8 · tireless junior (filters live) | KC-D-9 |
| G9 · honesty labels | KC-G-1, KC-D-2 (badges) |
| H2 · Outlook and Word add-ins | KC-E-1..KC-E-4 |
| J2 · activity hints | KC-E-5, KC-E-6, KC-E-7 |
| J3 · realization reporting (substrate + statement) | KC-E-6, KC-G-1 |
| F1 · OCR accuracy evidence | KC-F-5, KC-F-6 |
| F2 · whole estate (mailbox, XLSX/PPTX, PST) | KC-F-3, KC-F-4, KC-F-7, KC-F-8 |
| H1 · migration incl. PST | KC-F-8, KC-F-9, KC-F-10 |
| F5 · language at ingest | KC-F-1 |
| E1/E2 · deadline strategy declared | KC-G-1 |
| H6 · Justitia 4.0 | KC-G-1 |
| J1/J4 · time capture / invoicing declared | KC-G-1 |
| F4 · throughput statement in customer docs | KC-G-1, KC-G-5 |
| H5 · exit doc + export UI pointers | KC-G-3 (mirror `Export_and_Exit.md`) |
)
_LANG_RE = re.compile(r'^[a-z]{2,3}

---

## Appendix: tasks specified but not yet expanded

These task IDs are part of the plan's structure (Part Overview, traceability table, neighbouring **Interfaces** blocks) but their step-by-step bodies are **not yet written**. Each line is the task's brief — precise enough to expand, not licence to improvise. Expand one to the same standard as the tasks above (failing test → run → minimal implementation → run → commit, real code in every step) before starting it.

**KC-A (search UI — KC-A-1, 6, 7, 8 are written)**

| ID | Scope |
| --- | --- |
| KC-A-2 | `src/search_filters.py`: split the UI filter payload into "forward to the API" (`filters`, `sort`, `limit`, `offset`, `facets`, `scope`) and "apply locally" (`exact_match` and the other refinements `_apply_search_refinement` already implements); an allowed-filter list under `web.search.filters` in `config.yaml`; rework `app.py::search()` so UI-only keys are never forwarded (today every unknown key goes to `/secured/query` and logs a warning on every search) and so an API `400 validation_error` is surfaced with its `field`. Tests over the route contract. |
| KC-A-3 | Filter rail in `index.html` + `static/js/filters.js`: Akte (matter picker → `scope.node_ids` via the section-C `/api/graph/nodes?node_type_id=<Mandat>`), Praxisgebiet (matters whose `semantic_role=practice_area` fact matches → `scope`), Dokumenttyp, Autor, Zeitraum, Sprache, Status, Quelle, sort selector, and a real "Weitere Treffer" using `offset` instead of re-running the query with a doubled limit. `app.js` sends the typed payload; facet chips render from the response. |
| KC-A-4 | Hit card and honesty: metaline `Typ · Datum · Autor · Sprache` plus a version badge; `auto_summary` chunk hits labelled "KI-Zusammenfassung"; the empty state renders `no_strong_matches` and `semantix.status` instead of ignoring them; a persistent "Beispieldaten" banner whenever `SEARCH_USE_TEST_RESULTS` is on. |
| KC-A-5 | "Wer kennt sich aus?" (D5): a rail action that re-runs the current query with `facets=["author"]` and renders the author facet as a ranked list, each entry linking to the query filtered to that author. |

**KC-B (Parteien, Zefix, Konfliktprüfung — KC-B-1 is written)**

| ID | Scope |
| --- | --- |
| KC-B-2 | `src/web_interface/parties_routes.py` blueprint: `/parteien` page and `/api/parties`, `/api/parties/search`, `/api/parties/<id>`, `/api/parties/duplicates`, `/api/parties/merge`; party node types from `web.graph.party_node_types`; identifier editor with kinds; Dubletten queue with a merge sheet stating "Quelle bleibt als Verweis erhalten"; merge is a section-B guarded action (`ApprovalService` kind `party_merge`, admin bypass recorded, `audit.record` on execute). Templates + `static/js/parties.js` + tests incl. CSRF and the approval branch. |
| KC-B-3 | `src/zefix_client.py` + `/api/zefix/lookup`: Zefix public REST from the **customer's** network (`ZEFIX_USERNAME`/`ZEFIX_PASSWORD`, disabled when absent, timeouts, no retry on 4xx); "Aus Zefix übernehmen" uploads a generated `Zefix-Auszug <UID> <Datum>` document through the Platform upload path with `metadata` and `graph_assign`, then creates UID / Sitz / Rechtsform / Status facts with that document's chunk as evidence. State in the UI and the docs that signatories and group structure are **not** available from the cantonal extract. |
| KC-B-4 | `src/web_interface/conflicts_routes.py`: `/konfliktpruefung` form (names + role + context), `POST /api/conflict-checks` with `actor_ref` = the section-B user id, result page grouped by Parteien / Akten / Dokumente with `withheld_count` and `degraded` as prominent callouts, decision form, history list, and a printable `templates/conflict_protocol.html` (print CSS; check id, actor, time, queries, hits, decision, result hash). |
| KC-B-5 | Lateral-hire import (D4): `POST /api/conflict-checks/import` accepting CSV/XLSX (`openpyxl`) with columns client / counterparty / matter / period → validation → one check per row under a bundle context `lateral:<uuid>` → summary table with per-row status and protocol links. Sample fixtures in the tests. |
| KC-B-6 | Sidebar entries and `active_nav` values for both screens; fixture-mode state ("Wissensnetz-Modus erforderlich") wherever the graph is required. |
| KC-B-7 | Docs: `KnovasPlatform/docs/features/matters-and-parties.md` (register, identifier kinds, merge semantics, Zefix scope) and `conflicts-check.md` (workflow, what the evidentiary record contains, the wall policy = counted withheld hits, `degraded`, the protocol, the D4 import format); rows in `KnovasPlatform/docs/integration/graph-api.md`; RELEASE_NOTES lines. |

**KC-C (Fristen, Posteingang — KC-C-1 and KC-C-2a are written)**

| ID | Scope |
| --- | --- |
| KC-C-2b | Finish the deadlines routes: the three tabs (Vorschläge / Zur Bestätigung / Bestätigt) wired to `facts_list`, `fact_adopt`, confirm and reject; the confirm button disabled server-side for the user who entered the fact (compare the ledger's last human actor from the fact history); permanent-rejection copy; the per-matter widget include for the section-C `matter.html`. |
| KC-C-3 | `src/ics_feed.py` + `GET /feeds/deadlines.ics?token=`: RFC 5545 output, one VEVENT per confirmed deadline (`DTSTART` honouring `precision` — a month-precision fact is never drawn on a specific day), `ORGANIZER`/`ATTENDEE` from the matter's `responsible`/`deputy` entity_ref facts (Person nodes with an `email` identifier), `VALARM` −P7D and −P1D, `UID` = fact id, `X-KNOVAS-FACT-ID`; `feed_tokens` table (`0005_feed_tokens.sql`) with create/revoke in settings; golden-text test. |
| KC-C-4 | `src/events_poller.py`: leader election via a `platform-db` advisory lock, `events_poll(after=cursor)` every `EVENTS_POLL_SECONDS` (15), rows into `events` + `event_cursor` (`0002_events.sql`), started from `create_app` when `EVENTS_POLL_ENABLED`; safe under two gunicorn workers; tests with a fake client. |
| KC-C-5 | `src/web_interface/inbox_routes.py`: `/posteingang` grouped by kind (sort proposals, deadline proposals, pending confirmations, contradictions, job completions, conflict checks) with deep links, mark-read, and `/api/inbox/unread-count` for the sidebar badge. |
| KC-C-6 | Ingestion/upload screens poll `transmission_status` for pending keys and show indexed/failed; `Ereignisprotokoll` CSV export at `/api/inbox/export`. |
| KC-C-7 | Overdue escalation banner driven by `graph.fact.confirmation_overdue` events. |
| KC-C-8 | Docs: `features/deadlines.md` (E1 cross-link, the proposal → adopt → confirm chain, four-eyes semantics incl. `actor_kind` honesty, Outlook subscription steps, what a PMS integrator consumes instead), `features/reports-and-inbox.md` (inbox half), `integration/events.md`, `.env.example` keys, RELEASE_NOTES lines. |

**KC-D (Cortex live — KC-D-1..3 are written)**

| ID | Scope |
| --- | --- |
| KC-D-4 | G3 "Warum?" drawer in graph mode: facts with tier chips, evidence rows (pointer, page, quote) opening the viewer through the shared `openEvidence(...)` helper from KC-A-7; reachable from the search Trefferliste for a hit's assigned matter. |
| KC-D-5 | G4 `templates/_trust_chip.html` macro (German tier label, scope tag "firmenweit" / "Ihre Sicht", signals popover: independent sources, supporting links, contradiction pressure, curation status, validity elapsed) + CSS, reused by chronology, dossier, facts and the why-panel. |
| KC-D-6 | G5 `src/web_interface/reports_routes.py`: `/berichte` rendering contradictions and completeness with a node-type filter, paging, deep links to node/fact/evidence, and a CSV export. |
| KC-D-7 | G6 `/import` wizard: CSV upload → column mapping (matter number, client, counterparties, responsible lawyer, practice area, status, opened date) → build the `POST /secured/graph/imports` payload (identifiers with kinds, facts by `semantic_role`, Person nodes with email identifiers) → dry-run diff → apply → progress via `graph_job`; cross-link to the section-C file-structure bootstrap. |
| KC-D-8 | G7 `GraphOntologySource.create_type_relation` implemented on `target_node_type_id` (section-C A4/A5/B4) and `summary()` returning declared relations with `count: 0` so a dashed Vorgabe survives a reload; enable the type→type path in `ontology_connect.js` in graph mode. |
| KC-D-9 | G8 `GraphFilterEngine` wired to `filters/evaluate|apply|placements|reject|restore` in graph mode; `503 filter_embedding_model_stale` / `relevance_calibration_missing` rendered as "kann gerade nicht bewerten — bitte später" (never as "keine Treffer"); apply progress via `graph_job`; replace `_locate`'s scan over every node with the server-side node filters. |
| KC-D-10 | Docs: `features/import-and-bootstrap.md`, the reports half of `features/reports-and-inbox.md`, the ego section of `features/matters-and-parties.md`, a "Cortex live vs Demo" section in `KnovasPlatform/docs/README.md`, `docs/specifications.md` §2.5 (`ONTOLOGY_*`) and §2.3 (`/secured/graph/*`). |

**KC-E (add-ins and journal — KC-E-1 is written)**

| ID | Scope |
| --- | --- |
| KC-E-2 | `src/web_interface/filing_routes.py`: `POST /api/filing/email` (`{mime_base64|msg_base64, node_id, include_attachments}`, session auth + CSRF, 25 MB body limit with the matching nginx `client_max_body_size` and gunicorn timeout notes, `audit.record`) and `POST /api/filing/suggest` (`{from, to, subject}` → `identifiers_search` → ranked matters, recent matters from the journal when available). |
| KC-E-3 | `KnovasPlatform/components/knovas_office_addins/`: `manifest.outlook.xml` (Mailbox 1.8, `ReadWriteMailbox`, ribbon button "In Knovas ablegen"), `manifest.word.xml`, `taskpane/` (`index.html`, `common.js` login + CSRF, `outlook.js` — MIME via `makeEwsRequestAsync` `GetItem` `IncludeMimeContent` → `POST /api/filing/email`, matter picker with suggestions, toast; `word.js` — search over `/api/search`, "Öffnen" via `client-path` UNC or the companion token, "Zitat einfügen" via `setSelectedDataAsync`), Knovas design tokens in `styles.css`. |
| KC-E-4 | `src/web_interface/addins_routes.py` serving `/addins/*` over the Platform origin with cache headers and a CSP `frame-ancestors` allowing `outlook.office.com`, `office.live.com`, `*.officeapps.live.com` and localhost for development; a manifest well-formedness test (`xml.etree` parse + required elements) and route tests. |
| KC-E-5 | `src/journal.py`: `record(kind, *, user_id, matter_node_id, pointer, page, format, query_hash)` into `activity_journal` (`0003_journal.sql`), opt-in per user in `settings`, retention purge (`JOURNAL_RETENTION_DAYS`, default 90), `day_view(user_id, day)` splitting blocks on gaps > 20 minutes, `csv_export(user_id, from, to)`; hooks in `app.py::search()`, the document-open routes, the matter page and the viewer. |
| KC-E-6 | `src/web_interface/journal_routes.py`: `/mein-tag`, `/api/journal/day`, `/api/journal/export.csv`, `/api/journal/settings`; a user sees only their own rows and admins have no per-person view (works-council-friendly by construction); `/api/journal/format-stats` returns aggregate open-counts by format, which is the measurement the search backlog's pdf.js precondition asked for. |
| KC-E-7 | Docs: `features/activity-journal.md` (consent text, what is recorded and what is not, retention, export columns, PMS import hint) and the `integration/office-add-ins.md` page (architecture + sequence diagram, hosting on the Platform origin over HTTPS, permissions, central deployment vs sideload, on-prem Exchange note, troubleshooting); rows in `KnovasPlatform/components/README.md`, `docs/specifications.md` §2.8, `hosting-requirements.md`; RELEASE_NOTES section. |

**KC-F (RemoteController — KC-F-1 and KC-F-2 are written)**

| ID | Scope |
| --- | --- |
| KC-F-3 | One source of truth for `SYNCABLE_EXTENSIONS` (`document_text.py:49`) from which `DEFAULT_INCLUDE_GLOBS`, `default_sync_body.py`, the OneDrive `DEFAULT_ALLOWED_EXTENSIONS` and the `sync_request.schema.json` description derive — today the list exists in five places and a partial edit silently half-enables a format. |
| KC-F-4 | `src/sync/office_extractors.py`: `XlsxExtractor` (openpyxl `read_only`, `data_only`; one `Table` per worksheet block, ≤ 64 cols / 5 000 rows, ragged rows padded before `map_extractor_tables` drops them, hidden sheets skipped, sheet name as `title`, `client_table_hint = xlsx_s{i}_t{j}`, plus a flattened text rendering) and `PptxExtractor` (python-pptx; one page per slide, slide title as a section, notes included), registered into `knovas_extract.dispatch.MIME_REGISTRY` at RC import — the documented public hook — with provenance recorded as `remote-controller-office` so nothing is misattributed to the certified extractor. Upstreaming to `knovas-extract` is the named follow-up. |
| KC-F-5 | OCR: `tesseract-ocr-ita` in the Dockerfile, default `RC_TESSERACT_LANG=deu+fra+ita+eng`, `result.warnings` and an `ocr_used` flag kept on `ExtractedDocument`, Prometheus `knovas_rc_documents_extracted_total{ext,ocr}` and `knovas_rc_extract_errors_total{reason}` in `routes/metrics.py`, and `scripts/requeue_skipped.py` for rows parked as `skip:unconvertible` (enabling Italian later does not re-ingest them by itself). |
| KC-F-6 | `benchmarks/ocr/`: `build_corpus.py` renders ground-truth DE/FR/IT legal paragraphs to page images at 200/300 dpi with skew and noise (Pillow) → PDF; `run_ocr_benchmark.py` runs `knovas_extract.extract(use_ocr=True, ocr_language=…)` and reports CER/WER per language and dpi into `results/<ts>/{metrics.json,report.md}`; a README with the on-premise "Nachweis auf eigenen Scans" runbook, because real court scans cannot be published. |
| KC-F-7 | `src/mailbox_mirror/`: `graph_mail.py` (client-credentials auth reusing `onedrive_mirror/graph.py`; `mailFolders`, `messages/delta`, `messages/{id}/$value`, `attachments`), `mirror.py` (mailbox allow-list, folder include/exclude, per-folder delta with full-walk fallback, each message materialised as `.eml` under `<MAILBOX_MIRROR_PATH>/<upn>/<folder>/<sha1(internetMessageId)>.eml` with mtime pinned to `receivedDateTime`, attachments beside it as `<key>.att/<name>`, and the two OneDrive invariants copied verbatim: no cursor advance while downloads fail, no prune on incomplete enumeration), `runner.py`, `MAILBOX_*` env gating so a missing config never fails boot. |
| KC-F-8 | PST: `scripts/explode_pst.py` (`readpst -e -j N -o <staging>`, folder hierarchy preserved, `Message-ID` captured, idempotent, timeout) + `src/sync/pst_queue.py` (one PST per cycle from `RC_PST_INBOX`, resumable, state rows), `pst-utils` in the image, writable `RC_PST_INBOX`/`RC_PST_STAGING` volumes in `docker-compose.yml` and SETUP.md (today `./data` is mounted read-only), tests with a fake `readpst`. |
| KC-F-9 | State DB `content_sha256` + `index_status`/`indexed_at` (additive-migration idiom from `subfolder_queue.py:67-71`); skip or alias an upload whose content hash already exists under another path (a prerequisite for mailbox and PST, where one message appears in several folders); lazy polling of `GET /secured/transmissions/<key>/status` so `/sync/status` can report "N eingereicht, N indexiert"; `sync_response.schema.json` gains `rate_limit` and `subfolder_progress` (both already computed and discarded) and `_build_sync_response` serialises them. |
| KC-F-10 | Docs: `RemoteController/docs/connectors.md` (OneDrive, mailbox, PST, XLSX/PPTX, metadata rules — the OneDrive connector has no prose documentation at all today), `migration.md` (inventory, PST step, throughput settings against the API ceiling, dedup expectations, verification through index status, rollback, the fixed-price rule of thumb), `configuration.md` (format table, OCR languages, metadata env keys, `RC_MATTER_PATH_RULE`), SETUP volumes, CHANGELOG `Unreleased`, `docs/hosting-requirements.md` options C/D + Graph egress, `docs/specifications.md` §1.3/§1.6. |

## Verification

After all parts, on a Platform pointed at the dev tenant with `ONTOLOGY_SOURCE=graph` and section B enabled:

```bash
cd KnovasPlatform/components/docbridge_integration && python -m pytest
python -m pytest tests/test_graph_contract_live.py --knovas-api        # cassette refresh against dev
cd ../../../RemoteController && python -m pytest
python -m benchmarks.ocr.run_ocr_benchmark --dpi 200,300 --languages de,fr,it
```

Then walk the product path once by hand: search with the filter rail → open a hit in the viewer at its page with the snippet highlighted → open the document's versions and similar documents → open *Parteien*, search "Mueller", merge a duplicate → run a *Konfliktprüfung*, print the protocol → open *Fristen*, adopt an extracted deadline as user A, try to confirm as A (disabled), confirm as B, subscribe the ICS feed in Outlook → open *Posteingang* and see the day's events → open a matter's *Akten-Kompass* → open *Berichte* → run the CSV import wizard in dry-run → file an email to a matter from Outlook → read *Mein Tag* → export the journal CSV → in RemoteController, drop a PST into the inbox and watch `/sync/status` report indexed counts.

## Requirement traceability

| Requirement | Tasks |
| --- | --- |
| F3 · filters + pagination (UI half) | KC-A-1..KC-A-4, KC-A-8; RC metadata KC-F-1 |
| F9 · honest empty results (UI) | KC-A-4 |
| D5 · expertise location | KC-A-5; KC-F-1 |
| F6 · version history (UI) | KC-A-6 |
| F8 · similar documents / matters | KC-A-6 (documents), KC-D-3 (matters via ego + `kg_node_ids`) |
| H4 · tables (UI + XLSX) | KC-A-6, KC-F-4 |
| F7 · jump to the hit | KC-A-7 |
| D1 · party register + dedup | KC-B-1, KC-B-2, KC-B-6, KC-B-7 |
| D3 · Zefix/UID enrichment | KC-B-3 |
| D2 · conflicts check as evidence | KC-B-4, KC-B-7 |
| D4 · lateral-hire import | KC-B-5 |
| E3 · four-eyes (UI) | KC-C-1, KC-C-2, KC-C-8 |
| E4 · proposal inbox | KC-C-2, KC-C-5 |
| E5 · deadlines in Outlook with substitutes | KC-C-3, KC-C-8 |
| E6 · eventing consumer (Posteingang, job status) | KC-C-4..KC-C-7 |
| G1 · Cortex on the live graph | KC-D-2 |
| G2 · matter ego graph | KC-D-1, KC-D-3 |
| G3 · every node answers "why?" | KC-D-4 |
| G4 · trust made visible | KC-D-5 |
| G5 · partner's Monday report | KC-D-6 |
| G6 · non-empty graph (import wizard + bootstrap) | KC-D-7 (+ C-plan C11) |
| G7 · draw on the map (Vorgaben live) | KC-D-8 |
| G8 · tireless junior (filters live) | KC-D-9 |
| G9 · honesty labels | KC-G-1, KC-D-2 (badges) |
| H2 · Outlook and Word add-ins | KC-E-1..KC-E-4 |
| J2 · activity hints | KC-E-5, KC-E-6, KC-E-7 |
| J3 · realization reporting (substrate + statement) | KC-E-6, KC-G-1 |
| F1 · OCR accuracy evidence | KC-F-5, KC-F-6 |
| F2 · whole estate (mailbox, XLSX/PPTX, PST) | KC-F-3, KC-F-4, KC-F-7, KC-F-8 |
| H1 · migration incl. PST | KC-F-8, KC-F-9, KC-F-10 |
| F5 · language at ingest | KC-F-1 |
| E1/E2 · deadline strategy declared | KC-G-1 |
| H6 · Justitia 4.0 | KC-G-1 |
| J1/J4 · time capture / invoicing declared | KC-G-1 |
| F4 · throughput statement in customer docs | KC-G-1, KC-G-5 |
| H5 · exit doc + export UI pointers | KC-G-3 (mirror `Export_and_Exit.md`) |
)
_EXTRA_MAX_KEYS = 16


def validate_metadata_patch(body: Any) -> Dict[str, Any]:
    """Nur bekannte Schluessel, geprueft. ValueError traegt die deutsche Meldung.

    Leere Werte werden ausgelassen (die API kennt kein Loeschen per PATCH);
    bleibt danach nichts uebrig, ist das ein 400 und kein Leerlauf-Request.
    """
    if not isinstance(body, dict) or not body:
        raise ValueError('Keine Metadaten übergeben.')
    unknown = sorted(k for k in body if k not in METADATA_KEYS)
    if unknown:
        raise ValueError(f'Unbekannte Felder: {", ".join(unknown)}')
    out: Dict[str, Any] = {}
    for key, raw in body.items():
        if key == 'extra':
            if not isinstance(raw, dict) or len(raw) > _EXTRA_MAX_KEYS:
                raise ValueError('extra muss ein Objekt mit höchstens 16 Schlüsseln sein.')
            out['extra'] = {str(k)[:64]: str(v)[:256] for k, v in raw.items()}
            continue
        value = str(raw or '').strip()
        if not value:
            continue
        if key == 'author' and len(value) > 500:
            raise ValueError('Autor: höchstens 500 Zeichen.')
        if key == 'document_type' and len(value) > 128:
            raise ValueError('Dokumenttyp: höchstens 128 Zeichen.')
        if key == 'language':
            value = value.lower()
            if not _LANG_RE.match(value):
                raise ValueError('Sprache: ISO-639-Kürzel mit 2 oder 3 Buchstaben.')
        if key == 'document_date' and not _DATE_RE.match(value):
            raise ValueError('Datum: Format JJJJ-MM-TT.')
        if key == 'document_status' and value not in DOCUMENT_STATUS_VALUES:
            raise ValueError('Status: draft, final, executed oder unknown.')
        if key == 'source_kind' and value not in SOURCE_KIND_VALUES:
            raise ValueError('Quelle: share, onedrive, mailbox, pst, upload oder addin.')
        out[key] = value
    if not out:
        raise ValueError('Keine Änderungen übergeben.')
    return out


def group_similar_matters(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """kg_node_ids der Treffer -> [{node_id, hit_count}] absteigend.

    Die API hat die Ids bereits auf sichtbare Knoten gefiltert; hier wird
    nur gezaehlt. Die Aktenseite rendert daraus "Aehnliche Akten".
    """
    counts: Dict[str, int] = {}
    for row in rows:
        for node_id in row.get('kg_node_ids') or []:
            counts[node_id] = counts.get(node_id, 0) + 1
    return [{'node_id': nid, 'hit_count': n}
            for nid, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


def _clamp_limit(raw: Any) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return SIMILAR_LIMIT_DEFAULT
    return max(1, min(SIMILAR_LIMIT_MAX, value))


def register_document_routes(app, api_client, *,
                             enhance: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None):
    """enhance: optional callable over {'results': [...]} adding the platform's
    open hints (autodoc_rel_path, file_exists, thumbnails) - the same
    enrichment /api/search applies."""
    bp = Blueprint('documents_api', __name__)

    def _fail(exc: Exception):
        if isinstance(exc, SecuredApiError):
            if exc.status in _PASSTHROUGH_STATUS:
                payload: Dict[str, Any] = {
                    'success': False,
                    'error': exc.message or exc.error_code or 'Anfrage abgelehnt',
                    'error_code': exc.error_code,
                }
                if exc.details is not None:
                    payload['details'] = exc.details
                response = jsonify(payload)
                response.status_code = exc.status
                if exc.retry_after:
                    response.headers['Retry-After'] = str(exc.retry_after)
                return response
            logger.warning("Document route: Knovas API answered %s (%s)",
                           exc.status, exc.error_code)
            return jsonify({'success': False, 'error': 'Knovas API nicht erreichbar'}), 502
        logger.error("Document route error", exc_info=True)
        return jsonify({'success': False, 'error': _GENERIC_ERROR}), 500

    def _not_found():
        return jsonify({'success': False, 'error': _NOT_FOUND}), 404

    @bp.route('/api/documents/<document_uuid>/versions', methods=['GET'])
    def document_versions(document_uuid: str):
        if not _UUID_RE.match(document_uuid or ''):
            return _not_found()
        try:
            data = api_client.document_versions(document_uuid)
        except Exception as exc:                       # noqa: BLE001
            return _fail(exc)
        if data is None:
            return _not_found()
        versions = [v for v in (data.get('versions') or []) if isinstance(v, dict)]
        return jsonify({
            'success': True,
            'document_uuid': document_uuid,
            'current': data.get('current') or {},
            'versions': versions,
            'version_count': len(versions),
        })

    @bp.route('/api/documents/<document_uuid>/similar', methods=['POST'])
    def document_similar(document_uuid: str):
        if not _UUID_RE.match(document_uuid or ''):
            return _not_found()
        body = request.get_json(silent=True) or {}
        limit = _clamp_limit(body.get('limit', SIMILAR_LIMIT_DEFAULT))
        filters = body.get('filters') if isinstance(body.get('filters'), dict) else None
        scope = body.get('scope') if isinstance(body.get('scope'), dict) else None
        try:
            data = api_client.similar_documents(document_uuid, limit=limit,
                                                filters=filters, scope=scope)
        except Exception as exc:                       # noqa: BLE001
            return _fail(exc)
        if data is None:
            return _not_found()
        payload: Dict[str, Any] = {'results': list(data.get('results') or [])}
        if enhance is not None:
            try:
                payload = enhance(payload)
            except Exception:                          # noqa: BLE001
                logger.warning("Similar documents: enrichment failed", exc_info=True)
        rows = payload.get('results') or []
        return jsonify({
            'success': True,
            'document_uuid': document_uuid,
            'results': rows,
            'total': len(rows),
            'no_strong_matches': bool(data.get('no_strong_matches')),
            'similar_matters': group_similar_matters(rows),
            'semantix': data.get('semantix') or {},
        })

    @bp.route('/api/documents/<document_uuid>/metadata', methods=['PATCH'])
    def document_metadata(document_uuid: str):
        if not _UUID_RE.match(document_uuid or ''):
            return _not_found()
        try:
            metadata = validate_metadata_patch(request.get_json(silent=True))
        except ValueError as exc:
            return jsonify({'success': False, 'error': str(exc)}), 400
        try:
            result = api_client.update_document_metadata(document_uuid, metadata)
        except Exception as exc:                       # noqa: BLE001
            return _fail(exc)
        if result is None:
            return _not_found()
        stored = result.get('metadata') if isinstance(result, dict) else None
        return jsonify({
            'success': True,
            'document_uuid': document_uuid,
            'metadata': stored if isinstance(stored, dict) else metadata,
        })

    app.register_blueprint(bp)
    return bp
```

- [ ] **Step 4: Register the blueprint in `app.py`**

In `src/web_interface/app.py`, immediately before `@app.route('/api/ontology/summary', methods=['GET'])` (line 1717), add:

```python
    # Dokument-Endpunkte des Vorschau-Dialogs (F6/F8/Metadaten). Eigener
    # Blueprint; die Anreicherung ist dieselbe wie bei /api/search.
    from web_interface.document_routes import register_document_routes
    register_document_routes(
        app, api_client,
        enhance=lambda payload: _enhance_search_results(payload, file_handler, config))
```

- [ ] **Step 5: Run the route tests**

Run: `py -3.13 -m pytest tests/test_document_routes.py -v`
Expected: PASS (19 tests).

- [ ] **Step 6: CSRF regression coverage**

In `tests/test_csrf_enforcement.py`, replace docstring lines 7–8

```
  POST /api/search
  POST /api/document/<id>/open
```

with

```
  POST /api/search
  POST /api/document/<id>/open
  POST /api/documents/<uuid>/similar        (document_routes.py)
  PATCH /api/documents/<uuid>/metadata      (document_routes.py)
```

and append at the end of the file:

```python
## ---------------------------------------------------------------------------
## Document routes (F6/F8/Metadaten): every mutating verb is gated, GET is not
## ---------------------------------------------------------------------------
def test_document_similar_and_metadata_without_csrf_are_forbidden(csrf_app):
    client = csrf_app.test_client()
    _login_and_token(client)
    uuid = "1a2b3c4d-0000-4000-8000-000000000001"
    assert client.post(f"/api/documents/{uuid}/similar", json={}).status_code == 403
    assert client.patch(f"/api/documents/{uuid}/metadata",
                        json={"author": "X"}).status_code == 403
    # GET stays ungated (the stub client has no document_versions -> 500, but
    # never 403).
    assert client.get(f"/api/documents/{uuid}/versions").status_code != 403
```

Run: `py -3.13 -m pytest tests/test_csrf_enforcement.py -v` — Expected: PASS.

- [ ] **Step 7: Tables — extraction side (H4)**

Append to `tests/test_preview_extract.py`:

```python
def test_extract_docx_table_is_returned_structured_and_as_pipe_rows(tmp_path):
    """H4: Tabellen ueberleben bis in die Vorschau. knovas_extract liefert
    content.tables[] (strukturiert) und - ueber mammoth/markdownify - eine
    GFM-Pipe-Tabelle im Markdown, die static/js/markdown.js rendert."""
    import docx

    target = tmp_path / "tabelle.docx"
    document = docx.Document()
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Position"
    table.cell(0, 1).text = "Betrag"
    table.cell(1, 0).text = "Miete"
    table.cell(1, 1).text = "1'200"
    document.save(str(target))

    result = extract_markdown(str(target))

    assert result["tables"], result
    first = result["tables"][0]
    assert first["headers"] == ["Position", "Betrag"]
    assert first["rows"] == [["Miete", "1'200"]]
    assert first["client_table_hint"].startswith("docx_t")
    pipe_rows = [ln for ln in result["markdown"].splitlines() if ln.strip().startswith("|")]
    assert any("Position" in ln and "Betrag" in ln for ln in pipe_rows), result["markdown"]


def test_extract_txt_has_no_tables(tmp_path):
    target = tmp_path / "notiz.txt"
    target.write_text("Kein | Tabellen | Trenner\n", encoding="utf-8")
    assert extract_markdown(str(target))["tables"] == []
```

Run: `py -3.13 -m pytest tests/test_preview_extract.py -v` — Expected: FAIL with `KeyError: 'tables'`.

In `src/web_interface/preview.py`, replace the tail of `extract_markdown` (from `return {` at line 84) with:

```python
    tables = []
    for table in (result.content.tables or []):
        headers = [str(h) for h in (getattr(table, "headers", None) or [])]
        rows = [[str(c) for c in row] for row in (getattr(table, "rows", None) or [])]
        if not headers and not rows:
            continue
        tables.append({
            "client_table_hint": str(getattr(table, "client_table_hint", "") or ""),
            "title": getattr(table, "title", None),
            "headers": headers,
            "rows": rows,
            "page": getattr(table, "page", None),
        })

    return {
        "kind": kind,
        "markdown": markdown,
        "meta": meta,
        "warnings": list(result.warnings),
        "tables": tables,
    }
```

In `src/web_interface/app.py` `preview_content` (line 1424–1431), add `'tables': extracted.get('tables') or [],` after `'warnings': extracted['warnings'],`.

Run: `py -3.13 -m pytest tests/test_preview_extract.py tests/test_preview_endpoint.py -v` — Expected: PASS.

- [ ] **Step 8: Tables — renderer side, with a node smoke test**

Create `tests/test_js_smoke.py` (skips when `node` is not on PATH; there is no JS harness in the repo, so the pure functions of the static scripts are exercised through `node -e` in a `vm` sandbox):

```python
"""Smoke checks for static/js pure functions through node's vm module.

Skipped when node is not installed. Only DOM-free helpers are exercised:
markdown.js (renderer), evidence.js (viewer URL), viewer.js (needle search).
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")

STATIC_JS = Path(__file__).resolve().parents[1] / "src" / "web_interface" / "static" / "js"


def _run(script: str) -> str:
    proc = subprocess.run([NODE, "-e", script], capture_output=True, text=True,
                          encoding="utf-8", timeout=30)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def _load(*names: str) -> str:
    """JS prelude: a sandbox with a bare window and the named scripts loaded."""
    files = json.dumps([str(STATIC_JS / n) for n in names])
    return (
        "const vm = require('vm'); const fs = require('fs');"
        "const sandbox = { window: {}, URLSearchParams, JSON, console };"
        "sandbox.window.URLSearchParams = URLSearchParams;"
        "vm.createContext(sandbox);"
        f"for (const f of {files}) vm.runInContext(fs.readFileSync(f, 'utf8'), sandbox, {{filename: f}});"
    )


def test_markdown_renders_gfm_pipe_table():
    md = "| Position | Betrag |\n| --- | ---: |\n| Miete | 1'200 |\n| <b>x</b> | 2 \\| 3 |"
    out = _run(_load("markdown.js") + "console.log(sandbox.window.KnovasMarkdown.render("
               + json.dumps(md) + "));")
    assert '<table class="md-table">' in out
    assert "<th>Position</th>" in out
    assert '<td class="md-align-right">1&#39;200</td>' in out
    assert "&lt;b&gt;x&lt;/b&gt;" in out          # escaped, never HTML
    assert "2 | 3" in out                        # \| is a literal pipe
    assert "<p>|" not in out


def test_markdown_has_table_and_render_table():
    script = _load("markdown.js") + (
        "const M = sandbox.window.KnovasMarkdown;"
        "console.log(JSON.stringify([M.hasTable('| a |\\n|---|\\n| 1 |'), M.hasTable('| a | b'),"
        " M.renderTable({title: 'T', headers: ['A', 'B'], rows: [['1', '<i>']]})]));"
    )
    has_a, has_b, html = json.loads(_run(script))
    assert has_a is True and has_b is False
    assert html.startswith('<table class="md-table"><caption>T</caption>')
    assert "<td>&lt;i&gt;</td>" in html


def test_markdown_paragraphs_still_render_around_tables():
    md = "Vor\n\n| a |\n|---|\n| 1 |\n\nNach"
    out = _run(_load("markdown.js") + "console.log(sandbox.window.KnovasMarkdown.render("
               + json.dumps(md) + "));")
    assert out.startswith("<p>Vor</p><table")
    assert out.endswith("</table><p>Nach</p>")
```

Run: `py -3.13 -m pytest tests/test_js_smoke.py -v` — Expected: FAIL (`hasTable` undefined; table rendered as paragraphs).

In `src/web_interface/static/js/markdown.js`, insert after `renderInline` (line 36):

```js
    var TABLE_ROW = /^\s*\|.*\|\s*$/;
    var TABLE_SEPARATOR = /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$/;

    /** Zellen einer Pipe-Zeile; `\|` bleibt ein Pipe-Zeichen im Text. */
    function splitCells(line) {
        var text = line.trim();
        if (text.charAt(0) === '|') text = text.slice(1);
        if (text.length && text.charAt(text.length - 1) === '|'
                && text.charAt(text.length - 2) !== '\\') {
            text = text.slice(0, -1);
        }
        var cells = [];
        var cur = '';
        for (var i = 0; i < text.length; i++) {
            var ch = text.charAt(i);
            if (ch === '\\' && text.charAt(i + 1) === '|') { cur += '|'; i++; continue; }
            if (ch === '|') { cells.push(cur.trim()); cur = ''; continue; }
            cur += ch;
        }
        cells.push(cur.trim());
        return cells;
    }

    function alignments(separatorLine) {
        return splitCells(separatorLine).map(function (cell) {
            var left = cell.charAt(0) === ':';
            var right = cell.charAt(cell.length - 1) === ':';
            if (left && right) return 'center';
            if (right) return 'right';
            return '';
        });
    }

    /** GFM-Tabelle aus bereits escaptem Text (Kopfzeile, Trenner, Zeilen). */
    function renderPipeTable(headerLine, separatorLine, bodyLines) {
        var aligns = alignments(separatorLine);
        var heads = splitCells(headerLine);
        var cls = function (i) { return aligns[i] ? ' class="md-align-' + aligns[i] + '"' : ''; };
        var out = ['<table class="md-table"><thead><tr>'];
        heads.forEach(function (h, i) { out.push('<th' + cls(i) + '>' + renderInline(h) + '</th>'); });
        out.push('</tr></thead>');
        if (bodyLines.length) {
            out.push('<tbody>');
            bodyLines.forEach(function (line) {
                var cells = splitCells(line);
                out.push('<tr>');
                for (var i = 0; i < heads.length; i++) {
                    out.push('<td' + cls(i) + '>' + renderInline(cells[i] == null ? '' : cells[i]) + '</td>');
                }
                out.push('</tr>');
            });
            out.push('</tbody>');
        }
        out.push('</table>');
        return out.join('');
    }

    /** Enthaelt das Markdown eine Pipe-Tabelle (Kopfzeile + Trennzeile)? */
    function hasTable(markdown) {
        var lines = String(markdown == null ? '' : markdown).split(/\r?\n/);
        for (var i = 0; i + 1 < lines.length; i++) {
            if (TABLE_ROW.test(lines[i]) && TABLE_SEPARATOR.test(lines[i + 1])) return true;
        }
        return false;
    }

    /** Strukturierte Tabelle {title, headers, rows} aus preview-content. Escaped hier. */
    function renderTable(table) {
        if (!table || typeof table !== 'object') return '';
        var headers = Array.isArray(table.headers) ? table.headers : [];
        var rows = Array.isArray(table.rows) ? table.rows : [];
        var out = ['<table class="md-table">'];
        if (table.title) out.push('<caption>' + escapeHtml(table.title) + '</caption>');
        if (headers.length) {
            out.push('<thead><tr>');
            headers.forEach(function (h) { out.push('<th>' + escapeHtml(h) + '</th>'); });
            out.push('</tr></thead>');
        }
        if (rows.length) {
            out.push('<tbody>');
            rows.forEach(function (row) {
                out.push('<tr>');
                (Array.isArray(row) ? row : []).forEach(function (cell) {
                    out.push('<td>' + escapeHtml(cell == null ? '' : cell) + '</td>');
                });
                out.push('</tr>');
            });
            out.push('</tbody>');
        }
        out.push('</table>');
        return out.join('');
    }
```

In `render`, inside the `for` loop (line 51), before `var heading = ...`, insert:

```js
            if (TABLE_ROW.test(line) && i + 1 < lines.length && TABLE_SEPARATOR.test(lines[i + 1])) {
                closeList();
                var body = [];
                var j = i + 2;
                while (j < lines.length && TABLE_ROW.test(lines[j])) { body.push(lines[j]); j++; }
                html.push(renderPipeTable(line, lines[i + 1], body));
                i = j - 1;
                continue;
            }
```

Change line 77 to:

```js
    window.KnovasMarkdown = { render: render, escapeHtml: escapeHtml,
                              hasTable: hasTable, renderTable: renderTable };
```

Append to `src/web_interface/static/css/style.css` (tokens only, after the `.preview-skeleton` block):

```css
/* --- Tabellen in der Vorschau (H4): GFM aus markdown.js oder strukturiert
   aus preview-content. Global, weil der Viewer dieselben Klassen nutzt. */
.md-table {
    border-collapse: collapse;
    margin: 0 0 12px;
    max-width: 100%;
    font-size: 0.85rem;
}

.md-table caption {
    text-align: left;
    color: var(--text-secondary);
    padding: 0 0 4px;
}

.md-table th,
.md-table td {
    border: 1px solid var(--border-color);
    padding: 4px 8px;
    text-align: left;
    vertical-align: top;
    overflow-wrap: anywhere;
}

.md-table th {
    background: var(--surface-sunken);
    font-weight: 600;
}

.md-table .md-align-right { text-align: right; }
.md-table .md-align-center { text-align: center; }
```

Run: `py -3.13 -m pytest tests/test_js_smoke.py -v` — Expected: PASS (3 tests, or SKIPPED without node).

- [ ] **Step 9: Dialog markup**

In `src/web_interface/templates/index.html`, between line 95 (`<div class="preview-actions" id="previewActions"></div>`) and line 96 (`<div class="preview-body" ...>`), insert:

```html
            <!-- Versionen, Aehnliche, Metadaten: nur sichtbar, wenn der Treffer
                 eine document_uuid traegt (Fixtures und Alt-Tenants haben keine).
                 Inhalte werden erst beim Aufklappen geladen: die API-Rate ist
                 knapp, und die meisten Vorschauen brauchen keins davon. -->
            <div class="preview-details" id="previewDetails" hidden>
                <details class="preview-detail" id="previewVersions">
                    <summary>Versionen <span class="preview-detail-hint" id="previewVersionsHint"></span></summary>
                    <div class="preview-detail-body" id="previewVersionsBody"></div>
                </details>
                <details class="preview-detail" id="previewSimilar">
                    <summary>Ähnliche Dokumente</summary>
                    <div class="preview-detail-body" id="previewSimilarBody"></div>
                </details>
                <details class="preview-detail" id="previewMetadata">
                    <summary>Metadaten</summary>
                    <div class="preview-detail-body" id="previewMetadataBody"></div>
                </details>
            </div>
```

Append to `style.css`:

```css
/* --- Dialog-Details: Versionen / Aehnliche / Metadaten ------------------ */
.preview-details {
    flex: 0 0 auto;
    display: flex;
    flex-wrap: wrap;
    gap: 6px 14px;
    padding: 0 var(--dialog-pad-x) 10px;
    border-bottom: 1px solid var(--border-color);
    font-size: 0.85rem;
}

.preview-detail summary {
    cursor: pointer;
    color: var(--primary-color);
    list-style: none;
}

.preview-detail summary::-webkit-details-marker { display: none; }
.preview-detail summary::before { content: '› '; }
.preview-detail[open] summary::before { content: '˅ '; }

.preview-detail-hint {
    color: var(--text-secondary);
    margin-left: 6px;
}

.preview-detail-body {
    flex-basis: 100%;
    padding: 6px 0 4px 14px;
}

.preview-detail[open] { flex-basis: 100%; }

.preview-detail-note {
    color: var(--text-secondary);
    margin: 4px 0;
}

.version-list { display: grid; gap: 4px; }

.version-row {
    display: grid;
    grid-template-columns: 7rem 6.5rem 1fr;
    gap: 0 12px;
    align-items: baseline;
}

.version-row--current { margin-bottom: 6px; }

.version-path {
    grid-column: 1 / -1;
    color: var(--text-secondary);
    overflow-wrap: anywhere;
    font-size: 0.8rem;
}

.badge-current {
    background: var(--highlight);
    color: var(--primary-hover);
}

.similar-list { list-style: none; margin: 0; padding: 0; display: grid; gap: 4px; }

.similar-item {
    display: grid;
    width: 100%;
    text-align: left;
    background: none;
    border: 1px solid var(--border-color);
    border-radius: var(--radius);
    padding: 6px 10px;
    cursor: pointer;
    color: var(--text-primary);
}

.similar-item:hover { background: var(--surface-sunken); }
.similar-meta { color: var(--text-secondary); font-size: 0.8rem; }

.metadata-form {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
    gap: 8px 12px;
}

.metadata-form label { display: grid; gap: 2px; color: var(--text-secondary); }
.metadata-form input,
.metadata-form select {
    padding: 4px 6px;
    border: 1px solid var(--border-color);
    border-radius: var(--radius);
    font: inherit;
    color: var(--text-primary);
    background: var(--card-bg);
}

.metadata-form-actions {
    grid-column: 1 / -1;
    display: flex;
    gap: 10px;
    align-items: center;
}

.metadata-form-note { color: var(--text-secondary); }
.metadata-form-note.is-error { color: var(--error-color); }
```

- [ ] **Step 10: `app.js` — refactor `openPreview`, add the three panels**

In `src/web_interface/static/js/app.js` constructor, after `this.previewPosition = document.getElementById('previewPosition');` (line 41) add:

```js
        this.previewDetails = document.getElementById('previewDetails');
        this.previewVersions = document.getElementById('previewVersions');
        this.previewVersionsHint = document.getElementById('previewVersionsHint');
        this.previewVersionsBody = document.getElementById('previewVersionsBody');
        this.previewSimilar = document.getElementById('previewSimilar');
        this.previewSimilarBody = document.getElementById('previewSimilarBody');
        this.previewMetadata = document.getElementById('previewMetadata');
        this.previewMetadataBody = document.getElementById('previewMetadataBody');
        /** Laufnummer der Vorschau-Anfrage: verspaetete Antworten erkennen. */
        this._previewToken = 0;
        /** @type {object|null} das gerade gezeigte Dokument (auch ausserhalb der Liste) */
        this._previewDoc = null;
        /** @type {number|null} Treffer-Index, zu dem "Zurueck" fuehrt */
        this._adhocOrigin = null;
        this._detailsState = null;
        this._similarResults = [];
```

In `initializeEventListeners`, after the `previewNext` listener (line 121) add:

```js
        // Details werden erst beim Aufklappen geladen (API-Rate).
        this.previewVersions.addEventListener('toggle', () => {
            if (this.previewVersions.open) this._loadVersions();
        });
        this.previewSimilar.addEventListener('toggle', () => {
            if (this.previewSimilar.open) this._loadSimilar();
        });
        this.previewSimilarBody.addEventListener('click', (e) => {
            const btn = e.target.closest('.similar-item');
            if (!btn) return;
            const row = this._similarResults[Number(btn.dataset.similarIndex)];
            if (row) this.openAdhocDocument(row, this._previewIndex);
        });
```

Replace `_afterPreviewClosed` (lines 243–254) with:

```js
    _afterPreviewClosed() {
        if (this._previewAbort) {
            this._previewAbort.abort();
            this._previewAbort = null;
        }
        this._previewIndex = null;
        this._previewDoc = null;
        this._adhocOrigin = null;
        this._detailsState = null;
        this._similarResults = [];
        this._markActiveCard(null);
        this.previewBody.classList.remove('is-pdf');
        this.previewBody.innerHTML = '';
        this.previewActions.innerHTML = '';
        this.previewPosition.textContent = '';
        this.previewDetails.hidden = true;
        this.previewVersions.open = false;
        this.previewSimilar.open = false;
        this.previewMetadata.open = false;
    }
```

Replace `_updatePreviewPosition` (lines 259–264) with:

```js
    _updatePreviewPosition(index) {
        if (index == null) {
            // Dokument ausserhalb der Trefferliste (aehnliches Dokument):
            // kein Blaettern, dafuer "Zurueck" in den Aktionen.
            this.previewPosition.textContent = 'Ähnliches Dokument';
            this.previewPrev.disabled = true;
            this.previewNext.disabled = true;
            return;
        }
        const total = this.currentResults.length;
        this.previewPosition.textContent = total ? `${index + 1} von ${total}` : '';
        this.previewPrev.disabled = index <= 0;
        this.previewNext.disabled = index >= total - 1;
    }
```

Replace `openPreview` (lines 281–367) with:

```js
    async openPreview(index) {
        const doc = this.currentResults[index];
        if (!doc) return;
        this._adhocOrigin = null;
        await this._showDocument(doc, index);
    }

    /** Dokument, das nicht in der Trefferliste steht (z. B. ein aehnliches). */
    async openAdhocDocument(doc, originIndex) {
        if (!doc) return;
        this._adhocOrigin = Number.isInteger(originIndex) ? originIndex : null;
        await this._showDocument(doc, null);
    }

    async _showDocument(doc, index) {
        // Laufende Anfrage abbrechen, damit ein schneller Kartenwechsel nicht
        // die Antwort des vorherigen Dokuments einblendet.
        if (this._previewAbort) this._previewAbort.abort();
        const controller = new AbortController();
        this._previewAbort = controller;
        this._previewIndex = index;
        this._previewDoc = doc;
        const token = ++this._previewToken;
        const stale = () => this._previewToken !== token;

        const docId = String(doc.doc_id || doc.pointer || '');
        const path = String(doc.path || '');
        const title = this.displayTitle(doc);

        if (!this.previewDialog.open) {
            this.previewDialog.showModal();
        }
        this._markActiveCard(index);
        this._updatePreviewPosition(index);
        this.previewTitle.textContent = title;
        this.previewMeta.textContent = '';
        this.previewActions.innerHTML = this._previewActionsHtml(doc) + this._backToResultsHtml(index);
        this._renderPreviewDetails(doc, index);
        this.previewBody.classList.remove('is-pdf');
        this.previewBody.innerHTML =
            '<div class="preview-skeleton"><span></span><span></span><span></span><span></span></div>';

        if (path.toLowerCase().endsWith('.pdf')) {
            const cfg = typeof window !== 'undefined' ? window.__DOCBRIDGE__ || {} : {};
            if (!cfg.pdfInlineInBrowser) {
                this.previewMeta.textContent = 'PDF';
                this.previewBody.innerHTML =
                    '<p class="preview-error">Die PDF-Vorschau ist deaktiviert. Nutzen Sie „Öffnen“.</p>';
                this._previewAbort = null;
                return;
            }
            const src = `/api/document/${encodeURIComponent(docId)}/preview?path=${encodeURIComponent(path)}`;
            try {
                const probe = await fetch(src, { method: 'GET', headers: { Range: 'bytes=0-0' },
                                                 credentials: 'same-origin', signal: controller.signal });
                if (this._redirectIfLoginRequired(probe)) return;
                if (stale()) return;
                if (!probe.ok && probe.status !== 206) {
                    throw new Error(`HTTP ${probe.status}`);
                }
                this.previewMeta.textContent = 'PDF';
                this.previewBody.classList.add('is-pdf');
                this.previewBody.innerHTML =
                    `<iframe src="${this.escapeAttr(src)}" title="PDF-Vorschau"></iframe>`;
            } catch (error) {
                if (error.name === 'AbortError') return;
                this.previewBody.innerHTML =
                    `<p class="preview-error">Vorschau nicht verfügbar (${this.escapeHtml(error.message)}). Nutzen Sie „Öffnen“.</p>`;
            } finally {
                if (this._previewAbort === controller) this._previewAbort = null;
            }
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
            // Zwischenzeitlicher Wechsel: Antwort verwerfen, bevor sie
            // irgendetwas ins Panel schreibt -- Erfolg wie Fehler.
            if (stale()) return;
            if (!response.ok || !data.success) {
                throw new Error(data.error || `HTTP ${response.status}`);
            }

            this.previewMeta.textContent = this._previewMetaText(data.kind, data.meta);
            let html = this._mailHeaderHtml(data.meta) + window.KnovasMarkdown.render(data.markdown);
            // Strukturierte Tabellen nur dann zusaetzlich, wenn das Markdown
            // selbst keine traegt -- sonst stuende jede Tabelle doppelt da.
            const tables = Array.isArray(data.tables) ? data.tables : [];
            if (tables.length && !window.KnovasMarkdown.hasTable(data.markdown)) {
                html += `<h4>Tabellen (${tables.length})</h4>`
                    + tables.map((t) => window.KnovasMarkdown.renderTable(t)).join('');
            }
            this.previewBody.innerHTML = html;
        } catch (error) {
            if (error.name === 'AbortError') return;
            console.warn('Preview:', error);
            this.previewBody.innerHTML =
                `<p class="preview-error">Vorschau nicht verfügbar (${this.escapeHtml(error.message)}). Nutzen Sie „Öffnen“.</p>`;
        } finally {
            if (this._previewAbort === controller) this._previewAbort = null;
        }
    }

    /** "Zurueck zum Treffer", wenn ein aehnliches Dokument gezeigt wird. */
    _backToResultsHtml(index) {
        if (index != null || this._adhocOrigin == null) return '';
        return `<button type="button" class="btn btn-outline" onclick="app.openPreview(${Number(this._adhocOrigin)})">Zurück zum Treffer</button>`;
    }

    // --- Details: Versionen (F6), Aehnliche (F8), Metadaten ---------------

    /** Nur wenn die API eine document_uuid mitgab; sonst bleibt der Block weg. */
    _renderPreviewDetails(doc, index) {
        const uuid = String(doc.document_uuid || '').trim();
        this._detailsState = { uuid, index, loaded: {} };
        this._similarResults = [];
        this.previewVersions.open = false;
        this.previewSimilar.open = false;
        this.previewMetadata.open = false;
        if (!uuid) {
            this.previewDetails.hidden = true;
            return;
        }
        this.previewDetails.hidden = false;
        this.previewVersionsHint.textContent = this._versionHint(doc);
        this.previewVersionsBody.innerHTML = '';
        this.previewSimilarBody.innerHTML = '';
        this.previewMetadataBody.innerHTML = this._metadataFormHtml(doc);
        this._bindMetadataForm(doc, index);
    }

    /** Versionsangabe aus dem Treffer selbst (F6 Stufe 1), ohne Anfrage. */
    _versionHint(doc) {
        if (doc.is_current === false) return 'ältere Version';
        const n = Number(doc.version_count);
        if (doc.has_versions && n > 0) return `aktuelle Version · ${n} ${n === 1 ? 'Vorversion' : 'Vorversionen'}`;
        if (doc.has_versions === false) return 'einzige Version';
        return '';
    }

    async _loadVersions() {
        const st = this._detailsState;
        if (!st || !st.uuid || st.loaded.versions) return;
        st.loaded.versions = true;
        const body = this.previewVersionsBody;
        body.innerHTML = '<p class="preview-detail-note">Wird geladen …</p>';
        try {
            const resp = await fetch(`/api/documents/${encodeURIComponent(st.uuid)}/versions`,
                                     { credentials: 'same-origin' });
            if (this._redirectIfLoginRequired(resp)) return;
            const data = await resp.json().catch(() => ({}));
            if (this._detailsState !== st) return;
            if (!resp.ok || !data.success) throw new Error(data.error || `HTTP ${resp.status}`);
            body.innerHTML = this._versionsHtml(data);
        } catch (err) {
            st.loaded.versions = false;
            body.innerHTML = `<p class="preview-error">Versionen nicht verfügbar (${this.escapeHtml(err.message)}).</p>`;
        }
    }

    _versionsHtml(data) {
        const rows = Array.isArray(data.versions) ? data.versions.slice() : [];
        const current = data.current || {};
        const head = `<div class="version-row version-row--current">`
            + `<span class="badge badge-current">aktuelle Version</span>`
            + `<span class="version-when">${this.escapeHtml(this._formatDateShort(current.timestamp || current.updated_at || '') || '')}</span>`
            + `<span class="version-path">${this.escapeHtml(current.pointer || current.path || '')}</span></div>`;
        if (!rows.length) {
            return head + '<p class="preview-detail-note">Keine Vorversionen bekannt.</p>';
        }
        rows.sort((a, b) => Number(b.version_number || 0) - Number(a.version_number || 0));
        const list = rows.map((v) => `
            <div class="version-row">
                <span class="version-number">Version ${this.escapeHtml(String(v.version_number ?? '–'))}</span>
                <span class="version-when">${this.escapeHtml(this._formatDateShort(v.timestamp || '') || '')}</span>
                <span class="version-by">${this.escapeHtml(this._changedByLabel(v))}</span>
                <span class="version-path">${this.escapeHtml(v.pointer_at_version || v.path || '')}</span>
            </div>`).join('');
        return head + `<div class="version-list">${list}</div>`
            + '<p class="preview-detail-note">Durchsucht wird nur die aktuelle Version; ältere Fassungen sind gelistet, nicht indexiert.</p>';
    }

    /** Wer hat geaendert -- und ist das ein angemeldeter Nutzer oder eine Angabe des Clients? */
    _changedByLabel(v) {
        const who = String(v.changed_by || '').trim();
        if (!who) return '';
        const kind = String(v.changed_by_kind || '');
        if (kind === 'subject') return `von ${who}`;
        if (kind === 'system') return 'System';
        return `gemeldet von ${who}`;
    }

    async _loadSimilar() {
        const st = this._detailsState;
        if (!st || !st.uuid || st.loaded.similar) return;
        st.loaded.similar = true;
        const body = this.previewSimilarBody;
        body.innerHTML = '<p class="preview-detail-note">Wird gesucht …</p>';
        try {
            const resp = await fetch(`/api/documents/${encodeURIComponent(st.uuid)}/similar`, {
                method: 'POST',
                credentials: 'same-origin',
                headers: this._jsonHeadersWithCsrf(),
                body: JSON.stringify({ limit: 5 }),
            });
            if (this._redirectIfLoginRequired(resp)) return;
            const data = await resp.json().catch(() => ({}));
            if (this._detailsState !== st) return;
            if (!resp.ok || !data.success) throw new Error(data.error || `HTTP ${resp.status}`);
            this._similarResults = Array.isArray(data.results) ? data.results : [];
            body.innerHTML = this._similarHtml(data);
        } catch (err) {
            st.loaded.similar = false;
            body.innerHTML = `<p class="preview-error">Ähnliche Dokumente nicht verfügbar (${this.escapeHtml(err.message)}).</p>`;
        }
    }

    _similarHtml(data) {
        const rows = Array.isArray(data.results) ? data.results : [];
        if (!rows.length) {
            return data.no_strong_matches
                ? '<p class="preview-detail-note">Keine ähnlichen Dokumente mit belastbarer Ähnlichkeit gefunden.</p>'
                : '<p class="preview-detail-note">Keine ähnlichen Dokumente gefunden.</p>';
        }
        return `<ul class="similar-list">${rows.map((r, i) => `
            <li><button type="button" class="similar-item" data-similar-index="${i}">
                <span class="similar-title">${this.escapeHtml(this.displayTitle(r))}</span>
                <span class="similar-meta">${this.escapeHtml(this._similarMeta(r))}</span>
            </button></li>`).join('')}</ul>`;
    }

    _similarMeta(r) {
        const parts = [];
        if (r.document_type) parts.push(String(r.document_type));
        const d = r.document_date || r.date;
        if (d) parts.push(this._formatDateShort(d));
        if (r.author) parts.push(String(r.author));
        if (r.language) parts.push(String(r.language).toUpperCase());
        return parts.join(' · ');
    }

    static METADATA_STATUS = [
        ['', '–'], ['draft', 'Entwurf'], ['final', 'Final'],
        ['executed', 'Unterzeichnet'], ['unknown', 'Unbekannt'],
    ];

    static METADATA_LANGUAGES = [
        ['', '–'], ['de', 'Deutsch'], ['fr', 'Französisch'], ['it', 'Italienisch'],
        ['en', 'Englisch'], ['und', 'Unbestimmt'],
    ];

    _metadataFormHtml(doc) {
        const opt = (list, cur) => list.map(([v, l]) =>
            `<option value="${this.escapeAttr(v)}"${v === String(cur || '') ? ' selected' : ''}>${this.escapeHtml(l)}</option>`).join('');
        return `<form class="metadata-form" id="previewMetadataForm">
            <label>Dokumenttyp <input name="document_type" maxlength="128" value="${this.escapeAttr(doc.document_type || '')}"></label>
            <label>Status <select name="document_status">${opt(DocumentSearchApp.METADATA_STATUS, doc.document_status)}</select></label>
            <label>Datum <input name="document_date" type="date" value="${this.escapeAttr(String(doc.document_date || '').slice(0, 10))}"></label>
            <label>Sprache <select name="language">${opt(DocumentSearchApp.METADATA_LANGUAGES, doc.language)}</select></label>
            <label>Autor <input name="author" maxlength="500" value="${this.escapeAttr(doc.author || '')}"></label>
            <div class="metadata-form-actions">
                <button type="submit" class="btn btn-primary">Speichern</button>
                <span class="metadata-form-note" aria-live="polite"></span>
            </div>
        </form>`;
    }

    _bindMetadataForm(doc, index) {
        const form = this.previewMetadataBody.querySelector('#previewMetadataForm');
        if (!form) return;
        form.addEventListener('submit', (e) => {
            e.preventDefault();
            this._submitMetadata(form, doc, index);
        });
    }

    async _submitMetadata(form, doc, index) {
        const uuid = String(doc.document_uuid || '');
        const note = form.querySelector('.metadata-form-note');
        const fd = new FormData(form);
        const payload = {};
        for (const key of ['document_type', 'document_status', 'document_date', 'language', 'author']) {
            const val = String(fd.get(key) || '').trim();
            if (val && val !== String(doc[key] || '')) payload[key] = val;
        }
        if (!Object.keys(payload).length) {
            note.textContent = 'Keine Änderungen.';
            note.classList.remove('is-error');
            return;
        }
        note.textContent = 'Wird gespeichert …';
        note.classList.remove('is-error');
        try {
            const resp = await fetch(`/api/documents/${encodeURIComponent(uuid)}/metadata`, {
                method: 'PATCH',
                credentials: 'same-origin',
                headers: this._jsonHeadersWithCsrf(),
                body: JSON.stringify(payload),
            });
            if (this._redirectIfLoginRequired(resp)) return;
            const data = await resp.json().catch(() => ({}));
            if (!resp.ok || !data.success) {
                // Ablehnungen der API werden gezeigt, nie verschluckt (F3-Regel).
                throw new Error(data.error || `HTTP ${resp.status}`);
            }
            Object.assign(doc, payload);
            if (payload.document_date) doc.date = payload.document_date;
            note.textContent = 'Gespeichert.';
            this.showSuccess('Metadaten gespeichert.');
            if (index != null) this._replaceCard(index);
        } catch (err) {
            note.textContent = err.message;
            note.classList.add('is-error');
        }
    }

    /** Karte nach einer Metadaten-Aenderung neu zeichnen, ohne die Liste zu verlieren. */
    _replaceCard(index) {
        const old = this.resultsContainer.querySelector(`.document-card[data-index="${index}"]`);
        if (!old) return;
        const fresh = this.createDocumentCard(this.currentResults[index], index);
        fresh.classList.add('is-active');
        old.replaceWith(fresh);
    }
```

- [ ] **Step 11: Template test for the dialog block**

Append to `tests/test_document_routes.py`:

```python
def test_index_renders_the_dialog_detail_blocks(docs_app):
    client = docs_app.test_client()
    _login(client)
    html = client.get("/").get_data(as_text=True)
    for element_id in ("previewDetails", "previewVersions", "previewVersionsHint",
                       "previewSimilar", "previewMetadata", "previewMetadataBody"):
        assert f'id="{element_id}"' in html, element_id
    assert "Ähnliche Dokumente" in html
```

Run: `py -3.13 -m pytest tests/test_document_routes.py tests/test_js_smoke.py tests/test_preview_extract.py tests/test_csrf_enforcement.py tests/test_platform_health.py -v`
Expected: PASS. Then hand-check in a browser at `http://localhost:8081` (login per `docs/superpowers/plans/2026-07-30-trefferliste.md`): open a hit → the three `<details>` appear only for hits carrying `document_uuid`; "Versionen" loads on expand; "Ähnliche Dokumente" lists five entries and "Zurück zum Treffer" returns; saving a status change re-renders the card.

- [ ] **Step 12: Document the Platform routes**

Create `KnovasPlatform/docs/integration/documents-api.md`:

```markdown
## Document API (Platform)

Session cookie required on every route; the mutating verbs additionally
require `X-CSRF-Token` (see [open-tokens-api.md](open-tokens-api.md) for the
convention). `<uuid>` is the Knovas `document_uuid` a search hit carries.
Ids you may not see answer **404**, never 403 — the platform does not widen
what the tenant API discloses.

## Versions (F6, tier 1) — `GET /api/documents/<uuid>/versions`

```json
{
  "success": true,
  "document_uuid": "…",
  "current": {"document_uuid": "…", "version_number": 3, "pointer": "corpus/akte/v3.pdf"},
  "versions": [
    {"version_number": 2, "content_hash_raw": "…", "pointer_at_version": "corpus/akte/v2.pdf",
     "path": "/…", "timestamp": "2026-02-01T10:00:00Z",
     "changed_by": "rc-01", "changed_by_kind": "client_ref"}
  ],
  "version_count": 1
}
```

`changed_by_kind` is `subject` when the change was made by a verified,
brokered user, `client_ref` when the ingestion client asserted the name,
`system` otherwise. Only the current version is searchable (tier 2 — searching
superseded text — is not offered).

## Similar documents (F8) — `POST /api/documents/<uuid>/similar`

Body: `{"limit": 5, "filters": {…API filter keys…}, "scope": {…}}` — all
optional; `limit` is clamped to 1..20. Shares the tenant's query rate budget.

```json
{
  "success": true,
  "document_uuid": "…",
  "results": [ {"doc_id": "…", "path": "…", "title": "…", "score": 0.71,
                "document_type": "Vertrag", "document_date": "2024-03-15",
                "author": "…", "language": "de", "kg_node_ids": ["…"], "…": "…"} ],
  "total": 5,
  "no_strong_matches": false,
  "similar_matters": [ {"node_id": "…", "hit_count": 2} ],
  "semantix": {"status": "success", "message": null, "query_session_id": "…"}
}
```

`results` carry the same platform enrichment as `/api/search` hits (open
hints, `autodoc_rel_path`). `similar_matters` groups the hits' visible
`kg_node_ids`; the matter page renders it as "Ähnliche Akten".
`no_strong_matches: true` with an empty list means the relevance gate found
nothing it would stand behind — the UI says so instead of padding.

## Metadata (F3) — `PATCH /api/documents/<uuid>/metadata`

Body: any subset of `author` (≤ 500), `document_type` (≤ 128), `language`
(ISO-639, 2–3 letters), `document_date` (`YYYY-MM-DD`), `document_status`
(`draft|final|executed|unknown`), `source_kind`
(`share|onedrive|mailbox|pst|upload|addin`), `extra` (≤ 16 keys). Empty
values are ignored; a body without an effective change is `400`.

```json
{"success": true, "document_uuid": "…", "metadata": {"document_status": "executed"}}
```

## Errors

| Status | Meaning |
|--------|---------|
| 400 | Platform-side validation, or a `validation_error` from the tenant API (`error`, `error_code`, `details` are passed through verbatim) |
| 404 | Unknown, foreign or malformed `uuid` |
| 409 / 422 / 429 / 503 | Passed through from the tenant API with `error_code` (429 keeps `Retry-After`) |
| 502 | Any other tenant-API failure (certificate, 5xx); the detail is logged, not returned |
```

- [ ] **Step 13: Commit**

```bash
git add src/web_interface/document_routes.py src/web_interface/app.py src/web_interface/preview.py \
        src/web_interface/static/js/markdown.js src/web_interface/static/js/app.js \
        src/web_interface/static/css/style.css src/web_interface/templates/index.html \
        tests/test_document_routes.py tests/test_js_smoke.py tests/test_preview_extract.py \
        tests/test_csrf_enforcement.py ../../docs/integration/documents-api.md
git commit -m "feat(search): document dialog - versions, similar documents, metadata edit, tables in preview"
```

---

---

### Task KC-A-7: Viewer — vendored pdf.js, `/viewer` route, jump-to-hit, shared `openEvidence`
**Requirements:** F7 (also serves G3 evidence drill-down and, later, fact evidence and conflict hits)
**Files:**
- Create: `src/web_interface/static/js/vendor/pdfjs/{pdf.mjs, pdf.worker.mjs, LICENSE, VERSION, SHA256SUMS, README.md}` (Step 1), `src/web_interface/viewer_routes.py`, `src/web_interface/templates/viewer.html`, `src/web_interface/static/js/viewer.js`, `src/web_interface/static/js/evidence.js`, `src/web_interface/static/css/viewer.css`, `tests/test_viewer_routes.py`.
- Modify: `src/web_interface/app.py` — imports (add `mimetypes` after line 13), `DOCBRIDGE_BUILD_ID` (line 544), `_prevent_stale_ui_assets` (lines 668–681), `open` block reads (after line 745 `pdf_inline_in_browser = ...`), `index()` (lines 1016–1035), blueprint registration before line 1717.
- Modify: `src/web_interface/templates/index.html` (`window.__DOCBRIDGE__` lines 104–113; script tags 115–116), `src/web_interface/templates/ontology.html` (script tags 77–79).
- Modify: `src/web_interface/static/js/app.js` — `_showDocument` PDF branch (Task KC-A-6 shape), new `_hitsFor(doc)`.
- Modify: `src/web_interface/static/js/ontology.js` — lines 1014–1016, 1081–1085, 1566–1568, 1581–1585, 1631–1649.
- Modify: `nginx/docbridge-web-local.conf` line 14; `KnovasPlatform/deploy/host-nginx/knovas-platform.conf.example` line 36; `config/config.yaml` open block (after line 259); `KnovasPlatform/.env.example` (after `OPEN_PDF_INLINE_IN_BROWSER=true`); `KnovasPlatform/docker-compose.yml` (after `OPEN_PDF_INLINE_IN_BROWSER:`).
- Modify: `tests/test_js_smoke.py` (append), `tests/test_platform_health.py` is unaffected (`externalOpenHref`, `_buildMatchLocationsHtml`, `pathOrBrowserFlag` stay in app.js).
**Interfaces:**
- Consumes: `GET /api/document/<doc_id>/preview?path=` (bytes, unchanged; app.py:1319) and `GET /api/document/<doc_id>/preview-content?path=` (+ `tables`, Task KC-A-6); `preview.preview_kind(path)`; result-row `page_number`, `snippet`, `top_chunks[].{page_number, snippet, chunk_kind}` (Task KC-A-1); `window.KnovasMarkdown.render/hasTable/renderTable` (Task KC-A-6); the Cortex evidence shape `{document:{path,title}, page, quote}` (ontology.js).
- Produces:
  - `GET /viewer?doc=&path=&page=&snippet=&hits=` (login-gated HTML; `hits` optional JSON `[{page, snippet}]`, ≤ 8 entries, snippets ≤ 300 chars) rendering `viewer.html` with `window.__VIEWER__ = {enabled, pdfInline, doc, path, title, page, snippet, hits, kind: 'pdf'|'docx'|'txt'|'msg'|null, previewUrl, contentUrl}`; response header `Content-Security-Policy: frame-ancestors 'self'; worker-src 'self'`; `400` when `path` is missing.
  - `viewer_routes.parse_hits(raw: str|None) -> list[dict]`, `viewer_routes.register_viewer_routes(app, *, viewer_enabled: bool, pdf_inline: bool, page_context: Callable[[], dict], asset_version: Callable[[], str])`, constants `MAX_SNIPPET_CHARS = 300`, `MAX_HITS = 8`, `VIEWER_CSP`.
  - `static/js/evidence.js`: `window.openEvidence(docId, path, page, snippet, options={container?: HTMLElement, hits?: [{page, snippet}], title?: string, frameClass?: string}) -> HTMLIFrameElement|null` (mounts an `<iframe>` into `container`, else opens a tab) and `window.KnovasEvidence.viewerUrl(docId, path, page, snippet, hits) -> string`. This is the registry's shared `openEvidence(docId, path, page, snippet)` helper — a standalone script rather than an app.js method because ontology.html does not load app.js.
  - `static/js/viewer.js`: `window.KnovasViewer.{normalizeText, buildIndex(items), findNeedle(text, snippet), itemsInRange(owner, start, end), highlightInElement(root, snippet), ZOOM_STEPS}` (pure, node-testable) plus the page controller.
  - Config `open.viewer_enabled` (env `OPEN_VIEWER_ENABLED`, default `true`) → `window.__DOCBRIDGE__.viewerEnabled`; `DOCBRIDGE_BUILD_ID = 'pflichtenheft-f-v1'`.
  - Vendored `pdf.js` files with `VERSION` = `6.2.108` and `SHA256SUMS`; `.mjs` served as `text/javascript`, `/static/js/vendor/*` with `Cache-Control: public, max-age=86400`.

- [ ] **Step 1: Vendor pdf.js (pinned, checksummed, licence kept)**

Run from `E:/Knovas/KnovasComponents/KnovasPlatform/components/docbridge_integration` (Git Bash; `unzip`, `curl`, `sha256sum` are in Git for Windows):

```bash
PDFJS_VERSION=6.2.108     # newest tagged release on 2026-08-15 (github.com/mozilla/pdf.js/releases)
VENDOR=src/web_interface/static/js/vendor/pdfjs
DL="$(mktemp -d)"
curl -fsSL -o "$DL/pdfjs.zip" \
  "https://github.com/mozilla/pdf.js/releases/download/v${PDFJS_VERSION}/pdfjs-${PDFJS_VERSION}-dist.zip"
unzip -o -q "$DL/pdfjs.zip" build/pdf.mjs build/pdf.worker.mjs LICENSE -d "$DL"
mkdir -p "$VENDOR"
cp "$DL/build/pdf.mjs" "$DL/build/pdf.worker.mjs" "$DL/LICENSE" "$VENDOR"/
printf '%s\n' "$PDFJS_VERSION" > "$VENDOR/VERSION"
( cd "$VENDOR" && sha256sum pdf.mjs pdf.worker.mjs > SHA256SUMS && sha256sum -c SHA256SUMS )
grep -c "pdfjsVersion = \"${PDFJS_VERSION}\"" "$VENDOR/pdf.mjs"     # expected: 1
ls -la "$VENDOR"
```

Expected: `pdf.mjs: OK`, `pdf.worker.mjs: OK`, the grep prints `1`. Create `src/web_interface/static/js/vendor/pdfjs/README.md`:

```markdown
## pdf.js (vendored)

Source: https://github.com/mozilla/pdf.js/releases/tag/v6.2.108 —
`pdfjs-6.2.108-dist.zip`, files `build/pdf.mjs`, `build/pdf.worker.mjs`,
`LICENSE` (Apache-2.0). Unmodified. Version in `VERSION`, hashes in `SHA256SUMS`.

Used by `static/js/viewer.js` (`/viewer`). No npm, no build step, no CDN — the
same rule as for Cytoscape and the IBM Plex fonts.

## Upgrade (also for a CVE)

1. Pick the release, set `PDFJS_VERSION`, run the fetch block from
   `docs/superpowers/plans/2026-08-15-pflichtenheft-d-j-*.md` Task KC-A-7 Step 1
   (curl → unzip → copy → `sha256sum > SHA256SUMS`).
2. `sha256sum -c SHA256SUMS`, then `py -3.13 -m pytest tests/test_viewer_routes.py tests/test_js_smoke.py`.
3. Open a hit in the browser: page jump, highlight, zoom, prev/next hit.
4. Note the version in `KnovasPlatform/CHANGELOG.md`.

The worker is loaded from the same origin (`worker-src 'self'`); a stricter
CSP that blocks module workers makes pdf.js fall back to its in-thread
"fake worker", which is slower but functional.
```

- [ ] **Step 2: Write the failing route and asset tests**

Create `tests/test_viewer_routes.py`:

```python
"""/viewer (F7): the page that opens a document at the hit and highlights it.

The route renders a template only; bytes keep flowing through
/api/document/<id>/preview and /preview-content with their path confinement.
"""
import json
import re
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
WEB_SRC = SRC / "web_interface"
for p in (SRC, WEB_SRC):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


@pytest.fixture
def logged_in_client(docbridge_app):
    client = docbridge_app.test_client()
    with client.session_transaction() as session:
        session["company_login_ok"] = True
    return client


def _viewer_config(html: str) -> dict:
    match = re.search(r"window\.__VIEWER__ = (\{.*?\});", html, re.S)
    assert match, html[:400]
    return json.loads(match.group(1))


def test_viewer_requires_login(docbridge_app):
    resp = docbridge_app.test_client().get("/viewer?path=a.pdf")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_viewer_without_path_is_400(logged_in_client):
    assert logged_in_client.get("/viewer").status_code == 400


def test_viewer_renders_sanitised_config(logged_in_client):
    resp = logged_in_client.get(
        "/viewer?doc=corpus%2Fakte%2Fvertrag.pdf&path=akte/vertrag.pdf&page=4"
        "&snippet=Die%20K%C3%BCndigungsfrist%20betr%C3%A4gt%20drei%20Monate.")
    assert resp.status_code == 200
    cfg = _viewer_config(resp.get_data(as_text=True))
    assert cfg["kind"] == "pdf"
    assert cfg["page"] == 4
    assert cfg["snippet"] == "Die Kündigungsfrist beträgt drei Monate."
    assert cfg["hits"] == [{"page": 4, "snippet": "Die Kündigungsfrist beträgt drei Monate."}]
    assert cfg["previewUrl"] == "/api/document/corpus%2Fakte%2Fvertrag.pdf/preview?path=akte%2Fvertrag.pdf"
    assert cfg["contentUrl"] == "/api/document/corpus%2Fakte%2Fvertrag.pdf/preview-content?path=akte%2Fvertrag.pdf"
    assert cfg["title"] == "vertrag.pdf"
    assert cfg["enabled"] is True and cfg["pdfInline"] is True


def test_viewer_page_and_snippet_are_bounded(logged_in_client):
    long_snippet = "x" * 400
    resp = logged_in_client.get(f"/viewer?path=a.pdf&page=abc&snippet={long_snippet}")
    cfg = _viewer_config(resp.get_data(as_text=True))
    assert cfg["page"] == 1
    assert len(cfg["snippet"]) == 300
    resp = logged_in_client.get("/viewer?path=a.pdf&page=-3")
    assert _viewer_config(resp.get_data(as_text=True))["page"] == 1


def test_viewer_hits_are_parsed_and_capped(logged_in_client):
    hits = [{"page": i + 1, "snippet": f"Treffer {i}"} for i in range(12)]
    resp = logged_in_client.get("/viewer?path=a.pdf&page=2&hits=" + json.dumps(hits))
    cfg = _viewer_config(resp.get_data(as_text=True))
    assert len(cfg["hits"]) == 8
    assert cfg["hits"][0] == {"page": 1, "snippet": "Treffer 0"}

    resp = logged_in_client.get("/viewer?path=a.pdf&hits=%7Bnot-json")
    assert _viewer_config(resp.get_data(as_text=True))["hits"] == []


def test_viewer_non_pdf_reports_kind_for_the_text_fallback(logged_in_client):
    cfg = _viewer_config(logged_in_client.get("/viewer?path=brief.docx").get_data(as_text=True))
    assert cfg["kind"] == "docx"
    cfg = _viewer_config(logged_in_client.get("/viewer?path=bild.png").get_data(as_text=True))
    assert cfg["kind"] is None


def test_viewer_sets_worker_src_csp(logged_in_client):
    resp = logged_in_client.get("/viewer?path=a.pdf")
    csp = resp.headers.get("Content-Security-Policy", "")
    assert "worker-src 'self'" in csp
    assert "frame-ancestors 'self'" in csp


def test_viewer_does_not_touch_the_filesystem(logged_in_client):
    """The page renders for any path; the bytes routes do the confinement."""
    resp = logged_in_client.get("/viewer?path=../../etc/passwd")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "root:" not in body
    assert logged_in_client.get(
        "/api/document/x/preview?path=../../etc/passwd").status_code == 400


def test_vendored_pdfjs_is_served_as_javascript_and_cacheable(logged_in_client):
    for name in ("pdf.mjs", "pdf.worker.mjs"):
        resp = logged_in_client.get(f"/static/js/vendor/pdfjs/{name}")
        assert resp.status_code == 200, name
        assert resp.mimetype in ("text/javascript", "application/javascript"), resp.mimetype
        assert resp.headers.get("Cache-Control", "").startswith("public")


def test_pages_load_evidence_js_and_expose_viewer_flag(logged_in_client):
    index = logged_in_client.get("/").get_data(as_text=True)
    assert "js/evidence.js" in index
    assert "viewerEnabled: true" in index
    ontology = logged_in_client.get("/ontology").get_data(as_text=True)
    assert "js/evidence.js" in ontology


def test_parse_hits_unit():
    from web_interface.viewer_routes import parse_hits

    assert parse_hits(None) == []
    assert parse_hits('[{"page": "3", "snippet": "  a   b  "}, {"page": 0}, "x", {"snippet": "no page"}]') \
        == [{"page": 3, "snippet": "a b"}]
```

Run: `py -3.13 -m pytest tests/test_viewer_routes.py -v`
Expected: FAIL — `/viewer` 404, `evidence.js` missing from pages, `parse_hits` import error; the vendored-asset test passes only for status once Step 1 ran but fails on `Cache-Control`.

- [ ] **Step 3: The route**

Create `src/web_interface/viewer_routes.py`:

```python
"""/viewer - Fundstelle anzeigen (F7).

Rendert nur die Seite. Die Bytes kommen weiterhin aus
/api/document/<id>/preview (PDF) bzw. /preview-content (uebrige Formate),
mit deren Pfad-Einhegung; diese Route fasst das Dateisystem nicht an.
Parameter werden hier begrenzt und als window.__VIEWER__ ins Template
gegeben, damit viewer.js keine URL parsen muss.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import quote

from flask import Blueprint, make_response, render_template, request

from web_interface.preview import preview_kind

logger = logging.getLogger(__name__)

MAX_SNIPPET_CHARS = 300
MAX_HITS = 8
MAX_PATH_CHARS = 2000
## worker-src: pdf.js startet einen Module-Worker von derselben Herkunft.
VIEWER_CSP = "frame-ancestors 'self'; worker-src 'self'"


def _clean_page(raw: Any) -> int:
    try:
        page = int(str(raw).strip())
    except (TypeError, ValueError):
        return 1
    return page if page >= 1 else 1


def _clean_snippet(raw: Any) -> str:
    return ' '.join(str(raw or '').split())[:MAX_SNIPPET_CHARS]


def parse_hits(raw: Optional[str]) -> List[Dict[str, Any]]:
    """[{page, snippet}] aus dem hits-Parameter; Unbrauchbares faellt weg."""
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except ValueError:
        return []
    if not isinstance(data, list):
        return []
    out: List[Dict[str, Any]] = []
    for entry in data[:MAX_HITS]:
        if not isinstance(entry, dict):
            continue
        try:
            page = int(entry.get('page'))
        except (TypeError, ValueError):
            continue
        if page < 1:
            continue
        out.append({'page': page, 'snippet': _clean_snippet(entry.get('snippet'))})
    return out


def register_viewer_routes(app, *, viewer_enabled: bool, pdf_inline: bool,
                           page_context: Callable[[], Dict[str, Any]],
                           asset_version: Callable[[], str]):
    bp = Blueprint('viewer', __name__)

    @bp.route('/viewer', methods=['GET'])
    def viewer_page():
        path = str(request.args.get('path') or '').strip()[:MAX_PATH_CHARS]
        doc_id = str(request.args.get('doc') or '').strip()[:MAX_PATH_CHARS] or path
        page = _clean_page(request.args.get('page'))
        snippet = _clean_snippet(request.args.get('snippet'))
        hits = parse_hits(request.args.get('hits'))
        if not hits and snippet:
            hits = [{'page': page, 'snippet': snippet}]
        kind = preview_kind(path) if path else None
        doc_seg = quote(doc_id, safe='')
        path_q = quote(path, safe='')
        viewer_config = {
            'enabled': bool(viewer_enabled),
            'pdfInline': bool(pdf_inline),
            'doc': doc_id,
            'path': path,
            'title': os.path.basename(path.replace('\\', '/')) or doc_id,
            'page': page,
            'snippet': snippet,
            'hits': hits,
            'kind': kind,
            'previewUrl': f'/api/document/{doc_seg}/preview?path={path_q}' if path else '',
            'contentUrl': f'/api/document/{doc_seg}/preview-content?path={path_q}' if path else '',
        }
        status = 200 if path else 400
        html = render_template(
            'viewer.html',
            **page_context(),
            viewer_config=viewer_config,
            error=None if path else 'Kein Dokument angegeben.',
            asset_version=asset_version(),
        )
        response = make_response(html, status)
        response.headers['Content-Security-Policy'] = VIEWER_CSP
        return response

    app.register_blueprint(bp)
    return bp
```

Create `src/web_interface/templates/viewer.html` (bare page — it lives in an `<iframe>` most of the time and must not carry the sidebar):

```html
<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ brand }} Viewer · {{ viewer_config.title }}</title>
    <link rel="icon" href="{{ url_for('static', filename='img/favicon.svg') }}" type="image/svg+xml">
    <link rel="stylesheet" href="{{ url_for('static', filename='css/style.css') }}?v={{ asset_version }}">
    <link rel="stylesheet" href="{{ url_for('static', filename='css/viewer.css') }}?v={{ asset_version }}">
</head>
<body class="viewer-body">
    <header class="viewer-toolbar" role="toolbar" aria-label="Viewer">
        <div class="viewer-toolbar-group" id="viewerPageTools">
            <button type="button" class="preview-icon-button" id="viewerPrevPage" aria-label="Vorherige Seite" title="Vorherige Seite">
                <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"
                     stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="m15 18-6-6 6-6"/></svg>
            </button>
            <label class="viewer-page-indicator">Seite
                <input id="viewerPageInput" type="number" min="1" value="{{ viewer_config.page }}" aria-label="Seite">
                / <span id="viewerPageCount">–</span>
            </label>
            <button type="button" class="preview-icon-button" id="viewerNextPage" aria-label="Nächste Seite" title="Nächste Seite">
                <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"
                     stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="m9 18 6-6-6-6"/></svg>
            </button>
        </div>
        <div class="viewer-toolbar-group" id="viewerHits" hidden>
            <button type="button" class="btn-text" id="viewerPrevHit">‹ Fundstelle</button>
            <span id="viewerHitLabel"></span>
            <button type="button" class="btn-text" id="viewerNextHit">Fundstelle ›</button>
        </div>
        <div class="viewer-toolbar-group" id="viewerZoomTools">
            <button type="button" class="preview-icon-button" id="viewerZoomOut" aria-label="Verkleinern" title="Verkleinern">−</button>
            <span id="viewerZoomLabel">Breite</span>
            <button type="button" class="preview-icon-button" id="viewerZoomIn" aria-label="Vergrössern" title="Vergrössern">+</button>
            <button type="button" class="btn-text" id="viewerZoomFit" title="An Breite anpassen">Breite</button>
        </div>
        <span class="viewer-status" id="viewerStatus" aria-live="polite"></span>
    </header>
    <main class="viewer-stage" id="viewerStage" tabindex="0">
        {% if error %}
        <p class="viewer-note is-error">{{ error }}</p>
        {% endif %}
        <div class="viewer-pages" id="viewerPages"></div>
        <div class="viewer-text preview-body" id="viewerText" hidden></div>
    </main>
    <script>window.__VIEWER__ = {{ viewer_config|tojson }};</script>
    <script src="{{ url_for('static', filename='js/markdown.js') }}?v={{ asset_version }}"></script>
    <script src="{{ url_for('static', filename='js/viewer.js') }}?v={{ asset_version }}"></script>
</body>
</html>
```

Create `src/web_interface/static/css/viewer.css` (the `.textLayer` block mirrors pdf.js `web/text_layer_builder.css` v6.2.108 — Apache-2.0 — reduced to what a read-only viewer needs; `--total-scale-factor` is what pdf.js' own viewer defines on `.page`):

```css
/* --- Viewer (F7): Werkzeugleiste, Seite, Textebene, Fundstelle. Nur Tokens. */

.viewer-body {
    margin: 0;
    background: var(--surface-sunken);
    color: var(--text-primary);
    font-family: var(--font-body);
    display: flex;
    flex-direction: column;
    height: 100vh;
}

.viewer-toolbar {
    flex: 0 0 auto;
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 6px 18px;
    padding: 6px 12px;
    background: var(--card-bg);
    border-bottom: 1px solid var(--border-color);
    font-size: 0.85rem;
}

.viewer-toolbar-group { display: flex; align-items: center; gap: 6px; }

.viewer-page-indicator input {
    width: 3.5rem;
    padding: 2px 4px;
    border: 1px solid var(--border-color);
    border-radius: var(--radius);
    font: inherit;
    text-align: right;
}

.viewer-status { color: var(--text-secondary); margin-left: auto; }
.viewer-status.is-error { color: var(--error-color); }

.viewer-stage {
    flex: 1 1 0;
    min-height: 0;
    overflow: auto;
    padding: 16px;
}

.viewer-pages { display: flex; justify-content: center; }

.viewer-page {
    position: relative;
    background: #fff;                    /* Papier: bewusst weiss, keine Token-Farbe */
    box-shadow: 0 1px 4px rgba(var(--text-primary-rgb), 0.18);
    --user-unit: 1;
    --total-scale-factor: calc(var(--scale-factor) * var(--user-unit));
}

.viewer-page canvas { display: block; }

.viewer-text {
    max-width: 860px;
    margin: 0 auto;
    padding: 20px 24px;
    background: var(--card-bg);
    border: 1px solid var(--border-color);
    border-radius: var(--radius-lg);
}

.viewer-note { color: var(--text-secondary); }
.viewer-note.is-error { color: var(--error-color); }

/* Fundstelle: in der pdf.js-Textebene (span) und im Markdown (mark). */
.textLayer .viewer-hit,
mark.viewer-hit {
    background: rgba(var(--accent-rgb), 0.35);
    border-radius: 2px;
    color: transparent;
}

mark.viewer-hit { color: inherit; }

/* pdf.js Textebene (aus web/text_layer_builder.css, v6.2.108, gekuerzt). */
.textLayer {
    color-scheme: only light;
    position: absolute;
    text-align: initial;
    inset: 0;
    overflow: clip;
    opacity: 1;
    line-height: 1;
    letter-spacing: normal;
    word-spacing: normal;
    text-size-adjust: none;
    forced-color-adjust: none;
    transform-origin: 0 0;
    caret-color: CanvasText;
    z-index: 0;
    --min-font-size: 1;
    --text-scale-factor: calc(var(--total-scale-factor) * var(--min-font-size));
    --min-font-size-inv: calc(1 / var(--min-font-size));
}

.textLayer :is(span, br) {
    color: transparent;
    position: absolute;
    white-space: pre;
    cursor: text;
    transform-origin: 0% 0%;
    user-select: text;
}

.textLayer > :not(.markedContent),
.textLayer .markedContent span:not(.markedContent) {
    z-index: 1;
    --font-height: 0;
    font-size: calc(var(--text-scale-factor) * var(--font-height));
    --scale-x: 1;
    --rotate: 0deg;
    transform: rotate(var(--rotate)) scaleX(var(--scale-x)) scale(var(--min-font-size-inv));
}

.textLayer .markedContent { display: contents; }

.textLayer ::selection { background: rgba(var(--accent-rgb), 0.25); color: transparent; }

.textLayer .endOfContent {
    display: block;
    position: absolute;
    inset: 100% 0 0;
    z-index: 0;
    cursor: default;
    user-select: none;
}
```

- [ ] **Step 4: `evidence.js` — the one helper for every "show me the passage"**

Create `src/web_interface/static/js/evidence.js`:

```js
// Gemeinsamer Einstieg "Fundstelle zeigen" (F7, G3): Suchtreffer, Cortex-Belege,
// Fakten-Belege und Konflikt-Treffer rufen alle openEvidence(...). Eigenes
// Skript, weil die Cortex-Seite app.js nicht laedt.
//
// openEvidence(docId, path, page, snippet, options)
//   options.container  Element, in das ein <iframe> auf /viewer gesetzt wird;
//                      fehlt es, oeffnet sich ein neuer Tab.
//   options.hits       [{page, snippet}] weitere Fundstellen (Vor/Zurueck im Viewer)
//   options.title      iframe-Titel
//   options.frameClass CSS-Klasse des iframes (Standard: doc-frame)
(function () {
    'use strict';

    var MAX_SNIPPET = 300;
    var MAX_HITS = 8;

    function cleanSnippet(value) {
        return String(value == null ? '' : value).replace(/\s+/g, ' ').trim().slice(0, MAX_SNIPPET);
    }

    function viewerUrl(docId, path, page, snippet, hits) {
        var q = new URLSearchParams();
        q.set('doc', String(docId == null ? '' : docId));
        q.set('path', String(path == null ? '' : path));
        var p = Number(page);
        if (Number.isInteger(p) && p >= 1) q.set('page', String(p));
        var s = cleanSnippet(snippet);
        if (s) q.set('snippet', s);
        var list = [];
        (Array.isArray(hits) ? hits : []).forEach(function (h) {
            var hp = Number(h && h.page);
            if (!Number.isInteger(hp) || hp < 1 || list.length >= MAX_HITS) return;
            list.push({ page: hp, snippet: cleanSnippet(h.snippet) });
        });
        if (list.length > 1) q.set('hits', JSON.stringify(list));
        return '/viewer?' + q.toString();
    }

    function openEvidence(docId, path, page, snippet, options) {
        var opts = options || {};
        var url = viewerUrl(docId, path, page, snippet, opts.hits);
        if (opts.container) {
            var frame = document.createElement('iframe');
            frame.className = opts.frameClass == null ? 'doc-frame' : opts.frameClass;
            frame.title = opts.title || 'Dokument';
            frame.src = url;
            opts.container.innerHTML = '';
            opts.container.appendChild(frame);
            return frame;
        }
        window.open(url, '_blank', 'noopener');
        return null;
    }

    window.KnovasEvidence = { viewerUrl: viewerUrl, openEvidence: openEvidence };
    window.openEvidence = openEvidence;
})();
```

- [ ] **Step 5: `viewer.js`**

Create `src/web_interface/static/js/viewer.js`:

```js
// Knovas Viewer -- Fundstelle anzeigen (F7).
//
// pdf.js (Apache-2.0, static/js/vendor/pdfjs) rendert die Seite auf ein
// Canvas und legt seine Textebene darueber; die Fundstelle wird im Text der
// Seite gesucht und die betroffenen Textlaeufe markiert. Nicht-PDF laeuft
// ueber /preview-content (Markdown), dort wird im gerenderten Text markiert.
// Es wird nichts erfunden: ohne Textebene (Scan ohne OCR) oder ohne Treffer
// im Wortlaut sagt der Viewer das und laesst die Seite unmarkiert.
//
// Reine Helfer haengen an window.KnovasViewer (tests/test_js_smoke.py); der
// Controller startet nur mit window.__VIEWER__ und einem DOM.
(function () {
    'use strict';

    var PDFJS_URL = '/static/js/vendor/pdfjs/pdf.mjs';
    var PDFJS_WORKER_URL = '/static/js/vendor/pdfjs/pdf.worker.mjs';
    var MIN_NEEDLE = 3;
    var ZOOM_STEPS = [0.5, 0.75, 1, 1.25, 1.5, 2, 3];

    function normalizeText(value) {
        return String(value == null ? '' : value)
            .toLowerCase()
            .replace(/[\u00ad\u200b]/g, '')
            .replace(/\s+/g, ' ')
            .trim();
    }

    /**
     * Text aller pdf.js-Textitems hintereinander; owner[i] ist der Index des
     * Items, aus dem Zeichen i stammt. Zwischen Items wird KEIN Leerzeichen
     * erfunden (Woerter sind oft ueber Items geteilt); hasEOL bringt eins.
     */
    function buildIndex(items) {
        var text = '';
        var owner = [];
        function push(ch, idx) {
            if (ch === ' ' && (text === '' || text.charAt(text.length - 1) === ' ')) return;
            text += ch;
            owner.push(idx);
        }
        for (var idx = 0; idx < items.length; idx++) {
            var it = items[idx] || {};
            var s = String(it.str == null ? '' : it.str)
                .toLowerCase().replace(/[\u00ad\u200b]/g, '').replace(/\s+/g, ' ');
            for (var k = 0; k < s.length; k++) push(s.charAt(k), idx);
            if (it.hasEOL) push(' ', idx);
        }
        return { text: text, owner: owner };
    }

    /** Fundstelle: ganzer Ausschnitt, sonst die ersten 8/5/3 Woerter (partial). */
    function findNeedle(text, snippet) {
        var needle = normalizeText(snippet);
        if (needle.length < MIN_NEEDLE) return null;
        var idx = text.indexOf(needle);
        if (idx >= 0) return { start: idx, end: idx + needle.length, partial: false };
        var words = needle.split(' ');
        var tries = [8, 5, 3];
        for (var t = 0; t < tries.length; t++) {
            if (words.length <= tries[t]) continue;
            var sub = words.slice(0, tries[t]).join(' ');
            idx = text.indexOf(sub);
            if (idx >= 0) return { start: idx, end: idx + sub.length, partial: true };
        }
        return null;
    }

    /** Item-Indizes, die den Bereich [start, end) beruehren, in Reihenfolge. */
    function itemsInRange(owner, start, end) {
        var seen = {};
        var out = [];
        for (var i = start; i < end && i < owner.length; i++) {
            var idx = owner[i];
            if (!seen[idx]) { seen[idx] = true; out.push(idx); }
        }
        return out;
    }

    /** Fundstelle im gerenderten Markdown markieren (Nicht-PDF). true bei Erfolg. */
    function highlightInElement(root, snippet) {
        var needle = normalizeText(snippet);
        if (!root || needle.length < MIN_NEEDLE) return false;
        var tries = [needle];
        var words = needle.split(' ');
        if (words.length > 8) tries.push(words.slice(0, 8).join(' '));
        if (words.length > 4) tries.push(words.slice(0, 4).join(' '));
        for (var t = 0; t < tries.length; t++) {
            var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
            var node;
            while ((node = walker.nextNode())) {
                var raw = String(node.nodeValue);
                var at = raw.toLowerCase().indexOf(tries[t]);
                if (at < 0) continue;
                var range = document.createRange();
                range.setStart(node, at);
                range.setEnd(node, Math.min(raw.length, at + tries[t].length));
                var mark = document.createElement('mark');
                mark.className = 'viewer-hit';
                range.surroundContents(mark);
                mark.scrollIntoView({ block: 'center' });
                return true;
            }
        }
        return false;
    }

    var helpers = {
        normalizeText: normalizeText, buildIndex: buildIndex, findNeedle: findNeedle,
        itemsInRange: itemsInRange, highlightInElement: highlightInElement, ZOOM_STEPS: ZOOM_STEPS,
    };
    if (typeof window !== 'undefined') window.KnovasViewer = helpers;
    if (typeof document === 'undefined' || typeof window === 'undefined' || !window.__VIEWER__) return;

    // ---- Controller -------------------------------------------------------

    var cfg = window.__VIEWER__;
    var el = function (id) { return document.getElementById(id); };
    var ui = {
        status: el('viewerStatus'), stage: el('viewerStage'), pages: el('viewerPages'), text: el('viewerText'),
        pageTools: el('viewerPageTools'), prevPage: el('viewerPrevPage'), nextPage: el('viewerNextPage'),
        pageInput: el('viewerPageInput'), pageCount: el('viewerPageCount'),
        hits: el('viewerHits'), prevHit: el('viewerPrevHit'), nextHit: el('viewerNextHit'), hitLabel: el('viewerHitLabel'),
        zoomTools: el('viewerZoomTools'), zoomIn: el('viewerZoomIn'), zoomOut: el('viewerZoomOut'),
        zoomFit: el('viewerZoomFit'), zoomLabel: el('viewerZoomLabel'),
    };
    var state = {
        pdfjs: null, pdf: null, pageNum: Number(cfg.page) || 1, scale: 1, fitWidth: true,
        hits: [], hitIndex: 0, snippet: String(cfg.snippet || ''), renderSeq: 0,
    };

    function setStatus(text, isError) {
        ui.status.textContent = text || '';
        ui.status.classList.toggle('is-error', !!isError);
    }

    function escapeHtml(value) {
        return window.KnovasMarkdown ? window.KnovasMarkdown.escapeHtml(value) : String(value);
    }

    // ---- PDF --------------------------------------------------------------

    async function loadPdf() {
        var pdfjsLib;
        try {
            pdfjsLib = await import(PDFJS_URL);
        } catch (err) {
            setStatus('Viewer konnte nicht geladen werden (pdf.js fehlt oder ist blockiert).', true);
            return;
        }
        pdfjsLib.GlobalWorkerOptions.workerSrc = PDFJS_WORKER_URL;
        try {
            state.pdf = await pdfjsLib.getDocument({ url: cfg.previewUrl }).promise;
        } catch (err) {
            setStatus(cfg.pdfInline
                ? 'PDF konnte nicht geladen werden (' + (err && err.message ? err.message : 'unbekannt') + ').'
                : 'Die PDF-Vorschau ist deaktiviert (OPEN_PDF_INLINE_IN_BROWSER=false).', true);
            return;
        }
        state.pdfjs = pdfjsLib;
        ui.pageCount.textContent = String(state.pdf.numPages);
        ui.pageInput.max = String(state.pdf.numPages);
        state.pageNum = Math.min(Math.max(1, state.pageNum), state.pdf.numPages);
        setupHits();
        await renderPage(state.pageNum, state.snippet);
    }

    async function renderPage(num, snippet) {
        if (!state.pdf) return;
        var seq = ++state.renderSeq;
        var page = await state.pdf.getPage(num);
        if (seq !== state.renderSeq) return;
        var base = page.getViewport({ scale: 1 });
        var stageWidth = Math.max(320, ui.stage.clientWidth - 32);
        var scale = state.fitWidth ? stageWidth / base.width : state.scale;
        state.scale = scale;
        var viewport = page.getViewport({ scale: scale });
        var ratio = window.devicePixelRatio || 1;

        var pageEl = document.createElement('div');
        pageEl.className = 'viewer-page';
        pageEl.style.width = Math.floor(viewport.width) + 'px';
        pageEl.style.height = Math.floor(viewport.height) + 'px';
        pageEl.style.setProperty('--scale-factor', String(viewport.scale));
        var canvas = document.createElement('canvas');
        canvas.width = Math.floor(viewport.width * ratio);
        canvas.height = Math.floor(viewport.height * ratio);
        canvas.style.width = Math.floor(viewport.width) + 'px';
        canvas.style.height = Math.floor(viewport.height) + 'px';
        var textLayerEl = document.createElement('div');
        textLayerEl.className = 'textLayer';
        pageEl.appendChild(canvas);
        pageEl.appendChild(textLayerEl);

        var renderTask = page.render({
            canvas: canvas, viewport: viewport,
            transform: ratio !== 1 ? [ratio, 0, 0, ratio, 0, 0] : null,
        });
        var textContent = await page.getTextContent();
        var layer = new state.pdfjs.TextLayer({
            textContentSource: textContent, container: textLayerEl, viewport: viewport,
        });
        await Promise.all([renderTask.promise, layer.render()]);
        if (seq !== state.renderSeq) return;

        ui.pages.innerHTML = '';
        ui.pages.appendChild(pageEl);
        state.pageNum = num;
        ui.pageInput.value = String(num);
        ui.prevPage.disabled = num <= 1;
        ui.nextPage.disabled = num >= state.pdf.numPages;
        ui.zoomLabel.textContent = state.fitWidth ? 'Breite' : Math.round(scale * 100) + ' %';

        if (!snippet) { setStatus(''); return; }
        var index = buildIndex(textContent.items || []);
        var found = findNeedle(index.text, snippet);
        if (!found) {
            setStatus('Seite ' + num + ' geöffnet – Fundstelle im Text nicht gefunden '
                + '(Scan ohne Textebene oder anderer Wortlaut).');
            return;
        }
        var divs = layer.textDivs || [];
        var first = null;
        itemsInRange(index.owner, found.start, found.end).forEach(function (i) {
            var d = divs[i];
            if (!d) return;
            d.classList.add('viewer-hit');
            if (!first) first = d;
        });
        if (first) first.scrollIntoView({ block: 'center' });
        setStatus(found.partial
            ? 'Fundstelle näherungsweise markiert (Seite ' + num + ').'
            : 'Fundstelle markiert (Seite ' + num + ').');
    }

    function setupHits() {
        var hits = Array.isArray(cfg.hits) ? cfg.hits : [];
        state.hits = hits;
        if (hits.length < 2) { ui.hits.hidden = true; return; }
        ui.hits.hidden = false;
        var start = hits.findIndex(function (h) { return h.page === state.pageNum && h.snippet === state.snippet; });
        state.hitIndex = start >= 0 ? start : 0;
        updateHitLabel();
    }

    function updateHitLabel() {
        var h = state.hits[state.hitIndex];
        ui.hitLabel.textContent = 'Fundstelle ' + (state.hitIndex + 1) + ' von ' + state.hits.length
            + (h ? ' · Seite ' + h.page : '');
        ui.prevHit.disabled = state.hitIndex <= 0;
        ui.nextHit.disabled = state.hitIndex >= state.hits.length - 1;
    }

    async function gotoHit(delta) {
        var next = state.hitIndex + delta;
        if (next < 0 || next >= state.hits.length) return;
        state.hitIndex = next;
        updateHitLabel();
        var h = state.hits[next];
        state.snippet = h.snippet;
        await renderPage(h.page, h.snippet);
    }

    async function gotoPage(num) {
        if (!state.pdf) return;
        var target = Math.min(Math.max(1, Number(num) || 1), state.pdf.numPages);
        // Nur auf der Trefferseite markieren -- auf anderen Seiten waere die
        // Markierung eine Behauptung ohne Beleg.
        var hit = state.hits[state.hitIndex];
        var snippet = (hit && hit.page === target) ? hit.snippet
            : (target === Number(cfg.page) ? state.snippet : '');
        await renderPage(target, snippet);
    }

    function zoom(delta) {
        var current = state.fitWidth ? state.scale : state.scale;
        var idx = ZOOM_STEPS.findIndex(function (s) { return s >= current - 0.001; });
        if (idx < 0) idx = ZOOM_STEPS.length - 1;
        var next = Math.min(ZOOM_STEPS.length - 1, Math.max(0, idx + delta));
        if (delta < 0 && ZOOM_STEPS[idx] > current + 0.001) next = Math.max(0, idx - 1 + 1) - 1 >= 0 ? idx - 1 : 0;
        state.fitWidth = false;
        state.scale = ZOOM_STEPS[Math.max(0, Math.min(ZOOM_STEPS.length - 1, next))];
        renderPage(state.pageNum, currentSnippet());
    }

    function currentSnippet() {
        var hit = state.hits[state.hitIndex];
        return hit && hit.page === state.pageNum ? hit.snippet
            : (state.pageNum === Number(cfg.page) ? state.snippet : '');
    }

    // ---- Nicht-PDF: Markdown aus /preview-content -------------------------

    async function loadText() {
        ui.pageTools.hidden = true;
        ui.zoomTools.hidden = true;
        ui.hits.hidden = true;
        ui.pages.hidden = true;
        ui.text.hidden = false;
        if (!cfg.kind) {
            ui.text.innerHTML = '<p class="viewer-note">Für dieses Format gibt es keine Vorschau. '
                + 'Nutzen Sie „Öffnen“ in der Trefferliste.</p>';
            return;
        }
        try {
            var resp = await fetch(cfg.contentUrl, { credentials: 'same-origin' });
            if (resp.status === 401) throw new Error('Sitzung abgelaufen – bitte neu anmelden');
            var data = await resp.json().catch(function () { return {}; });
            if (!resp.ok || !data.success) throw new Error(data.error || ('HTTP ' + resp.status));
            var html = window.KnovasMarkdown.render(data.markdown);
            var tables = Array.isArray(data.tables) ? data.tables : [];
            if (tables.length && !window.KnovasMarkdown.hasTable(data.markdown)) {
                html += tables.map(window.KnovasMarkdown.renderTable).join('');
            }
            ui.text.innerHTML = html;
            if (cfg.snippet) {
                var ok = highlightInElement(ui.text, cfg.snippet);
                setStatus(ok ? 'Fundstelle markiert.'
                    : 'Fundstelle im Text nicht gefunden – Textvorschau ohne Seiten (' + String(cfg.kind).toUpperCase() + ').');
            }
        } catch (err) {
            ui.text.innerHTML = '<p class="viewer-note is-error">Vorschau nicht verfügbar ('
                + escapeHtml(err.message) + ').</p>';
        }
    }

    // ---- Start ------------------------------------------------------------

    function bind() {
        ui.prevPage.addEventListener('click', function () { gotoPage(state.pageNum - 1); });
        ui.nextPage.addEventListener('click', function () { gotoPage(state.pageNum + 1); });
        ui.pageInput.addEventListener('change', function () { gotoPage(ui.pageInput.value); });
        ui.prevHit.addEventListener('click', function () { gotoHit(-1); });
        ui.nextHit.addEventListener('click', function () { gotoHit(1); });
        ui.zoomIn.addEventListener('click', function () { zoom(1); });
        ui.zoomOut.addEventListener('click', function () { zoom(-1); });
        ui.zoomFit.addEventListener('click', function () {
            state.fitWidth = true;
            renderPage(state.pageNum, currentSnippet());
        });
        ui.stage.addEventListener('keydown', function (e) {
            if (e.key === 'ArrowRight' || e.key === 'PageDown') { e.preventDefault(); gotoPage(state.pageNum + 1); }
            if (e.key === 'ArrowLeft' || e.key === 'PageUp') { e.preventDefault(); gotoPage(state.pageNum - 1); }
        });
        var resizeTimer = null;
        window.addEventListener('resize', function () {
            if (!state.fitWidth || !state.pdf) return;
            window.clearTimeout(resizeTimer);
            resizeTimer = window.setTimeout(function () { renderPage(state.pageNum, currentSnippet()); }, 150);
        });
    }

    function init() {
        bind();
        if (!cfg.path) return;
        if (!cfg.enabled) {
            setStatus('Der Viewer ist deaktiviert (OPEN_VIEWER_ENABLED=false).', true);
            ui.text.hidden = false;
            ui.text.innerHTML = '<p class="viewer-note"><a href="' + escapeHtml(cfg.previewUrl)
                + '#page=' + Number(cfg.page) + '">Im Browser-Viewer öffnen</a></p>';
            return;
        }
        if (cfg.kind === 'pdf') loadPdf(); else loadText();
    }

    document.addEventListener('DOMContentLoaded', init);
})();
```

Replace the muddled `zoom` above with this exact body before committing (kept separate here so the intent is unmistakable — this is the version that ships):

```js
    function zoom(delta) {
        var current = state.scale;
        // Naechste Stufe oberhalb (delta > 0) bzw. unterhalb (delta < 0) der
        // aktuellen Skalierung; "Breite" ist keine Stufe und wird verlassen.
        var next = current;
        if (delta > 0) {
            for (var i = 0; i < ZOOM_STEPS.length; i++) {
                if (ZOOM_STEPS[i] > current + 0.001) { next = ZOOM_STEPS[i]; break; }
            }
        } else {
            for (var j = ZOOM_STEPS.length - 1; j >= 0; j--) {
                if (ZOOM_STEPS[j] < current - 0.001) { next = ZOOM_STEPS[j]; break; }
            }
        }
        state.fitWidth = false;
        state.scale = next;
        renderPage(state.pageNum, currentSnippet());
    }
```

- [ ] **Step 6: Wire `app.py` (mimetypes, cache, config, index flag, blueprint, build id)**

In `src/web_interface/app.py`:

1. After `import time` (line 13) add:

```python
import mimetypes
## Module scripts (.mjs) are refused by browsers unless served as JavaScript;
## python:3.11-slim has no /etc/mime.types and Windows maps .mjs to text/plain.
## woff2 has the same problem (docs/search-ui-backlog.md §4).
mimetypes.add_type('text/javascript', '.mjs')
mimetypes.add_type('font/woff2', '.woff2')
```

2. Line 544: `DOCBRIDGE_BUILD_ID = 'pflichtenheft-f-v1'` (one bump for the whole F-section UI; if the constant already reads that value from an earlier task on this branch, leave it).

3. Replace `_prevent_stale_ui_assets` (lines 668–681) with:

```python
    @app.after_request
    def _prevent_stale_ui_assets(response):
        """Avoid browsers serving cached HTML/JS after docker rebuild."""
        path = request.path or ''
        if path.startswith('/static/js/vendor/'):
            # Vendored libraries change only with their VERSION file and are
            # cache-busted by ?v=; pdf.js is several MB and must not be
            # re-fetched on every viewer open.
            response.headers['Cache-Control'] = 'public, max-age=86400'
            return response
        if (
            path in ('/', '/login', '/ontology', '/viewer')
            or path.endswith('.js')
            or path.endswith('.css')
            or path.startswith('/static/')
        ):
            response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
            response.headers['Pragma'] = 'no-cache'
        return response
```

4. After `pdf_inline_in_browser = config.get_bool('open.pdf_inline_in_browser', True)` (line 745) add:

```python
    # F7: eigener Viewer (pdf.js) statt des Browser-Viewers im iframe.
    viewer_enabled = config.get_bool('open.viewer_enabled', True)
```

5. In `index()` (lines 1016–1035) add `viewer_enabled=viewer_enabled,` after `pdf_inline_in_browser=pdf_inline_in_browser,`.

6. Immediately before `@app.route('/api/ontology/summary', methods=['GET'])` (line 1717, next to the document-routes registration from Task KC-A-6) add:

```python
    from web_interface.viewer_routes import register_viewer_routes
    register_viewer_routes(
        app,
        viewer_enabled=viewer_enabled,
        pdf_inline=pdf_inline_in_browser,
        page_context=lambda: {'app_title': web_app_title, 'brand': web_brand},
        asset_version=_static_asset_version)
```

In `src/web_interface/templates/index.html`: add `viewerEnabled: {{ viewer_enabled|tojson }},` after the `pdfInlineInBrowser` line (109), and insert before the `markdown.js` script tag (line 115):

```html
    <script src="{{ url_for('static', filename='js/evidence.js') }}?v={{ asset_version }}"></script>
```

In `src/web_interface/templates/ontology.html`, insert before the `ontology_connect.js` script tag (line 78):

```html
    <script src="{{ url_for('static', filename='js/evidence.js') }}?v={{ asset_version }}"></script>
```

Config plumbing:
- `config/config.yaml`, in the `open:` block after `pdf_inline_in_browser:` add `  viewer_enabled: "${OPEN_VIEWER_ENABLED:-true}"`.
- `KnovasPlatform/.env.example`, after `OPEN_PDF_INLINE_IN_BROWSER=true` add:

```bash
## F7: eigener PDF-Viewer (pdf.js, springt zur Fundstelle und markiert sie).
## false = Browser-Viewer im iframe wie bisher (nur #page=N).
OPEN_VIEWER_ENABLED=true
```

- `KnovasPlatform/docker-compose.yml`, after `OPEN_PDF_INLINE_IN_BROWSER: ${OPEN_PDF_INLINE_IN_BROWSER:-true}` add `      OPEN_VIEWER_ENABLED: ${OPEN_VIEWER_ENABLED:-true}`.

CSP at the edge (design §6.4):
- `nginx/docbridge-web-local.conf` line 14 → `add_header Content-Security-Policy "frame-ancestors 'self'; worker-src 'self'" always;`
- `KnovasPlatform/deploy/host-nginx/knovas-platform.conf.example` line 36 → the same value.

Run: `py -3.13 -m pytest tests/test_viewer_routes.py tests/test_platform_health.py tests/test_ontology_api.py -v`
Expected: PASS except `test_pages_load_evidence_js_and_expose_viewer_flag` is now green too; the pre-existing `test_sidebar_shows_corpus_only_when_fixture_has_it` failure is unrelated (see the part header).

- [ ] **Step 7: Search hits and Cortex evidence use `openEvidence`**

In `src/web_interface/static/js/app.js`, inside `_showDocument` (Task KC-A-6), replace the PDF branch body between `const src = ...` and the legacy `try {` with a viewer-first path — i.e. after the `if (!cfg.pdfInlineInBrowser) { ... return; }` block insert:

```js
            if (cfg.viewerEnabled && typeof window.openEvidence === 'function') {
                // F7: eigener Viewer, springt zur Trefferseite und markiert den
                // Ausschnitt. Bytes kommen weiter aus /preview (Pfad-Einhegung).
                this.previewMeta.textContent = 'PDF';
                this.previewBody.classList.add('is-pdf');
                this.previewBody.innerHTML = '';
                window.openEvidence(docId, path, doc.page_number, doc.snippet, {
                    container: this.previewBody,
                    hits: this._hitsFor(doc),
                    title: `Viewer: ${title}`,
                    frameClass: '',
                });
                if (this._previewAbort === controller) this._previewAbort = null;
                return;
            }
```

and add after `_backToResultsHtml`:

```js
    /** Fundstellen eines Treffers fuer den Viewer: echte Passagen, keine KI-Zusammenfassung. */
    _hitsFor(doc) {
        const chunks = Array.isArray(doc.top_chunks) ? doc.top_chunks : [];
        const hits = chunks
            .filter((c) => c && c.chunk_kind !== 'auto_summary' && Number(c.page_number) >= 1)
            .map((c) => ({ page: Number(c.page_number), snippet: String(c.snippet || '') }));
        if (!hits.length && Number(doc.page_number) >= 1) {
            hits.push({ page: Number(doc.page_number), snippet: String(doc.snippet || '') });
        }
        return hits;
    }
```

In `src/web_interface/static/js/ontology.js`:

- Lines 1014–1016 (entity evidence buttons): add `data-quote="${esc(ev.quote || '')}"` after `data-title="${esc(ev.document.title)}"`.
- Lines 1081–1085 (handler): pass the quote —

```js
                    this.onEvidenceSelect({ path: btn.dataset.path,
                                            page: Number(btn.dataset.page),
                                            title: btn.dataset.title,
                                            quote: btn.dataset.quote || '' });
```

- Lines 1566–1568 (proposal quote buttons): add `data-quote="${esc(p.quote || '')}"` after `data-title="${esc(p.document.title)}"`.
- Lines 1581–1585 (handler): same four-field object as above.
- Replace `onEvidenceSelect` (lines 1631–1649) with:

```js
    onEvidenceSelect(evidence) {
        const body = document.getElementById('docPaneBody');
        document.getElementById('docPaneTitle').textContent =
            `${evidence.title}, Seite ${evidence.page}`;
        // Gemeinsamer Einstieg mit der Suche (evidence.js): der Viewer springt
        // zur Seite und markiert das Zitat -- oder sagt, dass er es nicht fand.
        // Der Titel steht als doc-Segment, wie bisher; die Route ignoriert ihn.
        const frame = window.openEvidence(evidence.title, evidence.path, evidence.page,
                                          evidence.quote || '', {
            container: body,
            title: `Vorschau: ${evidence.title}`,
        });
        if (frame) {
            frame.addEventListener('error', () => {
                body.innerHTML =
                    '<p class="ontology-empty">Dokument konnte nicht geladen werden.</p>';
            });
        }
        this.openDocDrawer();
    }
```

- [ ] **Step 8: JS smoke checks for the pure helpers**

Append to `tests/test_js_smoke.py`:

```python
def test_evidence_viewer_url_encodes_and_caps():
    script = _load("evidence.js") + (
        "const E = sandbox.window.KnovasEvidence;"
        "const hits = Array.from({length: 12}, (_, i) => ({page: i + 1, snippet: 'x'.repeat(400)}));"
        "console.log(E.viewerUrl('corpus/a b.pdf', 'a b.pdf', 4, '  Kündigungs  frist ', hits));"
    )
    url = _run(script)
    assert url.startswith("/viewer?doc=corpus%2Fa+b.pdf&path=a+b.pdf&page=4&snippet=K%C3%BCndigungs+frist&hits=")
    from urllib.parse import parse_qs, urlparse
    hits = json.loads(parse_qs(urlparse(url).query)["hits"][0])
    assert len(hits) == 8 and all(len(h["snippet"]) == 300 for h in hits)


def test_evidence_viewer_url_omits_bad_page_and_single_hit():
    script = _load("evidence.js") + (
        "console.log(sandbox.window.KnovasEvidence.viewerUrl('d', 'p.pdf', 'x', '', [{page: 2, snippet: 'a'}]));"
    )
    assert _run(script) == "/viewer?doc=d&path=p.pdf"


def test_viewer_find_needle_over_pdfjs_items():
    script = _load("viewer.js") + (
        "const V = sandbox.window.KnovasViewer;"
        "const items = [{str: 'Die Kündi', hasEOL: false}, {str: 'gungsfrist beträgt', hasEOL: true},"
        "               {str: 'drei Monate.', hasEOL: true}, {str: 'Zweiter Absatz', hasEOL: true}];"
        "const idx = V.buildIndex(items);"
        "const found = V.findNeedle(idx.text, 'Die Kündigungsfrist beträgt drei Monate.');"
        "const partial = V.findNeedle(idx.text, 'Die Kündigungsfrist beträgt drei Monate und noch etwas ganz anderes das nicht vorkommt');"
        "console.log(JSON.stringify({text: idx.text, found, items: V.itemsInRange(idx.owner, found.start, found.end),"
        " partial: partial && partial.partial, missing: V.findNeedle(idx.text, 'gibt es nicht'), short: V.findNeedle(idx.text, 'ab')}));"
    )
    out = json.loads(_run(script))
    assert out["text"] == "die kündigungsfrist beträgt drei monate. zweiter absatz"
    assert out["found"] == {"start": 0, "end": 40, "partial": False}
    assert out["items"] == [0, 1, 2]
    assert out["partial"] is True
    assert out["missing"] is None and out["short"] is None
```

Run: `py -3.13 -m pytest tests/test_js_smoke.py tests/test_viewer_routes.py -v` — Expected: PASS (JS tests SKIPPED without node).

- [ ] **Step 9: Browser hand-check**

Start the stack (`./start_stack.sh` or the local dev server), log in, search, click a PDF hit: the dialog shows the viewer at the hit page with the passage marked and "Fundstelle 1 von N" when several chunks exist; `+`/`−`/`Breite` work; open Cortex, click a Beleg: the drawer shows the same viewer at the cited page. Set `OPEN_VIEWER_ENABLED=false`, recreate: the dialog falls back to the browser viewer. Confirm in DevTools that `pdf.worker.mjs` loads with `text/javascript` and no CSP violation is logged.

- [ ] **Step 10: Commit**

```bash
git add src/web_interface/static/js/vendor/pdfjs src/web_interface/viewer_routes.py \
        src/web_interface/templates/viewer.html src/web_interface/static/js/viewer.js \
        src/web_interface/static/js/evidence.js src/web_interface/static/css/viewer.css \
        src/web_interface/app.py src/web_interface/templates/index.html src/web_interface/templates/ontology.html \
        src/web_interface/static/js/app.js src/web_interface/static/js/ontology.js \
        nginx/docbridge-web-local.conf config/config.yaml \
        ../../deploy/host-nginx/knovas-platform.conf.example ../../.env.example ../../docker-compose.yml \
        tests/test_viewer_routes.py tests/test_js_smoke.py
git commit -m "feat(search): F7 viewer - vendored pdf.js 6.2.108, /viewer route, jump-to-hit highlight, shared openEvidence"
```

---

---

### Task KC-A-8: Documentation — feature docs, backlog closure, changelog, release notes
**Requirements:** F3, F6, F7, F8, F9, D5, H4 (customer-facing description with LIVE/GATED labels — G9 discipline)
**Files:**
- Create: `KnovasPlatform/docs/features/search-filters-and-versions.md`, `KnovasPlatform/docs/features/viewer.md`, `KnovasPlatform/CHANGELOG.md`.
- Modify: `docs/search-ui-backlog.md` (line 3; §3 heading line 47 + a new first paragraph; §5 lines 110–118; §6 bullets lines 131–135; new §7/§8 appended), `KnovasPlatform/docs/README.md` (append a section after line 22), `RELEASE_NOTES.md` (KnovasPlatform section lines 5–10), `KnovasPlatform/docs/integration/troubleshooting.md` (append rows before the closing lines), `KnovasPlatform/docs/integration/opening-documents.md` (line 27), `KnovasPlatform/README.md` (line 3).
**Interfaces:**
- Consumes: everything Tasks KC-A-1…KC-A-7 produce; from the parallel tasks the registry names `src/search_filters.py`, `web.search.filters` (Task KC-A-2), `static/js/filters.js`, the filter rail and "Weitere Treffer" (Task KC-A-3), the metaline/"KI-Zusammenfassung"/"Beispieldaten"/empty state (Task KC-A-4), "Wer kennt sich aus?" (Task KC-A-5); the label vocabulary of `docs/product-statements.md` (defined in the docs part: LIVE · BUILT · GATED · DEMO · PARTIAL · PLANNED · MISSING · HYPOTHESIS).
- Produces: the two feature docs, the Platform changelog, the closed backlog items, the docs index rows.

- [ ] **Step 1: `search-filters-and-versions.md`**

Create `KnovasPlatform/docs/features/search-filters-and-versions.md`:

```markdown
## Search: filters, paging, honesty, versions, similar documents

Status labels follow [`docs/product-statements.md`](../../../docs/product-statements.md)
(LIVE · BUILT · GATED · DEMO · PARTIAL · PLANNED · MISSING). "LIVE" here
means: shipped in the Platform **and** the tenant runs the Knovas Secure API
contract of August 2026 (`POST /secured/query` with `filters/limit/offset/sort/facets`,
`GET /secured/document/<uuid>/versions`, `POST /secured/documents/<uuid>/similar`,
`PATCH /secured/documents/<uuid>/metadata`). Against an older tenant every
screen degrades to the previous behaviour and says so.

| Feature | Status | Notes |
|---------|--------|-------|
| Filter rail: Dokumenttyp, Autor, Sprache, Status, Quelle, Zeitraum | LIVE | Sent as `filters` to the API; filters only narrow, never widen ACL or scope. |
| Akte / Praxisgebiet filter (`scope`) | GATED | Needs the tenant's relevance gate enabled (§5.1 of the design). Until then the rail shows the metadata filters only. |
| Sort (Relevanz / Datum ↓ / Datum ↑) | LIVE | Date sort re-orders the gated set. |
| "Weitere Treffer" (real paging) | LIVE | `offset` over one ranked, gated set — not a corpus offset. Ceiling = the reranked pool (`QUERY_COLBERT_STAGE2_TOP_DOCUMENTS`). |
| Facet chips / "Wer kennt sich aus?" (author facet) | LIVE | Counted over the ranked, ACL-filtered pool: "Verteilung in den Treffern", not a corpus statistic. |
| Hit metaline: type · date · author · language · version badge | LIVE | Fields exist only for documents ingested with `metadata` (RemoteController ≥ this release, `RC_SEND_DOCUMENT_METADATA=1`) or backfilled (`manage_weaviate.py backfill-metadata`). Older documents show format and date only. |
| Honest empty state, `no_strong_matches`, "Beispieldaten" banner | LIVE | The banner appears whenever `SEARCH_USE_TEST_RESULTS` is on. |
| API validation errors shown, never dropped | LIVE | A `400 validation_error` from the tenant renders as a message with the field. |
| Version list ("Versionen" in the dialog) | LIVE (tier 1) | Lists predecessors with `changed_by`/`changed_by_kind`. Tier 2 — searching superseded text — is **not** offered. |
| "Ähnliche Dokumente" | LIVE | `/api/documents/<uuid>/similar`; shares the query rate budget; `no_strong_matches` shown honestly. |
| "Ähnliche Akten" (matter page) | LIVE with graph mode | Grouped from visible `kg_node_ids` (`similar_matters`). |
| Metadata edit (type, status, date, language, author) | LIVE | `PATCH` without re-upload; a card re-renders after saving. |
| Tables in the preview (DOCX/MSG/HTML tables, structured tables) | LIVE | GFM pipe tables render as tables; XLSX preview in the Platform is PLANNED (the Platform image has no XLSX extractor; RemoteController extracts XLSX for search). |

## How a filter reaches the API

`static/js/filters.js` builds `{query, limit, offset, sort, filters, facets, scope}` →
`POST /api/search` → `src/search_filters.py` splits it into API filters (the eight
documented keys `author, document_type, language, document_status, source_kind,
date_from, date_to, pointer_prefix`) and local-only refinements (`exact_match`) →
`KnovasAPIClient.search_documents(...)`. UI-only keys never leave the Platform.
The allowed keys are configurable under `web.search.filters` in
`components/docbridge_integration/config/config.yaml`.

Rules the code keeps (design §9):

1. **The API first, then the UI.** No screen post-filters what the API filters;
   no screen invents metadata the API does not return.
2. **Facets are a distribution over the ranked pool**, not over the corpus.
3. **`offset + limit ≤ pool`** — the UI stops offering "Weitere Treffer" when
   `has_more` is false.

## The document dialog

Opened by clicking a hit. Below the actions three collapsible blocks appear
**only when the hit carries a `document_uuid`** (demo fixtures and pre-2026-08
tenants do not): *Versionen* (loaded on expand), *Ähnliche Dokumente* (loaded
on expand — the API rate budget is shared with search), *Metadaten* (form;
saves via `PATCH`). Route reference:
[`integration/documents-api.md`](../integration/documents-api.md).

## Configuration

| Key | Default | Meaning |
|-----|---------|---------|
| `web.search.results_per_page` | 20 | Page size (`limit`) |
| `web.search.filters` | all eight API keys | Filter keys offered in the rail |
| `SEARCH_USE_TEST_RESULTS` | false | Demo hits; shows the "Beispieldaten" banner |
| `OPEN_VIEWER_ENABLED` | true | Jump-to-hit viewer, see [viewer.md](viewer.md) |

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Rail shows "Filter nicht verfügbar" | The tenant answers `/secured/query` without the F3 fields; ask Knovas to roll out the August-2026 contract |
| Metaline shows only format · date | Documents were ingested without `metadata`; run the backfill or re-sync with `RC_SEND_DOCUMENT_METADATA=1` |
| "Weitere Treffer" never appears | `has_more` is false: the ranked pool is exhausted (ceiling `QUERY_COLBERT_STAGE2_TOP_DOCUMENTS`) |
| "Ähnliche Dokumente" answers 429 | Shared query budget (6/min/seat sustained, burst 18); wait for `Retry-After` |
```

- [ ] **Step 2: `viewer.md`**

Create `KnovasPlatform/docs/features/viewer.md`:

```markdown
## Viewer: jump to the hit (F7)

**Status: LIVE** for PDFs with a text layer; **PARTIAL** for scanned PDFs
without OCR text (the page opens, nothing can be highlighted, the status line
says so); **LIVE (text mode)** for DOCX/TXT/MSG (rendered text with the passage
marked, no pages).

## What it does

A search hit carries the page and the passage (`page_number`, `snippet` from
the tenant's `top_chunks`). Clicking the hit opens `/viewer` inside the
preview dialog: pdf.js renders the page, the passage is searched in the page's
text layer and the matching text runs are marked; "Fundstelle 1 von N" steps
through the hit's chunks. Cortex evidence ("Belege"), fact evidence and
conflict hits use the same helper (`window.openEvidence`), so every "why?" in
the product lands on the same viewer.

Honesty rules: the viewer marks only what it finds. If the wording differs or
the PDF has no text layer it opens the page and reports
"Fundstelle im Text nicht gefunden". It never fabricates a position.

## Route

`GET /viewer?doc=<pointer>&path=<autodoc-relative>&page=<n>&snippet=<text>&hits=<json>`
— login required. `hits` is optional JSON `[{"page": n, "snippet": "…"}]`
(≤ 8 entries, snippets ≤ 300 characters). The page renders a template only;
bytes come from `GET /api/document/<doc>/preview?path=` (PDF) or
`GET /api/document/<doc>/preview-content?path=` (text formats), both with the
existing path confinement — `/viewer` itself never touches the filesystem.

## Files and provenance

- `static/js/vendor/pdfjs/pdf.mjs`, `pdf.worker.mjs` — pdf.js
  **v6.2.108** (Apache-2.0), unmodified; `VERSION`, `SHA256SUMS`, `LICENSE`
  and the upgrade procedure in `static/js/vendor/pdfjs/README.md`. No npm, no
  build step, no CDN.
- `static/js/viewer.js`, `static/css/viewer.css`, `templates/viewer.html`,
  `src/web_interface/viewer_routes.py`, `static/js/evidence.js`.

## Configuration and headers

| Setting | Default | Meaning |
|---------|---------|---------|
| `OPEN_VIEWER_ENABLED` | `true` | `false` restores the browser's built-in viewer (`#page=N` only) |
| `OPEN_PDF_INLINE_IN_BROWSER` | `true` | Must stay on; the viewer streams PDFs from the same endpoint |
| CSP | `frame-ancestors 'self'; worker-src 'self'` | Set by the container nginx, the host nginx example and the route itself; pdf.js starts a same-origin module worker |

`.mjs` files are served as `text/javascript` (browsers refuse module scripts
with other MIME types); `/static/js/vendor/*` is cached for a day, everything
else stays `no-store` as before.

## Limits

- One page at a time (no continuous scroll); zoom steps 50–300 % and "Breite".
- Highlighting needs the passage text; tenants that do not yet return
  `top_chunks[].snippet` get the page jump only.
- Word-level geometry from the extractor is not used (the extractor has none);
  the text layer of pdf.js is the source of positions.
- Usage by format (PDF vs DOCX/MSG opens) is counted by the opt-in
  Arbeitstag-Journal (J2) — the measurement the backlog asked for before
  building this.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Viewer stays blank, console shows a CSP violation for the worker | Add `worker-src 'self'` to the edge CSP (see `deploy/host-nginx/knovas-platform.conf.example`) |
| "Viewer konnte nicht geladen werden" | `/static/js/vendor/pdfjs/pdf.mjs` missing or served as `text/plain`; rebuild the image, check `SHA256SUMS` |
| Page opens, nothing marked | Scanned PDF without text layer (OCR runs at ingestion, not here) or a different wording; page-level jump still works |
| Everything opens on page 1 | `OPEN_VIEWER_ENABLED=false` (browser viewer honours `#page=N` only sometimes) |
```

- [ ] **Step 3: Close the backlog items**

In `docs/search-ui-backlog.md`:

Line 3 → `Stand: 2026-08-15, nach Pflichtenheft F3/F6/F7/F8 (Suchfilter, Versionen, Viewer, Ähnliche Dokumente).`

Line 47 → `## 3. ~~Eigener PDF-Viewer statt des Browser-Viewers~~ — erledigt` and insert directly below it:

```markdown
Umgesetzt am 2026-08-15: pdf.js **v6.2.108** selbst gehostet (zwei Dateien plus
Lizenz, Prüfsummen und Upgrade-Anleitung unter `static/js/vendor/pdfjs/`),
Route `/viewer?doc=&path=&page=&snippet=`, `worker-src 'self'` in beiden
nginx-Konfigurationen und in der Route selbst. Der Viewer springt zur
Trefferseite und markiert den Ausschnitt in der Textebene; findet er den
Wortlaut nicht (Scan ohne OCR-Text, anderer Wortlaut), sagt er das und lässt
die Seite unmarkiert. Suchtreffer, Cortex-Belege und künftig Fakten-Belege
und Konflikt-Treffer rufen denselben Einstieg `openEvidence(...)`. Die
Messbedingung von unten (PDF gegen DOCX/MSG) übernimmt das opt-in
Arbeitstag-Journal (J2). Dokumentation: `KnovasPlatform/docs/features/viewer.md`.
```

Lines 110–118 (§5) → replace with:

```markdown
## 5. ~~Der große Block: Facetten, Sortierung, Pagination~~ — erledigt

Erledigt am 2026-08-15, in der hier geforderten Reihenfolge — **erst die API,
dann die UI**: `POST /secured/query` nimmt seit dem August-Vertrag `filters`
(author, document_type, language, document_status, source_kind, date_from,
date_to, pointer_prefix), `limit`, `offset`, `sort` und `facets` entgegen und
antwortet mit `total_ranked`, `has_more`, `facets` und Metadaten je Treffer.
Die Filterleiste, Sortierung, „Weitere Treffer" und die Facetten-Chips bauen
darauf auf; UI-eigene Schlüssel (`exact_match`) verlassen die Plattform nicht
mehr. Was bleibt: `offset` ist ein Fenster über **eine** gerankte Menge, keine
Korpus-Position, und Facetten zählen die Verteilung in den Treffern, nicht im
Korpus — beides steht so in der Oberfläche und in
`KnovasPlatform/docs/features/search-filters-and-versions.md`. Der Akten- und
Praxisgebietsfilter (`scope`) bleibt an das Relevanz-Gate des Tenants
gebunden (GATED).
```

Lines 131–135 (§6, the last two bullets) → replace with:

```markdown
- ~~**„Mehr laden" ist eine zweite vollständige Suche.**~~ Erledigt am
  2026-08-15: „Weitere Treffer" lädt mit `offset` nach und hängt an; der Knopf
  verschwindet, sobald `has_more` falsch ist. Bei einem Tenant ohne den neuen
  Vertrag bleibt es beim alten Verhalten und die Oberfläche sagt es.
- ~~**Leerzustand ungetestet.**~~ Erledigt am 2026-08-15: der Leerzustand
  zeigt `no_strong_matches` und `semantix.status`; bei `SEARCH_USE_TEST_RESULTS`
  steht dauerhaft „Beispieldaten" über der Liste.
```

Append at the end of the file:

```markdown

## 7. ~~Versionsliste (F6)~~ — erledigt

Erledigt am 2026-08-15. Bis dahin galt: kein Endpunkt, also keine UI. Jetzt
liefert `GET /secured/document/<uuid>/versions` die Vorversionen mit
`changed_by`/`changed_by_kind`, jeder Treffer trägt `has_versions`,
`version_count`, `is_current`, und der Dialog zeigt „Versionen" (geladen erst
beim Aufklappen). Bewusst nicht gebaut: **Stufe 2**, die Suche in
überholtem Text — das bräuchte alte Chunks und Vektoren im Index. „Die
unterzeichnete Fassung finden" geht über den Filter `document_status`.

## 8. ~~Ähnliche Dokumente (F8)~~ — erledigt

Erledigt am 2026-08-15. `POST /secured/documents/<uuid>/similar` liefert
Treffer in der Query-Form aus den Chunk-Vektoren des Dokuments (nur, was der
Aufrufer sehen darf; `no_strong_matches`, wenn nichts belastbar ist). Der
Dialog zeigt sie unter „Ähnliche Dokumente", die Aktenseite gruppiert die
sichtbaren `kg_node_ids` zu „Ähnliche Akten". Teilt sich das Abfrage-Budget mit
der Suche — deshalb erst beim Aufklappen geladen.
```

- [ ] **Step 4: Docs index, README, release notes, changelog, troubleshooting**

Append to `KnovasPlatform/docs/README.md`:

```markdown

## Screens and features

What the app does, with the status of each capability (LIVE · GATED · PLANNED
per [`docs/product-statements.md`](../../docs/product-statements.md)):

| Screen / feature | Doc |
|------------------|-----|
| Search: filters, sort, paging, facets, honest empty state, version list, similar documents, metadata edit, tables in preview | [features/search-filters-and-versions.md](features/search-filters-and-versions.md) |
| Viewer: open a hit at the page and highlight the passage | [features/viewer.md](features/viewer.md) |
| Document API used by the dialog (`/api/documents/*`) | [integration/documents-api.md](integration/documents-api.md) |
```

`KnovasPlatform/README.md` line 3 — replace the first sentence with:

```markdown
Ready-to-run **search web app** for your Knovas tenant (Docker), usually on a Linux server: filtered search with facets and paging, a document dialog with versions, similar documents and metadata, a viewer that opens a hit at the passage, and Cortex ([docs/README.md → Screens and features](docs/README.md#screens-and-features)). **Öffnen** uses the browser to open files on each user's PC via the shared drive — no client install ([docs/integration/opening-documents.md](docs/integration/opening-documents.md)).
```

`KnovasPlatform/docs/integration/opening-documents.md` line 27 → `PDFs open in the built-in viewer at the hit page with the passage marked (see [../features/viewer.md](../features/viewer.md)); the bytes are streamed from Server A.`

`RELEASE_NOTES.md` — replace lines 5–10 with:

```markdown
## KnovasPlatform

Docker search UI for an indexed Knovas tenant. Requires mTLS client certificates and company login configuration.

New in this release (needs the Knovas Secure API contract of August 2026):

- Search filters (Dokumenttyp, Autor, Sprache, Status, Quelle, Zeitraum), sort, real paging and facet chips; "Wer kennt sich aus?" (author facet); honest empty state and a "Beispieldaten" banner in demo mode.
- Document dialog: version list, "Ähnliche Dokumente", metadata edit, tables rendered in the preview.
- Viewer: opens a PDF hit at the page and highlights the passage (vendored pdf.js 6.2.108); Cortex evidence uses the same viewer. New env `OPEN_VIEWER_ENABLED` (default `true`); the edge CSP gains `worker-src 'self'`.

- Deploy: [KnovasPlatform/docs/setup.md](KnovasPlatform/docs/setup.md)
- Screens and features: [KnovasPlatform/docs/README.md](KnovasPlatform/docs/README.md#screens-and-features)
- Changelog: [KnovasPlatform/CHANGELOG.md](KnovasPlatform/CHANGELOG.md)
- API reference: [docs/KnovasAPI/README.md](docs/KnovasAPI/README.md)
```

Create `KnovasPlatform/CHANGELOG.md` (same shape as `RemoteController/CHANGELOG.md`):

```markdown
## Changelog

## Unreleased

- Search (F3): filter rail (Dokumenttyp, Autor, Sprache, Status, Quelle, Zeitraum), sort selector, real "Weitere Treffer" via `offset`, facet chips; `POST /api/search` forwards only the eight documented API filter keys (`web.search.filters`), UI-only keys such as `exact_match` stay local; API `400 validation_error` is shown, never dropped.
- Search (D5): "Wer kennt sich aus?" — the author facet of a query, one click from the rail.
- Search (F9): empty state renders `no_strong_matches` and `semantix.status`; persistent "Beispieldaten" banner when `SEARCH_USE_TEST_RESULTS` is on; `auto_summary` chunk hits are labelled "KI-Zusammenfassung".
- Hit cards: metaline type · date · author · language · version badge (fields present only for documents ingested with metadata).
- Document dialog (F6/F8): "Versionen" (`GET /api/documents/<uuid>/versions`), "Ähnliche Dokumente" (`POST /api/documents/<uuid>/similar`, incl. `similar_matters`), metadata edit (`PATCH /api/documents/<uuid>/metadata`); tables (H4) render in the preview (GFM pipe tables + structured `tables` from `preview-content`).
- Viewer (F7): `/viewer?doc=&path=&page=&snippet=` with vendored pdf.js **6.2.108** (`static/js/vendor/pdfjs/`, checksummed), page jump + passage highlight, prev/next hit, zoom, text-mode fallback for DOCX/TXT/MSG; shared `openEvidence()` used by search hits and Cortex evidence; env `OPEN_VIEWER_ENABLED` (default `true`); CSP `worker-src 'self'`; `.mjs` served as `text/javascript`; `/static/js/vendor/*` cached for a day.
- Client: `KnovasAPIClient.search_documents(query, *, filters, limit, offset, sort, facets, scope)`, `document_versions`, `similar_documents`, `update_document_metadata`; `SecuredApiError` carries the API's status and `error_code`.
- Build id `pflichtenheft-f-v1`.
```

Append to `KnovasPlatform/docs/integration/troubleshooting.md` (before the line `Check the Network tab …`):

```markdown
| Viewer blank, console: CSP blocks worker | Add `worker-src 'self'` to the edge CSP (`deploy/host-nginx/knovas-platform.conf.example`); the container nginx already has it |
| Viewer: "pdf.js fehlt oder ist blockiert" | `/static/js/vendor/pdfjs/pdf.mjs` must return `text/javascript`; rebuild the image, `sha256sum -c SHA256SUMS` |
| Hits open on page 1 with no highlight | `OPEN_VIEWER_ENABLED=false`, or the tenant does not return `top_chunks[].snippet` yet |
| Filter rail says "Filter nicht verfügbar" | Tenant `/secured/query` answers without `total_ranked`/`facets` — the August-2026 contract is not rolled out there |
| Dialog shows no "Versionen / Ähnliche / Metadaten" | The hit has no `document_uuid` (demo fixtures, old tenant) — expected, not an error |
| `/api/documents/*` answers 502 | Tenant API 401/5xx (certificate, outage); details in `docker compose logs docbridge-web` |
```

- [ ] **Step 5: Verify links and commit**

Run from `E:/Knovas/KnovasComponents`:

```bash
for f in KnovasPlatform/docs/features/search-filters-and-versions.md KnovasPlatform/docs/features/viewer.md \
         KnovasPlatform/docs/integration/documents-api.md KnovasPlatform/CHANGELOG.md docs/product-statements.md; do
  test -f "$f" && echo "ok  $f" || echo "MISSING $f"; done
grep -n "features/viewer.md\|features/search-filters-and-versions.md\|documents-api.md" KnovasPlatform/docs/README.md
```

Expected: every file `ok` (`docs/product-statements.md` is created by the docs part; if it is still missing on this branch, the two feature docs keep the link — it resolves once that part lands), three matches in the index.

```bash
git add KnovasPlatform/docs/features/search-filters-and-versions.md KnovasPlatform/docs/features/viewer.md \
        KnovasPlatform/docs/README.md KnovasPlatform/README.md KnovasPlatform/CHANGELOG.md \
        KnovasPlatform/docs/integration/troubleshooting.md KnovasPlatform/docs/integration/opening-documents.md \
        docs/search-ui-backlog.md RELEASE_NOTES.md
git commit -m "docs(search): feature docs for filters/versions/similar and the viewer, backlog closed, changelog and release notes"
```

---

## PART KC-B — KnovasPlatform — Parteien register, Zefix, Konfliktprüfung + lateral import (D1, D2, D3, D4)

### Task KC-B-1: Client methods and typed model for parties and conflict checks

**Requirements:** D1, D2, D3, D4
**Files:**
- Modify: `src/knovas_client.py` — append after the section-C sort-proposal methods (B6, `graph_sort_document`); edit B6's `graph_create_identifier` (adds `kind`) and B5's `graph_create_fact` (adds `provenance_pointer`); edit `_sync_single_document_secured` (`src/knovas_client.py:1807-1846` on main) to forward `document['metadata']` and return `transmission_key_id`; add `_clean_document_metadata` above `_secured_transmit_parts_from_document` (`:439`)
- Modify: `src/graph_model.py` — append the dataclasses (file created by C-plan B3)
- Modify: `tests/test_graph_contract_live.py` (C-plan B1) — add the party/conflict recording; `tests/test_knovas_client_graph_sorting.py` (one assertion gains `kind`)
- Create: `tests/test_knovas_client_parties.py`, `tests/test_graph_model_parties.py`, `tests/test_graph_contract_parties.py`
**Interfaces:**
- Consumes: `GraphError(status, error_code, message)` and `KnowledgeGraphDisabled` (C-plan B2); `_graph_request`, `_graph_payload_list` (`src/knovas_client.py:1531`, `:866`); `FakeResponse`, `FakeSession`, `make_secured_client` from `tests/test_knovas_client_hardening.py`; the cassette recorder fixture `live_client` (C-plan B1).
- Produces on `KnovasAPIClient`:
  - `identifiers_search(q, *, kind=None, node_type_id=None, threshold=None, limit=None) -> {'matches': list[dict], 'degraded': bool}` — `GET /secured/graph/identifiers/search`
  - `node_duplicates(node_type_id=None, threshold=None, limit=None) -> {'candidates': list[dict]}` — `GET /secured/graph/nodes/duplicates`
  - `merge_nodes(target_id, source_id, actor_ref=None) -> dict | None` — `POST /secured/graph/nodes/<target>/merge {source_node_id, actor_ref?}`; `None` = 404 (either node unknown/invisible); `ValueError` when target == source; `GraphError` for 409/503
  - `conflict_check_run(queries, context=None, actor_ref=None) -> dict | None` — `POST /secured/graph/conflict-checks`; `queries` = list of `str` or `{'name', 'role'?}`, 1..50; `ValueError` on empty name / bad count
  - `conflict_check_list(limit=None, offset=None, since=None) -> {'checks': list[dict], 'total': int | None}`
  - `conflict_check_get(check_id) -> dict | None`
  - `conflict_check_decide(check_id, decision, note=None, actor_ref=None) -> dict | None`; `ValueError` when `decision` not in `CONFLICT_DECISIONS`
  - `graph_create_identifier(node_id, identifier_text, kind='name')` (extends C-plan B6: sends `{'identifier_text', 'kind'}`)
  - `graph_create_fact(..., provenance_pointer=None)` (extends C-plan B5: forwards `provenance_pointer`)
  - `sync_single_document(document)` accepts `document['metadata']` (dict; only the seven keys `author, document_type, language, document_date, document_status, source_kind, extra` are forwarded as the init `metadata` object) and returns `{'status','identifier','mode','transmission_key_id'}`
  - class constants `KnovasAPIClient.IDENTIFIER_KINDS`, `KnovasAPIClient.CONFLICT_DECISIONS`
- Produces in `src/graph_model.py`: `IDENTIFIER_KINDS`, `IDENTIFIER_KIND_LABELS`, `CONFLICT_DECISIONS`, `CONFLICT_DECISION_LABELS`, dataclasses `Identifier(id, node_id, text, kind, normalized)`, `IdentifierMatch(node_id, node_name, node_type_id, identifier_id, identifier_text, kind, score, channel)`, `DuplicatePair(node_a, node_b, score, identifiers)`, `ConflictHit(query_index, kind, node_id, pointer, matched_text, score, channel, matter_node_ids)`, `Decision(check_id, decision, note, actor, actor_kind, decided_at)`, `ConflictCheck(check_id, executed_at, queries, context, hits, hit_count, withheld_count, degraded, principal_scoped, actor, actor_kind, actor_ref, result_hash, decisions)` — each with `from_api(row) -> cls` and `to_dict()`, plus `ConflictCheck.grouped() -> {'parties', 'documents', 'matters'}`.

- [ ] **Step 1: Write the failing client test**

Create `tests/test_knovas_client_parties.py`:

```python
"""Client methods for the party register, duplicates, merge and conflict checks.

Fakes only (house style of test_knovas_client_hardening.py); the live shape is
pinned separately by tests/test_graph_contract_parties.py.
"""
from __future__ import annotations

import pytest

from knovas_client import GraphError
from test_knovas_client_hardening import FakeResponse, FakeSession, make_secured_client


def _client(status=200, body=None):
    client = make_secured_client()
    seen = {}

    def responder(method, url, **kw):
        seen.update(method=method, url=url, json=kw.get("json"), params=kw.get("params"))
        return FakeResponse(status, {} if body is None else body)

    client._session = FakeSession(responder)
    return client, seen


def test_identifiers_search_sends_kind_and_reads_degraded():
    client, seen = _client(200, {"status": "success", "matches": [
        {"node_id": "n1", "node_name": "Muster AG", "identifier_text": "Muster AG",
         "kind": "legal_name", "score": 0.93, "channel": "lexical"}], "degraded": True})

    out = client.identifiers_search("Muster", kind="legal_name", limit=5)

    assert seen["url"].endswith("/secured/graph/identifiers/search")
    assert seen["params"] == {"q": "Muster", "kind": "legal_name", "limit": 5}
    assert out["degraded"] is True
    assert out["matches"][0]["node_id"] == "n1"


def test_identifiers_search_without_degraded_key_reads_false():
    client, _ = _client(200, {"status": "success", "matches": []})
    assert client.identifiers_search("x") == {"matches": [], "degraded": False}


def test_node_duplicates_passes_filters():
    client, seen = _client(200, {"status": "success", "candidates": []})
    client.node_duplicates(node_type_id="t1", threshold=0.9, limit=20)
    assert seen["params"] == {"node_type_id": "t1", "threshold": 0.9, "limit": 20}


def test_merge_sends_source_and_actor():
    client, seen = _client(200, {"status": "success", "node": {"id": "n1"}})
    client.merge_nodes("n1", "n2", actor_ref="user-7")
    assert seen["url"].endswith("/secured/graph/nodes/n1/merge")
    assert seen["json"] == {"source_node_id": "n2", "actor_ref": "user-7"}


def test_merge_refuses_self_merge_before_calling_the_api():
    client, seen = _client(200, {})
    with pytest.raises(ValueError):
        client.merge_nodes("n1", "n1")
    assert "url" not in seen


def test_merge_404_is_none_and_409_is_graph_error():
    client, _ = _client(404, {"status": "error", "error_code": "NOT_FOUND"})
    assert client.merge_nodes("n1", "n2") is None
    client, _ = _client(409, {"error_code": "node_already_merged", "message": "x"})
    with pytest.raises(GraphError) as caught:
        client.merge_nodes("n1", "n2")
    assert caught.value.error_code == "node_already_merged"


def test_conflict_check_run_normalises_queries():
    client, seen = _client(201, {"status": "success", "check_id": "c1", "hits": []})
    client.conflict_check_run(["Muster AG", {"name": "Meier", "role": "counterparty"}],
                              context="Neumandat", actor_ref="user-7")
    assert seen["url"].endswith("/secured/graph/conflict-checks")
    assert seen["json"] == {"queries": [{"name": "Muster AG"},
                                        {"name": "Meier", "role": "counterparty"}],
                            "context": "Neumandat", "actor_ref": "user-7"}


def test_conflict_check_run_rejects_empty_and_oversized():
    client, _ = _client(201, {})
    with pytest.raises(ValueError):
        client.conflict_check_run([])
    with pytest.raises(ValueError):
        client.conflict_check_run([{"name": "  "}])
    with pytest.raises(ValueError):
        client.conflict_check_run([f"n{i}" for i in range(51)])


def test_conflict_check_list_and_get_and_decide():
    client, seen = _client(200, {"status": "success", "checks": [{"check_id": "c1"}], "total": 1})
    assert client.conflict_check_list(limit=10, offset=20)["checks"][0]["check_id"] == "c1"
    assert seen["params"] == {"limit": 10, "offset": 20}

    client, seen = _client(200, {"status": "success", "check_id": "c1", "hits": []})
    assert client.conflict_check_get("c1")["check_id"] == "c1"

    client, seen = _client(201, {"status": "success", "decision": {"decision": "clear"}})
    client.conflict_check_decide("c1", "clear", note="ok", actor_ref="user-7")
    assert seen["url"].endswith("/secured/graph/conflict-checks/c1/decisions")
    assert seen["json"] == {"decision": "clear", "note": "ok", "actor_ref": "user-7"}
    with pytest.raises(ValueError):
        client.conflict_check_decide("c1", "maybe")


def test_identifier_create_sends_kind():
    client, seen = _client(201, {"status": "success", "identifier": {"id": "i1"}})
    client.graph_create_identifier("n1", "CHE-123.456.789", kind="uid")
    assert seen["json"] == {"identifier_text": "CHE-123.456.789", "kind": "uid"}


def test_fact_create_forwards_provenance_pointer():
    client, seen = _client(201, {"status": "success", "fact": {"id": "f1"}})
    client.graph_create_fact("n1", "Zürich", attribute_id="a1",
                             provenance_pointer="platform/zefix/CHE123456789/2026-08-15.txt")
    assert seen["json"]["provenance_pointer"] == "platform/zefix/CHE123456789/2026-08-15.txt"


def test_sync_single_document_forwards_metadata_and_returns_key():
    client = make_secured_client()
    bodies = []

    def responder(method, url, **kw):
        bodies.append((url, kw.get("json")))
        if url.endswith("/secured/init_document_transmission"):
            return FakeResponse(200, {"transmission_key_id": "tk-1"})
        return FakeResponse(200, {"status": "success"})

    client._session = FakeSession(responder)
    out = client.sync_single_document({
        "doc_id": "platform/zefix/CHE123456789/2026-08-15.txt",
        "path": "/platform/zefix/CHE123456789/2026-08-15.txt",
        "metadata": {"document_type": "Registerauszug", "source_kind": "upload",
                     "document_date": "2026-08-15", "language": "de",
                     "extra": {"zefix:uid": "CHE123456789"}, "bogus": "dropped"},
    })
    init_body = bodies[0][1]
    assert init_body["metadata"] == {"document_type": "Registerauszug", "source_kind": "upload",
                                     "document_date": "2026-08-15", "language": "de",
                                     "extra": {"zefix:uid": "CHE123456789"}}
    assert out["transmission_key_id"] == "tk-1"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_knovas_client_parties.py -v`
Expected: FAIL — `AttributeError: 'KnovasAPIClient' object has no attribute 'identifiers_search'` (first test); the later tests fail on the missing `kind` / `provenance_pointer` / `metadata` / `transmission_key_id`.

- [ ] **Step 3: Implement the client methods**

Append to `class KnovasAPIClient` in `src/knovas_client.py`, after `graph_sort_document` (C-plan B6):

```python
    # ------------------------------------------------------------------
    # Parteienregister, Dubletten, Konfliktpruefung
    # Spezifikation: design 2026-08-15 §5.8 (identifier kinds, search,
    # duplicates, merge) und §5.9 (conflict checks). Alle Antworten kommen in
    # der flachen Envelope; Listen werden tolerant gezogen.
    # ------------------------------------------------------------------

    IDENTIFIER_KINDS = ('name', 'alias', 'legal_name', 'uid', 'matter_number',
                        'email', 'iban', 'other')
    CONFLICT_DECISIONS = ('clear', 'conflict', 'waived_with_consent', 'needs_review')

    def identifiers_search(self, q: str, *, kind: Optional[str] = None,
                           node_type_id: Optional[str] = None,
                           threshold: Optional[float] = None,
                           limit: Optional[int] = None) -> Dict[str, Any]:
        """GET /secured/graph/identifiers/search - mandantenweite unscharfe Suche.

        Liefert {'matches': [...], 'degraded': bool}. degraded=True heisst
        'ein Kanal ist ausgefallen' - die Oberflaeche muss das zeigen, sonst
        sieht ein Ausfall aus wie 'kein Treffer'.
        """
        params: Dict[str, Any] = {'q': str(q)}
        if kind:
            params['kind'] = str(kind)
        if node_type_id:
            params['node_type_id'] = str(node_type_id)
        if threshold is not None:
            params['threshold'] = float(threshold)
        if limit is not None:
            params['limit'] = int(limit)
        payload = self._graph_request('GET', '/identifiers/search', params=params) or {}
        return {
            'matches': _graph_payload_list(payload, 'matches', 'results', 'identifiers'),
            'degraded': bool(payload.get('degraded', False)) if isinstance(payload, dict) else False,
        }

    def node_duplicates(self, node_type_id: Optional[str] = None,
                        threshold: Optional[float] = None,
                        limit: Optional[int] = None) -> Dict[str, Any]:
        """GET /secured/graph/nodes/duplicates - Kandidatenpaare nach Identifikator-Aehnlichkeit."""
        params: Dict[str, Any] = {}
        if node_type_id:
            params['node_type_id'] = str(node_type_id)
        if threshold is not None:
            params['threshold'] = float(threshold)
        if limit is not None:
            params['limit'] = int(limit)
        payload = self._graph_request('GET', '/nodes/duplicates', params=params or None) or {}
        return {'candidates': _graph_payload_list(payload, 'candidates', 'pairs', 'duplicates')}

    def merge_nodes(self, target_id: str, source_id: str,
                    actor_ref: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """POST /secured/graph/nodes/<target>/merge {source_node_id, actor_ref?}.

        Die Quelle bleibt als Verweis (status='merged', merged_into) lesbar;
        None = einer der Knoten ist unbekannt oder nicht sichtbar (404).
        """
        if str(target_id) == str(source_id):
            raise ValueError('Ziel und Quelle sind derselbe Knoten')
        data: Dict[str, Any] = {'source_node_id': str(source_id)}
        if actor_ref:
            data['actor_ref'] = str(actor_ref)
        return self._graph_request(
            'POST', f'/nodes/{quote(str(target_id), safe="")}/merge', data=data)

    def conflict_check_run(self, queries: List[Any], context: Optional[str] = None,
                           actor_ref: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """POST /secured/graph/conflict-checks - fuehrt die Pruefung aus UND legt den Beleg an.

        Jeder Aufruf ist eine neue, unveraenderliche Zeile im Backend - es gibt
        bewusst keine 'erneut ausfuehren'-Variante.
        """
        cleaned: List[Dict[str, str]] = []
        for entry in queries or []:
            if isinstance(entry, str):
                entry = {'name': entry}
            name = str((entry or {}).get('name') or '').strip()
            if not name:
                raise ValueError('Jede Abfrage braucht einen Namen')
            item = {'name': name}
            role = str((entry or {}).get('role') or '').strip()
            if role:
                item['role'] = role
            cleaned.append(item)
        if not 1 <= len(cleaned) <= 50:
            raise ValueError('Eine Pruefung umfasst 1 bis 50 Namen')
        data: Dict[str, Any] = {'queries': cleaned}
        if context:
            data['context'] = str(context)
        if actor_ref:
            data['actor_ref'] = str(actor_ref)
        return self._graph_request('POST', '/conflict-checks', data=data)

    def conflict_check_list(self, limit: Optional[int] = None, offset: Optional[int] = None,
                            since: Optional[str] = None) -> Dict[str, Any]:
        """GET /secured/graph/conflict-checks?limit=&offset=&since= - Verlauf."""
        params: Dict[str, Any] = {}
        if limit is not None:
            params['limit'] = int(limit)
        if offset is not None:
            params['offset'] = int(offset)
        if since:
            params['since'] = str(since)
        payload = self._graph_request('GET', '/conflict-checks', params=params or None) or {}
        total = payload.get('total') if isinstance(payload, dict) else None
        return {'checks': _graph_payload_list(payload, 'checks', 'conflict_checks'),
                'total': int(total) if isinstance(total, int) else None}

    def conflict_check_get(self, check_id: str) -> Optional[Dict[str, Any]]:
        """GET /secured/graph/conflict-checks/<id> - eine Pruefung samt Treffern und Entscheiden."""
        payload = self._graph_request(
            'GET', f'/conflict-checks/{quote(str(check_id), safe="")}')
        if payload is None:
            return None
        inner = payload.get('check') if isinstance(payload, dict) else None
        return inner if isinstance(inner, dict) else payload

    def conflict_check_decide(self, check_id: str, decision: str, note: Optional[str] = None,
                              actor_ref: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """POST /secured/graph/conflict-checks/<id>/decisions - append-only Entscheid."""
        if decision not in self.CONFLICT_DECISIONS:
            raise ValueError(
                f'decision muss eines von {", ".join(self.CONFLICT_DECISIONS)} sein: {decision}')
        data: Dict[str, Any] = {'decision': decision}
        if note:
            data['note'] = str(note)
        if actor_ref:
            data['actor_ref'] = str(actor_ref)
        return self._graph_request(
            'POST', f'/conflict-checks/{quote(str(check_id), safe="")}/decisions', data=data)
```

Edit C-plan B6's `graph_create_identifier` so it reads:

```python
    def graph_create_identifier(self, node_id: str, identifier_text: str,
                                kind: str = 'name') -> Optional[Dict[str, Any]]:
        """POST /secured/graph/nodes/<id>/identifiers {identifier_text, kind}.

        kind: name | alias | legal_name | uid | matter_number | email | iban | other
        (design 2026-08-15 §5.8). Ein unbekannter kind wird zu 'other', nie
        stillschweigend zu 'name'.
        """
        kind = kind if kind in self.IDENTIFIER_KINDS else 'other'
        return self._graph_request(
            'POST', f'/nodes/{quote(str(node_id), safe="")}/identifiers',
            data={'identifier_text': identifier_text, 'kind': kind})
```

Edit C-plan B5's `graph_create_fact`: add the parameter after `provenance_chunk_id`

```python
                          provenance_chunk_id: Optional[str] = None,
                          provenance_pointer: Optional[str] = None,
                          valid_from: Optional[str] = None,
```
and extend its forwarding loop:

```python
        for key, val in (('provenance_chunk_id', provenance_chunk_id),
                         ('provenance_pointer', provenance_pointer),
                         ('valid_from', valid_from), ('valid_to', valid_to)):
            if val:
                payload[key] = val
```

- [ ] **Step 4: Forward `metadata` in the secured single-document path**

In `src/knovas_client.py`, add this module-level helper directly above `_secured_transmit_parts_from_document` (`:439` on main):

```python
_DOCUMENT_METADATA_KEYS = ('author', 'document_type', 'language', 'document_date',
                           'document_status', 'source_kind', 'extra')


def _clean_document_metadata(metadata: Any) -> Dict[str, Any]:
    """Nur die sieben Felder des init-'metadata'-Objekts (Secure_API.md, design §5.2).

    Alles andere faellt weg, damit ein UI-Feld nie versehentlich an die
    Mandanten-API geht (dieselbe Regel wie fuer 'exact_match' bei Filtern).
    """
    if not isinstance(metadata, dict):
        return {}
    out: Dict[str, Any] = {}
    for key in _DOCUMENT_METADATA_KEYS:
        value = metadata.get(key)
        if value is None or value == '' or value == {}:
            continue
        if key == 'extra':
            if isinstance(value, dict):
                out['extra'] = {str(k)[:64]: str(v)[:256] for k, v in list(value.items())[:16]}
            continue
        limit = 500 if key == 'author' else 128
        out[key] = str(value).strip()[:limit]
    return out
```

Then in `_sync_single_document_secured`, immediately after `init_body.update(init_fields)`:

```python
        metadata = _clean_document_metadata(document.get('metadata'))
        if metadata:
            init_body['metadata'] = metadata
```

and change its final `return` to:

```python
        return {'status': 'success', 'identifier': identifier, 'mode': 'secured',
                'transmission_key_id': str(transmission_key_id)}
```

If another part of this plan (the search/F3 part or the add-in filing part) has already added the same forwarding when you get here, keep the existing implementation and only make sure the test above passes.

- [ ] **Step 5: Run the client tests**

Run: `python -m pytest tests/test_knovas_client_parties.py tests/test_knovas_client_graph_sorting.py tests/test_knovas_client_graph_facts.py -v`
Expected: PASS — 12 new tests. In `tests/test_knovas_client_graph_sorting.py` (C-plan B6) change the identifier assertion to `assert graph_client_stub.last_data == {"identifier_text": "Michael Xample", "kind": "name"}` — the body now always carries `kind`.

- [ ] **Step 6: Write the failing model test**

Create `tests/test_graph_model_parties.py`:

```python
"""Typed rows for identifiers, duplicate pairs and conflict checks."""
from __future__ import annotations

from graph_model import (CONFLICT_DECISIONS, IDENTIFIER_KINDS, ConflictCheck, ConflictHit,
                         Decision, DuplicatePair, Identifier, IdentifierMatch)


def test_identifier_defaults_kind_to_name_and_folds_unknown_kind_to_other():
    assert Identifier.from_api({"id": "i1", "node_id": "n1", "identifier_text": "Muster AG"}).kind == "name"
    assert Identifier.from_api({"id": "i1", "node_id": "n1", "identifier_text": "x",
                                "kind": "nickname"}).kind == "other"
    assert "uid" in IDENTIFIER_KINDS


def test_identifier_match_reads_score_as_float():
    match = IdentifierMatch.from_api({"node_id": "n1", "node_name": "Muster AG",
                                      "node_type_id": "t1", "identifier_id": "i1",
                                      "identifier_text": "Muster AG", "kind": "legal_name",
                                      "score": "0.93", "channel": "lexical"})
    assert match.score == 0.93
    assert match.to_dict()["kind"] == "legal_name"


def test_duplicate_pair_tolerates_node_a_b_and_nodes_list():
    a = {"node_id": "n1", "node_name": "Muster AG", "node_type_id": "t1"}
    b = {"node_id": "n2", "node_name": "Muster-Bau AG", "node_type_id": "t1"}
    pair = DuplicatePair.from_api({"node_a": a, "node_b": b, "score": 0.91,
                                   "identifiers": [{"a": "Muster AG", "b": "Muster-Bau AG",
                                                    "kind": "legal_name"}]})
    assert pair.node_a["node_id"] == "n1" and pair.node_b["node_id"] == "n2"
    assert DuplicatePair.from_api({"nodes": [a, b], "score": 0.9}).node_b["node_id"] == "n2"
    assert pair.to_dict()["identifiers"][0]["kind"] == "legal_name"


def _payload():
    return {
        "check_id": "c1", "executed_at": "2026-08-15T10:00:00+00:00",
        "queries": [{"name": "Muster AG", "role": "counterparty"}], "context": "Neumandat",
        "hits": [
            {"query_index": 0, "kind": "party", "node_id": "n1", "matched_text": "Muster AG",
             "score": 0.95, "channel": "lexical", "matter_node_ids": ["m1", "m2"]},
            {"query_index": 0, "kind": "document", "pointer": "akten/m1/vertrag.pdf",
             "matched_text": "Muster AG", "score": 0.8, "channel": "bm25", "matter_node_ids": ["m1"]},
        ],
        "hit_count": 2, "withheld_count": 1, "degraded": False, "principal_scoped": True,
        "actor": "user-7", "actor_kind": "client_ref", "actor_ref": "user-7", "result_hash": "abc",
        "decisions": [{"check_id": "c1", "decision": "needs_review", "note": "n",
                       "actor": "user-9", "actor_kind": "client_ref",
                       "decided_at": "2026-08-15T11:00:00+00:00"}],
    }


def test_conflict_check_from_api_and_grouping():
    check = ConflictCheck.from_api(_payload())
    assert check.withheld_count == 1 and check.principal_scoped is True
    grouped = check.grouped()
    assert [h["node_id"] for h in grouped["parties"]] == ["n1"]
    assert [h["pointer"] for h in grouped["documents"]] == ["akten/m1/vertrag.pdf"]
    assert grouped["matters"][0]["matter_node_id"] == "m1"
    assert len(grouped["matters"][0]["hits"]) == 2
    assert check.decisions[0].decision == "needs_review"
    assert check.to_dict()["hits"][0]["kind"] == "party"


def test_conflict_check_id_falls_back_to_id_key():
    payload = _payload()
    payload["id"] = payload.pop("check_id")
    assert ConflictCheck.from_api(payload).check_id == "c1"


def test_decision_vocabulary_is_the_design_vocabulary():
    assert CONFLICT_DECISIONS == ("clear", "conflict", "waived_with_consent", "needs_review")
    assert Decision.from_api({"decision": "clear"}).decision == "clear"
    assert ConflictHit.from_api({"kind": "party"}).matter_node_ids == ()
```

- [ ] **Step 7: Run it to verify it fails**

Run: `python -m pytest tests/test_graph_model_parties.py -v`
Expected: FAIL with `ImportError: cannot import name 'ConflictCheck' from 'graph_model'`

- [ ] **Step 8: Implement the dataclasses**

Append to `src/graph_model.py` (extend the imports at the top to `from dataclasses import asdict, dataclass` and `from typing import Any, Dict, List, Tuple`):

```python
## ---------------------------------------------------------------------------
## Parteien, Dubletten, Konfliktprüfung (design 2026-08-15 §5.8 / §5.9)
## ---------------------------------------------------------------------------

IDENTIFIER_KINDS = ("name", "alias", "legal_name", "uid", "matter_number",
                    "email", "iban", "other")
IDENTIFIER_KIND_LABELS = {
    "name": "Name", "alias": "Alias / Kurzname", "legal_name": "Firma (rechtlicher Name)",
    "uid": "UID (CHE-…)", "matter_number": "Aktennummer", "email": "E-Mail",
    "iban": "IBAN", "other": "Sonstige",
}
CONFLICT_DECISIONS = ("clear", "conflict", "waived_with_consent", "needs_review")
CONFLICT_DECISION_LABELS = {
    "clear": "Kein Konflikt",
    "conflict": "Konflikt",
    "waived_with_consent": "Konflikt – mit Einwilligung freigegeben",
    "needs_review": "Weitere Prüfung nötig",
}
CONFLICT_HIT_KINDS = ("party", "document")


def _s(value: Any) -> str:
    return "" if value is None else str(value)


def _f(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


@dataclass(frozen=True)
class Identifier:
    id: str
    node_id: str
    text: str
    kind: str = "name"
    normalized: str = ""

    @classmethod
    def from_api(cls, row: Dict[str, Any]) -> "Identifier":
        kind = _s(row.get("kind") or "name")
        return cls(id=_s(row.get("id") or row.get("identifier_id")),
                   node_id=_s(row.get("node_id")),
                   text=_s(row.get("identifier_text") or row.get("text")),
                   kind=kind if kind in IDENTIFIER_KINDS else "other",
                   normalized=_s(row.get("identifier_normalized")))

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "node_id": self.node_id, "text": self.text, "kind": self.kind,
                "kind_label": IDENTIFIER_KIND_LABELS.get(self.kind, self.kind)}


@dataclass(frozen=True)
class IdentifierMatch:
    node_id: str
    node_name: str
    node_type_id: str
    identifier_id: str
    identifier_text: str
    kind: str
    score: float
    channel: str

    @classmethod
    def from_api(cls, row: Dict[str, Any]) -> "IdentifierMatch":
        return cls(node_id=_s(row.get("node_id")),
                   node_name=_s(row.get("node_name") or row.get("name")),
                   node_type_id=_s(row.get("node_type_id")),
                   identifier_id=_s(row.get("identifier_id")),
                   identifier_text=_s(row.get("identifier_text") or row.get("matched_text")),
                   kind=_s(row.get("kind") or "name"), score=_f(row.get("score")),
                   channel=_s(row.get("channel") or "lexical"))

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _pair_nodes(row: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Beide Knoten eines Kandidatenpaares, egal wie die API sie benennt."""
    for a_key, b_key in (("node_a", "node_b"), ("left", "right"), ("target", "source")):
        if isinstance(row.get(a_key), dict) and isinstance(row.get(b_key), dict):
            return row[a_key], row[b_key]
    nodes = row.get("nodes")
    if isinstance(nodes, list) and len(nodes) >= 2:
        return nodes[0], nodes[1]
    return {}, {}


def _node_ref(node: Dict[str, Any]) -> Dict[str, Any]:
    return {"node_id": _s(node.get("node_id") or node.get("id")),
            "node_name": _s(node.get("node_name") or node.get("name")),
            "node_type_id": _s(node.get("node_type_id"))}


@dataclass(frozen=True)
class DuplicatePair:
    node_a: Dict[str, Any]
    node_b: Dict[str, Any]
    score: float
    identifiers: Tuple[Dict[str, Any], ...]

    @classmethod
    def from_api(cls, row: Dict[str, Any]) -> "DuplicatePair":
        a, b = _pair_nodes(row)
        idents = row.get("identifiers") or row.get("matches") or []
        return cls(node_a=_node_ref(a), node_b=_node_ref(b), score=_f(row.get("score")),
                   identifiers=tuple({"a": _s(i.get("a") or i.get("identifier_a")),
                                      "b": _s(i.get("b") or i.get("identifier_b")),
                                      "kind": _s(i.get("kind") or "name")}
                                     for i in idents if isinstance(i, dict)))

    def to_dict(self) -> Dict[str, Any]:
        return {"node_a": self.node_a, "node_b": self.node_b, "score": self.score,
                "identifiers": list(self.identifiers)}


@dataclass(frozen=True)
class ConflictHit:
    query_index: int
    kind: str
    node_id: str
    pointer: str
    matched_text: str
    score: float
    channel: str
    matter_node_ids: Tuple[str, ...]

    @classmethod
    def from_api(cls, row: Dict[str, Any]) -> "ConflictHit":
        kind = _s(row.get("kind") or "party")
        return cls(query_index=int(row.get("query_index") or 0),
                   kind=kind if kind in CONFLICT_HIT_KINDS else "party",
                   node_id=_s(row.get("node_id")), pointer=_s(row.get("pointer")),
                   matched_text=_s(row.get("matched_text")), score=_f(row.get("score")),
                   channel=_s(row.get("channel")),
                   matter_node_ids=tuple(_s(m) for m in (row.get("matter_node_ids") or [])))

    def to_dict(self) -> Dict[str, Any]:
        out = asdict(self)
        out["matter_node_ids"] = list(self.matter_node_ids)
        return out


@dataclass(frozen=True)
class Decision:
    check_id: str
    decision: str
    note: str
    actor: str
    actor_kind: str
    decided_at: str

    @classmethod
    def from_api(cls, row: Dict[str, Any]) -> "Decision":
        return cls(check_id=_s(row.get("check_id")), decision=_s(row.get("decision")),
                   note=_s(row.get("note")), actor=_s(row.get("actor")),
                   actor_kind=_s(row.get("actor_kind") or "client_ref"),
                   decided_at=_s(row.get("decided_at")))

    def to_dict(self) -> Dict[str, Any]:
        out = asdict(self)
        out["decision_label"] = CONFLICT_DECISION_LABELS.get(self.decision, self.decision)
        return out


@dataclass(frozen=True)
class ConflictCheck:
    check_id: str
    executed_at: str
    queries: Tuple[Dict[str, Any], ...]
    context: str
    hits: Tuple[ConflictHit, ...]
    hit_count: int
    withheld_count: int
    degraded: bool
    principal_scoped: bool
    actor: str
    actor_kind: str
    actor_ref: str
    result_hash: str
    decisions: Tuple[Decision, ...]

    @classmethod
    def from_api(cls, payload: Dict[str, Any]) -> "ConflictCheck":
        hits = tuple(ConflictHit.from_api(h) for h in (payload.get("hits") or [])
                     if isinstance(h, dict))
        return cls(check_id=_s(payload.get("check_id") or payload.get("id")),
                   executed_at=_s(payload.get("executed_at")),
                   queries=tuple(dict(q) for q in (payload.get("queries") or [])
                                 if isinstance(q, dict)),
                   context=_s(payload.get("context")), hits=hits,
                   hit_count=int(payload.get("hit_count") if payload.get("hit_count") is not None
                                 else len(hits)),
                   withheld_count=int(payload.get("withheld_count") or 0),
                   degraded=bool(payload.get("degraded", False)),
                   principal_scoped=bool(payload.get("principal_scoped", False)),
                   actor=_s(payload.get("actor")),
                   actor_kind=_s(payload.get("actor_kind") or "client_ref"),
                   actor_ref=_s(payload.get("actor_ref")), result_hash=_s(payload.get("result_hash")),
                   decisions=tuple(Decision.from_api(d) for d in (payload.get("decisions") or [])
                                   if isinstance(d, dict)))

    def grouped(self) -> Dict[str, Any]:
        """Treffer nach Parteien / Akten / Dokumenten - die drei Fragen des Konfliktbeauftragten."""
        parties = [h.to_dict() for h in self.hits if h.kind == "party"]
        documents = [h.to_dict() for h in self.hits if h.kind == "document"]
        matters: Dict[str, Dict[str, Any]] = {}
        for hit in self.hits:
            for matter_id in hit.matter_node_ids:
                bucket = matters.setdefault(matter_id, {"matter_node_id": matter_id, "hits": []})
                bucket["hits"].append(hit.to_dict())
        return {"parties": parties, "documents": documents,
                "matters": sorted(matters.values(), key=lambda m: -len(m["hits"]))}

    def to_dict(self) -> Dict[str, Any]:
        return {"check_id": self.check_id, "executed_at": self.executed_at,
                "queries": list(self.queries), "context": self.context,
                "hits": [h.to_dict() for h in self.hits], "hit_count": self.hit_count,
                "withheld_count": self.withheld_count, "degraded": self.degraded,
                "principal_scoped": self.principal_scoped, "actor": self.actor,
                "actor_kind": self.actor_kind, "actor_ref": self.actor_ref,
                "result_hash": self.result_hash,
                "decisions": [d.to_dict() for d in self.decisions],
                "grouped": self.grouped()}
```

- [ ] **Step 9: Run the model tests**

Run: `python -m pytest tests/test_graph_model_parties.py tests/test_graph_model.py -v`
Expected: PASS — 6 new tests, the C-plan B3 tests untouched.

- [ ] **Step 10: Extend the live recorder and pin the shapes offline**

Append to `tests/test_graph_contract_live.py` (C-plan B1; the module already carries `pytestmark = pytest.mark.knovas_api`):

```python
def test_record_party_and_conflict_contract(live_client):
    """§5.8/§5.9 shapes. Conflict checks are append-only server-side, so this
    leaves two rows in the dev tenant; that is the price of an honest cassette."""
    recorded = json.loads(CASSETTE.read_text(encoding="utf-8")) if CASSETTE.exists() else {}

    created_type = live_client.graph_create_node_type("Organisationstest")
    type_id = created_type["node_type"]["id"]
    org_id = live_client.graph_create_node("Muster Bau AG", node_type_id=type_id)["node"]["id"]
    twin_id = live_client.graph_create_node("Muster-Bau AG", node_type_id=type_id)["node"]["id"]
    live_client.graph_create_identifier(org_id, "Muster Bau AG", kind="legal_name")
    live_client.graph_create_identifier(twin_id, "Muster-Bau AG", kind="legal_name")

    recorded["GET /identifiers/search"] = live_client._graph_request(
        "GET", "/identifiers/search", params={"q": "Muster Bau", "limit": 5})
    recorded["GET /nodes/duplicates"] = live_client._graph_request(
        "GET", "/nodes/duplicates", params={"node_type_id": type_id, "limit": 5})

    check = live_client._graph_request(
        "POST", "/conflict-checks",
        data={"queries": [{"name": "Muster Bau AG", "role": "counterparty"}],
              "context": "cassette", "actor_ref": "cassette-recorder"})
    recorded["POST /conflict-checks"] = check
    check_id = check["check_id"]
    recorded["GET /conflict-checks/<id>"] = live_client._graph_request(
        "GET", f"/conflict-checks/{check_id}")
    recorded["POST /conflict-checks/<id>/decisions"] = live_client._graph_request(
        "POST", f"/conflict-checks/{check_id}/decisions",
        data={"decision": "needs_review", "note": "cassette", "actor_ref": "cassette-recorder"})

    recorded["POST /nodes/<id>/merge"] = live_client._graph_request(
        "POST", f"/nodes/{org_id}/merge",
        data={"source_node_id": twin_id, "actor_ref": "cassette-recorder"})
    recorded["GET /nodes/<merged-source>"] = live_client._graph_request("GET", f"/nodes/{twin_id}")

    CASSETTE.write_text(json.dumps(recorded, indent=2, ensure_ascii=False), encoding="utf-8")

    live_client.graph_delete_node(org_id)
    live_client.graph_delete_node_type(type_id)
```

Create `tests/test_graph_contract_parties.py`:

```python
"""Offline pins against the recorded §5.8/§5.9 shapes."""
from __future__ import annotations

import json
import pathlib

import pytest

CASSETTE = pathlib.Path(__file__).parent / "cassettes" / "graph_contract.json"


@pytest.fixture
def contract():
    return json.loads(CASSETTE.read_text(encoding="utf-8"))


def test_identifier_search_rows_carry_kind_and_score(contract):
    payload = contract["GET /identifiers/search"]
    assert "degraded" in payload
    rows = payload.get("matches") or payload.get("results")
    assert rows and {"node_id", "identifier_text", "kind", "score", "channel"} <= set(rows[0])


def test_conflict_check_carries_the_honesty_fields(contract):
    payload = contract["POST /conflict-checks"]
    assert {"check_id", "executed_at", "hits", "hit_count",
            "withheld_count", "degraded", "principal_scoped"} <= set(payload)


def test_merged_source_is_a_readable_tombstone(contract):
    node = contract["GET /nodes/<merged-source>"]["node"]
    assert node.get("merged_into"), "a merged source must stay readable and point at its target"
```

Run: `KNOVAS_API_URL=<dev url> python -m pytest tests/test_graph_contract_live.py --knovas-api -v`
Expected: PASS and the cassette gains the seven keys. If any route answers 404 with an empty body, the KnowledgeBase parts for §5.8/§5.9 are not deployed on dev yet — stop and report; never hand-edit the cassette.
Then run: `python -m pytest tests/test_graph_contract_parties.py -v` — Expected: PASS, 3 tests.

- [ ] **Step 11: Commit**

```bash
git add src/knovas_client.py src/graph_model.py tests/test_knovas_client_parties.py \
        tests/test_graph_model_parties.py tests/test_graph_contract_parties.py \
        tests/test_graph_contract_live.py tests/cassettes/graph_contract.json \
        tests/test_knovas_client_graph_sorting.py
git commit -m "feat(parties): client methods and typed rows for identifiers, duplicates, merge and conflict checks"
```

---

---

## PART KC-C — KnovasPlatform — Fristen (proposals, four-eyes, ICS feed) and Posteingang / events poller (E3, E4, E5, E6)

### Task KC-C-1: Client — facts listing, adopt, propose, events, transmission and job status; `Event` dataclass; cassettes

**Requirements:** E3, E4, E6

**Files:**
- Modify: `src/knovas_client.py` — add `SecureApiError` beside `KnowledgeGraphDisabled` (line 859 on `main`; after C-plan B2 it sits beside `GraphError`); add `_secured_json` directly after `_request_no_retry` (lines 1324-1348); append the six public methods after the last `graph_*` method (after `graph_restore_placement`, lines 1707-1712 on `main`; after C-plan B5/B6 the last one is `graph_sort_document`).
- Modify: `src/graph_model.py` (created by C-plan B3) — append the `Event` dataclass.
- Test: `tests/test_knovas_client_events_facts.py`
- Test: `tests/test_graph_model_event.py`
- Create: `tests/test_events_contract_live.py` (marked `knovas_api`) → records `tests/cassettes/events_facts_contract.json`
- Modify: `tests/cassettes/README.md` (C-plan B1) — one paragraph on the second cassette.

**Interfaces:**
- Consumes: `KnovasAPIClient._make_request` (`src/knovas_client.py:1284`), `_graph_request` (`:1531`, GraphError-aware after C-plan B2), `_graph_payload_list` (`:867`), `GraphError` (defined in C-plan B2); test helpers `FakeResponse`, `FakeSession`, `make_secured_client` (`tests/test_knovas_client_hardening.py:63-131`). Backend routes `GET /secured/graph/facts?curation_status=&confirmation=&semantic_role=&node_type_id=&older_than=&limit=&offset=`, `POST /secured/graph/facts/<fact_id>/adopt`, `POST /secured/graph/facts/propose` (defined in Part KB-D); `GET /secured/events?after=&limit=&types=`, `GET /secured/transmissions/<transmission_key_id>/status`, `GET /secured/graph/jobs/<job_id>` (defined in Part KB-C).
- Produces on `KnovasAPIClient`:
  - `facts_list(**filters) -> dict` — `{'facts': list[dict], 'count': int, 'limit': int|None, 'offset': int}`; allowed keys exactly `curation_status, confirmation, semantic_role, node_type_id, older_than, limit, offset`; unknown key → `ValueError`.
  - `fact_adopt(fact_id: str, actor_ref: str | None) -> dict | None` — `POST /facts/<id>/adopt {actor_ref}`; `None` on 404; `GraphError` (409 `actor_required` / `four_eyes_required`) otherwise.
  - `fact_propose(node_id: str, value, *, attribute_id=None, label=None, evidence: list[dict], confidence: float | None = None) -> dict | None` — `POST /facts/propose`; evidence items `{chunk_id, char_start?, char_end?, quote?}`; empty evidence → `ValueError`.
  - `events_poll(after: int = 0, limit: int = 200, types: list[str] | None = None) -> dict` — `{'events': list[dict], 'next_after': int, 'has_more': bool}`.
  - `transmission_status(transmission_key_id: str) -> dict | None` — status object (`status`, `pointer`, `attempts`, `error`, `updated_at`) or `None` on 404.
  - `graph_job(job_id: str) -> dict | None` — job object (`id`, `kind`, `target_id`, `status`, `total`, `done_count`, `failed_count`, `error`) or `None`.
  - `SecureApiError(status: int, error_code: str, message: str = '')`.
  - `graph_model.Event(seq, event_type, subject_type='', subject_id='', occurred_at='', payload={}, event_id='')` with `Event.from_api(raw) -> Event` and `.to_row() -> dict`.
  Tasks KC-C-2a/2b/3b/4b/5/6/7 call these.

- [ ] **Step 1: Write the failing client test**

Create `tests/test_knovas_client_events_facts.py`:

```python
"""Facts listing / adopt / propose, events pull, transmission and job status.

Same fakes as test_knovas_client_hardening (FakeSession records every call);
nothing touches the network.
"""
from __future__ import annotations

import pytest

from knovas_client import SecureApiError
from test_knovas_client_hardening import FakeResponse, FakeSession, make_secured_client


def _client_with(responder):
    client = make_secured_client()
    client._session = FakeSession(responder)
    return client


def test_facts_list_sends_only_known_filters_as_params():
    seen = {}

    def responder(method, url, **kw):
        seen["url"] = url
        seen["params"] = kw.get("params")
        return FakeResponse(200, {"status": "success", "facts": [{"id": "f1"}], "count": 1,
                                  "limit": 50, "offset": 0})

    client = _client_with(responder)
    result = client.facts_list(curation_status="extracted", semantic_role="deadline",
                               limit=50, offset=0, older_than=None)

    assert seen["url"].endswith("/secured/graph/facts")
    assert seen["params"] == {"curation_status": "extracted", "semantic_role": "deadline",
                              "limit": 50, "offset": 0}
    assert result == {"facts": [{"id": "f1"}], "count": 1, "limit": 50, "offset": 0}


def test_facts_list_rejects_an_unknown_filter():
    client = _client_with(lambda *a, **k: FakeResponse(200, {}))
    with pytest.raises(ValueError):
        client.facts_list(node_id="n1")


def test_fact_adopt_posts_the_actor_ref():
    seen = {}

    def responder(method, url, **kw):
        seen.update(method=method, url=url, json=kw.get("json"))
        return FakeResponse(200, {"status": "success",
                                  "fact": {"id": "f1", "curation_status": "manual"}})

    client = _client_with(responder)
    result = client.fact_adopt("f1", "user-42")

    assert seen["method"] == "POST"
    assert seen["url"].endswith("/secured/graph/facts/f1/adopt")
    assert seen["json"] == {"actor_ref": "user-42"}
    assert result["fact"]["curation_status"] == "manual"


def test_fact_propose_requires_evidence_and_sends_offsets():
    seen = {}

    def responder(method, url, **kw):
        seen.update(url=url, json=kw.get("json"))
        return FakeResponse(201, {"status": "success", "fact": {"id": "f9"}})

    client = _client_with(responder)
    with pytest.raises(ValueError):
        client.fact_propose("n1", {"value": "2026-03-31", "precision": "day"},
                            attribute_id="a1", evidence=[])

    client.fact_propose("n1", {"value": "2026-03-31", "precision": "day"}, attribute_id="a1",
                        evidence=[{"chunk_id": "c1", "char_start": 10, "char_end": 40,
                                   "quote": "innert 30 Tagen"}], confidence=0.8)

    assert seen["url"].endswith("/secured/graph/facts/propose")
    assert seen["json"] == {"node_id": "n1", "attribute_id": "a1",
                            "value": {"value": "2026-03-31", "precision": "day"},
                            "evidence": [{"chunk_id": "c1", "char_start": 10, "char_end": 40,
                                          "quote": "innert 30 Tagen"}],
                            "confidence": 0.8}


def test_events_poll_normalises_cursor_and_has_more():
    seen = {}

    def responder(method, url, **kw):
        seen.update(url=url, params=kw.get("params"))
        return FakeResponse(200, {"status": "success", "events": [
            {"seq": 11, "event_type": "document.indexed", "subject_type": "document",
             "subject_id": "d1", "payload": {}, "occurred_at": "2026-08-15T10:00:00Z"},
            {"seq": 12, "event_type": "graph.fact.proposed", "subject_type": "fact",
             "subject_id": "f1", "payload": {"node_id": "n1"},
             "occurred_at": "2026-08-15T10:00:01Z"}], "has_more": True})

    client = _client_with(responder)
    result = client.events_poll(after=10, limit=2,
                                types=["document.indexed", "graph.fact.proposed"])

    assert seen["url"].endswith("/secured/events")
    assert seen["params"] == {"after": 10, "limit": 2,
                              "types": "document.indexed,graph.fact.proposed"}
    assert result["next_after"] == 12
    assert result["has_more"] is True
    assert [e["seq"] for e in result["events"]] == [11, 12]


def test_events_poll_without_events_keeps_the_cursor():
    client = _client_with(lambda *a, **k: FakeResponse(200, {"status": "success",
                                                              "events": []}))
    assert client.events_poll(after=40) == {"events": [], "next_after": 40,
                                            "has_more": False}


def test_transmission_status_returns_none_on_404_and_the_object_otherwise():
    def responder(method, url, **kw):
        if url.endswith("/secured/transmissions/gone/status"):
            return FakeResponse(404, {"status": "error", "error": "not found"})
        return FakeResponse(200, {"status": "success", "message": "ok",
                                  "transmission": {"status": "indexed", "pointer": "a/b.pdf",
                                                   "attempts": 1, "error": None}})

    client = _client_with(responder)
    assert client.transmission_status("gone") is None
    assert client.transmission_status("k1")["status"] == "indexed"


def test_non_404_secured_error_carries_the_code():
    client = _client_with(lambda *a, **k: FakeResponse(
        429, {"status": "error", "error_code": "rate_limited", "message": "slow down"}))
    with pytest.raises(SecureApiError) as caught:
        client.events_poll(after=0)
    assert caught.value.status == 429
    assert caught.value.error_code == "rate_limited"


def test_graph_job_unwraps_the_job_key():
    client = _client_with(lambda *a, **k: FakeResponse(200, {
        "status": "success", "job": {"id": "j1", "status": "done", "total": 3,
                                     "done_count": 3, "failed_count": 0}}))
    assert client.graph_job("j1")["done_count"] == 3
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_knovas_client_events_facts.py -v`
Expected: FAIL with `ImportError: cannot import name 'SecureApiError' from 'knovas_client'`

- [ ] **Step 3: Implement `SecureApiError` and `_secured_json`**

In `src/knovas_client.py`, directly after `class KnowledgeGraphDisabled` (lines 859-865 on `main`; after C-plan B2 directly after `class GraphError`), add:

```python
class SecureApiError(RuntimeError):
    """A non-graph /secured/* call failed with an error_code the caller can act on.

    Sibling of GraphError for the events, transmission-status and export
    routes. 404 stays outside this type for the same reason: an unknown key
    is a normal state and returns None.
    """

    def __init__(self, status: int, error_code: str, message: str = ""):
        super().__init__(message or error_code or f"HTTP {status}")
        self.status = status
        self.error_code = error_code
```

Directly after `_request_no_retry` (ends at line 1348 on `main`), add:

```python
    def _secured_json(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """JSON call to a non-graph /secured/* route.

        404 -> None (unknown key, normal state). Any other HTTP error ->
        SecureApiError carrying the body's error_code, so a route can say
        "spaeter erneut" for 429/503 instead of "kaputt".
        """
        try:
            response = self._make_request(method=method, endpoint=endpoint,
                                          data=data, params=params)
        except requests.exceptions.HTTPError as exc:
            response = exc.response
            if response is None:
                raise
            body: Dict[str, Any] = {}
            try:
                body = response.json() or {}
            except ValueError:
                body = {}
            if response.status_code == 404:
                logger.info("Secure-API 404: %s %s", method, endpoint)
                return None
            raise SecureApiError(
                response.status_code,
                str(body.get('error_code') or ''),
                str(body.get('message') or body.get('error') or ''),
            ) from exc
        try:
            return response.json() or {}
        except ValueError:
            logger.warning("Secure-API-Antwort ohne JSON-Body: %s %s", method, endpoint)
            return {}
```

- [ ] **Step 4: Implement the six public methods**

Append after the last `graph_*` method of `KnovasAPIClient` (after `graph_restore_placement`, lines 1707-1712 on `main`):

```python
    # -- Fristen / Vier-Augen (Part A KB-D) --------------------------------

    FACT_LIST_FILTERS = ('curation_status', 'confirmation', 'semantic_role',
                         'node_type_id', 'older_than', 'limit', 'offset')

    def facts_list(self, **filters: Any) -> Dict[str, Any]:
        """GET /secured/graph/facts - mandantenweite, sichtbare Fakten.

        Die Pruefliste (extracted), die Vier-Augen-Warteschlange
        (confirmation=pending) und die Fristenliste (semantic_role=deadline)
        sind derselbe Endpunkt mit anderen Parametern.
        """
        unknown = set(filters) - set(self.FACT_LIST_FILTERS)
        if unknown:
            raise ValueError(f'unbekannte Filter fuer facts_list: {sorted(unknown)}')
        params = {k: v for k, v in filters.items() if v is not None and v != ''}
        payload = self._graph_request('GET', '/facts', params=params or None) or {}
        facts = _graph_payload_list(payload, 'facts')
        try:
            count = int(payload.get('count', len(facts)))
        except (TypeError, ValueError):
            count = len(facts)
        return {'facts': facts, 'count': count,
                'limit': payload.get('limit'), 'offset': int(payload.get('offset') or 0)}

    def fact_adopt(self, fact_id: str,
                   actor_ref: Optional[str]) -> Optional[Dict[str, Any]]:
        """POST /secured/graph/facts/<fid>/adopt - Vorschlag zum eigenen Fakt machen.

        Der uebernehmende Mensch ist der 'Erfasser'; die zweite Person
        bestaetigt. Ohne actor_ref antwortet die API 409 actor_required.
        """
        data = {'actor_ref': actor_ref} if actor_ref else None
        return self._graph_request(
            'POST', f'/facts/{quote(str(fact_id), safe="")}/adopt', data=data)

    def fact_propose(self, node_id: str, value: Any, *,
                     attribute_id: Optional[str] = None,
                     label: Optional[str] = None,
                     evidence: List[Dict[str, Any]],
                     confidence: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """POST /secured/graph/facts/propose - Vorschlag mit Belegstelle.

        Ohne Beleg kein Vorschlag (GI-FACT-02) - deshalb hier schon ein
        ValueError statt eines 422 aus der API.
        """
        cleaned = [dict(item) for item in (evidence or []) if isinstance(item, dict)
                   and str(item.get('chunk_id') or '').strip()]
        if not cleaned:
            raise ValueError('evidence muss mindestens einen chunk_id enthalten')
        payload: Dict[str, Any] = {'node_id': node_id, 'value': value, 'evidence': cleaned}
        if attribute_id:
            payload['attribute_id'] = attribute_id
        elif label:
            payload['label'] = label
        if confidence is not None:
            payload['confidence'] = float(confidence)
        return self._graph_request('POST', '/facts/propose', data=payload)

    # -- Ereignisse und Auftraege (Part A KB-C) -----------------------------

    def events_poll(self, after: int = 0, limit: int = 200,
                    types: Optional[List[str]] = None) -> Dict[str, Any]:
        """GET /secured/events?after=&limit=&types= - Cursor-Pull.

        Liefert immer {'events', 'next_after', 'has_more'}; ohne Ereignisse
        bleibt der Cursor stehen, damit ein leerer Lauf nichts ueberspringt.
        """
        params: Dict[str, Any] = {'after': int(after), 'limit': int(limit)}
        if types:
            params['types'] = ','.join(str(t) for t in types)
        payload = self._secured_json('GET', '/secured/events', params=params) or {}
        events = [e for e in (payload.get('events') or []) if isinstance(e, dict)]
        seqs = [int(e['seq']) for e in events if e.get('seq') is not None]
        next_after = payload.get('next_after')
        if next_after is None:
            next_after = max(seqs) if seqs else int(after)
        has_more = payload.get('has_more')
        if has_more is None:
            has_more = bool(events) and len(events) >= int(limit)
        return {'events': events, 'next_after': int(next_after), 'has_more': bool(has_more)}

    def transmission_status(self, transmission_key_id: str) -> Optional[Dict[str, Any]]:
        """GET /secured/transmissions/<key>/status - queued|running|indexed|failed|dead_lettered."""
        payload = self._secured_json(
            'GET', f'/secured/transmissions/{quote(str(transmission_key_id), safe="")}/status')
        if payload is None:
            return None
        for key in ('transmission', 'job', 'ingest_job'):
            if isinstance(payload.get(key), dict):
                return payload[key]
        # Flache Envelope: 'status'/'message' gehoeren der Envelope, nicht dem Auftrag.
        return {k: v for k, v in payload.items() if k not in ('status', 'message')}

    def graph_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        """GET /secured/graph/jobs/<id> - Fortschritt eines Filter-/Import-Auftrags."""
        payload = self._graph_request('GET', f'/jobs/{quote(str(job_id), safe="")}')
        if payload is None:
            return None
        if isinstance(payload.get('job'), dict):
            return payload['job']
        return {k: v for k, v in payload.items() if k not in ('status', 'message')}
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `python -m pytest tests/test_knovas_client_events_facts.py -v`
Expected: PASS, 9 tests.

- [ ] **Step 6: Write the failing `Event` dataclass test**

Create `tests/test_graph_model_event.py`:

```python
"""Event: the typed row the poller stores and the inbox renders."""
from __future__ import annotations

import pytest

from graph_model import Event


def test_from_api_reads_the_documented_fields():
    event = Event.from_api({"seq": "7", "id": "e-7", "event_type": "graph.fact.proposed",
                            "subject_type": "fact", "subject_id": "f1",
                            "payload": {"node_id": "n1"},
                            "occurred_at": "2026-08-15T10:00:00Z"})
    assert event.seq == 7
    assert event.event_id == "e-7"
    assert event.payload == {"node_id": "n1"}


def test_missing_seq_is_an_error_not_a_zero():
    with pytest.raises(ValueError):
        Event.from_api({"event_type": "x"})


def test_to_row_matches_the_events_table_columns():
    row = Event.from_api({"seq": 1, "event_type": "document.indexed",
                          "occurred_at": "2026-08-15T10:00:00Z"}).to_row()
    assert set(row) == {"seq", "event_type", "subject_type", "subject_id",
                        "payload", "occurred_at"}
    assert row["subject_type"] == ""
    assert row["payload"] == {}
```

- [ ] **Step 7: Run it to verify it fails**

Run: `python -m pytest tests/test_graph_model_event.py -v`
Expected: FAIL with `ImportError: cannot import name 'Event' from 'graph_model'`

- [ ] **Step 8: Implement `Event`**

Append to `src/graph_model.py` (after `decode_value`; extend the imports with `from dataclasses import dataclass, field` and `from typing import Any, Dict, List`):

```python
@dataclass(frozen=True)
class Event:
    """One row of GET /secured/events. Ids only - never document text (GI-EVENT-02)."""

    seq: int
    event_type: str
    subject_type: str = ""
    subject_id: str = ""
    occurred_at: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)
    event_id: str = ""

    @classmethod
    def from_api(cls, raw: Dict[str, Any]) -> "Event":
        try:
            seq = int(raw["seq"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("Ereignis ohne seq") from None
        payload = raw.get("payload")
        return cls(
            seq=seq,
            event_type=str(raw.get("event_type") or ""),
            subject_type=str(raw.get("subject_type") or ""),
            subject_id=str(raw.get("subject_id") or ""),
            occurred_at=str(raw.get("occurred_at") or ""),
            payload=dict(payload) if isinstance(payload, dict) else {},
            event_id=str(raw.get("id") or raw.get("event_id") or ""),
        )

    def to_row(self) -> Dict[str, Any]:
        """Column dict for events(seq, event_type, subject_type, subject_id, payload, occurred_at)."""
        return {"seq": self.seq, "event_type": self.event_type,
                "subject_type": self.subject_type, "subject_id": self.subject_id,
                "payload": self.payload, "occurred_at": self.occurred_at}
```

- [ ] **Step 9: Run it to verify it passes**

Run: `python -m pytest tests/test_graph_model_event.py tests/test_graph_model.py -v`
Expected: PASS, no regressions in the C-plan codec tests.

- [ ] **Step 10: Write the live recorder and document the cassette**

Create `tests/test_events_contract_live.py`:

```python
"""Records the live shapes of the events / facts routes KC-C depends on.

Skipped unless --knovas-api is passed (tests/conftest.py). Needs a dev tenant
with Part A KB-C and KB-D deployed and a node type carrying a
semantic_role='deadline' attribute.
"""
from __future__ import annotations

import json
import pathlib

import pytest

pytestmark = pytest.mark.knovas_api

CASSETTE = pathlib.Path(__file__).parent / "cassettes" / "events_facts_contract.json"


@pytest.fixture(scope="module")
def live_client():
    from knovas_client import KnovasAPIClient
    from config_loader import get_config
    return KnovasAPIClient(get_config())


def test_record_events_and_facts_contract(live_client):
    recorded = {}
    recorded["GET /secured/events"] = live_client.events_poll(after=0, limit=5)
    recorded["GET /secured/graph/facts?curation_status=extracted"] = live_client.facts_list(
        curation_status="extracted", semantic_role="deadline", limit=5)
    recorded["GET /secured/graph/facts?confirmation=pending"] = live_client.facts_list(
        confirmation="pending", limit=5)
    facts = recorded["GET /secured/graph/facts?confirmation=pending"]["facts"]
    if facts:
        recorded["GET /facts/<id>/history"] = live_client.graph_fact_history(facts[0]["id"])
        recorded["GET /facts/<id>/evidence"] = live_client.graph_fact_evidence(facts[0]["id"])
    CASSETTE.parent.mkdir(parents=True, exist_ok=True)
    CASSETTE.write_text(json.dumps(recorded, indent=2, ensure_ascii=False), encoding="utf-8")
    assert "events" in recorded["GET /secured/events"]
```

Append to `tests/cassettes/README.md`:

```markdown
## events_facts_contract.json

Recorded by `tests/test_events_contract_live.py` (`--knovas-api`) from a dev
tenant running Part A KB-C/KB-D: `GET /secured/events`, the tenant-wide facts
listing (extracted / pending), one fact history and one evidence list. The
deadlines and inbox tests read it when present and skip their shape
assertions when it has not been recorded yet — they never invent a shape.
```

- [ ] **Step 11: Record once against the dev tenant, then commit**

Run: `python -m pytest tests/test_events_contract_live.py --knovas-api -v`
Expected: PASS and `tests/cassettes/events_facts_contract.json` written. If the KB routes are not deployed yet, commit without the cassette and re-run this step when they are.

```bash
git add src/knovas_client.py src/graph_model.py tests/test_knovas_client_events_facts.py \
        tests/test_graph_model_event.py tests/test_events_contract_live.py tests/cassettes/
git commit -m "feat(deadlines): client methods for facts listing/adopt/propose, events pull, transmission and job status"
```

---

---

### Task KC-C-2a: `deadlines_view.py` — the Fristen queue composer (three tabs, matter widget, four-eyes rule)

**Requirements:** E3, E4

**Files:**
- Create: `src/deadlines_view.py`
- Test: `tests/test_deadlines_view.py`

**Interfaces:**
- Consumes: `KnovasAPIClient.facts_list`, (KC-C-1); `graph_fact_history(fact_id) -> list[dict]`, `graph_fact_evidence(fact_id) -> list[dict]`, `graph_facts(node_id) -> list[dict]`, `graph_node(node_id) -> dict|None` (C-plan B5/`main`), `graph_schema(type_id) -> list[dict]` (C-plan B4); `graph_model.decode_value` (C-plan B3). Fact rows from `GET /secured/graph/facts` (Part KB-D) carry the `kg_node_fact` columns (`id, node_id, attribute_id, label, value, curation_status, created_at, updated_at, provenance_pointer`) plus `node_name`, `attribute_name`, `datatype`, `semantic_role`, `confirmation_policy` and optionally `evidence[]`; evidence rows (`GET /facts/<id>/evidence`, Part KB-D-10) carry `chunk_id, pointer, page_number, sentence_number, quote, char_start, char_end`; history rows (`GET /facts/<id>/history`) carry `event_type, actor, actor_kind, actor_ref, occurred_at`; schema attributes (`GET /node-types/<id>/schema`, Part KB-D) carry `id, name, datatype, semantic_role, confirmation_policy`. Every read is tolerant: a missing `node_name` is looked up (cached), a missing `evidence` list is fetched per fact.
- Produces (module `deadlines_view`):
  - `HUMAN_ENTRY_EVENTS = ('fact_created', 'fact_updated', 'fact_adopted')`, `NO_ACCOUNT_REASON`, `SELF_CONFIRM_REASON` (German copy).
  - `actor_identity(event: dict) -> str | None` — `actor` for `actor_kind == 'subject'`, else `actor_ref`.
  - `last_human_actor(history: list[dict]) -> str | None`.
  - `viewer_href(pointer: str, page: int | None, quote: str | None) -> str` — `/viewer?doc=&path=&page=&snippet=` (route defined in Part KC-A).
  - `DeadlineQueue(client, *, ttl_seconds=300, now=None)` with `proposals(limit=100)`, `pending(actor_id, limit=100)`, `confirmed(limit=200)`, `for_matter(node_id, actor_id=None)`, `can_confirm(fact, actor_id) -> tuple[bool, str | None]`; each returned fact dict has keys `fact_id, node_id, matter_name, attribute_id, label, value, display, iso_date, precision, curation_status, confirmation_policy, semantic_role, created_at, updated_at, evidence[] (chunk_id, pointer, page_number, quote, char_start, char_end, viewer_href)`, and in `pending()` additionally `can_confirm, confirm_blocked_reason, entered_by_you`.
  Tasks KC-C-2b, KC-C-3b, KC-C-7 consume this.

- [ ] **Step 1: Write the failing test**

Create `tests/test_deadlines_view.py`:

```python
"""Composes the Fristen tabs from the tenant-wide facts listing.

The four-eyes rule is computed here so a route can disable a button with a
reason instead of letting the API answer 409 after the click.
"""
from __future__ import annotations

import pytest

from deadlines_view import (DeadlineQueue, NO_ACCOUNT_REASON, SELF_CONFIRM_REASON,
                            last_human_actor, viewer_href)

DEADLINE_FACT = {
    "id": "f1", "node_id": "n1", "node_name": "Mandat Meier", "attribute_id": "a1",
    "attribute_name": "Rechtsmittelfrist", "datatype": "date",
    "value": {"value": "2026-03-31", "precision": "day"},
    "curation_status": "extracted", "semantic_role": "deadline",
    "confirmation_policy": "four_eyes", "created_at": "2026-08-14T09:00:00Z",
    "updated_at": "2026-08-14T09:00:00Z",
}


class RecordingClient:
    def __init__(self):
        self.calls = []
        self.history = {"f1": [
            {"event_type": "fact_created", "actor": "tenant-uuid", "actor_kind": "system",
             "actor_ref": None, "occurred_at": "2026-08-14T09:00:00Z"},
            {"event_type": "fact_adopted", "actor": "tenant-uuid", "actor_kind": "client_ref",
             "actor_ref": "user-a", "occurred_at": "2026-08-14T10:00:00Z"},
            {"event_type": "tier_changed", "actor": "tenant-uuid", "actor_kind": "system",
             "actor_ref": None, "occurred_at": "2026-08-14T10:00:01Z"}]}

    def facts_list(self, **filters):
        self.calls.append(("facts_list", filters))
        fact = dict(DEADLINE_FACT)
        if filters.get("confirmation") == "pending":
            fact["curation_status"] = "manual"
        if filters.get("curation_status") == "confirmed":
            fact["curation_status"] = "confirmed"
        return {"facts": [fact], "count": 1, "limit": filters.get("limit"), "offset": 0}

    def graph_fact_history(self, fact_id):
        self.calls.append(("history", fact_id))
        return self.history.get(fact_id, [])

    def graph_fact_evidence(self, fact_id):
        self.calls.append(("evidence", fact_id))
        return [{"chunk_id": "c1", "pointer": "akten/meier/verfuegung.pdf", "page_number": 3,
                 "quote": "innert 30 Tagen seit Zustellung", "char_start": 120,
                 "char_end": 151, "stance": "supports"}]

    def graph_node(self, node_id):
        self.calls.append(("node", node_id))
        return {"node": {"id": node_id, "name": "Mandat Meier", "node_type_id": "t-mandat"},
                "assignments": [], "facts": []}

    def graph_schema(self, type_id, include_deprecated=False):
        self.calls.append(("schema", type_id))
        return [{"id": "a1", "name": "Rechtsmittelfrist", "datatype": "date",
                 "semantic_role": "deadline", "confirmation_policy": "four_eyes"},
                {"id": "a2", "name": "Mandant", "datatype": "entity_ref",
                 "semantic_role": "client", "confirmation_policy": "single"}]

    def graph_facts(self, node_id):
        self.calls.append(("facts", node_id))
        return [dict(DEADLINE_FACT, curation_status="confirmed"),
                {"id": "f2", "node_id": node_id, "attribute_id": "a2",
                 "value": {"node_id": "p1"}, "curation_status": "manual"}]


def test_last_human_actor_skips_system_events_and_reads_backwards():
    history = RecordingClient().history["f1"]
    assert last_human_actor(history) == "user-a"


def test_last_human_actor_prefers_the_verified_subject():
    history = [{"event_type": "fact_created", "actor": "sub-9", "actor_kind": "subject",
                "actor_ref": "spoofed"}]
    assert last_human_actor(history) == "sub-9"


def test_viewer_href_carries_doc_path_page_and_snippet():
    href = viewer_href("akten/meier/verfuegung.pdf", 3, "innert 30 Tagen")
    assert href.startswith("/viewer?")
    assert "doc=akten%2Fmeier%2Fverfuegung.pdf" in href
    assert "page=3" in href
    assert "snippet=innert+30+Tagen" in href


def test_proposals_carry_quote_page_and_a_viewer_link():
    queue = DeadlineQueue(RecordingClient())
    proposal = queue.proposals()[0]

    assert proposal["fact_id"] == "f1"
    assert proposal["matter_name"] == "Mandat Meier"
    assert proposal["display"] == "31.3.2026"
    assert proposal["evidence"][0]["quote"] == "innert 30 Tagen seit Zustellung"
    assert proposal["evidence"][0]["page_number"] == 3
    assert proposal["evidence"][0]["viewer_href"].startswith("/viewer?")


def test_proposals_query_the_extracted_deadline_listing():
    client = RecordingClient()
    DeadlineQueue(client).proposals(limit=25)
    assert ("facts_list", {"curation_status": "extracted", "semantic_role": "deadline",
                           "limit": 25, "offset": 0}) in client.calls


def test_the_entering_user_may_not_confirm():
    queue = DeadlineQueue(RecordingClient())
    pending = queue.pending(actor_id="user-a")[0]

    assert pending["can_confirm"] is False
    assert pending["confirm_blocked_reason"] == SELF_CONFIRM_REASON
    assert pending["entered_by_you"] is True


def test_a_second_person_may_confirm():
    pending = DeadlineQueue(RecordingClient()).pending(actor_id="user-b")[0]
    assert pending["can_confirm"] is True
    assert pending["confirm_blocked_reason"] is None
    assert pending["entered_by_you"] is False


def test_without_a_personal_account_four_eyes_facts_cannot_be_confirmed():
    pending = DeadlineQueue(RecordingClient()).pending(actor_id=None)[0]
    assert pending["can_confirm"] is False
    assert pending["confirm_blocked_reason"] == NO_ACCOUNT_REASON


def test_single_policy_facts_need_no_second_person():
    class SinglePolicy(RecordingClient):
        def facts_list(self, **filters):
            fact = dict(DEADLINE_FACT, confirmation_policy="single", curation_status="manual")
            return {"facts": [fact], "count": 1, "limit": None, "offset": 0}

    pending = DeadlineQueue(SinglePolicy()).pending(actor_id="user-a")[0]
    assert pending["can_confirm"] is True


def test_for_matter_groups_only_deadline_attributes():
    client = RecordingClient()
    widget = DeadlineQueue(client).for_matter("n1", actor_id="user-b")

    assert widget["matter"]["name"] == "Mandat Meier"
    assert [f["fact_id"] for f in widget["confirmed"]] == ["f1"]
    assert widget["proposals"] == [] and widget["pending"] == []
    assert ("schema", "t-mandat") in client.calls


def test_for_matter_unknown_node_is_none():
    class Missing(RecordingClient):
        def graph_node(self, node_id):
            return None

    assert DeadlineQueue(Missing()).for_matter("nope") is None


def test_node_names_are_cached_between_calls():
    class Nameless(RecordingClient):
        def facts_list(self, **filters):
            fact = {k: v for k, v in DEADLINE_FACT.items() if k != "node_name"}
            return {"facts": [fact, dict(fact, id="f3")], "count": 2, "limit": None,
                    "offset": 0}

    client = Nameless()
    DeadlineQueue(client, now=lambda: 1000.0).confirmed()
    assert [c for c in client.calls if c[0] == "node"] == [("node", "n1")]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_deadlines_view.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'deadlines_view'`

- [ ] **Step 3: Implement the module**

Create `src/deadlines_view.py`:

```python
"""Fristen: the three review tabs, the matter widget and the four-eyes rule.

Traegt bewusst kein Flask-Wissen (Muster: matter_view.py, C-plan C5).

Why the rule lives here and not only in the API: the API answers 409
four_eyes_required *after* the click. A lawyer should see the disabled
button and its reason *before* - the same fact history the API consults
tells us who entered the value.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlencode

from graph_model import decode_value

logger = logging.getLogger(__name__)

DEADLINE_ROLE = "deadline"
FOUR_EYES = "four_eyes"
HUMAN_ENTRY_EVENTS = ("fact_created", "fact_updated", "fact_adopted")

NO_ACCOUNT_REASON = ("Bestätigung erfordert ein persönliches Benutzerkonto — "
                     "mit dem gemeinsamen Firmenlogin ist keine zweite Person nachweisbar.")
SELF_CONFIRM_REASON = ("Sie haben diese Frist selbst erfasst — die Bestätigung muss "
                       "durch eine zweite Person erfolgen (Vier-Augen-Prinzip).")

DEFAULT_TTL_SECONDS = 300
MAX_SNIPPET_CHARS = 300


def actor_identity(event: Dict[str, Any]) -> Optional[str]:
    """Who acted: the verified subject when the API says so, else the client reference.

    actor_kind is the honesty label of the ledger (design §3): 'subject' means
    the API verified the person; 'client_ref' means we asserted it ourselves.
    'tenant'/'system' carry no person unless an actor_ref was recorded.
    """
    if event.get("actor_kind") == "subject":
        return str(event.get("actor") or "") or None
    return str(event.get("actor_ref") or "") or None


def last_human_actor(history: List[Dict[str, Any]]) -> Optional[str]:
    """The last person who entered/adopted/changed the value; None when unknown."""
    for event in reversed(history or []):
        if event.get("event_type") not in HUMAN_ENTRY_EVENTS:
            continue
        if event.get("actor_kind") == "system":
            continue
        return actor_identity(event)
    return None


def viewer_href(pointer: str, page: Optional[int], quote: Optional[str]) -> str:
    """Deep link into the viewer (Part KC-A: /viewer?doc=&path=&page=&snippet=)."""
    return "/viewer?" + urlencode({
        "doc": pointer or "",
        "path": pointer or "",
        "page": int(page) if page else 1,
        "snippet": (quote or "")[:MAX_SNIPPET_CHARS],
    })


class DeadlineQueue:
    def __init__(self, client: Any, *, ttl_seconds: int = DEFAULT_TTL_SECONDS,
                 now: Optional[Callable[[], float]] = None):
        self._client = client
        self._ttl = max(0, int(ttl_seconds))
        self._now = now or time.time
        self._names: Dict[str, Tuple[float, str]] = {}
        self._deadline_attrs: Dict[str, Tuple[float, set]] = {}

    # -- the three tabs -----------------------------------------------------

    def proposals(self, limit: int = 100) -> List[Dict[str, Any]]:
        rows = self._client.facts_list(curation_status="extracted",
                                       semantic_role=DEADLINE_ROLE,
                                       limit=int(limit), offset=0)["facts"]
        return [self._decorate(row, with_evidence=True) for row in rows]

    def pending(self, actor_id: Optional[str], limit: int = 100) -> List[Dict[str, Any]]:
        rows = self._client.facts_list(confirmation="pending",
                                       semantic_role=DEADLINE_ROLE,
                                       limit=int(limit), offset=0)["facts"]
        out = []
        for row in rows:
            fact = self._decorate(row, with_evidence=True)
            allowed, reason = self.can_confirm(fact, actor_id)
            fact["can_confirm"] = allowed
            fact["confirm_blocked_reason"] = reason
            fact["entered_by_you"] = reason == SELF_CONFIRM_REASON
            out.append(fact)
        return out

    def confirmed(self, limit: int = 200) -> List[Dict[str, Any]]:
        rows = self._client.facts_list(curation_status="confirmed",
                                       semantic_role=DEADLINE_ROLE,
                                       limit=int(limit), offset=0)["facts"]
        return [self._decorate(row, with_evidence=False) for row in rows]

    # -- the matter widget --------------------------------------------------

    def for_matter(self, node_id: str,
                   actor_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        detail = self._client.graph_node(node_id)
        if not detail:
            return None
        node = detail.get("node") if isinstance(detail.get("node"), dict) else detail
        deadline_attrs = self._deadline_attribute_ids(str(node.get("node_type_id") or ""))
        groups: Dict[str, List[Dict[str, Any]]] = {"proposals": [], "pending": [],
                                                   "confirmed": []}
        for row in self._client.graph_facts(node_id) or []:
            if str(row.get("attribute_id") or "") not in deadline_attrs:
                continue
            row = dict(row)
            row.setdefault("node_name", node.get("name"))
            row.setdefault("semantic_role", DEADLINE_ROLE)
            row.setdefault("datatype", "date")
            fact = self._decorate(row, with_evidence=False)
            status = fact["curation_status"]
            if status == "extracted":
                groups["proposals"].append(fact)
            elif status == "confirmed":
                groups["confirmed"].append(fact)
            elif status == "manual":
                allowed, reason = self.can_confirm(fact, actor_id)
                fact["can_confirm"] = allowed
                fact["confirm_blocked_reason"] = reason
                fact["entered_by_you"] = reason == SELF_CONFIRM_REASON
                groups["pending"].append(fact)
            # 'rejected' facts are tombstones: shown nowhere, on purpose.
        return {"matter": node, **groups}

    # -- the rule -----------------------------------------------------------

    def can_confirm(self, fact: Dict[str, Any],
                    actor_id: Optional[str]) -> Tuple[bool, Optional[str]]:
        if fact.get("confirmation_policy") != FOUR_EYES:
            return True, None
        if not actor_id:
            return False, NO_ACCOUNT_REASON
        history = self._client.graph_fact_history(fact["fact_id"]) or []
        entered_by = last_human_actor(history)
        if entered_by is not None and entered_by == str(actor_id):
            return False, SELF_CONFIRM_REASON
        return True, None

    # -- helpers ------------------------------------------------------------

    def _decorate(self, row: Dict[str, Any], *, with_evidence: bool) -> Dict[str, Any]:
        value = row.get("value")
        datatype = str(row.get("datatype") or "date")
        iso_date = ""
        precision = "day"
        if isinstance(value, dict):
            iso_date = str(value.get("value") or "")
            precision = str(value.get("precision") or "day")
        node_id = str(row.get("node_id") or "")
        fact = {
            "fact_id": str(row.get("id") or row.get("fact_id") or ""),
            "node_id": node_id,
            "matter_name": str(row.get("node_name") or "") or self._node_name(node_id),
            "attribute_id": str(row.get("attribute_id") or ""),
            "label": str(row.get("attribute_name") or row.get("label") or "Frist"),
            "value": value,
            "display": decode_value(datatype, value),
            "iso_date": iso_date,
            "precision": precision,
            "curation_status": str(row.get("curation_status") or ""),
            "confirmation_policy": str(row.get("confirmation_policy") or "single"),
            "semantic_role": str(row.get("semantic_role") or ""),
            "created_at": str(row.get("created_at") or ""),
            "updated_at": str(row.get("updated_at") or ""),
            "evidence": [],
        }
        if with_evidence:
            raw = row.get("evidence")
            if not isinstance(raw, list):
                raw = self._client.graph_fact_evidence(fact["fact_id"]) or []
            fact["evidence"] = [self._evidence_row(item) for item in raw
                                if isinstance(item, dict)]
        return fact

    @staticmethod
    def _evidence_row(item: Dict[str, Any]) -> Dict[str, Any]:
        pointer = str(item.get("pointer") or "")
        page = item.get("page_number")
        quote = str(item.get("quote") or "")
        return {"chunk_id": str(item.get("chunk_id") or ""), "pointer": pointer,
                "page_number": int(page) if page else None, "quote": quote,
                "char_start": item.get("char_start"), "char_end": item.get("char_end"),
                "viewer_href": viewer_href(pointer, page, quote) if pointer else ""}

    def _node_name(self, node_id: str) -> str:
        if not node_id:
            return ""
        cached = self._names.get(node_id)
        if cached and self._now() - cached[0] < self._ttl:
            return cached[1]
        name = ""
        try:
            detail = self._client.graph_node(node_id) or {}
            node = detail.get("node") if isinstance(detail.get("node"), dict) else detail
            name = str((node or {}).get("name") or "")
        except Exception:                              # noqa: BLE001
            logger.warning("Node name unavailable for %s", node_id, exc_info=True)
        self._names[node_id] = (self._now(), name)
        return name

    def _deadline_attribute_ids(self, node_type_id: str) -> set:
        if not node_type_id:
            return set()
        cached = self._deadline_attrs.get(node_type_id)
        if cached and self._now() - cached[0] < self._ttl:
            return cached[1]
        ids = {str(a.get("id")) for a in (self._client.graph_schema(node_type_id) or [])
               if a.get("semantic_role") == DEADLINE_ROLE}
        self._deadline_attrs[node_type_id] = (self._now(), ids)
        return ids
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_deadlines_view.py -v`
Expected: PASS, 12 tests.

- [ ] **Step 5: Commit**

```bash
git add src/deadlines_view.py tests/test_deadlines_view.py
git commit -m "feat(deadlines): DeadlineQueue composes proposals, pending and confirmed tabs with the four-eyes rule"
```

---

---

## PART KC-D — KnovasPlatform — Cortex on the live graph (G1–G8)

### Task KC-D-1: Client — `node_ego`, `graph_import`, `EgoGraph`; cassette recording

**Requirements:** G2, G6

**Files:**
- Modify: `src/knovas_client.py` — append after `graph_restore_placement` (`src/knovas_client.py:1704-1707`)
- Modify: `src/graph_model.py` — append after `decode_value` (module created by C-plan B3)
- Modify: `tests/test_graph_contract_live.py` — append one recorder (file created by C-plan B1)
- Test: `tests/test_graph_model_ego.py`, `tests/test_knovas_client_graph_ego_import.py`

**Interfaces:**
- Consumes: `KnovasAPIClient._graph_request(method, path, data=None, params=None) -> Optional[dict]` (`src/knovas_client.py:1531`, GraphError-aware after C-plan B2), `_graph_payload_list` (`:867`); test helpers `FakeResponse`, `FakeSession`, `make_secured_client` from `tests/test_knovas_client_hardening.py:63-125`; backend `GET /secured/graph/nodes/<id>/ego?depth=1..3&limit=` → `{"status","message","nodes":[{id,name,node_type_id,hop,…}],"edges":[{id,node_lo,node_hi,relation,edge_source}],"truncated":bool,"depth_applied":int}` (defined in Part KB-F-8); `POST /secured/graph/imports {dry_run, nodes:[{ref,name,node_type_id,identifiers:[{text,kind}],facts:[{attribute_id,value}]}], edges:[{src_ref,dst_ref,relation}]}` (≤ 500 nodes) → dry-run `{"preview": {"nodes":{"create":n,"match":m,"conflict":c},"facts":{"create":n},"edges":{"create":n},"items":[{ref,action:"create"|"match"|"conflict"|"skip",node_id?,matched_by?,reason?}]}}`, apply → `{"job_id": "<uuid>"|null, "applied": {"nodes_created","nodes_matched","facts_created","edges_created"}}` (defined in Part KB-F-10; `ref`, `src_ref`/`dst_ref` and the entity_ref value `{"node_ref": "<ref>"}` are the in-payload references this Part sends — see notes).
- Produces: `graph_model.EgoNode(id, label, type_id, hop)`, `graph_model.EgoEdge(id, src, dst, relation, source)`, `graph_model.EgoGraph(anchor_id, nodes, edges, truncated, depth_applied)` with `EgoGraph.from_payload(payload: dict, anchor_id: str) -> EgoGraph` and `EgoGraph.to_cortex() -> dict` (`{"anchor","nodes":[{id,label,type,hop}],"edges":[{src,predicate,dst}],"truncated","depth_applied"}`); `graph_model.IMPORT_MAX_NODES = 500`; `KnovasAPIClient.node_ego(node_id, *, depth=1, limit=None) -> Optional[EgoGraph]` (None on 404); `KnovasAPIClient.graph_import(payload: dict, *, dry_run=True) -> Optional[dict]` (raises `ValueError` before any request when `payload["nodes"]` exceeds 500 or is not a list). Consumed by KC-D-3, KC-D-7.

- [ ] **Step 1: Write the failing model test**

Create `tests/test_graph_model_ego.py`:

```python
"""EgoGraph — the typed shape of GET /secured/graph/nodes/<id>/ego."""
from __future__ import annotations

from graph_model import EgoGraph

PAYLOAD = {
    "status": "success", "message": "Ego graph",
    "nodes": [
        {"id": "n1", "name": "Mandat Meier", "node_type_id": "t-akte", "hop": 0},
        {"id": "n2", "name": "Müller AG", "node_type_id": "t-partei", "hop": 1},
        {"id": "n3", "name": "Bezirksgericht", "node_type_id": "t-gericht", "hop": 1},
    ],
    "edges": [
        {"id": "e1", "node_lo": "n1", "node_hi": "n2", "relation": "Gegenpartei",
         "edge_source": "fact_derived"},
        {"id": "e2", "node_lo": "n3", "node_hi": "n1", "relation": "zuständig",
         "edge_source": "manual"},
        {"id": "e9", "node_lo": "n1", "node_hi": "n-hidden", "relation": "x"},
    ],
    "truncated": True, "depth_applied": 1,
}


def test_from_payload_keeps_hops_and_marks_the_anchor():
    ego = EgoGraph.from_payload(PAYLOAD, anchor_id="n1")

    assert ego.anchor_id == "n1"
    assert [(n.id, n.hop) for n in ego.nodes] == [("n1", 0), ("n2", 1), ("n3", 1)]
    assert ego.truncated is True and ego.depth_applied == 1


def test_edges_whose_endpoint_is_not_in_the_node_set_are_dropped():
    """The API filters nodes by visibility; an edge to a node it did not return
    would leak the shape of something the caller may not see."""
    ego = EgoGraph.from_payload(PAYLOAD, anchor_id="n1")

    assert [e.id for e in ego.edges] == ["e1", "e2"]


def test_anchor_missing_from_payload_is_added_with_hop_zero():
    payload = {"nodes": [{"id": "n2", "name": "Müller AG", "node_type_id": "t-partei", "hop": 1}],
               "edges": [], "truncated": False, "depth_applied": 1}
    ego = EgoGraph.from_payload(payload, anchor_id="n1")

    assert ego.nodes[0].id == "n1" and ego.nodes[0].hop == 0
    assert ego.nodes[0].label == ""          # unknown label stays empty, never invented


def test_to_cortex_uses_the_ontology_contract_field_names():
    cortex = EgoGraph.from_payload(PAYLOAD, anchor_id="n1").to_cortex()

    assert cortex["anchor"] == "n1"
    assert cortex["nodes"][1] == {"id": "n2", "label": "Müller AG", "type": "t-partei", "hop": 1}
    assert cortex["edges"][0] == {"src": "n1", "predicate": "Gegenpartei", "dst": "n2"}
    assert cortex["truncated"] is True and cortex["depth_applied"] == 1


def test_tolerates_alternative_field_names():
    payload = {"neighbors": [{"node_id": "n2", "label": "X", "type_id": "t", "hop": "1"}],
               "edges": [{"source": "n1", "target": "n2", "predicate": "hat"}]}
    ego = EgoGraph.from_payload(payload, anchor_id="n1")

    assert ego.nodes[-1].hop == 1 and ego.edges[0].relation == "hat"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_graph_model_ego.py -v`
Expected: FAIL with `ImportError: cannot import name 'EgoGraph' from 'graph_model'`

- [ ] **Step 3: Implement the dataclasses**

Append to `src/graph_model.py`:

```python
## -- Ego graph (G2) ----------------------------------------------------------
## One guarded call answers "what surrounds this matter?". The typed shape exists
## so that the Cortex source, the compass page and the tests agree on field
## names — the API rows are tolerant-read exactly once, here.

from dataclasses import dataclass, field

IMPORT_MAX_NODES = 500          # POST /secured/graph/imports ceiling per call


def _pick(row: Dict[str, Any], *keys: str, default: Any = "") -> Any:
    for key in keys:
        if isinstance(row, dict) and row.get(key) not in (None, ""):
            return row[key]
    return default


@dataclass(frozen=True)
class EgoNode:
    id: str
    label: str
    type_id: str
    hop: int


@dataclass(frozen=True)
class EgoEdge:
    id: str
    src: str
    dst: str
    relation: str
    source: str = ""


@dataclass
class EgoGraph:
    anchor_id: str
    nodes: List[EgoNode] = field(default_factory=list)
    edges: List[EgoEdge] = field(default_factory=list)
    truncated: bool = False
    depth_applied: int = 1

    @classmethod
    def from_payload(cls, payload: Dict[str, Any], anchor_id: str) -> "EgoGraph":
        raw_nodes = payload.get("nodes")
        if not isinstance(raw_nodes, list):
            raw_nodes = payload.get("neighbors") if isinstance(payload.get("neighbors"), list) else []
        nodes: List[EgoNode] = []
        for row in raw_nodes:
            if not isinstance(row, dict):
                continue
            node_id = str(_pick(row, "id", "node_id", "uuid"))
            if not node_id:
                continue
            try:
                hop = int(_pick(row, "hop", default=1))
            except (TypeError, ValueError):
                hop = 1
            type_value = _pick(row, "node_type_id", "type_id", "node_type", "type")
            if isinstance(type_value, dict):
                type_value = _pick(type_value, "id", "node_type_id", "uuid")
            nodes.append(EgoNode(id=node_id, label=str(_pick(row, "name", "label", "title")),
                                 type_id=str(type_value), hop=hop))
        if all(n.id != str(anchor_id) for n in nodes):
            # The anchor is the one node the caller already knows; an unknown
            # label stays empty rather than being guessed from the id.
            nodes.insert(0, EgoNode(id=str(anchor_id), label="", type_id="", hop=0))
        known = {n.id for n in nodes}
        edges: List[EgoEdge] = []
        for row in payload.get("edges") or []:
            if not isinstance(row, dict):
                continue
            src = str(_pick(row, "node_lo", "source", "src", "from_node_id"))
            dst = str(_pick(row, "node_hi", "target", "dst", "to_node_id"))
            relation = str(_pick(row, "relation", "predicate", "label", "type"))
            if not src or not dst or not relation or src not in known or dst not in known:
                continue
            edges.append(EgoEdge(id=str(_pick(row, "id", "edge_id", "uuid")), src=src, dst=dst,
                                 relation=relation, source=str(_pick(row, "edge_source"))))
        try:
            depth_applied = int(payload.get("depth_applied") or 1)
        except (TypeError, ValueError):
            depth_applied = 1
        return cls(anchor_id=str(anchor_id), nodes=nodes, edges=edges,
                   truncated=bool(payload.get("truncated")), depth_applied=depth_applied)

    def to_cortex(self) -> Dict[str, Any]:
        """Field names of the /api/ontology contract (id/label/type; src/predicate/dst)."""
        return {
            "anchor": self.anchor_id,
            "nodes": [{"id": n.id, "label": n.label, "type": n.type_id, "hop": n.hop}
                      for n in self.nodes],
            "edges": [{"src": e.src, "predicate": e.relation, "dst": e.dst} for e in self.edges],
            "truncated": self.truncated,
            "depth_applied": self.depth_applied,
        }
```

- [ ] **Step 4: Run it to verify it passes**

Run: `python -m pytest tests/test_graph_model_ego.py -v`
Expected: PASS, 5 tests.

- [ ] **Step 5: Write the failing client test**

Create `tests/test_knovas_client_graph_ego_import.py`:

```python
"""node_ego and graph_import over a fake session (house pattern:
tests/test_knovas_client_hardening.py)."""
from __future__ import annotations

import pytest

from graph_model import EgoGraph
from test_knovas_client_hardening import FakeResponse, FakeSession, make_secured_client

EGO = {"status": "success", "message": "Ego graph",
       "nodes": [{"id": "n1", "name": "Mandat Meier", "node_type_id": "t1", "hop": 0},
                 {"id": "n2", "name": "Müller AG", "node_type_id": "t2", "hop": 1}],
       "edges": [{"id": "e1", "node_lo": "n1", "node_hi": "n2", "relation": "Gegenpartei"}],
       "truncated": False, "depth_applied": 1}


def _client(responder):
    client = make_secured_client()
    client._session = FakeSession(responder)
    return client


def test_node_ego_sends_depth_and_limit_and_returns_the_typed_graph():
    seen = {}

    def responder(method, url, **kw):
        seen.update(method=method, url=url, params=kw.get("params"))
        return FakeResponse(200, EGO)

    ego = _client(responder).node_ego("n1", depth=2, limit=40)

    assert seen["method"] == "GET" and seen["url"].endswith("/secured/graph/nodes/n1/ego")
    assert seen["params"] == {"depth": 2, "limit": 40}
    assert isinstance(ego, EgoGraph)
    assert [n.id for n in ego.nodes] == ["n1", "n2"]
    assert ego.edges[0].relation == "Gegenpartei"


def test_node_ego_clamps_depth_to_the_traversal_cap():
    seen = {}

    def responder(method, url, **kw):
        seen["params"] = kw.get("params")
        return FakeResponse(200, EGO)

    _client(responder).node_ego("n1", depth=9)

    assert seen["params"] == {"depth": 3}


def test_node_ego_unknown_or_foreign_id_is_none_not_an_error():
    assert _client(lambda m, u, **kw: FakeResponse(404, {})).node_ego("nope") is None


def test_graph_import_sends_dry_run_in_the_body():
    seen = {}

    def responder(method, url, **kw):
        seen.update(method=method, url=url, json=kw.get("json"))
        return FakeResponse(200, {"status": "success", "preview": {"items": []}})

    payload = {"nodes": [{"ref": "r1", "name": "Akte 2026-042", "node_type_id": "t1",
                          "identifiers": [{"text": "2026-042", "kind": "matter_number"}],
                          "facts": []}], "edges": []}
    result = _client(responder).graph_import(payload, dry_run=True)

    assert seen["method"] == "POST" and seen["url"].endswith("/secured/graph/imports")
    assert seen["json"]["dry_run"] is True
    assert seen["json"]["nodes"][0]["ref"] == "r1"
    assert result["preview"] == {"items": []}


def test_graph_import_apply_flag_is_explicit_false():
    seen = {}

    def responder(method, url, **kw):
        seen["json"] = kw.get("json")
        return FakeResponse(200, {"status": "success", "job_id": None,
                                  "applied": {"nodes_created": 1}})

    _client(responder).graph_import({"nodes": [], "edges": []}, dry_run=False)

    assert seen["json"]["dry_run"] is False


def test_graph_import_refuses_more_than_500_nodes_before_any_request():
    calls = []
    client = _client(lambda m, u, **kw: calls.append(u) or FakeResponse(200, {}))

    with pytest.raises(ValueError):
        client.graph_import({"nodes": [{"name": f"n{i}"} for i in range(501)], "edges": []})

    assert calls == []
```

- [ ] **Step 6: Run it to verify it fails**

Run: `python -m pytest tests/test_knovas_client_graph_ego_import.py -v`
Expected: FAIL with `AttributeError: 'KnovasAPIClient' object has no attribute 'node_ego'`

- [ ] **Step 7: Implement the two client methods**

Append to `src/knovas_client.py` directly after `graph_restore_placement` (`:1704-1707`):

```python
    # -- Ego-Graph und Import (Pflichtenheft G2, G6) ----------------------

    def node_ego(self, node_id: str, *, depth: int = 1,
                 limit: Optional[int] = None):
        """GET /secured/graph/nodes/<id>/ego - Nachbarschaft samt Kanten in einem Aufruf.

        Ersetzt neighbors + N x edges. depth ist auf den Traversal-Deckel 3
        begrenzt (GI-GRAPH-04); truncated sagt, ob der Server gekappt hat.
        Unbekannte oder fremde Id -> None (404 bleibt 404).
        """
        from graph_model import EgoGraph
        params: Dict[str, Any] = {'depth': max(1, min(3, int(depth)))}
        if limit is not None:
            params['limit'] = max(1, int(limit))
        payload = self._graph_request(
            'GET', f'/nodes/{quote(str(node_id), safe="")}/ego', params=params)
        if payload is None:
            return None
        return EgoGraph.from_payload(payload, anchor_id=str(node_id))

    def graph_import(self, payload: Dict[str, Any], *,
                     dry_run: bool = True) -> Optional[Dict[str, Any]]:
        """POST /secured/graph/imports - Massenimport mit Vorschau.

        dry_run=True liefert den Diff (nichts wird geschrieben); dry_run=False
        schreibt in einer Transaktion und nennt bei grossen Importen eine
        job_id. Die 500-Knoten-Grenze wird hier geprueft, bevor irgendetwas
        das Haus verlaesst.
        """
        from graph_model import IMPORT_MAX_NODES
        nodes = payload.get('nodes')
        if not isinstance(nodes, list):
            raise ValueError('nodes muss eine Liste sein')
        if len(nodes) > IMPORT_MAX_NODES:
            raise ValueError(f'Hoechstens {IMPORT_MAX_NODES} Knoten je Aufruf')
        body = dict(payload)
        body['dry_run'] = bool(dry_run)
        body.setdefault('edges', [])
        return self._graph_request('POST', '/imports', data=body)
```

- [ ] **Step 8: Run it to verify it passes**

Run: `python -m pytest tests/test_knovas_client_graph_ego_import.py tests/test_graph_model_ego.py -v`
Expected: PASS, 11 tests.

- [ ] **Step 9: Record the two live shapes into the cassette**

Append to `tests/test_graph_contract_live.py` (marked `knovas_api`, skipped without `--knovas-api`):

```python
def test_record_ego_and_import_contract(live_client):
    """Adds 'GET /nodes/<id>/ego' and 'POST /imports (dry_run)' to the cassette
    without touching the keys B1 recorded."""
    recorded = json.loads(CASSETTE.read_text(encoding="utf-8")) if CASSETTE.exists() else {}
    created_type = live_client.graph_create_node_type("Egotest")
    type_id = created_type["node_type"]["id"]
    anchor = live_client.graph_create_node("Ego-Anker", node_type_id=type_id)["node"]["id"]
    other = live_client.graph_create_node("Ego-Nachbar", node_type_id=type_id)["node"]["id"]
    live_client.graph_create_edge(anchor, other, "kennt")

    ego_payload = live_client._graph_request("GET", f"/nodes/{anchor}/ego",
                                             params={"depth": 1, "limit": 10})
    recorded["GET /nodes/<id>/ego"] = ego_payload
    assert {"nodes", "edges", "truncated", "depth_applied"} <= set(ego_payload), \
        "ego contract moved — update graph_model.EgoGraph.from_payload deliberately"

    preview = live_client.graph_import({"nodes": [
        {"ref": "r1", "name": "Import-Probe", "node_type_id": type_id,
         "identifiers": [{"text": "PROBE-1", "kind": "matter_number"}], "facts": []}],
        "edges": []}, dry_run=True)
    recorded["POST /imports (dry_run)"] = preview
    assert "preview" in preview, "import preview contract moved — update graph_import.py"

    CASSETTE.write_text(json.dumps(recorded, indent=2, ensure_ascii=False), encoding="utf-8")
    live_client.graph_delete_node(other)
    live_client.graph_delete_node(anchor)
    live_client.graph_delete_node_type(type_id)
```

Run once against the dev tenant (after KB-F-8 and KB-F-10 are deployed there):
`python -m pytest tests/test_graph_contract_live.py -k ego_and_import --knovas-api -v` — Expected: PASS and two new keys in `tests/cassettes/graph_contract.json`. If the ego payload has no `edges` key, stop: KB-F-8 has not landed and KC-D-3 must wait.

- [ ] **Step 10: Commit**

```bash
git add src/knovas_client.py src/graph_model.py tests/test_graph_model_ego.py \
        tests/test_knovas_client_graph_ego_import.py tests/test_graph_contract_live.py \
        tests/cassettes/graph_contract.json
git commit -m "feat(cortex): node_ego and graph_import client methods with a typed EgoGraph"
```

---

---

### Task KC-D-2: G1 — graph mode as the deploy default, honesty badges in sidebar and settings

**Requirements:** G1, G9

**Files:**
- Modify: `KnovasPlatform/.env.example:80-101` (Cortex block), `KnovasPlatform/docker-compose.yml:78-83`, `knovas.env.example` (repo root, 14 lines), `scripts/lib/expand_knovas_env.sh:20-28` and `:96-124`, `scripts/lib/test_expand_knovas_env.sh`
- Modify: `src/web_interface/app.py:1009-1014` (`_sidebar_context`), add a context processor right after it, `:1051-1063` (`settings_page`)
- Modify: `src/web_interface/templates/_sidebar.html:56-64`, `src/web_interface/templates/settings.html:45-63`
- Modify: `src/web_interface/static/css/style.css` (append after `.app-user-role`, `:1206`)
- Create: `tests/fixtures/graph_app.py`
- Test: `tests/test_honesty_badges.py`, `tests/test_deploy_defaults.py`

**Interfaces:**
- Consumes: `_ontology_source_is_graph()` (`app.py:1683`), `_search_use_test_results()` (`app.py:472`), `_static_asset_version()` (`:601`), `_ensure_csrf_token()` (`:827`).
- Produces: `tests/fixtures/graph_app.py::GraphStubClient` (class attribute `last` = the instance `create_app` built; every `graph_*`/`node_ego`/`graph_import` method returns configurable list/dict attributes: `node_types`, `nodes`, `edges`, `schemas: dict[type_id, list]`, `facts: dict[node_id, list]`, `evidence: dict[fact_id, list]`, `trust: dict[fact_id, dict]`, `node_trust: dict[node_id, dict]`, `ego: dict[node_id, dict]`, `import_preview`, `import_apply`, `jobs: dict[job_id, dict]`, `report_pages: dict[kind, dict]`, `filters`, `placements`, `fail_with: Exception|None`; records `calls: list[tuple]`); `build_graph_app(tmp_path, monkeypatch, client_cls=GraphStubClient, *, ontology_source="graph", env=None) -> Flask`; `login(client)`; `csrf_header(client) -> dict`. Template context keys available on every page via context processor: `honesty_badges: list[{key,label,title}]`, `company_name`, `feedback_url`, `app_title`, `brand`, `asset_version`, `csrf_token`. Deploy default `ONTOLOGY_SOURCE=graph` in the three config sources. Consumed by every later KC-D task's tests.

- [ ] **Step 1: Write the shared graph-mode app builder**

Create `tests/fixtures/graph_app.py`:

```python
"""Builds the Platform app in graph mode (ONTOLOGY_SOURCE=graph) around a stub
Knovas client — the fixture every Cortex-live test in this part uses.

Same construction as tests/test_ontology_api.py::_build_app (inline YAML in
tmp_path, env via monkeypatch, KnovasAPIClient monkeypatched), extended with a
stub whose graph responses each test can set before it makes a request.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


class GraphStubClient:
    """Stands in for KnovasAPIClient. GraphStubClient.last is the instance the
    app built; tests set its attributes and inspect .calls."""

    last: Optional["GraphStubClient"] = None

    def __init__(self, config):
        GraphStubClient.last = self
        self.config = config
        self.calls: List[tuple] = []
        self.fail_with: Optional[Exception] = None
        self.node_types = [{"id": "t-akte", "name": "Akte"}, {"id": "t-partei", "name": "Partei"}]
        self.nodes = [
            {"id": "n-1", "name": "Mandat Meier", "node_type_id": "t-akte",
             "assignments": [{"pointer": "akten/meier/klage.pdf"}]},
            {"id": "n-2", "name": "Müller AG", "node_type_id": "t-partei", "assignments": []},
        ]
        self.edges = [{"id": "e-1", "node_lo": "n-1", "node_hi": "n-2", "relation": "Gegenpartei"}]
        self.schemas: Dict[str, List[Dict[str, Any]]] = {"t-akte": [], "t-partei": []}
        self.facts: Dict[str, List[Dict[str, Any]]] = {}
        self.evidence: Dict[str, List[Dict[str, Any]]] = {}
        self.trust: Dict[str, Dict[str, Any]] = {}
        self.node_trust: Dict[str, Dict[str, Any]] = {}
        self.ego: Dict[str, Dict[str, Any]] = {}
        self.import_preview: Dict[str, Any] = {"preview": {"nodes": {"create": 0, "match": 0, "conflict": 0},
                                                           "facts": {"create": 0}, "edges": {"create": 0},
                                                           "items": []}}
        self.import_apply: Dict[str, Any] = {"job_id": None, "applied": {"nodes_created": 0}}
        self.jobs: Dict[str, Dict[str, Any]] = {}
        self.report_pages: Dict[str, Dict[str, Any]] = {}
        self.filters: Dict[str, List[Dict[str, Any]]] = {}
        self.placements: Dict[str, List[Dict[str, Any]]] = {}
        self.created_attributes: List[Dict[str, Any]] = []
        self.deprecated_attributes: List[tuple] = []
        self.applied: List[str] = []
        self.evaluated: List[tuple] = []
        self.rejected: List[str] = []
        self.restored: List[str] = []

    def _maybe_fail(self):
        if self.fail_with is not None:
            raise self.fail_with

    def health_check(self):
        return True

    def search_documents(self, query, limit=20, filters=None, **kwargs):
        return {"results": [], "total": 0}

    # -- topology (contract of ontology_graph.GraphOntologySource) --------
    def graph_export(self):
        self._maybe_fail()
        return {"status": "success", "node_types": self.node_types, "nodes": self.nodes,
                "edges": self.edges}

    def graph_node_types(self):
        return self.node_types

    def graph_nodes(self, node_type_id=None, q=None):
        self.calls.append(("graph_nodes", node_type_id, q))
        rows = self.nodes
        if node_type_id:
            rows = [n for n in rows if n.get("node_type_id") == node_type_id]
        if q:
            rows = [n for n in rows if q.lower() in str(n.get("name", "")).lower()]
        return rows

    def graph_node(self, node_id):
        self._maybe_fail()
        for node in self.nodes:
            if node["id"] == node_id:
                return {"status": "success", "node": node,
                        "assignments": node.get("assignments", [])}
        return None

    def graph_edges(self):
        return self.edges

    def graph_neighbors(self, node_id, depth=1):
        return []

    def graph_schema(self, type_id, include_deprecated=False):
        self.calls.append(("graph_schema", type_id))
        return list(self.schemas.get(type_id, []))

    def graph_create_schema_attribute(self, type_id, name, datatype, required=False,
                                      enum_values=None, target_node_type_id=None,
                                      description=None, sort_order=0):
        self._maybe_fail()
        row = {"id": f"a-{len(self.created_attributes) + 1}", "name": name, "datatype": datatype,
               "target_node_type_id": target_node_type_id, "required": required}
        self.created_attributes.append(row)
        self.schemas.setdefault(type_id, []).append(row)
        return {"status": "success", "attribute": row}

    def graph_deprecate_schema_attribute(self, type_id, attribute_id):
        self.deprecated_attributes.append((type_id, attribute_id))
        return {"status": "success"}

    def graph_create_node_type(self, name):
        row = {"id": f"t-{len(self.node_types) + 1}", "name": name}
        self.node_types.append(row)
        return {"status": "success", "node_type": row}

    def graph_create_node(self, name, node_type_id=None):
        row = {"id": f"n-{len(self.nodes) + 1}", "name": name, "node_type_id": node_type_id,
               "assignments": []}
        self.nodes.append(row)
        return {"status": "success", "node": row}

    def graph_create_edge(self, node_lo, node_hi, relation):
        row = {"id": f"e-{len(self.edges) + 1}", "node_lo": node_lo, "node_hi": node_hi,
               "relation": relation}
        self.edges.append(row)
        return {"status": "success", "edge": row}

    def graph_delete_edge(self, edge_id):
        return {"status": "success"}

    def graph_delete_node(self, node_id):
        return {"status": "success"}

    def graph_delete_node_type(self, type_id):
        return {"status": "success"}

    def graph_assign_knowledge(self, node_id, pointer):
        return {"status": "success"}

    # -- facts / evidence / trust (C-plan B5 names) ------------------------
    def graph_facts(self, node_id):
        self._maybe_fail()
        return list(self.facts.get(node_id, []))

    def graph_fact_evidence(self, fact_id):
        self.calls.append(("graph_fact_evidence", fact_id))
        return list(self.evidence.get(fact_id, []))

    def graph_fact_trust(self, fact_id):
        self.calls.append(("graph_fact_trust", fact_id))
        return self.trust.get(fact_id)

    def graph_node_trust(self, node_id):
        self.calls.append(("graph_node_trust", node_id))
        return self.node_trust.get(node_id)

    # -- ego / import / jobs (KC-D-1, KC-C) -------------------------------
    def node_ego(self, node_id, *, depth=1, limit=None):
        self._maybe_fail()
        self.calls.append(("node_ego", node_id, depth, limit))
        payload = self.ego.get(node_id)
        if payload is None:
            return None
        from graph_model import EgoGraph
        return EgoGraph.from_payload(payload, anchor_id=node_id)

    def graph_import(self, payload, *, dry_run=True):
        self._maybe_fail()
        self.calls.append(("graph_import", dry_run, json.loads(json.dumps(payload))))
        return dict(self.import_preview if dry_run else self.import_apply)

    def graph_job(self, job_id):
        self.calls.append(("graph_job", job_id))
        return self.jobs.get(job_id)

    # -- reports (KC-D-6) --------------------------------------------------
    def graph_report_page(self, kind, *, node_type_id=None, limit=50, offset=0):
        self.calls.append(("graph_report_page", kind, node_type_id, limit, offset))
        return dict(self.report_pages.get(kind) or {"items": [], "total": 0, "limit": limit,
                                                    "offset": offset})

    # -- filters (KC-D-9) --------------------------------------------------
    def graph_filters(self, node_id):
        self._maybe_fail()
        return list(self.filters.get(node_id, []))

    def graph_create_filter(self, node_id, query_text, child_node_name):
        self._maybe_fail()
        row = {"id": f"f-{sum(len(v) for v in self.filters.values()) + 1}",
               "query_text": query_text, "child_node_id": f"c-{node_id}"}
        self.filters.setdefault(node_id, []).append(row)
        return {"status": "success", "filter": row}

    def graph_placements(self, node_id, status="active", filter_id=None):
        return [p for p in self.placements.get(node_id, [])
                if p.get("_status", "active") == status]

    def graph_reject_placement(self, placement_id):
        self.rejected.append(placement_id)
        for rows in self.placements.values():
            for row in rows:
                if row["id"] == placement_id:
                    row["_status"] = "rejected"
                    return {"status": "success"}
        return None

    def graph_restore_placement(self, placement_id):
        self.restored.append(placement_id)
        for rows in self.placements.values():
            for row in rows:
                if row["id"] == placement_id:
                    row["_status"] = "active"
                    return {"status": "success"}
        return None

    def graph_evaluate_filters(self, node_id, pointer):
        self._maybe_fail()
        self.evaluated.append((node_id, pointer))
        return {"evaluation": {}}

    def graph_apply_filters(self, node_id):
        self._maybe_fail()
        self.applied.append(node_id)
        return {"job_id": "job-1", "documents": 3}


class _TmpAutodocHandler:
    def __init__(self, root):
        self.autodoc_path = str(root)


def build_graph_app(tmp_path, monkeypatch, client_cls=GraphStubClient, *,
                    ontology_source="graph", env=None):
    monkeypatch.setenv("WEB_SECRET_KEY", "test-secret-cortex-live")
    monkeypatch.setenv("COMPANY_LOGIN_ENABLED", "true")
    monkeypatch.setenv("COMPANY_DISPLAY_NAME", "Test Company")
    monkeypatch.setenv("COMPANY_LOGIN_NAME", "office")
    monkeypatch.setenv("COMPANY_LOGIN_PASSWORD", "s3cret")
    monkeypatch.setenv("ONTOLOGY_SOURCE", ontology_source)
    monkeypatch.delenv("SEARCH_USE_TEST_RESULTS", raising=False)
    monkeypatch.delenv("AUTODOC_IDENTIFIER_PREFIX", raising=False)
    monkeypatch.setenv("ONTOLOGY_FILTER_STATE_PATH", str(tmp_path / "filter_state.json"))
    fixture_path = tmp_path / "ontology_fixture.json"
    fixture_path.write_text(json.dumps({"types": [], "relations": [], "entities": [],
                                        "entity_relations": [], "evidence": []}),
                            encoding="utf-8")
    monkeypatch.setenv("ONTOLOGY_FIXTURE_PATH", str(fixture_path))
    for key, value in (env or {}).items():
        monkeypatch.setenv(key, value)
    import ontology_store
    ontology_store._cache = None
    import ontology_filters
    ontology_filters._engine_cache = None

    autodoc = tmp_path / "autodoc"
    autodoc.mkdir(exist_ok=True)
    ad_str = str(autodoc).replace("\\", "/")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""
web:
  secret_key: "${{WEB_SECRET_KEY}}"
  session_lifetime: 3600
  login:
    enabled: "${{COMPANY_LOGIN_ENABLED:-true}}"
    company_name: "${{COMPANY_DISPLAY_NAME:-Knovas}}"
    username: "${{COMPANY_LOGIN_NAME}}"
    password: "${{COMPANY_LOGIN_PASSWORD}}"
  search:
    results_per_page: 20
api:
  base_url: "http://example.test"
open:
  companion_enabled: false
  local_root: "{ad_str}"
""",
        encoding="utf-8",
    )
    import web_interface.app as web_app
    monkeypatch.setattr(web_app, "KnovasAPIClient", client_cls)
    monkeypatch.setattr(web_app, "AutoDocFileHandler", lambda: _TmpAutodocHandler(autodoc))
    flask_app = web_app.create_app(str(config_path))
    flask_app.config.update(TESTING=True)
    return flask_app


def login(client):
    client.get("/login")
    with client.session_transaction() as sess:
        token = sess["csrf_token"]
    client.post("/login", data={"login_name": "office", "password": "s3cret",
                                "csrf_token": token})


def csrf_header(client):
    with client.session_transaction() as sess:
        return {"X-CSRF-Token": sess["csrf_token"]}
```

- [ ] **Step 2: Write the failing badge tests**

Create `tests/test_honesty_badges.py`:

```python
"""G1/G9: the sidebar and the settings page say when a screen runs on demo data."""
from __future__ import annotations

from fixtures.graph_app import GraphStubClient, build_graph_app, login


def test_fixture_mode_shows_the_demo_badge_in_the_sidebar(tmp_path, monkeypatch):
    app = build_graph_app(tmp_path, monkeypatch, ontology_source="fixture")
    client = app.test_client()
    login(client)

    html = client.get("/ontology").get_data(as_text=True)

    assert "Demo-Daten (Fixture)" in html
    assert 'data-badge="cortex-fixture"' in html


def test_graph_mode_shows_no_demo_badge(tmp_path, monkeypatch):
    app = build_graph_app(tmp_path, monkeypatch, ontology_source="graph")
    client = app.test_client()
    login(client)

    html = client.get("/ontology").get_data(as_text=True)

    assert "Demo-Daten (Fixture)" not in html


def test_search_test_results_show_the_testdaten_badge(tmp_path, monkeypatch):
    app = build_graph_app(tmp_path, monkeypatch, env={"SEARCH_USE_TEST_RESULTS": "true"})
    client = app.test_client()
    login(client)

    html = client.get("/").get_data(as_text=True)

    assert 'data-badge="search-test"' in html and "Testdaten" in html


def test_settings_page_names_both_sources(tmp_path, monkeypatch):
    app = build_graph_app(tmp_path, monkeypatch, ontology_source="fixture",
                          env={"SEARCH_USE_TEST_RESULTS": "true"})
    client = app.test_client()
    login(client)

    html = client.get("/settings").get_data(as_text=True)

    assert "Cortex-Quelle" in html and "Fixture (Demo-Daten)" in html
    assert "Suche" in html and "Testdaten (SEARCH_USE_TEST_RESULTS)" in html


def test_settings_page_in_graph_mode_says_live(tmp_path, monkeypatch):
    app = build_graph_app(tmp_path, monkeypatch, ontology_source="graph")
    client = app.test_client()
    login(client)

    html = client.get("/settings").get_data(as_text=True)

    assert "Wissensgraph (live)" in html and "Knovas (live)" in html
```

- [ ] **Step 3: Run them to verify they fail**

Run: `python -m pytest tests/test_honesty_badges.py -v`
Expected: FAIL — `AssertionError` on `"Demo-Daten (Fixture)" in html` (badge markup absent).

- [ ] **Step 4: Add the badges to the sidebar context and a context processor**

In `src/web_interface/app.py` replace `_sidebar_context` (`:1009-1014`) with:

```python
    def _honesty_badges() -> List[Dict[str, str]]:
        """Sichtbare Marker, sobald ein Bildschirm auf Demo- oder Testdaten laeuft (G9).

        Pro Anfrage ausgewertet, wie _ontology_source_is_graph selbst: ein
        Umschalten der Umgebung braucht keinen Neustart der Oberflaeche.
        """
        badges: List[Dict[str, str]] = []
        if not _ontology_source_is_graph():
            badges.append({
                'key': 'cortex-fixture', 'label': 'Demo-Daten (Fixture)',
                'title': 'Cortex läuft auf einer lokalen Fixture-Datei, nicht auf dem '
                         'Wissensgraphen (ONTOLOGY_SOURCE=fixture).'})
        if _search_use_test_results():
            badges.append({
                'key': 'search-test', 'label': 'Testdaten',
                'title': 'Die Suche liefert Beispieltreffer (SEARCH_USE_TEST_RESULTS=true), '
                         'keine Ergebnisse aus Knovas.'})
        return badges

    def _sidebar_context() -> Dict[str, Any]:
        """Gemeinsame Werte der Plattform-Leiste."""
        return {
            'company_name': login_company_name,
            'feedback_url': feedback_url,
            'honesty_badges': _honesty_badges(),
        }

    @app.context_processor
    def _inject_platform_shell():
        """Blueprint-Seiten (Berichte, Import, Akten-Kompass) bekommen die
        Werte der Leiste, ohne dass jede Route sie erneut durchreicht.
        Explizite render_template-Argumente gewinnen (Flask-Vertrag)."""
        return {
            **_sidebar_context(),
            'app_title': web_app_title,
            'brand': web_brand,
            'asset_version': _static_asset_version(),
            'csrf_token': _ensure_csrf_token(),
        }
```

Then in `settings_page` (`:1051-1063`) add two template values after `login_name=...`:

```python
            cortex_source_label=('Wissensgraph (live)' if _ontology_source_is_graph()
                                 else 'Fixture (Demo-Daten)'),
            search_mode_label=('Testdaten (SEARCH_USE_TEST_RESULTS)' if _search_use_test_results()
                               else 'Knovas (live)'),
```

- [ ] **Step 5: Render the badges and the settings rows**

In `src/web_interface/templates/_sidebar.html` replace the `<div class="app-sidebar-foot">` block (`:56-64`) with:

```html
    <div class="app-sidebar-foot">
        {% if honesty_badges %}
        <ul class="honesty-badges" aria-label="Datenquellen-Hinweise">
            {% for badge in honesty_badges %}
            <li class="honesty-badge" data-badge="{{ badge.key }}" title="{{ badge.title }}">{{ badge.label }}</li>
            {% endfor %}
        </ul>
        {% endif %}
        <a class="app-user" href="{{ url_for('settings_page') }}">
            <span class="app-avatar" aria-hidden="true">{{ company_name[0] | upper }}</span>
            <span class="app-user-text">
                <span class="app-user-name">{{ company_name }}</span>
                <span class="app-user-role">Angemeldet</span>
            </span>
        </a>
    </div>
```

In `src/web_interface/templates/settings.html` insert after the `<dt>Oberfläche</dt>` row (`:47`):

```html
                            <dt>Cortex-Quelle</dt><dd>{{ cortex_source_label }}</dd>
                            <dt>Suche</dt><dd>{{ search_mode_label }}</dd>
```

Append to `src/web_interface/static/css/style.css` after `.app-user-role` (`:1206`):

```css
/* Ehrlichkeits-Marker (G9): Demo-/Testdaten sind auf jeder Seite sichtbar. */
.honesty-badges { list-style: none; margin: 0; padding: 0 10px; display: flex; flex-wrap: wrap; gap: 6px; }
.honesty-badge {
    font-family: var(--font-heading);
    font-size: 0.68rem;
    letter-spacing: .02em;
    color: var(--text-secondary);
    border: 1px dashed var(--callout);
    border-radius: 999px;
    padding: 2px 8px;
    background: var(--surface-sunken);
    cursor: help;
}
```

- [ ] **Step 6: Run the badge tests to verify they pass**

Run: `python -m pytest tests/test_honesty_badges.py tests/test_ontology_api.py tests/test_platform_health.py -v`
Expected: PASS, 5 new tests, no regressions.

- [ ] **Step 7: Write the failing deploy-default test**

Create `tests/test_deploy_defaults.py`:

```python
"""G1: the deploy bundle defaults Cortex to the live graph; fixture stays a documented fallback."""
from __future__ import annotations

import pathlib
import re

PLATFORM = pathlib.Path(__file__).resolve().parents[3]          # KnovasPlatform/
ROOT = PLATFORM.parent                                          # KnovasComponents/


def test_platform_env_example_defaults_to_graph():
    text = (PLATFORM / ".env.example").read_text(encoding="utf-8")
    assert re.search(r"^ONTOLOGY_SOURCE=graph$", text, re.M)
    assert "ONTOLOGY_SOURCE=fixture" in text            # the fallback is spelled out


def test_platform_compose_defaults_to_graph():
    text = (PLATFORM / "docker-compose.yml").read_text(encoding="utf-8")
    assert "ONTOLOGY_SOURCE: ${ONTOLOGY_SOURCE:-graph}" in text


def test_unified_bundle_writes_the_source_into_the_generated_env():
    script = (ROOT / "scripts" / "lib" / "expand_knovas_env.sh").read_text(encoding="utf-8")
    assert 'ONTOLOGY_SOURCE="$(read_knovas ONTOLOGY_SOURCE graph)"' in script
    assert "ONTOLOGY_SOURCE=${ONTOLOGY_SOURCE}" in script
    example = (ROOT / "knovas.env.example").read_text(encoding="utf-8")
    assert "# ONTOLOGY_SOURCE=graph" in example
```

- [ ] **Step 8: Run it to verify it fails**

Run: `python -m pytest tests/test_deploy_defaults.py -v`
Expected: FAIL on all three (defaults still `fixture`, script writes no `ONTOLOGY_SOURCE`).

- [ ] **Step 9: Flip the deploy defaults**

`KnovasPlatform/.env.example` — replace lines 80-89 (comment block + `ONTOLOGY_SOURCE=fixture`) with:

```
## -----------------------------------------------------------------------------
## Cortex (Wissensgraph unter /ontology)
## -----------------------------------------------------------------------------
## Datenquelle hinter demselben Vertrag:
##   graph   = Knovas Knowledge Graph API (/secured/graph, mTLS) - Standard.
##             Der Knowledge Graph muss im Tenant aktiviert sein, sonst antwortet
##             die API mit error_code knowledge_graph_disabled und Cortex zeigt
##             das als Zustand, nicht als Fehler.
##   fixture = lokale JSON-Datei (Demo/Schulung). Rueckfall fuer Vorfuehrungen
##             ohne Tenant: ONTOLOGY_SOURCE=fixture setzen und ONTOLOGY_FIXTURE_PATH
##             beschreibbar einhaengen. Die Oberflaeche zeigt dann das Abzeichen
##             "Demo-Daten (Fixture)" in der Seitenleiste.
ONTOLOGY_SOURCE=graph
```

`KnovasPlatform/docker-compose.yml:78-80` — replace the comment and default:

```yaml
      # Cortex. Standard ist der Wissensgraph; fixture nur fuer Demos ohne
      # Tenant (siehe .env.example). Ueberschreibbar mit denselben Vorgaben.
      ONTOLOGY_SOURCE: ${ONTOLOGY_SOURCE:-graph}
```

`knovas.env.example` — append under "# Optional":

```
## ONTOLOGY_SOURCE=graph            # graph (Standard) | fixture (Demo ohne Tenant)
```

`scripts/lib/expand_knovas_env.sh` — after line 28 (`WEB_SECRET_KEY=...`) add
`ONTOLOGY_SOURCE="$(read_knovas ONTOLOGY_SOURCE graph)"`, and in the `KP_ENV` heredoc add the line
`ONTOLOGY_SOURCE=${ONTOLOGY_SOURCE}` directly after `AUTODOC_IDENTIFIER_PREFIX=${KNOVAS_IDENTIFIER_PREFIX}`.
`scripts/lib/test_expand_knovas_env.sh` — add `grep -q 'ONTOLOGY_SOURCE=graph' KnovasPlatform/.env.generated` before the `rm -f`.

- [ ] **Step 10: Run the deploy test and the shell smoke**

Run: `python -m pytest tests/test_deploy_defaults.py -v` — Expected: PASS, 3 tests.
Run from the repo root: `bash scripts/lib/test_expand_knovas_env.sh` — Expected: `expand_knovas_env smoke OK`.

- [ ] **Step 11: Commit**

```bash
git add ../../.env.example ../../docker-compose.yml ../../../knovas.env.example \
        ../../../scripts/lib/expand_knovas_env.sh ../../../scripts/lib/test_expand_knovas_env.sh \
        src/web_interface/app.py src/web_interface/templates/_sidebar.html \
        src/web_interface/templates/settings.html src/web_interface/static/css/style.css \
        tests/fixtures/graph_app.py tests/test_honesty_badges.py tests/test_deploy_defaults.py
git commit -m "feat(cortex): graph mode is the deploy default; sidebar and settings carry honesty badges"
```

---

---

### Task KC-D-3: G2 — `GraphOntologySource.neighbors`, the ego route and the "Akten-Kompass" page

**Requirements:** G2, F8 (matters half: neighbours grouped by type)

**Files:**
- Modify: `src/ontology_graph.py` — insert `neighbors` + `_node_types_cached` after `entity_detail` (`src/ontology_graph.py:199-236`)
- Create: `src/web_interface/cortex_live_routes.py`
- Create: `src/web_interface/templates/matter_graph.html`, `src/web_interface/static/js/ego.js`
- Modify: `src/web_interface/static/css/ontology.css` (append), `src/web_interface/app.py:667-679` (`_prevent_stale_ui_assets` paths) and `:1717` (register the blueprint)
- Test: `tests/test_ontology_graph_ego.py`, `tests/test_cortex_live_routes_ego.py`

**Interfaces:**
- Consumes: `KnovasAPIClient.node_ego` and `graph_model.EgoGraph` (KC-D-1); `graph_node`, `graph_node_types` (`knovas_client.py:1578-1582`); `_ontology_source()` / `_ontology_source_is_graph()` (`app.py:1683-1704`); `tests/fixtures/graph_app.py` (KC-D-2); C-plan page `/matters/<node_id>` (C6) and Part KC-B page `/parteien?node=<id>` as click targets; vendored `static/js/vendor/cytoscape.min.js`.
- Produces: `GraphOntologySource.neighbors(node_id, depth=1, limit=60) -> Optional[dict]` (`{"anchor","anchor_label","anchor_type","nodes":[{id,label,type,type_label,hop}],"edges":[{src,predicate,dst}],"truncated","depth_applied"}`; None when the anchor is unknown); `register_cortex_live_routes(app, api_client, graph_mode, source, filter_engine=None) -> Blueprint` (Blueprint name `cortex_live`); routes `GET /api/graph/nodes/<node_id>/ego?depth=&limit=` → `{"success": true, …neighbors dict}` (409 on the fixture, 404 unknown), `GET /matters/<node_id>/graph` (page, 200 in both modes; the fixture renders "Wissensnetz-Modus erforderlich"); JS `ego.js`. KC-D-4/-9 add routes to the same Blueprint.

- [ ] **Step 1: Write the failing source test**

Create `tests/test_ontology_graph_ego.py`:

```python
"""GraphOntologySource.neighbors — one guarded ego call instead of a topology scan."""
from __future__ import annotations

from graph_model import EgoGraph
from ontology_graph import GraphOntologySource
from test_ontology_graph import FakeGraphClient

EGO = {"nodes": [{"id": "n-1", "name": "Müller Bau AG", "node_type_id": "t-mandant", "hop": 0},
                 {"id": "n-2", "name": "Dossier 2024-001", "node_type_id": "t-dossier", "hop": 1}],
       "edges": [{"id": "e-1", "node_lo": "n-1", "node_hi": "n-2", "relation": "hat_Dossier"}],
       "truncated": False, "depth_applied": 1}


class EgoClient(FakeGraphClient):
    def __init__(self, **overrides):
        super().__init__(**overrides)
        self.ego_calls = []
        self.ego_payload = EGO

    def node_ego(self, node_id, *, depth=1, limit=None):
        self.ego_calls.append((node_id, depth, limit))
        if node_id == "n-404":
            return None
        return EgoGraph.from_payload(self.ego_payload, anchor_id=node_id)


def test_neighbors_maps_to_the_cortex_contract_with_type_labels():
    client = EgoClient()
    out = GraphOntologySource(client).neighbors("n-1", depth=1, limit=25)

    assert client.ego_calls == [("n-1", 1, 25)]
    assert out["anchor"] == "n-1" and out["anchor_label"] == "Müller Bau AG"
    assert out["anchor_type"] == "t-mandant"
    assert out["nodes"][1] == {"id": "n-2", "label": "Dossier 2024-001", "type": "t-dossier",
                               "type_label": "Dossier", "hop": 1}
    assert out["edges"] == [{"src": "n-1", "predicate": "hat_Dossier", "dst": "n-2"}]
    assert out["truncated"] is False and out["depth_applied"] == 1


def test_neighbors_never_touches_the_topology_export():
    client = EgoClient()
    client.graph_export = lambda: (_ for _ in ()).throw(AssertionError("export must not be read"))
    out = GraphOntologySource(client).neighbors("n-1")

    assert out["nodes"][0]["id"] == "n-1"


def test_neighbors_of_an_unknown_node_is_none():
    assert GraphOntologySource(EgoClient()).neighbors("n-404") is None


def test_anchor_label_is_resolved_from_node_detail_when_the_ego_omits_it():
    client = EgoClient()
    client.ego_payload = {"nodes": [{"id": "n-2", "name": "Dossier 2024-001",
                                     "node_type_id": "t-dossier", "hop": 1}],
                          "edges": [], "truncated": True, "depth_applied": 1}
    out = GraphOntologySource(client).neighbors("n-1")

    assert out["anchor_label"] == "Müller Bau AG"          # from graph_node("n-1")
    assert out["truncated"] is True
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_ontology_graph_ego.py -v`
Expected: FAIL with `AttributeError: 'GraphOntologySource' object has no attribute 'neighbors'`

- [ ] **Step 3: Implement `neighbors`**

Insert into `src/ontology_graph.py` after `entity_detail` (before `_evidence`, `:238`):

```python
    # -- Ego-Graph (Pflichtenheft G2) -----------------------------------

    def _node_types_cached(self) -> List[Dict[str, Any]]:
        """Typ-Vokabular, kurz gecacht - klein und ohne den Topologie-Export."""
        cache = getattr(self, "_types_cache", None)
        if cache is not None and self._now() - cache[0] < self._ttl:
            return cache[1]
        types = self._client.graph_node_types() or []
        self._types_cache = (self._now(), types)
        return types

    def neighbors(self, node_id: str, depth: int = 1,
                  limit: int = 60) -> Optional[Dict[str, Any]]:
        """Nachbarschaft eines Knotens aus einem Aufruf (GET .../ego).

        Bewusst ohne den Topologie-Export: der Kompass einer Akte darf nicht
        den ganzen Mandanten laden. Unbekannter Anker -> None (404 bleibt 404).
        """
        node_id = str(node_id or "").strip()
        if not node_id:
            return None
        ego = self._client.node_ego(node_id, depth=depth, limit=limit)
        if ego is None:
            return None
        payload = ego.to_cortex()
        labels = {_type_id(t): _type_label(t) for t in self._node_types_cached()}
        anchor = next((n for n in payload["nodes"] if n["id"] == node_id), None)
        if anchor is not None and not anchor["label"]:
            detail = self._client.graph_node(node_id) or {}
            node = detail.get("node") if isinstance(detail.get("node"), dict) else detail
            anchor["label"] = _node_label(node)
            anchor["type"] = anchor["type"] or _node_type_id(node)
        for entry in payload["nodes"]:
            entry["type_label"] = labels.get(entry["type"], "")
        payload["anchor_label"] = anchor["label"] if anchor else ""
        payload["anchor_type"] = anchor["type"] if anchor else ""
        return payload
```

- [ ] **Step 4: Run it to verify it passes**

Run: `python -m pytest tests/test_ontology_graph_ego.py tests/test_ontology_graph.py -v`
Expected: PASS, 4 new tests, no regressions.

- [ ] **Step 5: Write the failing route tests**

Create `tests/test_cortex_live_routes_ego.py`:

```python
"""/api/graph/nodes/<id>/ego and the Akten-Kompass page."""
from __future__ import annotations

from fixtures.graph_app import GraphStubClient, build_graph_app, csrf_header, login

EGO = {"nodes": [{"id": "n-1", "name": "Mandat Meier", "node_type_id": "t-akte", "hop": 0},
                 {"id": "n-2", "name": "Müller AG", "node_type_id": "t-partei", "hop": 1}],
       "edges": [{"id": "e-1", "node_lo": "n-1", "node_hi": "n-2", "relation": "Gegenpartei"}],
       "truncated": False, "depth_applied": 1}


def _graph_client(tmp_path, monkeypatch):
    app = build_graph_app(tmp_path, monkeypatch)
    GraphStubClient.last.ego["n-1"] = EGO
    client = app.test_client()
    login(client)
    return client


def test_ego_requires_login(tmp_path, monkeypatch):
    app = build_graph_app(tmp_path, monkeypatch)
    assert app.test_client().get("/api/graph/nodes/n-1/ego").status_code == 401


def test_ego_returns_the_cortex_contract(tmp_path, monkeypatch):
    client = _graph_client(tmp_path, monkeypatch)

    payload = client.get("/api/graph/nodes/n-1/ego?depth=1&limit=10").get_json()

    assert payload["success"] is True
    assert payload["anchor"] == "n-1" and payload["anchor_label"] == "Mandat Meier"
    assert payload["nodes"][1]["type_label"] == "Partei"
    assert payload["edges"] == [{"src": "n-1", "predicate": "Gegenpartei", "dst": "n-2"}]
    assert GraphStubClient.last.calls[-1] == ("node_ego", "n-1", 1, 10)


def test_ego_depth_and_limit_are_bounded_server_side(tmp_path, monkeypatch):
    client = _graph_client(tmp_path, monkeypatch)

    client.get("/api/graph/nodes/n-1/ego?depth=7&limit=9999")

    assert GraphStubClient.last.calls[-1] == ("node_ego", "n-1", 3, 200)


def test_unknown_node_is_404_not_500(tmp_path, monkeypatch):
    client = _graph_client(tmp_path, monkeypatch)
    resp = client.get("/api/graph/nodes/n-404/ego")

    assert resp.status_code == 404
    assert resp.get_json() == {"success": False, "error": "Knoten nicht gefunden"}


def test_fixture_mode_answers_409(tmp_path, monkeypatch):
    app = build_graph_app(tmp_path, monkeypatch, ontology_source="fixture")
    client = app.test_client()
    login(client)

    resp = client.get("/api/graph/nodes/n-1/ego")

    assert resp.status_code == 409
    assert resp.get_json()["error"] == "Wissensnetz-Modus erforderlich"


def test_compass_page_renders_in_graph_mode(tmp_path, monkeypatch):
    client = _graph_client(tmp_path, monkeypatch)
    html = client.get("/matters/n-1/graph").get_data(as_text=True)

    assert "Akten-Kompass" in html and "ego.js" in html
    assert 'data-node-id="n-1"' in html


def test_compass_page_on_the_fixture_states_the_mode_requirement(tmp_path, monkeypatch):
    app = build_graph_app(tmp_path, monkeypatch, ontology_source="fixture")
    client = app.test_client()
    login(client)
    resp = client.get("/matters/n-1/graph")

    assert resp.status_code == 200
    assert "Wissensnetz-Modus erforderlich" in resp.get_data(as_text=True)
```

- [ ] **Step 6: Run them to verify they fail**

Run: `python -m pytest tests/test_cortex_live_routes_ego.py -v`
Expected: FAIL — 404 on `/api/graph/nodes/n-1/ego` and `/matters/n-1/graph` (blueprint missing); the 401 test passes already.

- [ ] **Step 7: Create the Blueprint**

Create `src/web_interface/cortex_live_routes.py`:

```python
"""Cortex on the live graph (Pflichtenheft G2, G3, G8): ego graph, "Warum?"
panel and filter jobs. Blueprint, not closures in create_app (house rule since
the section-C graph_routes.py).

Jede Route hier braucht den Graph-Modus. Auf der Fixture antwortet die API mit
409 "Wissensnetz-Modus erforderlich"; Seiten rendern denselben Satz - nie ein
500, nie erfundene Daten.
"""
from __future__ import annotations

import logging

from flask import Blueprint, jsonify, render_template, request

from knovas_client import GraphError, KnowledgeGraphDisabled

logger = logging.getLogger(__name__)

_GENERIC_ERROR = "Interner Serverfehler"
_NEEDS_GRAPH = "Wissensnetz-Modus erforderlich"
EGO_DEFAULT_LIMIT = 60
EGO_MAX_LIMIT = 200


def register_cortex_live_routes(app, api_client, graph_mode, source, filter_engine=None):
    """graph_mode(): bool; source(): the Cortex source (GraphOntologySource in
    graph mode); filter_engine(): the Cortex filter engine (used by KC-D-9)."""
    bp = Blueprint("cortex_live", __name__)

    def _guard():
        if not graph_mode():
            return jsonify({"success": False, "error": _NEEDS_GRAPH}), 409
        return None

    def _fail(exc):
        if isinstance(exc, KnowledgeGraphDisabled):
            return jsonify({"success": False, "error": str(exc)}), 409
        if isinstance(exc, GraphError):
            return jsonify({"success": False, "error_code": exc.error_code,
                            "error": str(exc)}), exc.status
        logger.error("Cortex live route error", exc_info=True)
        return jsonify({"success": False, "error": _GENERIC_ERROR}), 500

    def _int_arg(name, default, lo, hi):
        try:
            value = int(request.args.get(name, default))
        except (TypeError, ValueError):
            value = default
        return max(lo, min(hi, value))

    @bp.route("/api/graph/nodes/<node_id>/ego", methods=["GET"])
    def node_ego(node_id):
        blocked = _guard()
        if blocked:
            return blocked
        depth = _int_arg("depth", 1, 1, 3)
        limit = _int_arg("limit", EGO_DEFAULT_LIMIT, 1, EGO_MAX_LIMIT)
        try:
            ego = source().neighbors(node_id, depth=depth, limit=limit)
            if ego is None:
                return jsonify({"success": False, "error": "Knoten nicht gefunden"}), 404
            return jsonify({"success": True, **ego})
        except Exception as exc:                        # noqa: BLE001
            return _fail(exc)

    @bp.route("/matters/<node_id>/graph", methods=["GET"])
    def matter_graph_page(node_id):
        return render_template("matter_graph.html", node_id=node_id,
                               active_nav="cortex", needs_graph=not graph_mode())

    app.register_blueprint(bp)
    return bp
```

- [ ] **Step 8: Register it and add the page assets**

In `src/web_interface/app.py`, directly after the `register_graph_routes(...)` call (C-plan C1, before the `/api/ontology/summary` route at `:1717`) add:

```python
    from web_interface.cortex_live_routes import register_cortex_live_routes
    register_cortex_live_routes(app, api_client, _ontology_source_is_graph,
                                _ontology_source, _ontology_filter_engine)
```

In `_prevent_stale_ui_assets` (`app.py:667-679`) extend the path test so the new pages are never served stale:

```python
        if (
            path in ('/', '/login', '/ontology', '/berichte', '/import')
            or path.startswith('/matters/')
            or path.endswith('.js')
            or path.endswith('.css')
            or path.startswith('/static/')
        ):
```

Create `src/web_interface/templates/matter_graph.html`:

```html
<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="csrf-token" content="{{ csrf_token }}">
    <title>{{ brand }} Akten-Kompass</title>
    <link rel="icon" href="{{ url_for('static', filename='img/favicon.svg') }}" type="image/svg+xml">
    <link rel="stylesheet" href="{{ url_for('static', filename='css/style.css') }}?v={{ asset_version }}">
    <link rel="stylesheet" href="{{ url_for('static', filename='css/ontology.css') }}?v={{ asset_version }}">
</head>
<body>
    <div class="app-shell">
        {% include '_sidebar.html' %}
        <div class="app-content">
        <div class="container container-wide">
        <header class="site-header">
            <h1 class="site-greeting">Akten-Kompass<br>
                <span class="site-greeting-question" id="egoTitle">Was rund um diese Akte bekannt ist.</span>
            </h1>
        </header>
        <main class="ontology-stage" aria-label="Akten-Kompass">
            <div class="ontology-pane-header">
                <h2>Ein Schritt weit</h2>
                <div class="graph-toolbar" role="toolbar" aria-label="Werkzeuge">
                    <a class="btn btn-outline" href="{{ url_for('graph_api.matter_page', node_id=node_id) }}">Zur Akte</a>
                    <button type="button" id="zoomFit" class="btn btn-outline">Einpassen</button>
                </div>
            </div>
            <div class="ontology-stage-body">
                {% if needs_graph %}
                <p class="ontology-empty ontology-mode-note">Wissensnetz-Modus erforderlich: der Akten-Kompass
                    liest den Knovas Wissensgraphen (ONTOLOGY_SOURCE=graph). Auf der Demo-Fixture gibt es keine
                    Nachbarschaft zu zeigen.</p>
                {% else %}
                <div id="egoRoot" data-node-id="{{ node_id }}" data-depth="1" data-limit="60"></div>
                <p class="ontology-empty" id="egoEmpty" hidden></p>
                <p class="ontology-empty ontology-truncated" id="egoTruncated" hidden>Ausschnitt: nicht alle
                    Nachbarn werden gezeigt.</p>
                <aside class="ontology-drawer" id="egoPane" aria-label="Knoten">
                    <div class="ontology-pane-header">
                        <h2 id="egoPaneTitle">Knoten</h2>
                        <button type="button" class="btn btn-outline drawer-close" id="egoClose" aria-label="Schliessen">×</button>
                    </div>
                    <div id="egoPaneBody"></div>
                </aside>
                {% endif %}
            </div>
        </main>
        </div>
        </div>
    </div>
    {% if not needs_graph %}
    <script src="{{ url_for('static', filename='js/vendor/cytoscape.min.js') }}?v={{ asset_version }}"></script>
    <script src="{{ url_for('static', filename='js/ego.js') }}?v={{ asset_version }}"></script>
    {% endif %}
</body>
</html>
```

`url_for('graph_api.matter_page', ...)` is the C-plan C6 page endpoint (Blueprint `graph_api`, function `matter_page`). If C6 has not landed on the branch yet, use the literal `href="/matters/{{ node_id }}"` and note it in the commit.

Create `src/web_interface/static/js/ego.js`:

```js
// Akten-Kompass: die Nachbarschaft einer Akte aus einem Aufruf (G2).
// Cytoscape wie in ontology.js (vendored, Token-Farben), aber ohne CortexApp:
// die Seite hat einen Anker, keine Typ-Uebersicht.
'use strict';

(function () {
    const root = document.getElementById('egoRoot');
    if (!root) return;
    const nodeId = root.dataset.nodeId;
    const depth = Number(root.dataset.depth || 1);
    const limit = Number(root.dataset.limit || 60);
    const cssToken = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) =>
        ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const empty = document.getElementById('egoEmpty');
    const say = (text) => { empty.textContent = text; empty.hidden = false; };

    async function load() {
        const url = `/api/graph/nodes/${encodeURIComponent(nodeId)}/ego?depth=${depth}&limit=${limit}`;
        const resp = await fetch(url, { credentials: 'same-origin' });
        if (resp.status === 401) { window.location.assign('/login'); return; }
        if (resp.status === 404) { say('Akte nicht gefunden.'); return; }
        if (resp.status === 409) { say('Wissensnetz-Modus erforderlich.'); return; }
        if (!resp.ok) { say('Der Kompass konnte nicht geladen werden.'); return; }
        render(await resp.json());
    }

    function render(data) {
        document.getElementById('egoTitle').textContent = data.anchor_label || 'Akte';
        document.getElementById('egoTruncated').hidden = !data.truncated;
        if (data.nodes.length <= 1) { say('Zu dieser Akte sind noch keine Verbindungen erfasst.'); }
        const nodes = data.nodes.map((n) => ({
            data: { id: n.id, label: n.label || n.id, type: n.type, typeLabel: n.type_label || '',
                    hop: n.hop, anchor: n.id === data.anchor ? 1 : 0 },
            classes: n.id === data.anchor ? 'anchor' : 'entity',
        }));
        const edges = data.edges.map((e, i) => ({
            data: { id: `e-${i}`, source: e.src, target: e.dst, predicate: e.predicate,
                    label: e.predicate, width: 1.5 },
            classes: 'observed-relation',
        }));
        const cy = cytoscape({
            container: root,
            elements: { nodes, edges },
            minZoom: 0.2, maxZoom: 4, wheelSensitivity: 0.2,
            style: [
                { selector: 'node', style: {
                    'background-color': cssToken('--surface-sunken'), 'border-width': 1.5,
                    'border-color': cssToken('--callout'), 'label': 'data(label)',
                    'font-family': 'IBM Plex Sans, sans-serif', 'font-size': 11,
                    'color': cssToken('--text-primary'), 'text-valign': 'bottom',
                    'text-margin-y': 6, 'text-wrap': 'wrap', 'text-max-width': 150,
                    'width': 34, 'height': 34 } },
                { selector: 'node.anchor', style: {
                    'background-color': cssToken('--highlight'), 'border-width': 3,
                    'border-color': cssToken('--primary-color'), 'width': 58, 'height': 58,
                    'font-family': 'IBM Plex Mono, monospace', 'font-weight': 600, 'font-size': 13 } },
                { selector: 'node:selected', style: { 'border-color': cssToken('--primary-color'), 'border-width': 3 } },
                { selector: 'edge', style: {
                    'line-color': cssToken('--border-color'), 'target-arrow-shape': 'triangle',
                    'target-arrow-color': cssToken('--border-color'), 'curve-style': 'bezier',
                    'width': 'data(width)', 'label': 'data(label)', 'font-size': 11,
                    'color': cssToken('--text-secondary'), 'text-rotation': 'autorotate',
                    'text-background-color': cssToken('--surface-sunken'),
                    'text-background-opacity': 0.9, 'text-background-padding': 2 } },
            ],
            // Der Anker in der Mitte, die Nachbarn im Ring: concentric ist
            // deterministisch und braucht keine Zusatzbibliothek.
            layout: { name: 'concentric', fit: true, padding: 60, minNodeSpacing: 40,
                      concentric: (n) => 3 - Math.min(2, Number(n.data('hop') || 0)),
                      levelWidth: () => 1 },
        });
        document.getElementById('zoomFit').addEventListener('click', () => cy.fit(undefined, 60));
        cy.on('tap', 'node', (evt) => showNode(evt.target, data));
        cy.on('dbltap', 'node', (evt) => window.location.assign(targetFor(evt.target, data)));
    }

    // Klickziel: gleiche Typ wie der Anker -> Aktenseite; alles andere -> Parteien.
    function targetFor(node, data) {
        const id = node.data('id');
        if (id === data.anchor) return `/matters/${encodeURIComponent(id)}`;
        return node.data('type') === data.anchor_type
            ? `/matters/${encodeURIComponent(id)}`
            : `/parteien?node=${encodeURIComponent(id)}`;
    }

    function showNode(node, data) {
        const pane = document.getElementById('egoPane');
        const body = document.getElementById('egoPaneBody');
        const id = node.data('id');
        const sameType = node.data('type') === data.anchor_type;
        document.getElementById('egoPaneTitle').textContent = node.data('typeLabel') || 'Knoten';
        body.innerHTML = `
            <div class="entity-detail">
                <h3>${esc(node.data('label'))}</h3>
                <p class="entity-hint">${node.data('hop') === 0 ? 'Diese Akte' : `${node.data('hop')} Schritt entfernt`}</p>
                <div class="proposal-actions">
                    <a class="btn btn-primary" href="${sameType ? `/matters/${encodeURIComponent(id)}` : `/parteien?node=${encodeURIComponent(id)}`}">
                        ${sameType ? 'Akte öffnen' : 'In Parteien öffnen'}</a>
                    ${id !== data.anchor ? `<a class="btn btn-outline" href="/matters/${encodeURIComponent(id)}/graph">Kompass hier zentrieren</a>` : ''}
                </div>
            </div>`;
        pane.classList.add('open');
        document.getElementById('egoClose').onclick = () => pane.classList.remove('open');
    }

    load();
})();
```

Append to `src/web_interface/static/css/ontology.css`:

```css
/* Akten-Kompass (G2): Anker-Hinweise, sonst die Cortex-Buehne. */
#egoRoot { position: absolute; inset: 0; }
.ontology-truncated { position: absolute; left: 16px; bottom: 12px; margin: 0; }
.ontology-mode-note { max-width: 560px; }
```

- [ ] **Step 9: Run the route tests to verify they pass**

Run: `python -m pytest tests/test_cortex_live_routes_ego.py tests/test_csrf_enforcement.py -v`
Expected: PASS, 7 new tests, CSRF suite green (the new routes are GET-only).

- [ ] **Step 10: Verify by hand against the dev tenant**

With `ONTOLOGY_SOURCE=graph`, open `/matters/<a real matter id>/graph`. Expected: the anchor sits in the middle, its neighbours in a ring with type labels in the drawer, "Zur Akte" leads to the C-plan matter page, double-click on a party opens Parteien; a node with more than 60 neighbours shows the "Ausschnitt" note.

- [ ] **Step 11: Commit**

```bash
git add src/ontology_graph.py src/web_interface/cortex_live_routes.py src/web_interface/app.py \
        src/web_interface/templates/matter_graph.html src/web_interface/static/js/ego.js \
        src/web_interface/static/css/ontology.css tests/test_ontology_graph_ego.py \
        tests/test_cortex_live_routes_ego.py
git commit -m "feat(cortex): Akten-Kompass — ego graph from one guarded call (G2)"
```

---

---

## PART KC-E — KnovasComponents — Office add-ins + filing endpoint, Arbeitstag-Journal (H2, J2, J3)

### Task KC-E-1: `src/email_filing.py` — parse, metadata, dedup store, `file_email`

**Requirements:** H2 (file an e-mail and its attachments to a matter in ≤ 2 clicks, with dedup), F3/D5 (author, document_type, document_date, source_kind, language on the ingested document — the `metadata` object of §5.2)
**Files:**
- Create: `src/email_filing.py`
- Create: `src/identity/migrations/0004_filing.sql`
- Modify: `src/knovas_extract_upload.py:14-21` (public `SUPPORTED_EXTENSIONS` alias next to `_EXT_TO_MIME`)
- Modify: `src/knovas_client.py:1803-1815` (`_sync_single_document_secured` — `metadata` and `graph_assign` on the init body)
- Create: `tests/fixtures/sample_mails.py`
- Test: `tests/test_email_filing.py`, `tests/test_knovas_client_filing_metadata.py`, `tests/test_filed_email_store.py`

**Interfaces:**
- Consumes: `knovas_extract_upload._EXT_TO_MIME` (existing, `.txt .md .pdf .docx .eml .msg`); `KnovasAPIClient.sync_single_document(document: dict) -> dict` (existing; the document dict keys `doc_id`, `path`, `display_name`, `type`, `content_base64` are read by `_secured_transmit_parts_from_document`, `knovas_client.py:439`); `init_document_transmission` `metadata` object `{author, document_type, language, document_date, document_status, source_kind, extra}` and `graph_assign {"node_ids": [...]}` (defined in Part KB); `identity.migrate` (section B) applies the new `.sql` file by filename order.
- Produces:
  - `knovas_extract_upload.SUPPORTED_EXTENSIONS: frozenset[str]` (dotted, e.g. `".pdf"`).
  - `KnovasAPIClient.sync_single_document(document)` honours two optional keys: `document["metadata"]` (dict, forwarded verbatim as `metadata`) and `document["graph_assign"]` (`{"node_ids": [...]}`, forwarded verbatim) on `POST /secured/init_document_transmission`. If section-C Task B7 already added `graph_assign` here, keep its form and add only `metadata`.
  - `email_filing.parse_email(mime_bytes: bytes, *, max_bytes: int = MAX_MESSAGE_BYTES) -> ParsedEmail` (stdlib `email`, raises `EmailTooLarge` / `EmailUnparseable`);
    `email_filing.parse_msg(msg_bytes: bytes, *, max_bytes=...) -> ParsedEmail` (extract-msg, already installed through `knovas-extract[msg]`);
    `email_filing.build_mime_from_item(item: dict) -> bytes` (structured add-in fallback → RFC 5322 bytes);
    `email_filing.build_metadata(parsed: ParsedEmail) -> dict` and `email_filing.attachment_metadata(parsed, attachment) -> dict` (the §5.2 object: `author`=From, `document_type` `"E-Mail"` / by extension, `document_date`=Date, `source_kind` `"addin"`, `language` when detected, `extra` `{"eml:message_id", "eml:in_reply_to", "eml:attachment_count"}`);
    `email_filing.detect_language(text: str) -> str | None` (`de|fr|it|en`, stop-word ratio, no dependency);
    `email_filing.FiledEmailStore(conn)` with `.lookup(message_id_hash: str, node_id: str) -> dict | None` and `.mark_filed(*, message_id_hash, node_id, pointer, filed_by: str | None, attachment_count: int) -> None`;
    `email_filing.file_email(client, store, parsed, raw_bytes, *, node_id, include_attachments, filed_by, prefix, raw_ext="eml") -> FilingResult` where `FilingResult` has `status: "filed" | "already_filed"`, `pointer`, `node_id`, `attachments: [{name, pointer}]`, `skipped: [{name, reason}]`, `filed_at`, and `.to_dict()`;
    identifiers `"<prefix>/mail/<sha256[:32]>"` for the message and `"<prefix>/mail/<sha256[:32]>/att/<safe-name>"` for attachments (`safe_name(name) -> str`, `_2`/`_3` suffix on collision);
    table `filed_emails(id, message_id_hash CHAR(64), node_id TEXT, pointer TEXT, attachment_count INT, filed_by UUID NULL → users, filed_at TIMESTAMPTZ, UNIQUE(message_id_hash, node_id))`.
    Consumed by Task KC-E-2.

- [ ] **Step 1: Write the failing store/parse tests and the mail fixtures**

Create `tests/fixtures/sample_mails.py` (real, deterministic messages built with the stdlib — no binary fixtures):

```python
"""Deterministic e-mail fixtures for the filing tests (RFC 5322 via stdlib)."""
from __future__ import annotations

from email.message import EmailMessage

MINIMAL_PDF = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
               b"2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n")


def _base(subject: str = "Rückfrage zum Kaufvertrag 2024-001") -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = "Anna Muster <anna.muster@example.ch>"
    msg["To"] = "Beat Beispiel <beat.beispiel@kanzlei.ch>, buchhaltung@kanzlei.ch"
    msg["Cc"] = "Carla Cliente <carla@cliente.example>"
    msg["Subject"] = subject
    msg["Date"] = "Sun, 15 Mar 2026 10:00:00 +0100"
    msg["Message-ID"] = "<abc123@example.ch>"
    msg["In-Reply-To"] = "<parent-9@kanzlei.ch>"
    return msg


def simple_eml() -> bytes:
    msg = _base()
    msg.set_content("Guten Tag\n\nder Kaufpreis ist bis zum Übergabetermin zu bezahlen. "
                    "Bitte bestätigen Sie den Termin.\n\nFreundliche Grüsse\nAnna Muster")
    return msg.as_bytes()


def html_only_eml() -> bytes:
    msg = _base(subject="Délai de recours")
    msg.add_alternative("<html><body><p>Madame, Monsieur,</p><p>le délai de recours "
                        "expire dans les 30 jours. Merci de confirmer.</p></body></html>",
                        subtype="html")
    return msg.as_bytes()


def eml_with_attachments() -> bytes:
    msg = _base()
    msg.set_content("Anbei der Vertrag und die Notiz.")
    msg.add_attachment(MINIMAL_PDF, maintype="application", subtype="pdf",
                       filename="Vertrag ../final.pdf")
    msg.add_attachment("Notiz zum Termin.", subtype="plain", filename="notiz.txt")
    msg.add_attachment(b"PK\x03\x04zip", maintype="application", subtype="zip",
                       filename="archiv.zip")
    return msg.as_bytes()


def eml_without_message_id() -> bytes:
    msg = _base()
    del msg["Message-ID"]
    msg.set_content("Ohne Message-ID.")
    return msg.as_bytes()
```

Create `tests/test_email_filing.py`:

```python
"""email_filing — parse, metadata, dedup and the two-step upload (H2)."""
from __future__ import annotations

import base64
import re

import pytest

from fixtures.sample_mails import (MINIMAL_PDF, eml_with_attachments, eml_without_message_id,
                                   html_only_eml, simple_eml)
from email_filing import (EmailTooLarge, build_metadata, build_mime_from_item,
                          detect_language, file_email, parse_email, safe_name)


class RecordingClient:
    def __init__(self, fail_on=None):
        self.documents = []
        self.fail_on = fail_on

    def sync_single_document(self, document):
        if self.fail_on and document["doc_id"].endswith(self.fail_on):
            raise RuntimeError("upload failed")
        self.documents.append(document)
        return {"status": "success", "identifier": document["doc_id"]}


class InMemoryStore:
    """Same contract as FiledEmailStore, without PostgreSQL."""

    def __init__(self):
        self.rows = {}

    def lookup(self, message_id_hash, node_id):
        return self.rows.get((message_id_hash, node_id))

    def mark_filed(self, *, message_id_hash, node_id, pointer, filed_by, attachment_count):
        self.rows[(message_id_hash, node_id)] = {
            "pointer": pointer, "filed_by": filed_by,
            "attachment_count": attachment_count, "filed_at": "2026-08-15T10:00:00+00:00"}


class TestParse:
    def test_headers_are_decoded(self):
        parsed = parse_email(simple_eml())
        assert parsed.subject == "Rückfrage zum Kaufvertrag 2024-001"
        assert parsed.sender_email == "anna.muster@example.ch"
        assert parsed.recipients == ["beat.beispiel@kanzlei.ch", "buchhaltung@kanzlei.ch",
                                     "carla@cliente.example"]
        assert parsed.message_id == "abc123@example.ch"
        assert parsed.in_reply_to == "parent-9@kanzlei.ch"
        assert parsed.document_date == "2026-03-15"

    def test_plain_body_is_kept_and_html_is_stripped(self):
        assert "Übergabetermin" in parse_email(simple_eml()).body_text
        html = parse_email(html_only_eml())
        assert "<p>" not in html.body_text
        assert "délai de recours" in html.body_text

    def test_attachments_carry_bytes_and_names(self):
        parsed = parse_email(eml_with_attachments())
        names = [a.name for a in parsed.attachments]
        assert names == ["Vertrag ../final.pdf", "notiz.txt", "archiv.zip"]
        assert parsed.attachments[0].data == MINIMAL_PDF
        assert parsed.attachments[1].data == b"Notiz zum Termin.\n"

    def test_dedup_key_prefers_message_id_and_falls_back_to_content(self):
        with_id = parse_email(simple_eml())
        without = parse_email(eml_without_message_id())
        assert re.fullmatch(r"[0-9a-f]{64}", with_id.dedup_key)
        assert with_id.dedup_key != without.dedup_key
        assert parse_email(eml_without_message_id()).dedup_key == without.dedup_key

    def test_too_large_is_refused_before_parsing(self):
        with pytest.raises(EmailTooLarge):
            parse_email(b"x" * 11, max_bytes=10)

    def test_language_is_guessed_from_stopwords(self):
        assert detect_language(parse_email(simple_eml()).body_text) == "de"
        assert detect_language(parse_email(html_only_eml()).body_text) == "fr"
        assert detect_language("42 17 99") is None


class TestMetadata:
    def test_metadata_matches_the_ingest_contract(self):
        md = build_metadata(parse_email(simple_eml()))
        assert md["author"] == "Anna Muster <anna.muster@example.ch>"
        assert md["document_type"] == "E-Mail"
        assert md["source_kind"] == "addin"
        assert md["document_date"] == "2026-03-15"
        assert md["language"] == "de"
        assert md["extra"] == {"eml:message_id": "abc123@example.ch",
                               "eml:in_reply_to": "parent-9@kanzlei.ch"}

    def test_safe_name_strips_paths_and_control_characters(self):
        assert safe_name("Vertrag ../final.pdf") == "final.pdf"
        assert safe_name("a\x00b\\c.docx") == "c.docx"
        assert safe_name("") == "anhang"


class TestFileEmail:
    def test_body_and_supported_attachments_become_documents_of_the_matter(self):
        client, store = RecordingClient(), InMemoryStore()
        parsed = parse_email(eml_with_attachments())

        result = file_email(client, store, parsed, eml_with_attachments(), node_id="n1",
                            include_attachments=True, filed_by="u1", prefix="kanzlei")

        assert result.status == "filed"
        assert result.pointer.startswith("kanzlei/mail/")
        assert [d["doc_id"] for d in client.documents] == [
            result.pointer, f"{result.pointer}/att/final.pdf", f"{result.pointer}/att/notiz.txt"]
        assert all(d["graph_assign"] == {"node_ids": ["n1"]} for d in client.documents)
        assert client.documents[0]["type"] == "eml"
        assert client.documents[0]["metadata"]["document_type"] == "E-Mail"
        assert client.documents[1]["metadata"]["document_type"] == "PDF"
        assert client.documents[1]["metadata"]["author"] == "Anna Muster <anna.muster@example.ch>"
        assert base64.b64decode(client.documents[1]["content_base64"]) == MINIMAL_PDF
        assert result.skipped == [{"name": "archiv.zip", "reason": "unsupported_type"}]

    def test_second_filing_of_the_same_message_to_the_same_matter_is_reported(self):
        client, store = RecordingClient(), InMemoryStore()
        parsed = parse_email(simple_eml())
        file_email(client, store, parsed, simple_eml(), node_id="n1",
                   include_attachments=False, filed_by=None, prefix="k")

        again = file_email(client, store, parsed, simple_eml(), node_id="n1",
                           include_attachments=False, filed_by=None, prefix="k")

        assert again.status == "already_filed"
        assert len(client.documents) == 1

    def test_the_same_message_may_be_filed_under_a_second_matter(self):
        client, store = RecordingClient(), InMemoryStore()
        parsed = parse_email(simple_eml())
        file_email(client, store, parsed, simple_eml(), node_id="n1",
                   include_attachments=False, filed_by=None, prefix="k")
        second = file_email(client, store, parsed, simple_eml(), node_id="n2",
                            include_attachments=False, filed_by=None, prefix="k")
        assert second.status == "filed"
        assert client.documents[1]["graph_assign"] == {"node_ids": ["n2"]}

    def test_a_failed_attachment_leaves_no_dedup_row_so_a_retry_re_files(self):
        client, store = RecordingClient(fail_on="notiz.txt"), InMemoryStore()
        parsed = parse_email(eml_with_attachments())
        with pytest.raises(RuntimeError):
            file_email(client, store, parsed, eml_with_attachments(), node_id="n1",
                       include_attachments=True, filed_by=None, prefix="k")
        assert store.rows == {}


def test_build_mime_from_item_round_trips_through_parse_email():
    item = {"subject": "Aus dem Add-in", "from": "Anna Muster <anna@example.ch>",
            "to": ["beat@kanzlei.ch"], "cc": [], "date": "2026-03-15T09:00:00Z",
            "internet_message_id": "<item-1@example.ch>", "body_text": "Hallo",
            "body_html": "<p>Hallo</p>",
            "attachments": [{"name": "a.txt", "content_type": "text/plain",
                             "content_base64": base64.b64encode(b"inhalt").decode()}]}
    parsed = parse_email(build_mime_from_item(item))
    assert parsed.subject == "Aus dem Add-in"
    assert parsed.message_id == "item-1@example.ch"
    assert parsed.attachments[0].data == b"inhalt"
```

Create `tests/test_filed_email_store.py` (real `platform-db`, skipped without one — the section-B convention):

```python
"""filed_emails — one row per (message, matter), in platform-db (0004_filing.sql)."""
import pytest

from conftest import PLATFORM_DB_TEST_DSN, platform_db_reachable

pytestmark = pytest.mark.skipif(not platform_db_reachable(),
                                reason=f"No PostgreSQL at {PLATFORM_DB_TEST_DSN}")

from email_filing import FiledEmailStore  # noqa: E402


def test_lookup_is_none_until_marked(platform_db):
    store = FiledEmailStore(platform_db)
    assert store.lookup("a" * 64, "n1") is None
    store.mark_filed(message_id_hash="a" * 64, node_id="n1", pointer="k/mail/aaaa",
                     filed_by=None, attachment_count=2)
    row = store.lookup("a" * 64, "n1")
    assert row["pointer"] == "k/mail/aaaa"
    assert row["attachment_count"] == 2
    assert row["filed_at"]


def test_second_mark_is_idempotent(platform_db):
    store = FiledEmailStore(platform_db)
    store.mark_filed(message_id_hash="b" * 64, node_id="n1", pointer="p", filed_by=None,
                     attachment_count=0)
    store.mark_filed(message_id_hash="b" * 64, node_id="n1", pointer="p", filed_by=None,
                     attachment_count=0)
    assert platform_db.execute("SELECT count(*) FROM filed_emails").fetchone()[0] == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_email_filing.py tests/test_filed_email_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'email_filing'` (the store test is skipped when no PostgreSQL is reachable; with one, it fails on the missing table).

- [ ] **Step 3: Public extension set on the upload builder**

In `src/knovas_extract_upload.py`, directly after `_EXT_TO_MIME` (line 21) add:

```python
#: Extensions the Platform can turn into transmit parts. Filing (email_filing.py)
#: skips attachments outside this set instead of uploading a path-only stub.
SUPPORTED_EXTENSIONS = frozenset(_EXT_TO_MIME)
```

- [ ] **Step 4: Migration `0004_filing.sql`**

Create `src/identity/migrations/0004_filing.sql`:

```sql
-- Ablageprotokoll des Outlook-Add-ins (Pflichtenheft H2).
--
-- Eine Zeile je (Nachricht, Akte). Der Hash ist sha256 der Message-ID oder,
-- wenn die Kopfzeile fehlt, eines Inhalts-Fingerabdrucks. Die Nachricht
-- selbst liegt nie hier — nur in Knovas, unter `pointer`. Dieselbe Nachricht
-- darf unter einer zweiten Akte abgelegt werden; unter derselben nicht zweimal.
--
-- Plan: docs/superpowers/plans/2026-08-15-pflichtenheft-d-j-components.md (KC-E-1)

CREATE TABLE IF NOT EXISTS filed_emails (
    id               BIGSERIAL   PRIMARY KEY,
    message_id_hash  CHAR(64)    NOT NULL,
    node_id          TEXT        NOT NULL,
    pointer          TEXT        NOT NULL,
    attachment_count INTEGER     NOT NULL DEFAULT 0,
    filed_by         UUID        REFERENCES users(id) ON DELETE SET NULL,
    filed_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (message_id_hash, node_id)
);

CREATE INDEX IF NOT EXISTS idx_filed_emails_node ON filed_emails (node_id, filed_at DESC);
```

---

## PART KC-F — RemoteController — metadata, XLSX/PPTX, OCR evidence, mailbox mirror, PST + migration, index status (F1, F2, F3, D5, F5, H1, H4)

### Task KC-F-1: Document metadata at ingest (ExtractedDocument/ExtractionPayload fields, language fallback, rule table, `metadata` on init)

**Requirements:** F3, D5, F5, F6 (design §7.1, §5.2)
**Files:**
- Modify: `src/sync/extract_content.py:13-53` (`ExtractionPayload`, `payload_from_extraction_result`)
- Modify: `src/sync/document_text.py:101-128` (`ExtractedDocument`), `:264-275` (`_extract_bytes` return)
- Create: `src/sync/language_detect.py`
- Create: `src/sync/document_metadata.py`
- Modify: `src/sync/knovas_uploader.py:13-18` (imports), `:58-72` (`__init__`), `:143-187` (`upload_file` metadata + `path_prefix`)
- Modify: `pyproject.toml:11-24` (add `py3langid`), `.env.example` (new block after line 79), `docs/configuration.md:66-93`
- Test: `tests/unit/test_language_detect.py` (new), `tests/unit/test_document_metadata.py` (new), `tests/unit/test_document_text.py` (DOCX core-properties case), `tests/unit/test_knovas_uploader.py` (metadata assertions)

**Interfaces:**
- Consumes: `knovas_extract.result.Metadata` (`title, author, language, created, modified, extra`); `init_document_transmission` `metadata` object (defined in Part KB) — RC sends it only when `RC_SEND_DOCUMENT_METADATA` is on (default on) and the dict is non-empty.
- Produces: `ExtractionPayload`/`ExtractedDocument` fields `author: Optional[str]`, `language: Optional[str]`, `created: Optional[str]`, `modified: Optional[str]`, `document_type: Optional[str]`, `document_status: Optional[str]`, `document_date: Optional[str]`, `extra: Optional[dict[str, Any]]`; `sync.extract_content.METADATA_EXTRA_WHITELIST`; `sync.language_detect.detect_language(text: str) -> Optional[str]`, `normalize_language_tag(raw) -> Optional[str]`, `language_detect_enabled() -> bool`; `sync.document_metadata.MetadataRules(extension_types, type_vocab, default_source_kind, detect_language)`, `load_metadata_rules() -> MetadataRules`, `load_folder_meta(file_path: Path, relative_path: str) -> dict` (adds `"_subpath"`), `build_document_metadata(extracted, relative_path, rules, *, folder_meta=None) -> dict`, `send_document_metadata_enabled() -> bool`, `document_type_from_filename`, `document_status_from_filename`, `document_date_from(created, filename)`; env `RC_SEND_DOCUMENT_METADATA=1`, `RC_LANGUAGE_DETECT=1`, `RC_SOURCE_KIND=share`, `RC_DOCUMENT_TYPE_VOCAB` (CSV; default vocabulary below); folder sidecar `.knovas-meta.json` `{document_type?, document_status?, language?, source_kind?, path_prefix?}`; `init_body["metadata"]`, and `init_body["path"] = f"{path_prefix}/{_subpath}"` when a folder sidecar declares `path_prefix` (consumed by KC-F-7/KC-F-8 provenance).

- [ ] **Step 1: Failing test — payload carries author/language/created/extra**

Create `tests/unit/test_extract_content_metadata.py`:

```python
from knovas_extract.result import Content, ExtractionResult, Extractor, Metadata, Source

from sync.extract_content import METADATA_EXTRA_WHITELIST, payload_from_extraction_result


def _result(**meta):
    return ExtractionResult(
        spec_version="1.3.0",
        source=Source(mime_type="text/plain", sha256="0" * 64, size_bytes=1),
        metadata=Metadata(**meta),
        content=Content(text="Hallo Welt."),
        warnings=[],
        extractor=Extractor(name="knovas-extract-python", version="0.3.0"),
    )


def test_payload_reads_first_class_metadata():
    payload = payload_from_extraction_result(
        _result(author="Dr. A. Muster", language="de-CH", created="2026-03-01T09:00:00+00:00", modified="2026-03-02T09:00:00+00:00")
    )
    assert payload.author == "Dr. A. Muster"
    assert payload.language == "de-CH"
    assert payload.created == "2026-03-01T09:00:00+00:00"
    assert payload.modified == "2026-03-02T09:00:00+00:00"


def test_payload_whitelists_extra_keys():
    payload = payload_from_extraction_result(
        _result(extra={"eml:message_id": "<abc@example.ch>", "eml:auth_results": "spf=pass", "docx:company": "Kanzlei AG"})
    )
    assert payload.extra == {"eml:message_id": "<abc@example.ch>", "docx:company": "Kanzlei AG"}
    assert "eml:auth_results" not in METADATA_EXTRA_WHITELIST


def test_payload_extra_none_when_nothing_whitelisted():
    assert payload_from_extraction_result(_result(extra={"pdf:format": "PDF 1.7"})).extra is None
```

- [ ] **Step 2: Run** `python -m pytest tests/unit/test_extract_content_metadata.py -q` → FAIL (`ImportError: cannot import name 'METADATA_EXTRA_WHITELIST'`).

- [ ] **Step 3: Extend `ExtractionPayload` and `payload_from_extraction_result`** in `src/sync/extract_content.py`:

```python
## Metadata.extra keys forwarded to Knovas (design §5.2: <= 16 namespaced keys,
## values <= 256 chars). Everything else stays in the extractor result.
METADATA_EXTRA_WHITELIST = (
    "eml:message_id", "eml:from", "eml:to", "eml:in_reply_to",
    "msg:message_id", "msg:from", "msg:conversation_index",
    "docx:last_modified_by", "docx:company", "docx:content_status",
    "pdf:producer", "rc:extractor",
)


@dataclass(frozen=True)
class ExtractionPayload:
    """Normalized extractor output for the RC upload pipeline."""

    text: str
    sentences: Optional[list[Sentence]]
    sections: Optional[list[Section]]
    pages: Optional[list[Page]]
    title: Optional[str]
    description: Optional[str]
    tables: Optional[list[dict[str, Any]]]
    author: Optional[str] = None
    language: Optional[str] = None
    created: Optional[str] = None
    modified: Optional[str] = None
    document_type: Optional[str] = None
    document_status: Optional[str] = None
    document_date: Optional[str] = None
    extra: Optional[dict[str, Any]] = None


def _opt_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def whitelisted_extra(metadata: Any) -> Optional[dict[str, Any]]:
    extra = getattr(metadata, "extra", None) or {}
    if not isinstance(extra, dict):
        return None
    picked = {k: extra[k] for k in METADATA_EXTRA_WHITELIST if extra.get(k) is not None}
    return picked or None
```

and in `payload_from_extraction_result` return `ExtractionPayload(..., tables=tables, author=_opt_str(getattr(metadata, "author", None)), language=_opt_str(getattr(metadata, "language", None)), created=_opt_str(getattr(metadata, "created", None)), modified=_opt_str(getattr(metadata, "modified", None)), extra=whitelisted_extra(metadata))`.

- [ ] **Step 4: Run** the test file → PASS (3 tests). Then extend `ExtractedDocument` (`document_text.py:122-128`) with the same eight defaulted fields (`author`, `language`, `created`, `modified`, `document_type`, `document_status`, `document_date`, `extra`) and thread them in `_extract_bytes`'s return (`document_text.py:267-275`): `author=payload.author, language=payload.language, created=payload.created, modified=payload.modified, extra=payload.extra`. Add to `tests/unit/test_document_text.py`:

```python
def test_docx_core_properties_reach_extracted_document():
    from datetime import datetime

    docx = pytest.importorskip("docx")
    buf = io.BytesIO()
    document = docx.Document()
    document.core_properties.author = "Dr. A. Muster"
    document.core_properties.language = "de-CH"
    document.core_properties.created = datetime(2026, 3, 1, 9, 0, 0)
    document.add_paragraph("Der Vertrag wird auf unbestimmte Zeit geschlossen.")
    document.save(buf)
    from sync.document_text import _extract_bytes

    doc = _extract_bytes(buf.getvalue(), ".docx")
    assert doc.author == "Dr. A. Muster"
    assert doc.language == "de-CH"
    assert doc.created is not None and doc.created.startswith("2026-03-01")
```

Run `python -m pytest tests/unit/test_document_text.py -q -k core_properties` → PASS. Commit: `git commit -am "feat(rc): carry extractor author/language/created/modified/extra into ExtractedDocument"`.

- [ ] **Step 5: Failing tests — language fallback**

Create `tests/unit/test_language_detect.py`:

```python
import sync.language_detect as ld


class _Ident:
    def __init__(self, answer):
        self._answer = answer
        self.calls = 0

    def classify(self, text):
        self.calls += 1
        return self._answer


def test_normalize_language_tag():
    assert ld.normalize_language_tag("de-CH") == "de"
    assert ld.normalize_language_tag(" FR ") == "fr"
    assert ld.normalize_language_tag("und") == "und"
    assert ld.normalize_language_tag("") is None
    assert ld.normalize_language_tag("x-klingon-1") is None


def test_detect_language_uses_identifier_and_threshold(monkeypatch):
    monkeypatch.delenv("RC_LANGUAGE_DETECT", raising=False)
    monkeypatch.setattr(ld, "_get_identifier", lambda: _Ident(("de", 0.98)))
    assert ld.detect_language("Der Kläger beantragt die Aufhebung der Verfügung. " * 3) == "de"
    monkeypatch.setattr(ld, "_get_identifier", lambda: _Ident(("de", 0.40)))
    assert ld.detect_language("Der Kläger beantragt die Aufhebung der Verfügung. " * 3) is None


def test_detect_language_short_text_and_disabled(monkeypatch):
    ident = _Ident(("fr", 0.99))
    monkeypatch.setattr(ld, "_get_identifier", lambda: ident)
    assert ld.detect_language("Bonjour") is None
    monkeypatch.setenv("RC_LANGUAGE_DETECT", "0")
    assert ld.detect_language("Le recourant conclut à l'annulation de la décision attaquée. " * 3) is None
    assert ident.calls == 0


def test_detect_language_unavailable_model_degrades(monkeypatch):
    monkeypatch.delenv("RC_LANGUAGE_DETECT", raising=False)
    monkeypatch.setattr(ld, "_get_identifier", lambda: None)
    assert ld.detect_language("Il ricorrente chiede l'annullamento della decisione impugnata. " * 3) is None
```

- [ ] **Step 6: Run** → FAIL (`ModuleNotFoundError: sync.language_detect`). Create `src/sync/language_detect.py`:

```python
"""Language fallback for documents whose extractor metadata carries none (F5).

knovas-extract populates `metadata.language` only from explicit document
metadata (PDF XMP dc:language, DOCX dc:language, HTML lang) — absent on most
Swiss legal documents. When it is missing, RC classifies the first
`LANGUAGE_DETECT_MAX_CHARS` characters with py3langid restricted to the four
languages the Knovas index is tuned for. Anything uncertain degrades to None
(the API stores `und`); language never blocks ingest. Disable with
`RC_LANGUAGE_DETECT=0`.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

SUPPORTED_LANGUAGES = ("de", "fr", "it", "en")
LANGUAGE_DETECT_MAX_CHARS = 20_000
MIN_SAMPLE_CHARS = 40
MIN_CONFIDENCE = 0.85

_identifier: Any = None
_identifier_failed = False


def language_detect_enabled() -> bool:
    raw = (os.environ.get("RC_LANGUAGE_DETECT") or "").strip().lower()
    if raw in ("", "1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    logger.warning("Invalid RC_LANGUAGE_DETECT=%r; enabling detection", raw)
    return True


def normalize_language_tag(raw: Any) -> Optional[str]:
    """`de-CH` -> `de`; keeps 2-3 letter primary subtags, drops junk."""
    if raw is None:
        return None
    tag = str(raw).strip().lower().replace("_", "-")
    if not tag:
        return None
    primary = tag.split("-", 1)[0]
    if 2 <= len(primary) <= 3 and primary.isalpha():
        return primary
    return None


def _get_identifier() -> Any:
    global _identifier, _identifier_failed
    if _identifier is not None or _identifier_failed:
        return _identifier
    try:
        from py3langid.langid import MODEL_FILE, LanguageIdentifier

        ident = LanguageIdentifier.from_pickled_model(MODEL_FILE, norm_probs=True)
        ident.set_languages(list(SUPPORTED_LANGUAGES))
        _identifier = ident
    except Exception as exc:  # noqa: BLE001 - detection is optional
        _identifier_failed = True
        logger.warning("Language detection unavailable (%s); documents keep language=None", exc)
    return _identifier


def detect_language(text: str) -> Optional[str]:
    if not language_detect_enabled():
        return None
    sample = (text or "")[:LANGUAGE_DETECT_MAX_CHARS].strip()
    if len(sample) < MIN_SAMPLE_CHARS:
        return None
    ident = _get_identifier()
    if ident is None:
        return None
    try:
        lang, confidence = ident.classify(sample)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Language detection failed: %s", exc)
        return None
    if lang not in SUPPORTED_LANGUAGES or float(confidence) < MIN_CONFIDENCE:
        return None
    return str(lang)
```

Add `"py3langid>=0.3,<1",` to `pyproject.toml` `dependencies` (the Dockerfile builder loop at `Dockerfile:12-20` installs every non-knovas-extract dependency, so no Dockerfile edit). Run → PASS (4 tests). Commit: `git commit -am "feat(rc): py3langid language fallback behind RC_LANGUAGE_DETECT"`.

- [ ] **Step 7: Failing tests — the rule table**

Create `tests/unit/test_document_metadata.py`:

```python
import json
from pathlib import Path

import pytest

from sync.document_metadata import (
    MetadataRules,
    build_document_metadata,
    document_date_from,
    document_status_from_filename,
    document_type_from_filename,
    load_folder_meta,
    load_metadata_rules,
    send_document_metadata_enabled,
)
from sync.document_text import ExtractedDocument


def _doc(**kw) -> ExtractedDocument:
    return ExtractedDocument(text=kw.pop("text", "x" * 100), sentences=None, **kw)


def test_document_type_autodoc_pattern_and_vocab():
    rules = MetadataRules()
    assert document_type_from_filename("12345678-ABCD_Akte4711_Verfügung.pdf", rules) == "Verfügung"
    assert document_type_from_filename("Weber_Vertrag_Entwurf.docx", rules) == "Vertrag"
    assert document_type_from_filename("Brief_an_Mandant.docx", rules) == "Brief"
    assert document_type_from_filename("scan0001.pdf", rules) is None


def test_document_status_and_date_heuristics():
    assert document_status_from_filename("Vertrag_Entwurf_v3.docx") == "draft"
    assert document_status_from_filename("Vertrag unterzeichnet.pdf") == "executed"
    assert document_status_from_filename("Vertrag_final.pdf") == "final"
    assert document_status_from_filename("Vertrag.pdf") is None
    assert document_date_from("2026-03-01T09:00:00+00:00", "x.pdf") == "2026-03-01"
    assert document_date_from(None, "2026-03-01_Brief.pdf") == "2026-03-01"
    assert document_date_from(None, "Brief_01.03.2026.pdf") == "2026-03-01"
    assert document_date_from(None, "20260301_Brief.pdf") == "2026-03-01"
    assert document_date_from(None, "Brief_99.99.2026.pdf") is None


def test_build_document_metadata_precedence(monkeypatch):
    monkeypatch.setenv("RC_LANGUAGE_DETECT", "0")
    rules = MetadataRules()
    doc = _doc(author="  Dr. A. Muster ", language="de-CH", created="2026-03-01T09:00:00+00:00",
               extra={"eml:message_id": "<a@b>", "x": "not-namespaced", "eml:from": "a" * 400})
    meta = build_document_metadata(doc, "akten/2024-0815/Weber_Vertrag_Entwurf.docx", rules)
    assert meta == {
        "author": "Dr. A. Muster",
        "document_type": "Vertrag",
        "language": "de",
        "document_date": "2026-03-01",
        "document_status": "draft",
        "source_kind": "share",
        "extra": {"eml:message_id": "<a@b>", "eml:from": "a" * 256},
    }
    folder = {"document_type": "Korrespondenz", "source_kind": "mailbox", "document_status": "final"}
    meta2 = build_document_metadata(doc, "mail/x.eml", rules, folder_meta=folder)
    assert meta2["document_type"] == "Korrespondenz"
    assert meta2["source_kind"] == "mailbox"
    assert meta2["document_status"] == "final"
    assert build_document_metadata(_doc(), "unknown.bin", rules) == {"source_kind": "share"}


def test_extension_type_map_and_env_rules(monkeypatch):
    monkeypatch.setenv("RC_SOURCE_KIND", "onedrive")
    monkeypatch.setenv("RC_DOCUMENT_TYPE_VOCAB", "Mahnung, Offerte")
    monkeypatch.setenv("RC_LANGUAGE_DETECT", "0")
    rules = load_metadata_rules()
    assert rules.default_source_kind == "onedrive"
    assert rules.type_vocab == ("Mahnung", "Offerte")
    assert build_document_metadata(_doc(), "post/mail.eml", rules)["document_type"] == "E-Mail"
    assert build_document_metadata(_doc(), "post/Offerte_2026.pdf", rules)["document_type"] == "Offerte"


def test_load_folder_meta_nearest_wins(tmp_path: Path):
    root = tmp_path / "root"
    (root / "a" / "b").mkdir(parents=True)
    (root / ".knovas-meta.json").write_text(json.dumps({"source_kind": "pst", "document_type": "Alt"}), encoding="utf-8")
    (root / "a" / ".knovas-meta.json").write_text(json.dumps({"document_type": "Neu", "path_prefix": "pst://archiv.pst/a"}), encoding="utf-8")
    (root / "a" / "b" / "m.eml").write_bytes(b"x")
    meta = load_folder_meta(root / "a" / "b" / "m.eml", "a/b/m.eml")
    assert meta["source_kind"] == "pst"
    assert meta["document_type"] == "Neu"
    assert meta["path_prefix"] == "pst://archiv.pst/a"
    assert meta["_subpath"] == "b/m.eml"
    (root / "a" / "b" / ".knovas-meta.json").write_text("{not json", encoding="utf-8")
    assert load_folder_meta(root / "a" / "b" / "m.eml", "a/b/m.eml")["document_type"] == "Neu"


def test_send_document_metadata_enabled(monkeypatch):
    monkeypatch.delenv("RC_SEND_DOCUMENT_METADATA", raising=False)
    assert send_document_metadata_enabled() is True
    monkeypatch.setenv("RC_SEND_DOCUMENT_METADATA", "0")
    assert send_document_metadata_enabled() is False
    monkeypatch.setenv("RC_SEND_DOCUMENT_METADATA", "maybe")
    assert send_document_metadata_enabled() is True
```

- [ ] **Step 8: Run** → FAIL (`ModuleNotFoundError: sync.document_metadata`). Create `src/sync/document_metadata.py`:

```python
"""Per-document `metadata` for `init_document_transmission` (F3, D5, F5, F6).

Sources, in precedence order per field:
  document_type   folder `.knovas-meta.json` > extractor > filename (AutoDoc
                  `{GUID}_{AkteID}_{Typ}` or a vocabulary word) > extension map
  document_status folder sidecar > extractor > filename heuristics
  document_date   extractor document_date > extractor created > date in filename
  language        folder sidecar > extractor tag > py3langid fallback
  source_kind     folder sidecar > RC_SOURCE_KIND (default `share`)
The result contains only keys with values; an empty dict means "send nothing".
"""
from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping, Optional

from sync.language_detect import detect_language, language_detect_enabled, normalize_language_tag

logger = logging.getLogger(__name__)

FOLDER_META_FILENAME = ".knovas-meta.json"
MAX_FOLDER_META_DEPTH = 8
MAX_AUTHOR_CHARS = 500
MAX_TYPE_CHARS = 128
MAX_EXTRA_KEYS = 16
MAX_EXTRA_VALUE_CHARS = 256
DOCUMENT_STATUSES = frozenset({"draft", "final", "executed", "unknown"})
SOURCE_KINDS = frozenset({"share", "onedrive", "mailbox", "pst", "upload", "addin"})
DEFAULT_EXTENSION_TYPES: dict[str, str] = {
    ".eml": "E-Mail", ".msg": "E-Mail", ".xlsx": "Tabelle", ".pptx": "Präsentation",
}
DEFAULT_TYPE_VOCAB = (
    "Vertrag", "Verfügung", "Urteil", "Entscheid", "Beschluss", "Klage", "Klageantwort",
    "Replik", "Duplik", "Eingabe", "Stellungnahme", "Gutachten", "Brief", "Memo",
    "Aktennotiz", "Vollmacht", "Rechnung", "Protokoll", "E-Mail",
)
_AUTODOC_STEM_RE = re.compile(r"^(?P<guid>[0-9A-Fa-f-]{8,})_(?P<akten_id>[^_]+)_(?P<doc_type>.+)$")
_TOKEN_SPLIT_RE = re.compile(r"[\s_\-]+")
_STATUS_RULES = (
    (re.compile(r"(?:^|[\s_\-.])(?:entwurf|draft|brouillon|bozza)(?:[\s_\-.]|$)", re.I), "draft"),
    (re.compile(r"(?:^|[\s_\-.])(?:unterzeichnet|signiert|signed|executed|sign[ée]|firmato)(?:[\s_\-.]|$)", re.I), "executed"),
    (re.compile(r"(?:^|[\s_\-.])(?:final|definitiv|d[ée]finitif|definitivo)(?:[\s_\-.]|$)", re.I), "final"),
)
_DATE_RES = (
    (re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)"), ("y", "m", "d")),
    (re.compile(r"(?<!\d)(\d{4})(\d{2})(\d{2})(?!\d)"), ("y", "m", "d")),
    (re.compile(r"(?<!\d)(\d{2})\.(\d{2})\.(\d{4})(?!\d)"), ("d", "m", "y")),
)


@dataclass(frozen=True)
class MetadataRules:
    extension_types: Mapping[str, str] = field(default_factory=lambda: dict(DEFAULT_EXTENSION_TYPES))
    type_vocab: tuple[str, ...] = DEFAULT_TYPE_VOCAB
    default_source_kind: str = "share"
    detect_language: bool = True


def send_document_metadata_enabled() -> bool:
    raw = (os.environ.get("RC_SEND_DOCUMENT_METADATA") or "").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    if raw not in ("", "1", "true", "yes", "on"):
        logger.warning("Invalid RC_SEND_DOCUMENT_METADATA=%r; sending metadata", raw)
    return True


def load_metadata_rules() -> MetadataRules:
    vocab_raw = (os.environ.get("RC_DOCUMENT_TYPE_VOCAB") or "").strip()
    vocab = tuple(v.strip() for v in vocab_raw.split(",") if v.strip()) if vocab_raw else DEFAULT_TYPE_VOCAB
    source_kind = (os.environ.get("RC_SOURCE_KIND") or "share").strip().lower() or "share"
    if source_kind not in SOURCE_KINDS:
        logger.warning("Invalid RC_SOURCE_KIND=%r; using 'share'", source_kind)
        source_kind = "share"
    return MetadataRules(type_vocab=vocab, default_source_kind=source_kind, detect_language=language_detect_enabled())


def load_folder_meta(file_path: Path, relative_path: str) -> dict[str, Any]:
    """Nearest-wins merge of `.knovas-meta.json` from the file's directory up to
    the source root; `_subpath` is the path relative to the nearest sidecar's
    directory (used for `path_prefix` provenance)."""
    rel_parts = Path(relative_path.replace("\\", "/")).parts
    depth = min(max(len(rel_parts) - 1, 0), MAX_FOLDER_META_DEPTH)
    layers: list[tuple[int, dict[str, Any]]] = []
    directory = file_path.parent
    for level in range(depth + 1):
        candidate = directory / FOLDER_META_FILENAME
        if candidate.is_file():
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                logger.warning("Ignoring unreadable %s: %s", candidate, exc)
                data = None
            if isinstance(data, dict):
                layers.append((level, data))
        directory = directory.parent
    merged: dict[str, Any] = {}
    for _level, layer in reversed(layers):
        merged.update({k: v for k, v in layer.items() if not str(k).startswith("_")})
    if layers:
        nearest_level = layers[0][0]
        merged["_subpath"] = "/".join(rel_parts[len(rel_parts) - 1 - nearest_level:])
    return merged


def _clean_text(value: Any, max_chars: int) -> Optional[str]:
    if value is None:
        return None
    s = unicodedata.normalize("NFC", str(value))
    s = "".join(ch for ch in s if ch == "\t" or ord(ch) >= 32).strip()
    return s[:max_chars] if s else None


def document_type_from_filename(filename: str, rules: MetadataRules) -> Optional[str]:
    stem = Path(filename).stem
    m = _AUTODOC_STEM_RE.match(stem)
    if m:
        return _clean_text(m.group("doc_type").replace("_", " "), MAX_TYPE_CHARS)
    tokens = {t.casefold() for t in _TOKEN_SPLIT_RE.split(stem) if t}
    for word in rules.type_vocab:
        if word.casefold() in tokens:
            return word
    return None


def document_status_from_filename(filename: str) -> Optional[str]:
    stem = Path(filename).stem
    for pattern, status in _STATUS_RULES:
        if pattern.search(stem):
            return status
    return None


def document_date_from(created: Optional[str], filename: str) -> Optional[str]:
    if created:
        try:
            return datetime.fromisoformat(str(created).replace("Z", "+00:00")).date().isoformat()
        except ValueError:
            pass
    stem = Path(filename).stem
    for pattern, order in _DATE_RES:
        for m in pattern.finditer(stem):
            parts = dict(zip(order, m.groups()))
            try:
                return date(int(parts["y"]), int(parts["m"]), int(parts["d"])).isoformat()
            except ValueError:
                continue
    return None


def build_document_metadata(
    extracted: Any,
    relative_path: str,
    rules: MetadataRules,
    *,
    folder_meta: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    folder = dict(folder_meta or {})
    filename = Path(relative_path.replace("\\", "/")).name
    ext = Path(filename).suffix.lower()
    out: dict[str, Any] = {}

    author = _clean_text(getattr(extracted, "author", None), MAX_AUTHOR_CHARS)
    if author:
        out["author"] = author

    doc_type = (
        _clean_text(folder.get("document_type"), MAX_TYPE_CHARS)
        or _clean_text(getattr(extracted, "document_type", None), MAX_TYPE_CHARS)
        or document_type_from_filename(filename, rules)
        or rules.extension_types.get(ext)
    )
    if doc_type:
        out["document_type"] = doc_type

    language = normalize_language_tag(folder.get("language")) or normalize_language_tag(getattr(extracted, "language", None))
    if language is None and rules.detect_language:
        language = detect_language(getattr(extracted, "text", "") or "")
    if language:
        out["language"] = language

    doc_date = document_date_from(getattr(extracted, "document_date", None), "") or document_date_from(
        getattr(extracted, "created", None), filename
    )
    if doc_date:
        out["document_date"] = doc_date

    status = folder.get("document_status") or getattr(extracted, "document_status", None) or document_status_from_filename(filename)
    if status in DOCUMENT_STATUSES:
        out["document_status"] = status

    source_kind = str(folder.get("source_kind") or rules.default_source_kind).strip().lower()
    if source_kind in SOURCE_KINDS:
        out["source_kind"] = source_kind

    extra: dict[str, str] = {}
    for key, value in (getattr(extracted, "extra", None) or {}).items():
        if len(extra) >= MAX_EXTRA_KEYS:
            break
        k = str(key).strip()[:64]
        v = _clean_text(value, MAX_EXTRA_VALUE_CHARS)
        if k and ":" in k and v:
            extra[k] = v
    if extra:
        out["extra"] = extra
    return out
```

Run `python -m pytest tests/unit/test_document_metadata.py -q` → PASS (6 tests). Commit: `git commit -am "feat(rc): document metadata rule table (type/status/date/language/source_kind, folder sidecar)"`.

- [ ] **Step 9: Failing test — the uploader sends `metadata`**

Append to `tests/unit/test_knovas_uploader.py`:

```python
def test_upload_sends_metadata_object(mock_config, tmp_path, monkeypatch):
    monkeypatch.setenv("RC_LANGUAGE_DETECT", "0")
    monkeypatch.delenv("RC_SEND_DOCUMENT_METADATA", raising=False)
    f = tmp_path / "Weber_Vertrag_Entwurf_2026-03-01.txt"
    f.write_text("Die Parteien vereinbaren eine Kündigungsfrist von drei Monaten.", encoding="utf-8")

    uploader = SemantixUploader()
    sync_body = {"ingestion": {"identifier_prefix": "corpus"}}
    with patch.object(uploader, "_request") as req:
        req.side_effect = [_ok_response(), _ok_response()]
        result = uploader.upload_file(f, "akten/Weber_Vertrag_Entwurf_2026-03-01.txt", sync_body)

    assert result.status == "ok"
    meta = req.call_args_list[0].kwargs["json_body"]["metadata"]
    assert meta["document_type"] == "Vertrag"
    assert meta["document_status"] == "draft"
    assert meta["document_date"] == "2026-03-01"
    assert meta["source_kind"] == "share"


def test_upload_metadata_disabled_and_path_prefix(mock_config, tmp_path, monkeypatch):
    monkeypatch.setenv("RC_SEND_DOCUMENT_METADATA", "0")
    folder = tmp_path / "mail" / "Inbox"
    folder.mkdir(parents=True)
    (folder / ".knovas-meta.json").write_text(
        '{"source_kind": "mailbox", "path_prefix": "mailbox://anna@kanzlei.ch/Inbox"}', encoding="utf-8"
    )
    f = folder / "abc.txt"
    f.write_text("Hallo", encoding="utf-8")

    uploader = SemantixUploader()
    with patch.object(uploader, "_request") as req:
        req.side_effect = [_ok_response(), _ok_response()]
        uploader.upload_file(f, "mail/Inbox/abc.txt", {"ingestion": {"identifier_prefix": "corpus"}})

    init_json = req.call_args_list[0].kwargs["json_body"]
    assert "metadata" not in init_json
    assert init_json["path"] == "mailbox://anna@kanzlei.ch/Inbox/abc.txt"
    assert init_json["identifier"] == "corpus/mail/Inbox/abc.txt"
```

- [ ] **Step 10: Run** → FAIL (`KeyError: 'metadata'`). Wire the uploader (`src/sync/knovas_uploader.py`): import `from sync.document_metadata import build_document_metadata, load_folder_meta, load_metadata_rules, send_document_metadata_enabled`; in `__init__` add `self._metadata_rules = load_metadata_rules()`; inside the `try:` of `upload_file` right after `doc = extract_document_guarded(file_path)` add `folder_meta = load_folder_meta(file_path, relative_path)`; after the `init_body` literal (line 184) insert:

```python
        path_prefix = str(folder_meta.get("path_prefix") or "").strip().rstrip("/")
        if path_prefix and folder_meta.get("_subpath"):
            # Provenance from a connector sidecar (mailbox://…, pst://…). The
            # identifier keeps the mirror-relative path; only the BM25 `path`
            # field carries the origin.
            init_body["path"] = f"{path_prefix}/{folder_meta['_subpath']}"[:2000]
        if send_document_metadata_enabled():
            metadata = build_document_metadata(doc, relative_path, self._metadata_rules, folder_meta=folder_meta)
            if metadata:
                init_body["metadata"] = metadata
```

Run `python -m pytest tests/unit/test_knovas_uploader.py -q` → PASS (all, incl. the two new). Commit: `git commit -am "feat(rc): send document metadata on init_document_transmission (RC_SEND_DOCUMENT_METADATA)"`.

- [ ] **Step 11: Config + docs.** Append to `.env.example` after line 79:

```bash
## Document metadata sent with init_document_transmission (F3/D5/F5). Requires
## the Knovas API `metadata` contract (Secure_API.md, "metadata"); older APIs
## reject unknown keys with 400 -> set to 0 until the tenant API is upgraded.
## RC_SEND_DOCUMENT_METADATA=1
## RC_LANGUAGE_DETECT=1            # py3langid fallback (de/fr/it/en) when the extractor has no language
## RC_SOURCE_KIND=share            # share|onedrive|mailbox|pst|upload|addin default for this RC
## RC_DOCUMENT_TYPE_VOCAB=         # CSV of document-type words matched in file names (default: Vertrag,Verfügung,Urteil,...)
```

In `docs/configuration.md` add before "## Supported document formats" (line 66):

```markdown
## Document metadata

Every upload carries a `metadata` object (author, document_type, language, document_date, document_status, source_kind, extra) built by `sync/document_metadata.py`: extractor metadata first (DOCX core properties, PDF XMP, e-mail headers), then file-name rules (`{GUID}_{AkteID}_{Typ}`, a document-type vocabulary word, `Entwurf|draft` → draft, `unterzeichnet|signed` → executed, `final` → final, dates `YYYY-MM-DD` / `DD.MM.YYYY` / `YYYYMMDD`), then a per-folder sidecar `.knovas-meta.json` (`document_type`, `document_status`, `language`, `source_kind`, `path_prefix`; nearest folder wins). Language falls back to `py3langid` (`RC_LANGUAGE_DETECT`). Switch off with `RC_SEND_DOCUMENT_METADATA=0` when the tenant API predates the contract. Matter and practice area are **not** metadata — they are graph assignments (see `RC_MATTER_PATH_RULE`).
```

Add a `## Unreleased` bullet to `CHANGELOG.md`: `- Uploads carry document metadata (author, type, language, date, status, source kind, whitelisted extractor extras) — RC_SEND_DOCUMENT_METADATA, RC_LANGUAGE_DETECT, RC_SOURCE_KIND, RC_DOCUMENT_TYPE_VOCAB, per-folder .knovas-meta.json.` Run the whole suite `python -m pytest -q` → PASS. Commit: `git commit -am "docs(rc): document metadata rules and env keys"`.

---

---

### Task KC-F-2: `RC_MATTER_PATH_RULE` — matter node from the relative path, `graph_assign` on init

**Requirements:** F3, C-plan intake (design §7.1 last paragraph)
**Files:**
- Create: `src/sync/graph_lookup.py`
- Modify: `src/sync/knovas_uploader.py` (`__init__` after `self._metadata_rules`; `upload_file` after the metadata block from KC-F-1)
- Modify: `.env.example`, `docs/configuration.md` (Document metadata section), `CHANGELOG.md`
- Test: `tests/unit/test_graph_lookup.py` (new), `tests/unit/test_knovas_uploader.py`

**Interfaces:**
- Consumes: `GET /secured/graph/identifiers/search?q=&kind=matter_number&limit=5` → `{"results": [...], "degraded": bool}` (defined in Part KB); `graph_assign` on init (`Secure_API.md:126`); tenant mTLS settings from `config.AppConfig` (`semantix_secure_base_url`, `semantix_client_cert_path`, `semantix_client_key_path`, `semantix_ca_cert_path`).
- Produces: `sync.graph_lookup.matter_path_rule_from_env() -> Optional[re.Pattern]`, `extract_matter_number(relative_path: str, pattern) -> Optional[str]`, `GraphLookup(*, base_url, cert, verify, session=None, timeout=20.0, ttl_seconds=3600, clock=time.monotonic)` with `resolve_matter_node_id(matter_number) -> Optional[str]` (positive and negative results cached for `ttl_seconds`), `build_graph_lookup_from_env(cfg=None) -> Optional[GraphLookup]`; env `RC_MATTER_PATH_RULE` (regex with named group `matter`, applied with `search` over the forward-slash relative path), `RC_MATTER_LOOKUP_TTL_SECONDS=3600`; `init_body["graph_assign"] = {"node_ids": [node_id]}`.

- [ ] **Step 1: Failing tests** — create `tests/unit/test_graph_lookup.py`:

```python
import re

from sync.graph_lookup import GraphLookup, extract_matter_number, matter_path_rule_from_env


class FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, *, params, cert, verify, timeout):
        self.calls.append((url, dict(params), cert, verify, timeout))
        return self.response


HIT = {"node_id": "n-1", "node_name": "Weber ./. Muster", "node_type_id": "t-1", "identifier_id": "i-1",
       "identifier_text": "2024-0815", "kind": "matter_number", "score": 1.0, "channel": "lexical"}


def test_rule_from_env_requires_matter_group(monkeypatch):
    monkeypatch.setenv("RC_MATTER_PATH_RULE", r"^akten/(?P<matter>\d{4}-\d{4})/")
    assert matter_path_rule_from_env().groupindex == {"matter": 1}
    monkeypatch.setenv("RC_MATTER_PATH_RULE", r"^akten/(\d{4}-\d{4})/")
    assert matter_path_rule_from_env() is None
    monkeypatch.setenv("RC_MATTER_PATH_RULE", r"^akten/(?P<matter>[")
    assert matter_path_rule_from_env() is None
    monkeypatch.delenv("RC_MATTER_PATH_RULE")
    assert matter_path_rule_from_env() is None


def test_extract_matter_number_normalises_backslashes():
    pattern = re.compile(r"^akten/(?P<matter>\d{4}-\d{4})/")
    assert extract_matter_number("akten\\2024-0815\\Brief.pdf", pattern) == "2024-0815"
    assert extract_matter_number("sonstiges/Brief.pdf", pattern) is None


def test_lookup_exact_match_and_cache():
    session = FakeSession(FakeResponse(200, {"results": [HIT, {**HIT, "node_id": "n-2", "identifier_text": "2024-08150"}], "degraded": False}))
    clock = [100.0]
    lookup = GraphLookup(base_url="https://api:8443/", cert=("c", "k"), verify="ca", session=session, ttl_seconds=60, clock=lambda: clock[0])
    assert lookup.resolve_matter_node_id("2024-0815") == "n-1"
    assert lookup.resolve_matter_node_id(" 2024-0815 ") == "n-1"
    assert len(session.calls) == 1
    url, params, cert, verify, _ = session.calls[0]
    assert url == "https://api:8443/secured/graph/identifiers/search"
    assert params == {"q": "2024-0815", "kind": "matter_number", "limit": 5}
    assert cert == ("c", "k") and verify == "ca"
    clock[0] = 161.0
    lookup.resolve_matter_node_id("2024-0815")
    assert len(session.calls) == 2


def test_lookup_negative_paths_cached_and_never_raise():
    session = FakeSession(FakeResponse(200, {"results": [], "degraded": True}))
    lookup = GraphLookup(base_url="https://api:8443", cert=("c", "k"), verify="ca", session=session)
    assert lookup.resolve_matter_node_id("9999-0001") is None
    assert lookup.resolve_matter_node_id("9999-0001") is None
    assert len(session.calls) == 1
    session503 = FakeSession(FakeResponse(503, {}))
    assert GraphLookup(base_url="https://api:8443", cert=("c", "k"), verify="ca", session=session503).resolve_matter_node_id("x") is None
```

- [ ] **Step 2: Run** `python -m pytest tests/unit/test_graph_lookup.py -q` → FAIL (`ModuleNotFoundError`). Create `src/sync/graph_lookup.py`:

```python
"""Resolve a matter number to a graph node over tenant mTLS (design §7.1).

`RC_MATTER_PATH_RULE` is a regex with a named group `matter`, e.g.
`^akten/(?P<matter>\\d{4}-\\d{4})/` for WinJur-style trees. The captured value
is looked up with `GET /secured/graph/identifiers/search?kind=matter_number`
and only an exact (case-folded, whitespace-normalised) identifier match is
accepted — a fuzzy hit must never file a document under the wrong matter.
Results, including misses, are cached for `RC_MATTER_LOOKUP_TTL_SECONDS`.
"""
from __future__ import annotations

import logging
import os
import re
import time
from typing import Any, Callable, Optional

import requests

from config import get_config

logger = logging.getLogger(__name__)

IDENTIFIER_SEARCH_PATH = "/secured/graph/identifiers/search"
DEFAULT_LOOKUP_TTL_SECONDS = 3600


def matter_path_rule_from_env() -> Optional[re.Pattern[str]]:
    raw = (os.environ.get("RC_MATTER_PATH_RULE") or "").strip()
    if not raw:
        return None
    try:
        pattern = re.compile(raw)
    except re.error as exc:
        logger.warning("Invalid RC_MATTER_PATH_RULE=%r (%s); matter assignment disabled", raw, exc)
        return None
    if "matter" not in pattern.groupindex:
        logger.warning("RC_MATTER_PATH_RULE has no named group 'matter'; matter assignment disabled")
        return None
    return pattern


def extract_matter_number(relative_path: str, pattern: re.Pattern[str]) -> Optional[str]:
    match = pattern.search(str(relative_path or "").replace("\\", "/"))
    if not match:
        return None
    value = (match.group("matter") or "").strip()
    return value or None


def _norm(value: str) -> str:
    return " ".join(str(value or "").casefold().split())


class GraphLookup:
    def __init__(
        self,
        *,
        base_url: str,
        cert: tuple[str, str],
        verify: str,
        session: Any = None,
        timeout: float = 20.0,
        ttl_seconds: int = DEFAULT_LOOKUP_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._cert = cert
        self._verify = verify
        self._session = session or requests.Session()
        self._timeout = timeout
        self._ttl = max(0, int(ttl_seconds))
        self._clock = clock
        self._cache: dict[str, tuple[Optional[str], float]] = {}

    def resolve_matter_node_id(self, matter_number: str) -> Optional[str]:
        key = _norm(matter_number)
        if not key:
            return None
        now = self._clock()
        cached = self._cache.get(key)
        if cached is not None and cached[1] > now:
            return cached[0]
        node_id = self._lookup(str(matter_number).strip())
        self._cache[key] = (node_id, now + self._ttl)
        return node_id

    def _lookup(self, matter_number: str) -> Optional[str]:
        try:
            resp = self._session.get(
                f"{self._base}{IDENTIFIER_SEARCH_PATH}",
                params={"q": matter_number, "kind": "matter_number", "limit": 5},
                cert=self._cert,
                verify=self._verify,
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            logger.warning("Matter lookup failed for %r: %s", matter_number, exc)
            return None
        if resp.status_code != 200:
            logger.info("Matter lookup for %r returned HTTP %s", matter_number, resp.status_code)
            return None
        try:
            body = resp.json() or {}
        except ValueError:
            return None
        results = body.get("results") if isinstance(body, dict) else None
        if not isinstance(results, list):
            return None
        wanted = _norm(matter_number)
        for hit in results:
            if not isinstance(hit, dict) or hit.get("kind") not in (None, "matter_number"):
                continue
            if _norm(str(hit.get("identifier_text") or "")) == wanted and hit.get("node_id"):
                return str(hit["node_id"])
        return None

    def clear(self) -> None:
        self._cache.clear()


def build_graph_lookup_from_env(cfg: Any = None) -> Optional[GraphLookup]:
    if matter_path_rule_from_env() is None:
        return None
    cfg = cfg or get_config()
    raw_ttl = (os.environ.get("RC_MATTER_LOOKUP_TTL_SECONDS") or "").strip()
    try:
        ttl = int(raw_ttl) if raw_ttl else DEFAULT_LOOKUP_TTL_SECONDS
    except ValueError:
        logger.warning("Invalid RC_MATTER_LOOKUP_TTL_SECONDS=%r; using %d", raw_ttl, DEFAULT_LOOKUP_TTL_SECONDS)
        ttl = DEFAULT_LOOKUP_TTL_SECONDS
    return GraphLookup(
        base_url=cfg.semantix_secure_base_url,
        cert=(cfg.semantix_client_cert_path, cfg.semantix_client_key_path),
        verify=cfg.semantix_ca_cert_path,
        ttl_seconds=ttl,
    )
```

Run → PASS (4 tests). Commit: `git commit -am "feat(rc): matter lookup client over identifiers/search with TTL cache"`.

- [ ] **Step 3: Failing test — `graph_assign` on init.** Append to `tests/unit/test_knovas_uploader.py`:

```python
def test_upload_graph_assign_from_matter_path_rule(mock_config, tmp_path, monkeypatch):
    from sync.graph_lookup import GraphLookup

    monkeypatch.setenv("RC_MATTER_PATH_RULE", r"^akten/(?P<matter>\d{4}-\d{4})/")
    monkeypatch.setenv("RC_LANGUAGE_DETECT", "0")

    class _Resp:
        status_code = 200

        def json(self):
            return {"results": [{"node_id": "n-1", "identifier_text": "2024-0815", "kind": "matter_number"}], "degraded": False}

    class _Session:
        def get(self, url, **kw):
            return _Resp()

    lookup = GraphLookup(base_url="https://api.example:8443", cert=("c", "k"), verify="ca", session=_Session())
    monkeypatch.setattr("sync.knovas_uploader.build_graph_lookup_from_env", lambda cfg=None: lookup)

    f = tmp_path / "Brief.txt"
    f.write_text("Sehr geehrte Damen und Herren", encoding="utf-8")
    uploader = SemantixUploader()
    with patch.object(uploader, "_request") as req:
        req.side_effect = [_ok_response(), _ok_response(), _ok_response(), _ok_response()]
        uploader.upload_file(f, "akten/2024-0815/Brief.txt", {"ingestion": {"identifier_prefix": "corpus"}})
        uploader.upload_file(f, "sonstiges/Brief.txt", {"ingestion": {"identifier_prefix": "corpus"}})

    assert req.call_args_list[0].kwargs["json_body"]["graph_assign"] == {"node_ids": ["n-1"]}
    assert "graph_assign" not in req.call_args_list[2].kwargs["json_body"]
```

- [ ] **Step 4: Run** → FAIL. In `src/sync/knovas_uploader.py` import `from sync.graph_lookup import build_graph_lookup_from_env, extract_matter_number, matter_path_rule_from_env`; in `__init__` add `self._matter_rule = matter_path_rule_from_env()` and `self._graph_lookup = build_graph_lookup_from_env(cfg) if self._matter_rule is not None else None`; after the metadata block in `upload_file` add:

```python
        if self._matter_rule is not None and self._graph_lookup is not None:
            matter = extract_matter_number(relative_path, self._matter_rule)
            node_id = self._graph_lookup.resolve_matter_node_id(matter) if matter else None
            if node_id:
                # Node ids are validated by the API before any part is accepted
                # (foreign id -> 404); the assignment runs after full ingest.
                init_body["graph_assign"] = {"node_ids": [node_id]}
```

Run `python -m pytest tests/unit/test_knovas_uploader.py -q` → PASS. Commit: `git commit -am "feat(rc): RC_MATTER_PATH_RULE resolves the matter node and passes graph_assign on init"`.

- [ ] **Step 5: Docs.** `.env.example` (below the metadata block): `# RC_MATTER_PATH_RULE=^akten/(?P<matter>\d{4}-\d{4})/   # regex over the relative path; named group 'matter' is looked up as identifier kind matter_number` and `# RC_MATTER_LOOKUP_TTL_SECONDS=3600`. `docs/configuration.md` Document metadata section, append: "`RC_MATTER_PATH_RULE` (regex with a named group `matter`) resolves the captured matter number through `GET /secured/graph/identifiers/search?kind=matter_number` (exact match only, cached 1 h incl. misses) and files the document under that matter with `graph_assign` — day-one filing for WinJur-style folder trees. Requires the tenant's knowledge graph and matter nodes carrying a `matter_number` identifier (see the Platform's import wizard). A miss is silent: the document is still uploaded, only unassigned." CHANGELOG bullet: `- RC_MATTER_PATH_RULE: derive the matter number from the path and assign the document at upload (graph_assign).` Commit: `git commit -am "docs(rc): RC_MATTER_PATH_RULE"`.

---

---

## PART KC-G — KnovasComponents — docs, declarations and the capability legend (E1, G9, H6, J1, J4, F4-doc, F6-doc)

### Task KC-G-1: `docs/product-statements.md` — capability legend and the buyer-facing declarations

**Requirements:** E1, E2 (out of scope), F4, F6, G9, H6, J1, J3, J4 (plus the D–H,J status table)
**Files:**
- Create: `docs/product-statements.md`
- Modify: `README.md:26` (add the pointer sentence after the hosting-partners sentence)
**Interfaces:**
- Consumes: the three-label legend and "Claims discipline" rule from `E:/Knovas/KnowledgeBase/docs/ModernDocs/strategy/2026-08-02-value-proposition-v1-v15.md:13` (quoted, not linked — that file is not in this repository); design §4.1–§4.6 and §2 (status column); numbers from design §4.2 (6 q/min/seat, burst 18, p95 ≤ 3.0 s at 20 seats) and §5.4 (`SECURE_API_QUERY_PER_SEAT_PER_MIN=6`, `SECURE_API_QUERY_PER_SEAT_BURST=18`, `clients.seat_count`, `PUT /admin/clients/<client_id>/seats` — defined in the KB throughput part); config names quoted from the Interface Registry: `KG_DEADLINE_EXTRACTION_ENABLED=false`, `KG_FOUR_EYES_ESCALATION_HOURS=24`, `SEARCH_USE_TEST_RESULTS`, `ONTOLOGY_SOURCE`, `ZEFIX_USERNAME`/`ZEFIX_PASSWORD`, `JOURNAL_RETENTION_DAYS=90`.
- Produces: the anchor `docs/product-statements.md#1-how-to-read-a-status-label` that every feature doc, `docs/README.md` (KC-G-2), `specifications.md` §7 (KC-G-4), `hosting-requirements.md` (KC-G-5) and the release notes (KC-G-7) link to; the eight→three mapping table; the per-requirement status table (§8 of the doc) that the Platform feature docs copy their per-screen label from.

- [ ] **Step 1: Confirm the legend source and the absence of any legend in this repo (the "failing test")**

Run:

```bash
grep -n "Claims discipline" /e/Knovas/KnowledgeBase/docs/ModernDocs/strategy/2026-08-02-value-proposition-v1-v15.md
grep -rn "HYPOTHESIS\|\[ROADMAP\]" docs/ README.md RELEASE_NOTES.md KnovasPlatform/docs RemoteController/docs || echo "no legend in KnovasComponents"
test -f docs/product-statements.md && echo EXISTS || echo "missing: docs/product-statements.md"
```

Expected: the first grep prints line 13 of the value-proposition doc; the second prints only `no legend in KnovasComponents` (the design file under `docs/superpowers/specs/` is engineering intent, not customer docs — if it matches, that is fine); the third prints `missing: docs/product-statements.md`.

- [ ] **Step 2: Write `docs/product-statements.md` (part 1 — header, legend, E1/E2, F4)**

Create `docs/product-statements.md` with this content:

```markdown
## Knovas — product statements

|                      |                                                                                                    |
| -------------------- | -------------------------------------------------------------------------------------------------- |
| **Document version** | 1.0                                                                                                |
| **Last updated**     | 2026-08-15                                                                                         |
| **Audience**         | Buyer, project lead, customer IT — the people who file what Knovas does and does not do            |
| **Companion**        | [specifications.md](specifications.md) (how it is deployed) · [KnovasAPI/README.md](KnovasAPI/README.md) (the API contract) |
| **Source**           | Knovas Pflichtenheft (14 August 2026) §3 sections D–H, J and the design `superpowers/specs/2026-08-15-pflichtenheft-d-j-design.md` |

This document is written **before** the code so the code has something to be
honest against. Every statement carries a status label from §1. When a label
changes, this file changes in the same commit.

## 1. How to read a status label

Customer-facing Knovas documents use the eight labels of the Pflichtenheft.
They map onto the three public labels used in Knovas sales and positioning
material, whose rule we adopt verbatim: *"Never present a GATED or ROADMAP
item as available — our credibility with Datenschutzbeauftragte and CISOs
**is** the product."*

| Label | Meaning | Public label |
| --- | --- | --- |
| **LIVE** | Running in a production tenant today, with evidence (tests, logs or a measurement we can show). | **[NOW]** |
| **BUILT** | Code complete and tested; demonstrable on the reference deployment; not yet exercised in *your* production tenant. | **[NOW]** |
| **GATED** | Code complete; a **named** operational gate must clear before it is switched on for a tenant (a calibration, a backfill, a feature flag, a credential, a dependent release). The gate is always written next to the label. | **[GATED]** |
| **DEMO** | Works on fixture or demo data; has not run against a live tenant. | **[GATED]** |
| **PARTIAL** | A substantive part exists; the requirement is not met end-to-end. What is missing is written next to the label. | **[GATED]** |
| **PLANNED** | Designed and scheduled; no code. | **[ROADMAP]** |
| **MISSING** | Neither code nor design. | **[ROADMAP]** |
| **HYPOTHESIS** | A claim we believe and cannot yet evidence. | **[ROADMAP]** |

Rules of use:

1. A label without its gate (GATED), its missing part (PARTIAL) or its evidence
   (LIVE) is incomplete — write the second half.
2. Per-screen feature documents under `KnovasPlatform/docs/features/` carry
   one label per screen, in the first table of the document.
3. In the product itself, only §7 below decides what a user sees.

## 2. E1 — Deadlines: integrate-first (E2 is out of scope) — LIVE (this declaration)

The practice-management system (PMS) keeps the **calendar of record**. Knovas:

- **stores** deadlines as evidenced, audit-trailed date facts on the matter
  (precision `day | month | year`; a month-precision deadline renders as
  "März 2026", never as an invented day);
- **proposes** deadlines it reads from incoming documents, with the source
  passage attached (page, quote, offsets) and **never auto-committed** — a
  human adopts or rejects, and rejection is permanent and says so;
- **enforces** independent second confirmation on the attributes a firm marks
  four-eyes (`confirmation_policy = four_eyes` on the attribute; the confirming
  person must differ from the entering person; every change re-opens
  confirmation and leaves a ledger event);
- **hands** confirmed deadlines to the outside world through two channels: an
  iCalendar feed each user subscribes to in Outlook (responsible lawyer and
  deputy as attendees), and `graph.fact.*` events an integrator or the PMS
  vendor consumes.

Knovas does **not** compute procedural deadlines — ZPO/StPO/BGG/SchKG terms,
Gerichtsferien, Zustellfiktion, cantonal holidays. **E2 stays with the PMS's
Fristen module.** We say this in writing because a wrong computed deadline is
an actuarial risk we will not take on and the PMS already carries it.

Status of the mechanism behind this declaration: see §8 (E3, E4, E5).

## 3. F4 — Throughput per seat (the SLO statement) — BUILT after this plan; GATED until `seat_count` is set for your tenant

**Statement:** *Sustained 6 queries per minute per licensed seat, burst 18,
cluster-wide; p95 query latency ≤ 3.0 s at 20 concurrent seats on the
reference deployment; measured, and published with the load-test artefact.*

What this replaces: the API kit today documents **12 queries/minute per client
certificate (per tenant), burst 2** plus an application bucket of about one
query per 5 s. That number is a per-tenant default, not a per-seat promise, and
it lives partly in an in-process bucket that multiplies with replicas. The
plan moves the bucket to a cluster-wide store keyed on the tenant, sizes it
`seat_count × 6` per minute with a `seat_count × 18` burst, answers `429` with
`Retry-After`, and restores an edge backstop at a generous ceiling. `seat_count`
is the contractual seat number and is set by Knovas operations at onboarding
(`PUT /admin/clients/<client_id>/seats`); until it is set, the tenant default
applies and the label for your tenant is **GATED (seat_count not set)**.

Worked example: a 20-seat firm gets 120 queries/minute sustained and a burst
of 360, shared by all its users. Sizing of the VM that hosts KnovasPlatform is a
separate matter — see [hosting-requirements.md](hosting-requirements.md#query-throughput-per-seat).

## 4. F6 — What "version-aware retrieval" means here — BUILT (tier 1); tier 2 MISSING by decision

- **Tier 1 (in scope):** a document's version history is listable
  (`GET /secured/document/<uuid>/versions`), and every search hit says whether
  it is the current version and how many predecessors exist
  (`is_current`, `version_count`, `has_versions`). The Platform shows the list
  and an "aktuelle Version" badge.
- **Tier 2 (not in scope):** searching the *text* of a superseded version.
  That requires retaining old chunks and vectors — a data-model change and an
  index-size multiple. It is **not** planned.
- The Pflichtenheft's "find the executed version, not draft 7 of 12" is met by
  the `document_status` filter (`draft | final | executed | unknown`), which the
  ingestion side sets from filename heuristics and which can be corrected per
  document (`PATCH /secured/documents/<uuid>/metadata`) — not by tier 2.
```

- [ ] **Step 3: Append part 2 — H6, J1/J3/J4, G9 in-product honesty**

Append to `docs/product-statements.md`:

```markdown
## 5. H6 — Justitia 4.0 readiness — PLANNED (statement only; no code)

Knovas is a knowledge layer, not an e-filing client. Documents received
through justitia.swiss — once the cantonal rollout reaches the firm — enter
Knovas the same way every other document does: through the file share, the
mailbox connector or the Outlook add-in, and they carry `source_kind` and
`document_type` so they are findable and filterable like any court document.
**Knovas will not transmit to courts.** No integration with the Justitia
platform is built or scheduled; the statement exists so nobody has to ask.

## 6. J1 / J3 / J4 — Time capture, realization reporting, invoicing

**J1 — "integrate + journal" — LIVE (this declaration); the journal itself
is BUILT after this plan.** Knovas does not become a time-capture product. It
(a) keeps a per-user, opt-in, customer-hosted activity journal — matters
opened, documents opened, searches run, with timestamps — that the lawyer can
read back as "gestern: Akte Weber 14:00–16:30" and export; (b) emits the same
activity as CSV (user, day, matter, start, end, minutes, documents) for import
into the PMS timesheet; (c) offers the event spine (`GET /secured/events`,
webhooks) to a time-capture partner. Nothing of the journal leaves the firm's
host; only the user sees their own journal; administrators see nothing per
person; retention `JOURNAL_RETENTION_DAYS` (default 90). The business case is
therefore anchored at **4–6 h saved per lawyer per month**; the 15 h ceiling
stays an engineering target that requires PMS billing data.

**J3 — realization / write-off reporting — PARTIAL (substrate only).** The
report needs PMS billing data, which is the C4 PMS-synchronisation project
(a separate integration with a vendor dependency, not in this plan). The
journal CSV is the substrate; the report is out of scope until C4 exists.

**J4 — Swiss invoicing — LIVE (out of scope, stated).** QR-Rechnung, VAT,
cantonal tariffs and dunning stay in the PMS. Knovas does not issue invoices
and will not.

## 7. G9 — Honesty in the product itself — LIVE after this plan

What the user sees:

| Situation | What the product shows |
| --- | --- |
| Cortex / Wissensnetz runs on fixture data (`ONTOLOGY_SOURCE=fixture`) | Sidebar badge **"Demo-Daten"** on every graph screen |
| Search runs on canned results (`SEARCH_USE_TEST_RESULTS` set) | Persistent banner **"Beispieldaten"** above the result list |
| A search returns nothing strong | The empty state states it (`no_strong_matches`) and shows the API status; results are never padded |
| A trust tier is shown | Always with its scope: `tenant` (tenant-wide) or `principal` (computed over what *you* may see) |
| A conflicts check ran with hits you may not read | `withheld_count` and `degraded` are rendered prominently — "withheld" is never shown as "clean" |
| A "who" is recorded (entered, confirmed, decided) | The record says whether it was a verified subject or a client-supplied reference (`actor_kind`) |
| A screen needs the live graph but the deployment runs the fixture | An explicit **"Wissensnetz-Modus erforderlich"** state, never an error page |

What is deliberately **not shown at all** until its gate clears (a hidden
feature is more honest than a beta badge):

- **gap detection** ("this matter is missing X") — needs the completeness
  report on the live graph with schema coverage; until then no gap indicator
  appears anywhere;
- **composed-path conflicts** (conflicts inferred over multi-hop relations) —
  the conflicts check reports direct identifier and document hits only, and
  says so on the protocol;
- **drift alerts** (embedding-model or calibration drift) — the filter screens
  say "kann gerade nicht bewerten — bitte später" on `503`, nothing more.

Labels in the UI are the eight labels above only where a screen shows a status
of the *product* (the settings page). Screens never show a label of a document.
```

- [ ] **Step 4: Append part 3 — the per-requirement status table and the "not addressed" list**

Append to `docs/product-statements.md`:

```markdown
## 8. Status per requirement — today and after this plan ships

"Today" is the Pflichtenheft's own verdict on 2026-08-15. "After" is the label
the requirement carries when every task of the D–J plan is merged and deployed
on the reference deployment; the last column names what still stands between
that label and **LIVE in your tenant**.

| ID | Requirement | Today | After this plan | Gate to LIVE in a tenant |
| --- | --- | --- | --- | --- |
| D1 | Party register with duplicate detection and merge | PARTIAL | BUILT | graph mode on (`ONTOLOGY_SOURCE=graph`, tenant knowledge graph enabled) |
| D2 | Conflicts check, logged as evidence | MISSING | BUILT | actor is a verified user only after the identity release (section B); until then the record says `client_ref` |
| D3 | Zefix / UID enrichment | MISSING | GATED | firm Zefix credentials (`ZEFIX_USERNAME`, `ZEFIX_PASSWORD`) and egress to `www.zefix.admin.ch` |
| D4 | Lateral-hire conflict import (CSV/XLSX) | MISSING | BUILT | as D2 |
| D5 | Expertise location ("Wer kennt sich aus?") | MISSING | GATED | metadata backfill run once per tenant (`backfill-metadata`), else the author facet is empty for documents ingested before the release |
| E1 | Deadline strategy declared | MISSING | LIVE | — (§2) |
| E2 | Procedural deadline computation | — | out of scope | — (§2) |
| E3 | Four-eyes with immutable trail | PARTIAL | BUILT (API) · GATED (screen) | screen buttons need the identity release (section B) |
| E4 | AI reads the Verfügung, human confirms | PLANNED | GATED | `KG_DEADLINE_EXTRACTION_ENABLED=true` per tenant (default off) |
| E5 | Deadlines in Outlook with responsible + deputy | MISSING | GATED | identity release (feed tokens); Person nodes with an `email` identifier |
| E6 | Eventing spine (events, webhooks, job status) | MISSING | BUILT (pull) · GATED (webhooks) | delivery worker deployed with an egress policy in the Knovas cluster |
| F1 | OCR accuracy evidence DE/FR/IT | LIVE | LIVE | — (benchmark report published; on-prem runbook) |
| F2 | Whole estate: mailbox, XLSX/PPTX, PST | PARTIAL | BUILT | Entra app with `Mail.Read` for the mailbox mirror; IMAP/EWS remain PLANNED |
| F3 | Filters, sort, pagination, facets | MISSING | BUILT (new ingest) · GATED (existing corpus) | metadata backfill run once per tenant |
| F4 | Firm-scale throughput | PARTIAL | BUILT | `seat_count` set for the tenant; load artefact published (§3) |
| F5 | DE/FR/IT/EN retrieval evidenced | PARTIAL | BUILT | FR/IT quality suites published with the release |
| F6 | Version-aware retrieval | PARTIAL | BUILT (tier 1) · tier 2 MISSING by decision | — (§4) |
| F7 | Jump to the hit | PLANNED | BUILT (PDF); other formats open the text preview | — |
| F8 | Similar documents / matters | MISSING | BUILT | relevance gate calibrated per score mode |
| F9 | Honest empty results | BUILT | LIVE | per-mode gate calibration on production (GATED until then) |
| G1 | Knowledge map on the live graph | DEMO | BUILT | graph mode on; "Demo-Daten" badge otherwise |
| G2 | Matter ego graph ("Akten-Kompass") | MISSING | BUILT | graph mode on |
| G3 | Every node answers "why?" | BUILT | BUILT | graph mode on |
| G4 | Trust made visible (with scope) | BUILT | BUILT | node trust rollup of the matters plan deployed |
| G5 | Partner's Monday report | BUILT | BUILT | graph mode on |
| G6 | Week one is not an empty graph | PLANNED | BUILT | PMS export available as CSV |
| G7 | Draw on the map (type-level Vorgaben) | DEMO | GATED | `target_node_type_id` of the matters plan deployed |
| G8 | Tireless junior (filters on live endpoints) | BUILT | BUILT | graph mode on |
| G9 | Company-brain honesty | HYPOTHESIS | LIVE | — (§1, §7) |
| H1 | Fixed-price migration incl. PST | PARTIAL | BUILT | — (runbook `RemoteController/docs/migration.md`) |
| H2 | Outlook and Word add-ins | MISSING | BUILT | Platform on HTTPS with a certificate Office trusts; Centralized Deployment or sideload; login inside the taskpane after the identity release |
| H4 | Tables survive ingestion | LIVE | LIVE | — |
| H5 | Exit as easy as entry | PARTIAL | BUILT | — (`GET /secured/export/graph`, `GET /secured/export/documents`) |
| H6 | Justitia 4.0 readiness | MISSING | PLANNED | — (§5) |
| J1 | Time-capture strategy | MISSING | LIVE | — (§6) |
| J2 | Activity hints (Arbeitstag-Journal) | PARTIAL | BUILT | identity release (per-user journal) |
| J3 | Realization reporting | MISSING | PARTIAL | PMS billing data (C4) |
| J4 | Swiss invoicing out of scope | LIVE | LIVE | — (§6) |

## 9. Not addressed — stated so nobody discovers it later

E2 (procedural deadline computation), F10 (federated Swisslex/Weblaw search),
F6 tier 2 (searching superseded text), C4 (nightly PMS synchronisation),
IMAP/EWS mailbox variants (Microsoft Graph first), legacy `.doc`, standalone
scanned images (TIFF/JPG), encrypted files with password supply, section I
(retrieval-augmented generation), sections K and L. Each is either declared
out of scope above or belongs to a separate project with its own design.

## 10. Changes to this document

| Date | Change |
| --- | --- |
| 2026-08-15 | First version, written before the D–J implementation. |
```

- [ ] **Step 5: Verify the document's shape**

Run:

```bash
grep -c "^## " docs/product-statements.md
grep -n "LIVE · BUILT\|\*\*\[NOW\]\*\*\|\*\*\[GATED\]\*\*\|\*\*\[ROADMAP\]\*\*" docs/product-statements.md | head
grep -n "6 queries per minute\|burst 18\|p95 query latency ≤ 3.0 s" docs/product-statements.md
grep -c "^| [DEFGHJ][0-9] " docs/product-statements.md
```

Expected: `10` sections; the legend rows print `[NOW]` twice, `[GATED]` three times, `[ROADMAP]` three times; the F4 line matches; the status table has `38` requirement rows (D1–D5, E1–E6, F1–F9, G1–G9, H1/H2/H4/H5/H6, J1–J4).

- [ ] **Step 6: Link the statements from the root README**

In `README.md`, replace line 26

```markdown
See each folder’s README for component-only dev. **Hosting partners:** [docs/hosting-requirements.md](docs/hosting-requirements.md). To stop Docker or dev web servers: [docs/stopping-web-servers.md](docs/stopping-web-servers.md).
```

with

```markdown
See each folder’s README for component-only dev. **Hosting partners:** [docs/hosting-requirements.md](docs/hosting-requirements.md). **What Knovas does and does not do (status labels, deadlines, throughput, exit):** [docs/product-statements.md](docs/product-statements.md). All documentation by audience: [docs/README.md](docs/README.md). To stop Docker or dev web servers: [docs/stopping-web-servers.md](docs/stopping-web-servers.md).
```

(`docs/README.md` is created in KC-G-2; the link resolves once that task lands in the same branch.)

- [ ] **Step 7: Commit**

```bash
git add docs/product-statements.md README.md
git commit -m "docs(product): add product statements — capability legend, E1/F4/F6/H6/J1 declarations, D–J status table"
```

---

---

### Task KC-G-2: `docs/README.md` — documentation index by audience (+ index rows in the component doc indexes, PRD superseded header)

**Requirements:** G9 (discoverability of every declaration), H2/H5/E6/F2 (docs listed for their audiences)
**Files:**
- Create: `docs/README.md`
- Modify: `KnovasPlatform/docs/README.md:7-22` (add a "Screens and features" table and two integration rows)
- Modify: `RemoteController/docs/README.md:19-28` (add `connectors.md` and `migration.md` rows)
- Modify: `KnovasPlatform/components/README.md:3-6` (add the Office add-ins row)
- Modify: `docs/Frontend Product Requirements Document – Multi-format Search UI.md:1` (superseded header)
**Interfaces:**
- Consumes: `docs/product-statements.md` (KC-G-1); the mirror folder `docs/KnovasAPI/` including `Knowledge_Graph_API.md`, `Events_API.md`, `Export_and_Exit.md` (KC-G-3); documents written by other parts of this plan, referenced by their exact paths: `KnovasPlatform/docs/features/search-filters-and-versions.md`, `…/features/viewer.md`, `…/features/matters-and-parties.md`, `…/features/conflicts-check.md`, `…/features/deadlines.md`, `…/features/reports-and-inbox.md`, `…/features/activity-journal.md`, `…/features/import-and-bootstrap.md`, `KnovasPlatform/docs/integration/office-add-ins.md`, `KnovasPlatform/docs/integration/graph-api.md`, `KnovasPlatform/docs/integration/events.md`, `RemoteController/docs/connectors.md`, `RemoteController/docs/migration.md`, `KnovasPlatform/components/knovas_office_addins/README.md` (defined in the Platform, add-in and RemoteController parts of this plan).
- Produces: `docs/README.md` — the only place that lists every document with its audience; the row shape `| [path](path) | one-line purpose | label pointer |` reused by KC-G-4 §7.

- [ ] **Step 1: Record the current state (failing check)**

Run:

```bash
test -f docs/README.md && echo EXISTS || echo "missing: docs/README.md"
grep -n "features/\|office-add-ins\|connectors.md\|migration.md" KnovasPlatform/docs/README.md RemoteController/docs/README.md docs/specifications.md || echo "no rows for the new docs anywhere"
head -3 "docs/Frontend Product Requirements Document – Multi-format Search UI.md"
```

Expected: `missing: docs/README.md`; `no rows for the new docs anywhere`; the PRD starts with its title and no status header.

- [ ] **Step 2: Write `docs/README.md`**

Create `docs/README.md`:

```markdown
## Knovas Components — documentation by audience

Start with the row that describes you. Every document in this repository is
listed here once; a document that is not listed here does not exist as far as a
reader is concerned. Status labels used across these documents are defined in
[product-statements.md §1](product-statements.md#1-how-to-read-a-status-label).

## Customer IT — installing and operating

| Document | What it answers |
| --- | --- |
| [../README.md](../README.md) | The five-minute unified install (`knovas.env`, `scripts/setup.sh`, `scripts/start.sh`) |
| [specifications.md](specifications.md) | Runtime, network, credentials, storage, hardware, go-live checklists for both components |
| [certificates.md](certificates.md) | One mTLS bundle, per-component filenames and permissions — the most common setup failure |
| [stopping-web-servers.md](stopping-web-servers.md) | How to stop Docker stacks and stray dev servers |
| [../KnovasPlatform/docs/setup.md](../KnovasPlatform/docs/setup.md) | Platform quickstart |
| [../KnovasPlatform/docs/platforms/ubuntu.md](../KnovasPlatform/docs/platforms/ubuntu.md) · [debian.md](../KnovasPlatform/docs/platforms/debian.md) · [windows.md](../KnovasPlatform/docs/platforms/windows.md) | Host-specific notes |
| [../KnovasPlatform/docs/integration/troubleshooting.md](../KnovasPlatform/docs/integration/troubleshooting.md) | Symptom → fix table for the Platform |
| [../RemoteController/docs/local-setup.md](../RemoteController/docs/local-setup.md) | RemoteController local-only install (start here) |
| [../RemoteController/docs/SETUP.md](../RemoteController/docs/SETUP.md) | RemoteController production install (HTTPS edge, employee JWT) |
| [../RemoteController/docs/configuration.md](../RemoteController/docs/configuration.md) | `.env`, scheduler JSON, supported formats, OCR languages, metadata keys |
| [../RemoteController/docs/connectors.md](../RemoteController/docs/connectors.md) | Connectors: file share, OneDrive/SharePoint mirror, mailbox mirror (Microsoft Graph), PST import, XLSX/PPTX |
| [../RemoteController/docs/migration.md](../RemoteController/docs/migration.md) | Fixed-price migration runbook: inventory, PST step, throughput, dedup, verification, rollback |
| [../RemoteController/docs/operations.md](../RemoteController/docs/operations.md) | Health, metrics, stopping sync, upgrades |
| [../RemoteController/docs/onboarding-checklist.md](../RemoteController/docs/onboarding-checklist.md) | RemoteController go-live checklist |

## Hosting partner — provisioning the VM and the network

| Document | What it answers |
| --- | --- |
| [hosting-requirements.md](hosting-requirements.md) | VM sizing, ports, egress (Knovas API, Microsoft Graph, Zefix), document sources incl. mailbox and PST, per-seat query throughput, handover checklist |
| [../KnovasPlatform/docs/deployment/host-nginx-internal.md](../KnovasPlatform/docs/deployment/host-nginx-internal.md) | Production HTTPS topology (host NGINX, internal DNS, corporate CA) |
| [../KnovasPlatform/docs/deployment/checklist-host-nginx.md](../KnovasPlatform/docs/deployment/checklist-host-nginx.md) | Platform go-live checklist |
| [../RemoteController/docs/network-and-firewall.md](../RemoteController/docs/network-and-firewall.md) | RemoteController ingress/egress matrix |
| [../RemoteController/docs/nginx-edge.example.conf](../RemoteController/docs/nginx-edge.example.conf) | Reference NGINX edge configuration |

## Buyer and project lead — what the product does, and does not do

| Document | What it answers |
| --- | --- |
| [product-statements.md](product-statements.md) | Status-label legend; deadlines strategy (E1/E2); throughput per seat (F4); version-awareness tiers (F6); Justitia (H6); time capture and invoicing (J1/J3/J4); in-product honesty (G9); status of every D–H, J requirement |
| [../KnovasPlatform/docs/features/search-filters-and-versions.md](../KnovasPlatform/docs/features/search-filters-and-versions.md) | Search: filters, sort, paging, facets, "Wer kennt sich aus?", version list, similar documents, metadata edit |
| [../KnovasPlatform/docs/features/viewer.md](../KnovasPlatform/docs/features/viewer.md) | Jump to the hit: the PDF viewer, what non-PDF formats show |
| [../KnovasPlatform/docs/features/matters-and-parties.md](../KnovasPlatform/docs/features/matters-and-parties.md) | Matter page, party register, identifiers, duplicates and merge, Zefix enrichment |
| [../KnovasPlatform/docs/features/conflicts-check.md](../KnovasPlatform/docs/features/conflicts-check.md) | Conflicts check, decisions, protocol, lateral-hire import |
| [../KnovasPlatform/docs/features/deadlines.md](../KnovasPlatform/docs/features/deadlines.md) | Fristen: proposals, four-eyes confirmation, Outlook feed |
| [../KnovasPlatform/docs/features/reports-and-inbox.md](../KnovasPlatform/docs/features/reports-and-inbox.md) | Berichte (contradictions, completeness) and Posteingang (events) |
| [../KnovasPlatform/docs/features/activity-journal.md](../KnovasPlatform/docs/features/activity-journal.md) | Arbeitstag-Journal: opt-in, what is recorded, CSV export, retention |
| [../KnovasPlatform/docs/features/import-and-bootstrap.md](../KnovasPlatform/docs/features/import-and-bootstrap.md) | Week-one graph: CSV import wizard with dry-run, file-structure bootstrap |
| [../KnovasPlatform/docs/integration/office-add-ins.md](../KnovasPlatform/docs/integration/office-add-ins.md) | Outlook and Word add-ins: filing an e-mail to a matter, searching from Word, manifest hosting and deployment |
| [../KnovasPlatform/docs/integration/opening-documents.md](../KnovasPlatform/docs/integration/opening-documents.md) | Opening the original file from the browser |

## Integrator — calling the API or extending the Platform

| Document | What it answers |
| --- | --- |
| [KnovasAPI/README.md](KnovasAPI/README.md) | The Knovas Developer Kit mirror: read order, mirror policy |
| [KnovasAPI/Client_Integration_Guide.md](KnovasAPI/Client_Integration_Guide.md) | Onboarding, document preparation, metadata best practices, limits |
| [KnovasAPI/Secure_API.md](KnovasAPI/Secure_API.md) | `/secured/*` contract: upload with metadata, query with filters/paging/sort/facets, versions, similar, transmission status, export |
| [KnovasAPI/Knowledge_Graph_API.md](KnovasAPI/Knowledge_Graph_API.md) | `/secured/graph/*`: types, nodes, facts, evidence, trust, identifiers, conflict checks, four-eyes, ego, imports, jobs |
| [KnovasAPI/Events_API.md](KnovasAPI/Events_API.md) | Event catalogue, `GET /secured/events`, webhooks, signatures, delivery guarantees |
| [KnovasAPI/Export_and_Exit.md](KnovasAPI/Export_and_Exit.md) | NDJSON exports of graph and documents, the manifest line, scope marker |
| [KnovasAPI/Analytics_Integration_Guide.md](KnovasAPI/Analytics_Integration_Guide.md) | Engagement and feedback reporting |
| [../KnovasPlatform/docs/integration/graph-api.md](../KnovasPlatform/docs/integration/graph-api.md) | Platform-local `/api/graph/*`, `/api/matters/*`, `/api/parties/*`, `/api/conflict-checks/*`, `/api/deadlines/*`, `/api/inbox/*`, `/api/reports/*`, `/api/filing/*`, `/api/journal/*` |
| [../KnovasPlatform/docs/integration/events.md](../KnovasPlatform/docs/integration/events.md) | How the Platform consumes events (poller, cursor, Posteingang) and how an integrator should (webhooks) |
| [../KnovasPlatform/docs/integration/open-tokens-api.md](../KnovasPlatform/docs/integration/open-tokens-api.md) | Companion-mode open tokens |
| [../KnovasPlatform/components/knovas_office_addins/README.md](../KnovasPlatform/components/knovas_office_addins/README.md) | Add-in component: manifests, taskpane, hosting |

## Internal — engineering intent and backlog

| Document | What it answers |
| --- | --- |
| [superpowers/specs/](superpowers/specs/) | Design documents (`YYYY-MM-DD-<slug>-design.md`); the D–J design is `2026-08-15-pflichtenheft-d-j-design.md` and lives in both repositories |
| [superpowers/plans/](superpowers/plans/) | Task-checkboxed implementation plans derived from the designs |
| [search-ui-backlog.md](search-ui-backlog.md) | The Platform's honest search backlog (German), with the API-first ordering |
| [Frontend Product Requirements Document – Multi-format Search UI.md](Frontend%20Product%20Requirements%20Document%20%E2%80%93%20Multi-format%20Search%20UI.md) | Historical PRD — superseded, kept for the requirement wording only |
| [../scripts/check_devkit_mirror.py](../scripts/check_devkit_mirror.py) | Drift check between `KnovasAPI/` and the canonical Developer Kit (runs in CI) |
| [../RELEASE_NOTES.md](../RELEASE_NOTES.md) · [../KnovasPlatform/CHANGELOG.md](../KnovasPlatform/CHANGELOG.md) · [../RemoteController/CHANGELOG.md](../RemoteController/CHANGELOG.md) | What shipped, per release and per component |
```

- [ ] **Step 3: Check the links (expected: only the not-yet-written documents are missing)**

Run from the repository root:

```bash
cd docs && grep -o '](\.\./[^)#]*\|]([A-Za-z_][^)#]*' README.md | sed 's/^](//' | sed 's/%20/ /g; s/%E2%80%93/–/g' | sort -u | while read -r p; do [ -e "$p" ] || echo "missing: $p"; done; cd ..
```

Expected output is exactly the documents other parts of this plan create (and nothing else):

```
missing: ../KnovasPlatform/components/knovas_office_addins/README.md
missing: ../KnovasPlatform/docs/features/activity-journal.md
missing: ../KnovasPlatform/docs/features/conflicts-check.md
missing: ../KnovasPlatform/docs/features/deadlines.md
missing: ../KnovasPlatform/docs/features/import-and-bootstrap.md
missing: ../KnovasPlatform/docs/features/matters-and-parties.md
missing: ../KnovasPlatform/docs/features/reports-and-inbox.md
missing: ../KnovasPlatform/docs/features/search-filters-and-versions.md
missing: ../KnovasPlatform/docs/features/viewer.md
missing: ../KnovasPlatform/docs/integration/events.md
missing: ../KnovasPlatform/docs/integration/graph-api.md
missing: ../KnovasPlatform/docs/integration/office-add-ins.md
missing: ../RemoteController/docs/connectors.md
missing: ../RemoteController/docs/migration.md
missing: ../KnovasPlatform/CHANGELOG.md
missing: KnovasAPI/Events_API.md
missing: KnovasAPI/Export_and_Exit.md
```

`../KnovasPlatform/CHANGELOG.md` disappears after KC-G-7, `KnovasAPI/Events_API.md` and `KnovasAPI/Export_and_Exit.md` after the KB Developer-Kit changes are mirrored (KC-G-3, re-run). Re-run this command after the whole plan merges; expected: no output.

- [ ] **Step 4: Add the "Screens and features" table to `KnovasPlatform/docs/README.md`**

Append to `KnovasPlatform/docs/README.md` (after the existing table; if the Platform feature part already added an identical row, keep a single row):

```markdown

## Screens and features

Every document below states one status label per screen (legend:
[docs/product-statements.md §1](../../docs/product-statements.md#1-how-to-read-a-status-label)).

| Screen | Doc |
|--------|-----|
| Suche — Filter, Sortierung, Seiten, Facetten, Versionen, ähnliche Dokumente | [features/search-filters-and-versions.md](features/search-filters-and-versions.md) |
| Viewer — Sprung zur Fundstelle | [features/viewer.md](features/viewer.md) |
| Akte und Parteien — Register, Dubletten, Zusammenführen, Zefix | [features/matters-and-parties.md](features/matters-and-parties.md) |
| Konfliktprüfung — Prüfung, Entscheid, Protokoll, Import | [features/conflicts-check.md](features/conflicts-check.md) |
| Fristen — Vorschläge, Vier-Augen, Outlook-Feed | [features/deadlines.md](features/deadlines.md) |
| Berichte und Posteingang | [features/reports-and-inbox.md](features/reports-and-inbox.md) |
| Arbeitstag-Journal | [features/activity-journal.md](features/activity-journal.md) |
| Import und Bootstrap | [features/import-and-bootstrap.md](features/import-and-bootstrap.md) |
| Outlook- und Word-Add-ins | [integration/office-add-ins.md](integration/office-add-ins.md) |
| Platform HTTP routes (`/api/graph/*`, `/api/matters/*`, …) | [integration/graph-api.md](integration/graph-api.md) |
| Events — Poller, Posteingang, Webhooks für Integratoren | [integration/events.md](integration/events.md) |
```

- [ ] **Step 5: Add the connector and migration rows to `RemoteController/docs/README.md`**

In the **Reference** table (`RemoteController/docs/README.md:19-28`), insert after the `configuration.md` row:

```markdown
| [connectors.md](connectors.md) | **Connectors** — file share, OneDrive/SharePoint mirror, mailbox mirror (Microsoft Graph), PST import, XLSX/PPTX |
| [migration.md](migration.md) | **Migration runbook** — inventory, PST step, throughput vs. API ceiling, dedup, verification, rollback |
```

- [ ] **Step 6: Add the add-in row to `KnovasPlatform/components/README.md`**

Replace the table at `KnovasPlatform/components/README.md:3-6` with:

```markdown
| Directory | Role |
|-----------|------|
| [docbridge_integration](docbridge_integration/) | Web app (search, Knovas API client, open tokens, graph screens, add-in taskpane host) |
| Knovas Open Companion (`semantix_open_companion/`, `knovas_open_companion/linux/`) | Optional fallback if browser cannot launch UNC/local paths |
| [knovas_office_addins](knovas_office_addins/) | Outlook and Word add-ins (manifests + static taskpane served by the Platform at `/addins/*`) — see [docs/integration/office-add-ins.md](../docs/integration/office-add-ins.md) |
```

- [ ] **Step 7: Mark the Frontend PRD as superseded**

Insert at the very top of `docs/Frontend Product Requirements Document – Multi-format Search UI.md` (before line 1):

```markdown
> **Status: superseded (2026-08-15).** This PRD is kept for the wording of its
> requirements only. Its recommended stack (Next.js, Tailwind, TanStack Query,
> react-pdf) was rejected — the shipped Platform is Flask + Jinja2 + vanilla JS
> with **no build step** (see every plan under `superpowers/plans/`). Facets,
> sort and pagination (FR-9…FR-13) are re-adjudicated in
> [search-ui-backlog.md §5](search-ui-backlog.md) and delivered as an API
> contract first (`filters`, `limit`, `offset`, `sort`, `facets` on
> `POST /secured/query`); the preview and viewer (FR-14…FR-19) as the native
> `<dialog>` and the vendored pdf.js viewer. Do not plan new work from this file.

```

- [ ] **Step 8: Commit**

```bash
git add docs/README.md KnovasPlatform/docs/README.md RemoteController/docs/README.md \
        KnovasPlatform/components/README.md \
        "docs/Frontend Product Requirements Document – Multi-format Search UI.md"
git commit -m "docs(index): documentation index by audience; feature/connector rows in component indexes; PRD marked superseded"
```

---

---

### Task KC-G-3: Developer-Kit mirror — re-copy, new documents, `check_devkit_mirror.py` drift check, CI job

**Requirements:** F3, F6, F7, F8, F9, G1–G6, D1, D2, E3, E6, H5 (every one of them is a contract the integrator reads from this mirror), G9 (no silent drift)
**Files:**
- Create: `scripts/check_devkit_mirror.py`
- Create: `scripts/tests/test_check_devkit_mirror.py`
- Modify (rewritten from the kit by the script): `docs/KnovasAPI/Secure_API.md`, `docs/KnovasAPI/Client_Integration_Guide.md`, `docs/KnovasAPI/Analytics_Integration_Guide.md`
- Create (copied from the kit by the script): `docs/KnovasAPI/Knowledge_Graph_API.md`; later `docs/KnovasAPI/Events_API.md`, `docs/KnovasAPI/Export_and_Exit.md`
- Modify: `docs/KnovasAPI/README.md:11-17` (read-order table) and add the mirror-policy section
- Modify: `.github/workflows/ci.yml` (new job `devkit-mirror`)
**Interfaces:**
- Consumes: the canonical kit `E:/Knovas/KnowledgeBase/docs/Knovas_Developer_Kit/api/*.md` (`Secure_API.md` updated 2026-08-02, `Client_Integration_Guide.md`, `Analytics_Integration_Guide.md`, `Knowledge_Graph_API.md` updated 2026-08-04, and — once the KB parts land — `Events_API.md`, `Export_and_Exit.md`); GitHub secret `KNOWLEDGEBASE_REPO_TOKEN` (fine-grained PAT, read-only *Contents* on `Seifeddini/KnowledgeBase`) for the CI checkout.
- Produces: `scripts/check_devkit_mirror.py` with `normalize(text: str) -> str`, `rewrite_for_mirror(kit_text: str) -> str`, `compare(kit_dir: Path, mirror_dir: Path = MIRROR_DIR) -> list[str]`, `sync(kit_dir: Path, mirror_dir: Path = MIRROR_DIR) -> list[str]`, `main(argv: list[str] | None = None) -> int`; CLI `python scripts/check_devkit_mirror.py --kit-dir <path> [--sync]` exiting 0 (in sync), 1 (drift or README gap), 2 (usage); the two rewrite rules below, which every future mirror edit must respect; CI job `devkit-mirror`.

The two rewrite rules (the only permitted differences between kit and mirror):

1. Links to `../Audience/Client%20Integration%20Guide.md` (and its unencoded spelling) become `Client_Integration_Guide.md` — the mirror is a flat folder.
2. The paragraph starting `**Canonical reference:**` that points into `../../Docs/02_SERVICES/` is dropped together with the blank line after it — that internal path does not exist in this repository.

Everything else is compared after CRLF → LF and trailing-whitespace normalisation. `README.md` is the mirror's own index, is not compared, and must link every mirrored file.

- [ ] **Step 1: Write the failing tests**

Create `scripts/tests/test_check_devkit_mirror.py`:

```python
"""Tests for scripts/check_devkit_mirror.py — run: python -m pytest scripts/tests -q"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_devkit_mirror as cdm  # noqa: E402


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _kit_and_mirror(tmp_path: Path):
    kit = tmp_path / "kit"
    mirror = tmp_path / "mirror"
    _write(kit / "Secure_API.md",
           "# Secure API\n\n"
           "**Canonical reference:** [`docs/Docs/02_SERVICES/Secure_API.md`]"
           "(../../Docs/02_SERVICES/Secure_API.md) is the internal, fuller contract.\n\n"
           "See [Client Integration Guide](../Audience/Client%20Integration%20Guide.md)"
           " → *Page and sentence numbers*.\n")
    _write(kit / "Client_Integration_Guide.md", "# Client Integration Guide\n\nHello.\n")
    _write(mirror / "Secure_API.md",
           "# Secure API\n\n"
           "See [Client Integration Guide](Client_Integration_Guide.md)"
           " → *Page and sentence numbers*.\n")
    # CRLF and trailing spaces in the mirror must not count as drift
    _write(mirror / "Client_Integration_Guide.md",
           "# Client Integration Guide\r\n\r\nHello.   \r\n")
    _write(mirror / "README.md",
           "| 1 | [Client_Integration_Guide.md](Client_Integration_Guide.md) |\n"
           "| 2 | [Secure_API.md](Secure_API.md) |\n")
    return kit, mirror


def test_in_sync_ignores_link_rewrite_crlf_and_trailing_space(tmp_path):
    kit, mirror = _kit_and_mirror(tmp_path)
    assert cdm.compare(kit, mirror) == []


def test_content_drift_is_reported(tmp_path):
    kit, mirror = _kit_and_mirror(tmp_path)
    _write(kit / "Client_Integration_Guide.md",
           "# Client Integration Guide\n\nHello, changed.\n")
    assert cdm.compare(kit, mirror) == ["drift: Client_Integration_Guide.md"]


def test_missing_mirror_file_and_readme_row_are_reported(tmp_path):
    kit, mirror = _kit_and_mirror(tmp_path)
    _write(kit / "Knowledge_Graph_API.md", "# Knowledge Graph API\n")
    assert cdm.compare(kit, mirror) == [
        "missing in mirror: Knowledge_Graph_API.md",
        "not linked from README.md: Knowledge_Graph_API.md",
    ]


def test_stale_mirror_file_is_reported(tmp_path):
    kit, mirror = _kit_and_mirror(tmp_path)
    _write(mirror / "Old_Guide.md", "# gone upstream\n")
    assert cdm.compare(kit, mirror) == [
        "not in kit (stale mirror file): Old_Guide.md",
    ]


def test_sync_writes_rewritten_copy_but_never_the_readme(tmp_path):
    kit, mirror = _kit_and_mirror(tmp_path)
    _write(kit / "Knowledge_Graph_API.md",
           "# Knowledge Graph API\n\nSee [Secure API](Secure_API.md).\n")
    assert cdm.sync(kit, mirror) == ["Knowledge_Graph_API.md"]
    assert (mirror / "Knowledge_Graph_API.md").read_text(encoding="utf-8") == (
        "# Knowledge Graph API\n\nSee [Secure API](Secure_API.md).\n")
    # the index stays a human decision
    assert cdm.compare(kit, mirror) == [
        "not linked from README.md: Knowledge_Graph_API.md",
    ]


def test_rewrite_drops_canonical_reference_paragraph_and_its_blank_line():
    kit_text = ("# T\n\n**Canonical reference:** [x](../../Docs/02_SERVICES/x.md) internal.\n\n"
                "Body [g](../Audience/Client%20Integration%20Guide.md).\n")
    assert cdm.rewrite_for_mirror(kit_text) == "# T\n\nBody [g](Client_Integration_Guide.md).\n"


def test_main_exit_codes(tmp_path, capsys):
    kit, mirror = _kit_and_mirror(tmp_path)
    assert cdm.main(["--kit-dir", str(kit), "--mirror-dir", str(mirror)]) == 0
    _write(kit / "Secure_API.md", "# Secure API\n\nchanged\n")
    assert cdm.main(["--kit-dir", str(kit), "--mirror-dir", str(mirror)]) == 1
    assert "drift: Secure_API.md" in capsys.readouterr().out
    assert cdm.main(["--kit-dir", str(tmp_path / "nope"), "--mirror-dir", str(mirror)]) == 2
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest scripts/tests -q`
Expected: collection error `ModuleNotFoundError: No module named 'check_devkit_mirror'`.

- [ ] **Step 3: Write `scripts/check_devkit_mirror.py`**

Create `scripts/check_devkit_mirror.py`:

```python
#!/usr/bin/env python3
"""Check docs/KnovasAPI against the canonical Knovas Developer Kit.

The canonical contract lives in the KnowledgeBase repository under
``docs/Knovas_Developer_Kit/api/``. ``docs/KnovasAPI/`` is a copy that ships
with the customer-hosted components. Copies drift — this repository has retired
one mirror already for exactly that reason (see
KnovasPlatform/knovas-docs/Knovas_Developer_Implementation_Kit/README.md) — so
this script makes drift a CI failure instead of a discovery.

Two rewrites are applied to the kit before comparing (and when writing with
``--sync``), because the mirror is a flat folder without the kit's
``../Audience/`` and ``../../Docs/`` neighbours:

1. Links to ``../Audience/Client%20Integration%20Guide.md`` become
   ``Client_Integration_Guide.md``.
2. The ``**Canonical reference:**`` paragraph that points into
   ``../../Docs/02_SERVICES/`` is dropped, with the blank line after it.

Everything else must be identical after CRLF and trailing-whitespace
normalisation. ``README.md`` is the mirror's own index: it is not compared, but
it must link every mirrored file.

Usage:
    python scripts/check_devkit_mirror.py --kit-dir ../KnowledgeBase/docs/Knovas_Developer_Kit/api
    python scripts/check_devkit_mirror.py --kit-dir <same> --sync   # rewrite the mirror from the kit

Exit codes: 0 in sync · 1 drift or README gap · 2 usage error.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

MIRROR_DIR = Path(__file__).resolve().parent.parent / "docs" / "KnovasAPI"
MIRROR_INDEX = "README.md"

_LINK_REWRITES = (
    ("../Audience/Client%20Integration%20Guide.md", "Client_Integration_Guide.md"),
    ("../Audience/Client Integration Guide.md", "Client_Integration_Guide.md"),
)
_DROP_LINE_RE = re.compile(r"^\*\*Canonical reference:\*\* .*\.\./\.\./Docs/")


def normalize(text: str) -> str:
    """CRLF -> LF, strip trailing whitespace per line, exactly one final newline."""
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines) + "\n"


def rewrite_for_mirror(kit_text: str) -> str:
    """Return the kit document as it must appear in docs/KnovasAPI."""
    out: list[str] = []
    dropped_previous = False
    for line in normalize(kit_text).split("\n"):
        if _DROP_LINE_RE.match(line):
            dropped_previous = True
            continue
        if dropped_previous and line == "":
            dropped_previous = False
            continue
        dropped_previous = False
        for src, dst in _LINK_REWRITES:
            line = line.replace(src, dst)
        out.append(line)
    return normalize("\n".join(out))


def compare(kit_dir: Path, mirror_dir: Path = MIRROR_DIR) -> list[str]:
    """Return human-readable drift findings; an empty list means in sync."""
    findings: list[str] = []
    kit_files = sorted(kit_dir.glob("*.md"))
    if not kit_files:
        return [f"no *.md files under {kit_dir}"]
    expected_names = {p.name for p in kit_files}
    for kit_file in kit_files:
        mirror_file = mirror_dir / kit_file.name
        if not mirror_file.exists():
            findings.append(f"missing in mirror: {kit_file.name}")
            continue
        wanted = rewrite_for_mirror(kit_file.read_text(encoding="utf-8"))
        actual = normalize(mirror_file.read_text(encoding="utf-8"))
        if wanted != actual:
            findings.append(f"drift: {kit_file.name}")
    for mirror_file in sorted(mirror_dir.glob("*.md")):
        if mirror_file.name == MIRROR_INDEX or mirror_file.name in expected_names:
            continue
        findings.append(f"not in kit (stale mirror file): {mirror_file.name}")
    index = mirror_dir / MIRROR_INDEX
    index_text = index.read_text(encoding="utf-8") if index.exists() else ""
    for name in sorted(expected_names):
        if f"({name})" not in index_text:
            findings.append(f"not linked from {MIRROR_INDEX}: {name}")
    return findings


def sync(kit_dir: Path, mirror_dir: Path = MIRROR_DIR) -> list[str]:
    """Write every kit document into the mirror (rewritten); return the names written."""
    written: list[str] = []
    mirror_dir.mkdir(parents=True, exist_ok=True)
    for kit_file in sorted(kit_dir.glob("*.md")):
        target = mirror_dir / kit_file.name
        content = rewrite_for_mirror(kit_file.read_text(encoding="utf-8"))
        if target.exists() and normalize(target.read_text(encoding="utf-8")) == content:
            continue
        target.write_text(content, encoding="utf-8", newline="\n")
        written.append(kit_file.name)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check (or rewrite) docs/KnovasAPI against the canonical Knovas Developer Kit.")
    parser.add_argument("--kit-dir", required=True, type=Path,
                        help="path to KnowledgeBase/docs/Knovas_Developer_Kit/api")
    parser.add_argument("--mirror-dir", type=Path, default=MIRROR_DIR, help=argparse.SUPPRESS)
    parser.add_argument("--sync", action="store_true",
                        help="rewrite the mirror from the kit before checking")
    args = parser.parse_args(argv)
    if not args.kit_dir.is_dir():
        print(f"error: --kit-dir {args.kit_dir} is not a directory", file=sys.stderr)
        return 2
    if args.sync:
        for name in sync(args.kit_dir, args.mirror_dir):
            print(f"wrote {name}")
    findings = compare(args.kit_dir, args.mirror_dir)
    if findings:
        print("docs/KnovasAPI is out of sync with the Developer Kit:")
        for finding in findings:
            print(f"  - {finding}")
        print("Fix: python scripts/check_devkit_mirror.py --kit-dir <kit> --sync, "
              "then add a README.md row for any new file.")
        return 1
    count = len(list(args.kit_dir.glob("*.md")))
    print(f"docs/KnovasAPI in sync with {args.kit_dir} ({count} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest scripts/tests -q`
Expected: `7 passed`.

- [ ] **Step 5: Run the check against the real kit — it must report today's drift**

Run: `python scripts/check_devkit_mirror.py --kit-dir ../KnowledgeBase/docs/Knovas_Developer_Kit/api`
Expected (exit 1):

```
docs/KnovasAPI is out of sync with the Developer Kit:
  - drift: Client_Integration_Guide.md
  - missing in mirror: Knowledge_Graph_API.md
  - drift: Secure_API.md
  - not linked from README.md: Knowledge_Graph_API.md
```

(`Analytics_Integration_Guide.md` is byte-identical today and is not listed. If the KB parts have already added `Events_API.md` / `Export_and_Exit.md` to the kit, they appear as `missing in mirror` too.)

- [ ] **Step 6: Re-copy the mirror from the kit**

Run: `python scripts/check_devkit_mirror.py --kit-dir ../KnowledgeBase/docs/Knovas_Developer_Kit/api --sync`
Expected: lines `wrote Client_Integration_Guide.md`, `wrote Knowledge_Graph_API.md`, `wrote Secure_API.md` (plus `Events_API.md` / `Export_and_Exit.md` when present), then the remaining finding `not linked from README.md: Knowledge_Graph_API.md` (exit 1) — fixed in Step 8.

Verify the re-pointed copy by hand:

```bash
grep -n "Canonical reference\|\.\./Audience/\|\.\./\.\./Docs/" docs/KnovasAPI/*.md || echo "no kit-internal links left"
grep -n "^updated:" docs/KnovasAPI/Secure_API.md docs/KnovasAPI/Client_Integration_Guide.md docs/KnovasAPI/Knowledge_Graph_API.md
grep -n "relevance_tier\|graph_assign\|/secured/graph/\*" docs/KnovasAPI/Secure_API.md | head -5
```

Expected: `no kit-internal links left`; `updated:` shows `2026-08-02`, `2026-08-02` (or newer, whatever the kit says), `2026-08-04`; the relevance-gate paragraph, `graph_assign` and the `/secured/graph/*` row are now present in the mirror.

- [ ] **Step 7: Confirm the mirror is not accidentally the tombstone tree**

Run: `ls KnovasPlatform/knovas-docs/Knovas_Developer_Implementation_Kit/ && git status --short KnovasPlatform/knovas-docs/`
Expected: the four tombstone files unchanged, no modifications — that tree stays retired.

- [ ] **Step 8: Update `docs/KnovasAPI/README.md` — read order and mirror policy**

Replace `docs/KnovasAPI/README.md:11-17` (the "Read order" heading and table) with:

```markdown
## Read order

| Step | Document | Purpose |
|------|----------|---------|
| 1 | [Client_Integration_Guide.md](Client_Integration_Guide.md) | Onboarding, document preparation, metadata best practices, chunking, page and sentence numbers, ports, limits, error handling |
| 2 | [Secure_API.md](Secure_API.md) | Contract for `/secured/*`: upload with `metadata`, query with `filters`/`limit`/`offset`/`sort`/`facets`, versions, similar documents, transmission status, delete, export |
| 3 | [Knowledge_Graph_API.md](Knowledge_Graph_API.md) | Contract for `/secured/graph/*`: node types and schemas, nodes, facts, evidence, trust tiers, identifiers, duplicates and merge, conflict checks, four-eyes, ego graph, imports, jobs |
| 4 | [Analytics_Integration_Guide.md](Analytics_Integration_Guide.md) | Optional engagement reporting (`query_session_id`, `/secured/analytics/engagement`) |

Two further documents join this folder with the eventing and export releases and
are listed here the moment they are mirrored: `Events_API.md` (event catalogue,
`GET /secured/events`, webhooks) and `Export_and_Exit.md` (NDJSON exports).

## Mirror policy

This folder is a **copy**. The canonical source is
`KnowledgeBase/docs/Knovas_Developer_Kit/api/` in the Knovas backend repository;
the two differ only by relative links (`Client_Integration_Guide.md` instead of
`../Audience/Client%20Integration%20Guide.md`) and by the dropped
"Canonical reference" paragraph, which points at an internal document.
`scripts/check_devkit_mirror.py` enforces exactly that in CI (job
`devkit-mirror`) and rewrites the copy with `--sync`. Edit the canonical kit,
never this folder — a hand edit here is reverted by the next sync.
```

When `Events_API.md` and `Export_and_Exit.md` are mirrored (the KB events and export parts add them to the kit; re-run Step 6), replace the paragraph "Two further documents…" with two rows `| 5 | [Events_API.md](Events_API.md) | Event catalogue, cursor pull, webhooks, signatures, delivery guarantees |` and `| 6 | [Export_and_Exit.md](Export_and_Exit.md) | NDJSON exports of graph and documents, manifest line, scope marker |` — the check fails until the rows exist.

- [ ] **Step 9: Run the check again — expected in sync**

Run: `python scripts/check_devkit_mirror.py --kit-dir ../KnowledgeBase/docs/Knovas_Developer_Kit/api`
Expected: `docs/KnovasAPI in sync with ../KnowledgeBase/docs/Knovas_Developer_Kit/api (4 files)` (or 6 once the two new kit files exist), exit 0.

- [ ] **Step 10: Add the CI job**

Append to `.github/workflows/ci.yml` (after the `remote-controller` job, same indentation as the other jobs):

```yaml
  devkit-mirror:
    # docs/KnovasAPI is a copy of the Knovas Developer Kit. The check needs the
    # canonical kit from the (private) KnowledgeBase repository; when the
    # read-only token is not configured the check is skipped with a notice and
    # only the script's own unit tests run.
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Unit-test the drift checker
        run: |
          pip install pytest
          python -m pytest scripts/tests -q

      - name: Detect access to the canonical kit
        id: kit
        env:
          KIT_TOKEN: ${{ secrets.KNOWLEDGEBASE_REPO_TOKEN }}
        run: |
          if [ -n "$KIT_TOKEN" ]; then
            echo "available=true" >> "$GITHUB_OUTPUT"
          else
            echo "available=false" >> "$GITHUB_OUTPUT"
            echo "::notice title=devkit-mirror::KNOWLEDGEBASE_REPO_TOKEN not set — Developer Kit drift check skipped (run scripts/check_devkit_mirror.py locally)"
          fi

      - name: Check out the canonical Developer Kit
        if: steps.kit.outputs.available == 'true'
        uses: actions/checkout@v4
        with:
          repository: Seifeddini/KnowledgeBase
          token: ${{ secrets.KNOWLEDGEBASE_REPO_TOKEN }}
          path: .kit-src
          sparse-checkout: docs/Knovas_Developer_Kit/api

      - name: Check docs/KnovasAPI against the kit
        if: steps.kit.outputs.available == 'true'
        run: python scripts/check_devkit_mirror.py --kit-dir .kit-src/docs/Knovas_Developer_Kit/api
```

Validate the YAML parses:

```bash
python -c "import yaml,sys; d=yaml.safe_load(open('.github/workflows/ci.yml')); print(sorted(d['jobs']))"
```

Expected: `['devkit-mirror', 'knovas-platform', 'remote-controller']` (install `pyyaml` with `pip install pyyaml` if missing).

- [ ] **Step 11: Commit**

```bash
git add scripts/check_devkit_mirror.py scripts/tests/test_check_devkit_mirror.py \
        docs/KnovasAPI/README.md docs/KnovasAPI/Secure_API.md \
        docs/KnovasAPI/Client_Integration_Guide.md docs/KnovasAPI/Knowledge_Graph_API.md \
        .github/workflows/ci.yml
git commit -m "docs(api-kit): re-mirror the Developer Kit (+Knowledge_Graph_API), add check_devkit_mirror.py drift check and CI job"
```

After the KB parts change the kit (Secure_API metadata/filters/versions/similar/export, Knowledge_Graph_API identifiers/conflicts/four-eyes/ego/imports, new Events_API.md and Export_and_Exit.md, Client_Integration_Guide metadata/language), repeat Steps 6, 8, 9 and commit `docs(api-kit): re-sync mirror with Developer Kit <date>`.

---

---

### Task KC-G-4: `docs/specifications.md` — formats, connectors, endpoints, keys, add-ins, go-live rows, index, version bump

**Requirements:** F2, H1, H4 (§1.3/§1.6), E6, F3, F6, F8, G1–G6, H5 (§2.3), G1, E6, D3, J2 (§2.5), H2, E5 (§2.7/§2.8), all (§4, §7)
**Files:**
- Modify: `docs/specifications.md:4-9` (header), `:99-106` (§1.2 dependencies), `:108-112` (§1.3), `:196-198` (§1.6 OneDrive block → connectors), `:324` (§2.3 endpoints line), `:342-368` (§2.5), `:417-438` (§2.8), `:497-543` (§4), `:594-626` (§7)
**Interfaces:**
- Consumes: env names from the Interface Registry — RC: `RC_SEND_DOCUMENT_METADATA=1`, `RC_MATTER_PATH_RULE`, `RC_LANGUAGE_DETECT=1`, `RC_TESSERACT_LANG=deu+fra+ita+eng`, `MAILBOX_TENANT_ID`, `MAILBOX_CLIENT_ID`, `MAILBOX_CLIENT_SECRET`, `MAILBOX_USERS`, `MAILBOX_FOLDERS_INCLUDE`, `MAILBOX_FOLDERS_EXCLUDE`, `MAILBOX_MIRROR_PATH`, `MAILBOX_INTERVAL_SECONDS`, `MAILBOX_INCLUDE_ATTACHMENTS`, `MAILBOX_IDENTIFIER_PREFIX`, `RC_PST_INBOX`, `RC_PST_STAGING`, `scripts/explode_pst.py` (defined in the RemoteController part); Platform: `ONTOLOGY_SOURCE`, `ONTOLOGY_FIXTURE_PATH`, `ONTOLOGY_FILTER_STATE_PATH` (shipped, `KnovasPlatform/.env.example:80-99`), `ZEFIX_USERNAME`, `ZEFIX_PASSWORD` (design §6.7), `JOURNAL_RETENTION_DAYS=90` (design §6.12), the poller keys of `src/events_poller.py` (defined in the Platform events part — see Step 6 for the reconciliation rule); endpoints from the Registry: `/secured/graph/*`, `GET /secured/events`, `POST|GET /secured/webhooks`, `GET /secured/export/graph`, `GET /secured/export/documents`, `POST /secured/documents/<uuid>/similar`, `GET /secured/document/<uuid>/versions`, `PATCH /secured/documents/<uuid>/metadata`, `GET /secured/transmissions/<transmission_key_id>/status`, `GET /secured/graph/jobs/<job_id>`; add-in component `KnovasPlatform/components/knovas_office_addins/` with `manifest.outlook.xml`, `manifest.word.xml`, served at `/addins/*`.
- Produces: the customer-facing deployment facts every other doc defers to (formats list, connector env blocks, endpoint list, go-live rows).

- [ ] **Step 1: Record the state (failing check)**

Run:

```bash
grep -n "Document version\|Last updated" docs/specifications.md | head -2
grep -n "xlsx\|pptx\|MAILBOX_\|RC_PST_\|/secured/graph\|ONTOLOGY_SOURCE\|knovas_office_addins\|product-statements" docs/specifications.md || echo "none of the new surface is in the spec"
```

Expected: `1.0` / `July 2026`; `none of the new surface is in the spec`.

- [ ] **Step 2: Header bump (lines 4-9)**

Replace the header table with:

```markdown
|                      |                                                                                   |
| -------------------- | --------------------------------------------------------------------------------- |
| **Document version** | 1.1                                                                               |
| **Last updated**     | August 2026                                                                       |
| **Audience**         | Customer IT / operations teams deploying and operating Knovas-hosted components   |
| **Scope**            | RemoteController, KnovasPlatform and the Office add-ins as delivered in the Knovas Components package. What the product does and does not do: [product-statements.md](product-statements.md) |
```

- [ ] **Step 3: §1.2 runtime dependencies (lines 99-106) and §1.3 formats (lines 108-112)**

Replace the §1.2 bullet list with:

```markdown
- `flask`, `gunicorn`, `requests`, `cryptography`, `jsonschema`, `prometheus-client`
- `knovas-extract` — text, sentences, tables and metadata for every format below (OCR via Tesseract, language packs `deu`, `fra`, `ita`, `eng` in the image)
- `python-docx`, `mammoth` — `.docx`
- `pymupdf` — PDF text extraction and page rendering for OCR
- `extract-msg` — Outlook `.msg` e-mail parsing
- `openpyxl` — `.xlsx` (worksheets as tables)
- `python-pptx` — `.pptx` (one page per slide, notes included)
- `libpst` (`readpst`, GPL, invoked as a separate process by `scripts/explode_pst.py`) — `.pst` archives
- `py3langid` — document-language fallback when the extractor reports none
```

Replace §1.3 with:

```markdown
### 1.3 Supported source formats

`.md`, `.txt` (UTF-8), `.docx`, `.pdf` (text layer or OCR), `.eml`, `.msg`,
`.xlsx`, `.pptx`. `.pst` archives are not watched directly: `scripts/explode_pst.py`
unpacks them into `RC_PST_STAGING` (folder hierarchy preserved, `Message-ID`
recorded for dedup) and the resulting `.eml` files and attachments are ingested
like any other document (see `RemoteController/docs/connectors.md`).

Binary formats are converted to Markdown for indexing; tables in `.docx`,
`.pdf` and `.xlsx` are transmitted as structured tables. The original path is
preserved as the document identifier so KnovasPlatform can open or download the
source file. Every document is sent with metadata (author, language, dates,
document type, status, source kind) — see §1.6 "Document metadata".

Not supported: legacy `.doc`, standalone scanned images (TIFF/JPG), encrypted
files that need a password. Scanned PDFs are OCR'd; language packs default to
`deu+fra+ita+eng`.
```

- [ ] **Step 4: §1.6 — replace the OneDrive block (lines 196-198) with the connectors and metadata blocks**

Replace

```markdown
**Optional OneDrive mirror**

- `ONEDRIVE_DRIVE_ID`, `ONEDRIVE_TENANT_ID`, `ONEDRIVE_CLIENT_ID`, `ONEDRIVE_CLIENT_SECRET`
```

with

```markdown
**Optional OneDrive / SharePoint mirror** (Microsoft Graph, application permission `Files.Read.All`)

- `ONEDRIVE_DRIVE_ID`, `ONEDRIVE_TENANT_ID`, `ONEDRIVE_CLIENT_ID`, `ONEDRIVE_CLIENT_SECRET`
- `ONEDRIVE_MIRROR_PATH` (point `RC_WATCH_ROOTS` at it), `ONEDRIVE_ALLOWED_EXTENSIONS`, `ONEDRIVE_MIRROR_INTERVAL_SECONDS`

**Optional mailbox mirror** (Microsoft Graph, application permission `Mail.Read` with admin consent; messages are materialised as `.eml` under `MAILBOX_MIRROR_PATH`, attachments beside them, and ingested like files)

- `MAILBOX_TENANT_ID`, `MAILBOX_CLIENT_ID`, `MAILBOX_CLIENT_SECRET` — the Entra app
- `MAILBOX_USERS` — comma-separated user principal names; **only** these mailboxes are read
- `MAILBOX_FOLDERS_INCLUDE`, `MAILBOX_FOLDERS_EXCLUDE` — folder allow/deny lists
- `MAILBOX_MIRROR_PATH` (point `RC_WATCH_ROOTS` at it), `MAILBOX_INTERVAL_SECONDS`, `MAILBOX_INCLUDE_ATTACHMENTS`, `MAILBOX_IDENTIFIER_PREFIX`

**Optional PST import** (bulk migration; one archive per cycle, resumable)

- `RC_PST_INBOX` — writable directory where `.pst` files are dropped
- `RC_PST_STAGING` — writable directory for the exploded messages; add it to `RC_WATCH_ROOTS`

**Document metadata at ingest**

- `RC_SEND_DOCUMENT_METADATA` (default `1`) — send `metadata` (author, language, document date, type, status, source kind) with every transmission; requires a Knovas API that accepts it (Secure API ≥ the release documented in `KnovasAPI/Secure_API.md`)
- `RC_MATTER_PATH_RULE` — optional regex with a named group over the relative path; the match is resolved to a matter node and the document is assigned at upload
- `RC_LANGUAGE_DETECT` (default `1`) — detect the document language when the extractor reports none
- `RC_TESSERACT_LANG` (default `deu+fra+ita+eng`), `RC_PDF_OCR_ENABLED` (default `true`)

Defaults and behaviour of every connector key: `RemoteController/docs/connectors.md`; metadata keys: `RemoteController/docs/configuration.md`.
```

- [ ] **Step 5: §2.3 — the endpoints line (line 324)**

Replace

```markdown
Knovas API endpoints used (configurable base URL): `/secured/query`, `/secured/health`, `/api/search`, `/secured/init_document_transmission`, `/secured/transmit_document_part`, `/secured/generate_certificate`.
```

with

```markdown
Knovas API endpoints used by the Platform (configurable base URL, all mTLS):

| Group | Endpoints |
| --- | --- |
| Search | `POST /secured/query` (with `filters`, `limit`, `offset`, `sort`, `facets`, `scope`), `GET /secured/health`, `/api/search` (Platform-internal) |
| Documents | `GET /secured/document/<uuid>/versions`, `POST /secured/documents/<uuid>/similar`, `PATCH /secured/documents/<uuid>/metadata`, `GET /secured/transmissions/<transmission_key_id>/status` |
| Upload (add-in filing, Zefix evidence) | `POST /secured/init_document_transmission` (with `metadata`, `graph_assign`), `POST /secured/transmit_document_part` |
| Knowledge graph | `/secured/graph/*` — node types, nodes, facts, evidence, trust, identifiers search, duplicates, merge, conflict checks, facts listing/adopt/propose, ego, imports, `GET /secured/graph/jobs/<job_id>` |
| Events | `GET /secured/events?after=&limit=&types=` (the Platform's poller; pull only) |
| Export | `GET /secured/export/graph`, `GET /secured/export/documents` (NDJSON, streamed) |
| Certificates | `/secured/generate_certificate` |

Not used by the Platform: `POST|GET /secured/webhooks`, `DELETE /secured/webhooks/<id>`, `POST /secured/webhooks/<id>/test` — the push channel for integrators, see `KnovasAPI/Events_API.md`. When the tenant's knowledge graph is disabled every `/secured/graph/*` call answers `404 knowledge_graph_disabled` and the graph screens render "Wissensnetz-Modus erforderlich".
```

- [ ] **Step 6: §2.5 — Platform environment variables (after the "File-open companion" block, line 368)**

Append after the companion block:

```markdown
**Cortex / graph mode**

- `ONTOLOGY_SOURCE` (`fixture` | `graph`, default `fixture`) — `graph` uses `/secured/graph/*` over mTLS; the tenant's knowledge graph must be enabled, otherwise `error_code knowledge_graph_disabled`. On the fixture every graph screen shows the badge "Demo-Daten".
- `ONTOLOGY_FIXTURE_PATH` (default `/mnt/ontology/ontology_fixture.json`) — writable; without it Cortex is empty
- `ONTOLOGY_FILTER_STATE_PATH` (default `/app/data/ontology_filter_state.json`) — local review state of the Cortex filters (rejections are stored server-side, permanently)

**Events (Posteingang)**

- The Platform pulls `GET /secured/events` on a schedule and stores the cursor in `platform-db`; the poller's keys (`EVENTS_POLL_*` — enabled flag, interval in seconds, batch limit) are documented with their defaults in `KnovasPlatform/docs/integration/events.md`. Only one worker polls at a time (advisory lock), so `DOCBRIDGE_WEB_WORKERS=2` is safe.

**Zefix enrichment (optional)**

- `ZEFIX_USERNAME`, `ZEFIX_PASSWORD` — the firm's Zefix public REST credentials; the button is hidden when unset. Calls go from the Platform host to `www.zefix.admin.ch`, never from Knovas.

**Arbeitstag-Journal (opt-in per user)**

- `JOURNAL_RETENTION_DAYS` (default `90`)

**Development only**

- `SEARCH_USE_TEST_RESULTS` — canned search results; the UI shows a persistent "Beispieldaten" banner while set. Never in production.
```

Reconciliation rule for the poller keys: before publishing, run `grep -rn "EVENTS_POLL_" KnovasPlatform/components/docbridge_integration/src/events_poller.py` and list the exact names and defaults found (the Platform events part owns them) in the bullet above; if the module does not exist yet on the branch, keep the descriptive sentence and re-run this step when it lands.

- [ ] **Step 7: §2.8 — add the Office add-ins as a client component (after line 438, before §2.9)**

Insert before `### 2.9 Demo / mock mode`:

```markdown
**Office add-ins (optional client component)**

`KnovasPlatform/components/knovas_office_addins/` ships two manifests
(`manifest.outlook.xml`, `manifest.word.xml`) and one static taskpane app that
the Platform serves at `https://<fqdn>/addins/*` — the same HTTPS origin as the
web UI, so the Platform session cookie authenticates the taskpane.

| Item | Requirement |
| --- | --- |
| Office | Microsoft 365 Apps or Office 2019+ (Office.js / Edge WebView2); Outlook desktop, Outlook on the web, Word |
| Transport | HTTPS only (Office refuses `http:` manifests and taskpanes); the Platform TLS certificate must be trusted on the client PC |
| Deployment | Microsoft 365 admin center *Centralized Deployment* (recommended) or user sideload of the manifest URL |
| Permissions | Outlook manifest: `ReadWriteMailbox` (needed for `makeEwsRequestAsync` to fetch the message MIME); Word manifest: `ReadWriteDocument` |
| What it does | Outlook: "In Knovas ablegen" files the e-mail (and attachments) to a matter in two clicks; Word: search from Word, open the original, insert a citation |
| Platform routes | `POST /api/filing/email`, `GET /api/filing/suggest`, `/addins/*` |

Guide: `KnovasPlatform/docs/integration/office-add-ins.md`.
```

- [ ] **Step 8: §4 go-live checklist rows**

In **RemoteController → Remote-operator mode** and **Local-only control mode** (both lists, lines 503-525), append:

```markdown
- [ ] `RC_TESSERACT_LANG` includes every language of the estate (default `deu+fra+ita+eng`)
- [ ] `RC_SEND_DOCUMENT_METADATA=1` confirmed against the tenant API (a test document shows author/date/type in the Platform's hit metaline)
- [ ] (If mailbox mirror) Entra app with `Mail.Read` application permission and admin consent; `MAILBOX_USERS` allow-list agreed in writing; first delta cycle completed without cursor advance on failures
- [ ] (If PST migration) `RC_PST_INBOX` and `RC_PST_STAGING` on writable volumes; migration run per `RemoteController/docs/migration.md`; "N Dokumente eingereicht, N indexiert" verified via `GET /secured/transmissions/<key>/status`
```

In **KnovasPlatform (production intranet)** (lines 530-541), append:

```markdown
- [ ] `ONTOLOGY_SOURCE=graph` and the tenant knowledge graph enabled — no "Demo-Daten" badge in the sidebar
- [ ] Posteingang shows events (poller running; `GET /secured/events` reachable)
- [ ] Search filter rail shows facets for a test query; metadata backfill run for documents ingested before this release
- [ ] (If add-ins) `https://<fqdn>/addins/manifest.outlook.xml` and `manifest.word.xml` reachable from a client PC; add-in deployed via Centralized Deployment or sideloaded; one e-mail filed to a test matter
- [ ] (If Fristen in Outlook) one user's ICS feed subscribed in Outlook and a confirmed deadline visible
- [ ] `docs/product-statements.md` handed to the project lead together with this checklist
```

- [ ] **Step 9: §7 index tables**

In the **RemoteController** table (lines 597-607) add after the `configuration.md` row:

```markdown
| `RemoteController/docs/connectors.md`           | Connectors: share, OneDrive/SharePoint, mailbox (Graph), PST, XLSX/PPTX |
| `RemoteController/docs/migration.md`            | Fixed-price migration runbook       |
```

In the **KnovasPlatform** table (lines 612-624) add after the `open-tokens-api.md` row:

```markdown
| `KnovasPlatform/docs/integration/office-add-ins.md`      | Outlook and Word add-ins  |
| `KnovasPlatform/docs/integration/graph-api.md`           | Platform HTTP routes      |
| `KnovasPlatform/docs/integration/events.md`              | Events: poller, Posteingang, webhooks |
| `KnovasPlatform/docs/features/`                          | One document per screen family, each with its status label |
| `KnovasPlatform/CHANGELOG.md`                            | Platform changelog        |
```

Append a third table after the KnovasPlatform table:

```markdown
### Cross-component

| Document                                        | Purpose                                                        |
| ----------------------------------------------- | -------------------------------------------------------------- |
| `docs/README.md`                                | Documentation index by audience                                |
| `docs/product-statements.md`                    | Status-label legend and the E1/F4/F6/H6/J1 declarations       |
| `docs/hosting-requirements.md`                  | Hosting-partner provisioning contract                          |
| `docs/certificates.md`                          | mTLS bundle per component                                      |
| `docs/KnovasAPI/README.md`                      | Developer Kit mirror (API contract) and mirror policy          |
| `RELEASE_NOTES.md`                              | What shipped in this release                                   |
```

- [ ] **Step 10: Verify**

Run:

```bash
grep -n "Document version.*1.1\|Last updated.*August 2026" docs/specifications.md
grep -c "MAILBOX_\|RC_PST_\|/secured/graph/\*\|ONTOLOGY_SOURCE\|knovas_office_addins\|product-statements.md\|connectors.md\|migration.md" docs/specifications.md
grep -n "^- \[ \]" docs/specifications.md | wc -l
```

Expected: the two header lines; a count ≥ 20; `39` checklist rows (27 before + 4 + 4 + 6 — adjust the expected number if the two RC lists were counted differently, but the sum of new rows is 14).

- [ ] **Step 11: Commit**

```bash
git add docs/specifications.md
git commit -m "docs(spec): v1.1 — formats incl. xlsx/pptx/pst, mailbox/PST connectors, graph/events/export endpoints, ONTOLOGY/events/Zefix keys, Office add-ins, go-live rows, index"
```

---

---

### Task KC-G-5: `docs/hosting-requirements.md` — mailbox/PST options, Graph and Zefix egress, per-seat throughput, handover rows

**Requirements:** F2, H1 (document sources), D3 (Zefix egress), F4 (per-seat throughput), H2 (add-in client prerequisites)
**Files:**
- Modify: `docs/hosting-requirements.md:11-19` (at-a-glance table), `:50-55` (sizing table), `:92-95` (outbound table), `:125-132` (Option B) + new Options C/D, new section "Query throughput per seat", `:149-163` (handover checklist), `:169-174` (further reading)
**Interfaces:**
- Consumes: `docs/product-statements.md#3` F4 numbers (KC-G-1); env names `MAILBOX_*`, `RC_PST_INBOX`, `RC_PST_STAGING`, `ZEFIX_USERNAME`/`ZEFIX_PASSWORD` (as in KC-G-4); hosts `graph.microsoft.com`, `login.microsoftonline.com`, `www.zefix.admin.ch`.
- Produces: the section anchor `hosting-requirements.md#query-throughput-per-seat` linked from product-statements §3.

- [ ] **Step 1: Record the state (failing check)**

Run: `grep -n "Option C\|Option D\|zefix\|graph.microsoft.com\|per seat\|seat_count" docs/hosting-requirements.md || echo "none present"`
Expected: `none present`.

- [ ] **Step 2: At-a-glance table (lines 11-19) — add three rows**

Append to the table:

```markdown
| (Mailbox mirror) Entra app registration with `Mail.Read` application permission, admin consent | (Mailbox mirror) The list of mailboxes to mirror (`MAILBOX_USERS`), agreed in writing |
| (PST migration) Writable inbox and staging volumes on the VM (see sizing) | (PST migration) The `.pst` files, delivered to the inbox volume |
| (Zefix) Outbound HTTPS to `www.zefix.admin.ch` from the VM | (Zefix) The firm's Zefix public REST credentials |
```

- [ ] **Step 3: Sizing table (lines 50-55) — add the migration row**

Append to the table:

```markdown
| PST / mailbox migration window | — | +2 GB RAM | + 1.5 × total PST size on `RC_PST_STAGING` during the migration | Temporary; staging is deleted after verification. Ingest rate is raised for the window per `RemoteController/docs/migration.md` |
```

- [ ] **Step 4: Outbound table (lines 92-95) — replace with**

```markdown
| Target | Required when |
|--------|---------------|
| Knovas tenant API (HTTPS, typically `:8443`, mTLS) | Always — document sync, search, graph, events, export |
| `login.microsoftonline.com` (HTTPS 443) | OneDrive / SharePoint mirror **or** mailbox mirror — token endpoint |
| `graph.microsoft.com` (HTTPS 443) | OneDrive / SharePoint mirror (`Files.Read.All`) **or** mailbox mirror (`Mail.Read`) — data endpoint |
| `www.zefix.admin.ch` (HTTPS 443) | Zefix / UID enrichment only (`ZEFIX_USERNAME` set); called from the Platform on the VM, never from Knovas |

PST import needs no egress beyond the Knovas API. Nothing else is required —
no telemetry, no update server, no CDN.
```

- [ ] **Step 5: Document sources — Options C and D after Option B (after line 132)**

Insert:

```markdown
### Option C — Mailbox mirror via Microsoft Graph (optional)

| Item | Requirement |
|------|-------------|
| Microsoft Entra app | Tenant ID, client ID, client secret; **application** permission `Mail.Read` with admin consent |
| Mailbox scope | Explicit allow-list of user principal names (`MAILBOX_USERS`); folder include/exclude lists; nothing outside the list is read |
| VM storage | Local mirror path (`MAILBOX_MIRROR_PATH`) on the VM — messages as `.eml`, attachments beside them (size ≈ mailbox size of the mirrored folders) |
| Cadence | Delta queries per folder every `MAILBOX_INTERVAL_SECONDS`; full walk on first run |
| Not included | IMAP / Exchange Web Services (planned as later options); shared-mailbox rules beyond the allow-list |

### Option D — PST archives (migration)

| Item | Requirement |
|------|-------------|
| Delivery | `.pst` files copied to `RC_PST_INBOX` on the VM (secure channel; the files contain e-mail) |
| Staging | Writable `RC_PST_STAGING` sized 1.5 × the total PST size (see sizing table) |
| Tooling | `readpst` (libpst, GPL) inside the RemoteController image, invoked as a separate process; folder hierarchy preserved |
| Dedup | Message-ID recorded; a message already mirrored from the live mailbox is not indexed twice |
| Verification | "N Dokumente eingereicht, N indexiert" from `GET /secured/transmissions/<key>/status`, documented in `RemoteController/docs/migration.md` |
```

- [ ] **Step 6: New section "Query throughput per seat" (before "Admin access for Knovas setup", line 141)**

Insert:

```markdown
## Query throughput per seat

The sizing table above sizes the **VM**. The number of searches the tenant may
run per minute is decided by the Knovas API, not by the VM:

| | Value | Where it comes from |
|---|---|---|
| Today's tenant default | 12 queries/minute per client certificate, burst 2 (+ an application bucket of ~1 query per 5 s) | `KnovasAPI/Client_Integration_Guide.md` → Operational limits |
| Contractual, after seat count is set | **6 queries/minute per licensed seat sustained, burst 18 — cluster-wide, shared by all users of the tenant**; p95 latency ≤ 3.0 s at 20 concurrent seats on the reference deployment | [product-statements.md §3](product-statements.md#3-f4--throughput-per-seat-the-slo-statement--built-after-this-plan-gated-until-seat_count-is-set-for-your-tenant) |
| Example, 20 seats | 120 queries/minute sustained, burst 360 | seats × 6 / seats × 18 |
| Over the limit | `429` with `Retry-After`; the Platform waits and retries once, then tells the user | — |

The seat count is the contractual number and is set by Knovas operations at
onboarding; confirm it on the handover checklist. Platform-side capacity
(2 workers × 4 threads by default) is not the constraint at these rates.
```

- [ ] **Step 7: Handover checklist (lines 153-163) — append rows**

```markdown
- [ ] Seat count for the tenant confirmed with Knovas (drives the query budget above)
- [ ] (If mailbox mirror) Entra app with `Mail.Read` application permission and admin consent; `MAILBOX_USERS` allow-list agreed in writing; egress to `login.microsoftonline.com` and `graph.microsoft.com` allowed
- [ ] (If PST migration) `.pst` files delivered to the inbox volume; staging volume sized 1.5 × PST total; migration window agreed
- [ ] (If Zefix) egress to `www.zefix.admin.ch` allowed; firm's Zefix credentials delivered securely
- [ ] (If Office add-ins) Platform TLS certificate trusted by Office on client PCs; deployment channel decided (Centralized Deployment or sideload)
```

- [ ] **Step 8: Further reading (lines 169-174) — add rows**

```markdown
| [product-statements.md](product-statements.md) | Status labels, throughput statement, what is out of scope |
| [RemoteController/docs/connectors.md](../RemoteController/docs/connectors.md) | Mailbox, PST, OneDrive connectors |
| [RemoteController/docs/migration.md](../RemoteController/docs/migration.md) | Migration runbook |
```

- [ ] **Step 9: Verify**

Run: `grep -c "Option C\|Option D\|www.zefix.admin.ch\|graph.microsoft.com\|per licensed seat\|Seat count" docs/hosting-requirements.md`
Expected: `≥ 8`.

- [ ] **Step 10: Commit**

```bash
git add docs/hosting-requirements.md
git commit -m "docs(hosting): mailbox/PST options, Graph and Zefix egress, per-seat query throughput, handover rows"
```

---

---

### Task KC-G-6: `docs/search-ui-backlog.md` — dated update with the F3/F6/F7/F8 resolutions

**Requirements:** F3, F6, F7, F8, F9 (and §3a honesty)
**Files:**
- Modify: `docs/search-ui-backlog.md:3` (Stand line), `:47-68` (§3), `:70-85` (§3a), `:110-118` (§5), `:131-135` (§6 bullets); append §7 (F6) and §8 (F8)
**Interfaces:**
- Consumes: the API contract names from the Interface Registry (`filters/limit/offset/sort/facets`, `total_ranked`, `has_more`, `no_strong_matches`, `chunk_uuid`, `snippet`, `GET /secured/document/<uuid>/versions`, `POST /secured/documents/<uuid>/similar`, `kg_node_ids`); the design sections §5.3, §6.2–§6.4, §6.12; the plan file names `docs/superpowers/plans/2026-08-15-pflichtenheft-d-j-*.md`.
- Produces: the resolved backlog the Platform feature docs cite; the maintenance rule (strike the heading with the completion date once the screen ships).

- [ ] **Step 1: Record the state**

Run: `sed -n '3p' docs/search-ui-backlog.md && grep -n "^## " docs/search-ui-backlog.md`
Expected: `Stand: 2026-07-30, nach dem Trefferlisten-Umbau.` and headings §1–§6.

- [ ] **Step 2: Re-date and add the reading rule (line 3 and the paragraph after it)**

Replace line 3 with:

```markdown
Stand: 2026-08-15, nach dem Pflichtenheft-Plan D–J (`superpowers/plans/2026-08-15-pflichtenheft-d-j-*.md`).
```

Append after the introductory paragraph (after line 8):

```markdown
Konvention: Ein Punkt wird durchgestrichen und mit „erledigt" plus Datum
versehen, sobald die Umsetzung auf dem Zweig liegt — nicht, sobald sie geplant
ist. Was seit dem 2026-08-15 einen Plan hat, trägt den Vermerk „in Umsetzung"
mit dem Verweis; der Punkt bleibt offen, bis der Bildschirm läuft.
```

- [ ] **Step 3: §3 (F7) — the measurement precondition is resolved**

Append at the end of §3 (after line 68):

```markdown
**Stand 2026-08-15 — in Umsetzung** (Plan D–J, Platform §6.4, Anforderung F7).
Entschieden: pdf.js wird als `static/js/vendor/pdfjs/pdf.mjs` und
`pdf.worker.mjs` mitgeliefert, `worker-src 'self'` kommt in die CSP, die Route
`/viewer?doc=&path=&page=&snippet=` öffnet auf der Trefferseite und markiert den
Treffertext über die Textebene. Die API liefert dafür pro Treffer neu
`chunk_uuid`, `chunk_kind` und `snippet` (≤ 300 Zeichen des Originaltexts).
Die Vorbedingung „vorher erheben, wie oft PDFs geöffnet werden" ist aufgelöst:
das Arbeitstag-Journal (§6.12 des Plans, opt-in) zählt Öffnungen nach Format,
die ersten Wochen liefern die Zahl nach — nicht vor — der Umsetzung, weil
Sprung-zur-Fundstelle die meistgenannte Anforderung im Pflichtenheft (F7) ist.
Nur PDFs rendern eine echte Seite; DOCX, TXT und MSG öffnen weiterhin die
Textvorschau, das bleibt so.
```

- [ ] **Step 4: §3a — state the direction (graph scope replaces the sidecar for grouping)**

Append at the end of §3a (after line 85):

```markdown
**Stand 2026-08-15.** Die Akte kommt künftig aus dem Graphen, nicht aus der
Sidecar-Datei: Akten sind Knoten, ein Treffer trägt sichtbare `kg_node_ids`, und
die Suche wird über `scope` auf eine Akte eingeschränkt (Matters-Plan C8,
Plan D–J §6.2). Die `.search_enrichment.jsonl` bleibt für „In OneDrive
öffnen" zuständig; `onedrive_enrichment_loaded` wird im Systemstatus der
Einstellungen angezeigt, damit ein fehlendes Sidecar sichtbar ist. Der Punkt
bleibt offen, bis beides auf dem Zweig liegt.
```

- [ ] **Step 5: §5 (F3) — the API side is now a contract; the ordering stays**

Append at the end of §5 (after line 118):

```markdown
**Stand 2026-08-15 — in Umsetzung, in der richtigen Reihenfolge.** Die API
bekommt zuerst den Vertrag (Plan D–J, Backend §5.2/§5.3, Anforderung F3):
`POST /secured/query` nimmt `filters` (Autor, Dokumenttyp, Sprache, Status,
Quelle, Datum von/bis, Pointer-Präfix — konjunktiv, nie erweiternd), `limit`,
`offset`, `sort` (`relevance | date_desc | date_asc`) und `facets` entgegen; die
Antwort trägt `total_ranked`, `has_more`, `facets` (Verteilung **in den
Treffern**, nicht im Korpus — steht so in der UI) und je Treffer Titel, Autor,
Typ, Datum, Sprache, Status, Quelle. Die Metadaten kommen vom RemoteController
beim Einreichen (`metadata`); für den bestehenden Korpus braucht es einmal den
Backfill (`backfill-metadata`), sonst sind Facetten für alte Dokumente leer —
das steht im Go-live-Check der Spezifikation. Erst danach die Filterleiste
(§6.2 des Plans). `exact_match` wird nicht mehr an die API weitergereicht;
Filter, die die API mit `400 validation_error` ablehnt, werden angezeigt statt
still verworfen.
```

- [ ] **Step 6: §6 bullets — "Mehr laden" and "Leerzustand"**

Replace the two bullets at lines 131-135 with:

```markdown
- **„Mehr laden" ist eine zweite vollständige Suche.** Die API kennt kein
  `offset`; der Knopf erhöht das Limit und ersetzt die Liste. Bei langsamer API
  spürbar. Ungetestet, weil die Demo nie mehr Treffer liefert als das Limit.
  *Stand 2026-08-15 — in Umsetzung:* mit `offset`/`has_more` (F3) wird daraus
  ein echtes „Weitere Treffer"; das Fenster liegt über **einer** gerankten,
  gefilterten Menge (Obergrenze: der Rerank-Pool), nicht über dem Korpus.
- **Leerzustand ungetestet.** Die Demo-Fixtures liefern auch bei Unsinn-Anfragen
  Treffer, der neue Leerzustand liess sich deshalb im Browser nicht auslösen.
  *Stand 2026-08-15 — in Umsetzung:* `no_strong_matches` ist in jeder Antwort
  vorhanden (`false`, wenn das Gate nicht lief), der Leerzustand zeigt es samt
  API-Status an (F9); der Platform-Test für den Leerzustand kommt mit der
  Filterleiste.
```

- [ ] **Step 7: New §7 (F6) and §8 (F8)**

Append at the end of the file:

```markdown

## 7. Versionen eines Dokuments — in Umsetzung

Bis zum 2026-08-15 galt hier dasselbe wie in §5: kein Endpunkt, also keine
Frontend-Aufgabe. `DocumentVersion` existierte im Backend, war aber über
`/secured/*` nicht abrufbar, und die inhaltsadressierte Deduplikation liess
Historie beim Umhängen von Pointern verschwinden.

Neu (Plan D–J §5.3.3, Anforderung F6, Stufe 1): `GET /secured/document/<uuid>/versions`
liefert `current` und `versions[]` mit `version_number`, `pointer_at_version`,
`path`, `timestamp`, `changed_by`; jeder Treffer trägt `is_current`,
`version_count`, `has_versions`. Der Dokumentdialog zeigt die Liste mit
Abzeichen „aktuelle Version". **Stufe 2** — den Text einer überholten Version
durchsuchen — ist bewusst nicht im Umfang (Datenmodell und Indexgrösse); die
Anforderung „die unterzeichnete Fassung, nicht Entwurf 7 von 12" löst der
Filter `document_status` (Entwurf | final | unterzeichnet), siehe
`product-statements.md` §4.

## 8. Ähnliche Dokumente und ähnliche Akten — in Umsetzung

Bis zum 2026-08-15: nirgends erwähnt, kein Endpunkt, `Input` war das einzige
Feld. Neu (Plan D–J §5.3.4, Anforderung F8): `POST /secured/documents/<uuid>/similar`
mit `limit`, `filters`, `scope`, Antwort in der Trefferform der Suche, das
Ausgangsdokument ausgeschlossen, dasselbe Relevanz-Gate wie die Suche (also
auch hier ein ehrlicher Leerzustand). Jeder Treffer trägt sichtbare
`kg_node_ids`; die Aktenseite gruppiert danach zu „Ähnliche Akten". Teilt das
Anfragebudget mit der Suche. Die Nachbarschaft im Graphen
(`/secured/graph/nodes/<id>/neighbors`, Tiefe ≤ 3) bleibt die zweite Quelle für
verwandte Akten — Beziehungen, nicht Textähnlichkeit.
```

- [ ] **Step 8: Verify**

Run:

```bash
sed -n '3p' docs/search-ui-backlog.md
grep -c "Stand 2026-08-15" docs/search-ui-backlog.md
grep -n "^## 7\|^## 8" docs/search-ui-backlog.md
```

Expected: the new `Stand:` line; `6` dated notes (§3, §3a, §5, two bullets in §6 — counted twice as they are in the same section, plus §7/§8 mention the date in prose — accept ≥ 5); headings `## 7.` and `## 8.`.

- [ ] **Step 9: Commit**

```bash
git add docs/search-ui-backlog.md
git commit -m "docs(backlog): Stand 2026-08-15 — F3/F6/F7/F8 resolutions cross-linked to the D–J plan"
```

---

---

### Task KC-G-7: Release notes v1.1.0 draft, `KnovasPlatform/CHANGELOG.md`, RemoteController `Unreleased` entries

**Requirements:** all shipped D–H, J items (per-component release record); H2 (third component with its own deploy link and prerequisites)
**Files:**
- Modify: `RELEASE_NOTES.md` (whole file — the convention replaces the file per release; the previous content stays in the GitHub Release `v1.0.0`)
- Create: `KnovasPlatform/CHANGELOG.md`
- Modify: `RemoteController/CHANGELOG.md:3-8` (`## Unreleased` bullets)
**Interfaces:**
- Consumes: every deliverable named in the Interface Registry (routes, modules, env keys, metrics, schema fields) — copied verbatim into the bullets; the docs written in KC-G-1…KC-G-6.
- Produces: `RELEASE_NOTES.md` v1.1.0 (draft until tagged) with sections `## KnovasPlatform`, `## RemoteController`, `## Office add-ins`, `## Prerequisites (from Knovas)`, `## Prerequisites (from the customer)`; `KnovasPlatform/CHANGELOG.md` in the RemoteController's Keep-a-Changelog shape (`## Unreleased`, then `## <semver> — <YYYY-MM-DD>`).

- [ ] **Step 1: Record the state**

Run: `head -1 RELEASE_NOTES.md; test -f KnovasPlatform/CHANGELOG.md && echo EXISTS || echo "missing: KnovasPlatform/CHANGELOG.md"; sed -n '3,8p' RemoteController/CHANGELOG.md`
Expected: `# v1.0.0`; `missing: KnovasPlatform/CHANGELOG.md`; the four current Unreleased bullets.

- [ ] **Step 2: Rewrite `RELEASE_NOTES.md` as the v1.1.0 draft**

Replace the whole file with:

```markdown
## v1.1.0 (draft — tag when the D–J plan's last phase is merged)

Customer deploy bundle for Knovas. This release delivers the Pflichtenheft
sections D–H and J: search filters and versions, the party register and the
conflicts check, deadlines with four-eyes and an Outlook feed, events and the
Posteingang, the mailbox and PST connectors, XLSX/PPTX, the Office add-ins,
export, and the written declarations. What is switched on per tenant, and what
is deliberately not: [docs/product-statements.md](docs/product-statements.md).

## KnovasPlatform

Docker search UI for an indexed Knovas tenant. Requires mTLS client certificates
and company login configuration; the graph screens require `ONTOLOGY_SOURCE=graph`
and a tenant with the knowledge graph enabled.

- Search: filter rail (Akte, Praxisgebiet, Dokumenttyp, Autor, Zeitraum, Sprache, Status, Quelle), sort, real paging, facet chips, richer hit metaline, "Wer kennt sich aus?"; honest empty state and the "Beispieldaten" banner
- Document dialog: version list, "Ähnliche Dokumente" / "Ähnliche Akten", table rendering, metadata edit
- Viewer: vendored pdf.js, jump to the hit page with the snippet highlighted (`/viewer`)
- Parteien: register, identifier kinds, duplicates queue, merge (guarded), Zefix enrichment
- Konfliktprüfung: check, decisions, printable protocol, lateral-hire CSV/XLSX import
- Fristen: proposals from extracted deadlines, four-eyes confirmation, per-user ICS feed for Outlook (`/feeds/deadlines.ics`)
- Posteingang: event poller and inbox; sidebar badge; job polling
- Cortex on the live graph: "Demo-Daten" badge on fixture, "Akten-Kompass" (ego graph), "Warum?" panel, trust chips with scope, Berichte, `/import` wizard with dry-run
- Arbeitstag-Journal (opt-in): `/mein-tag`, CSV export for the PMS timesheet
- Deploy: [KnovasPlatform/docs/setup.md](KnovasPlatform/docs/setup.md)
- Screens: [KnovasPlatform/docs/README.md → Screens and features](KnovasPlatform/docs/README.md)
- Changelog: [KnovasPlatform/CHANGELOG.md](KnovasPlatform/CHANGELOG.md)
- API reference: [docs/KnovasAPI/README.md](docs/KnovasAPI/README.md)

## RemoteController

Discover and sync local files, mirrored OneDrive/SharePoint libraries, mirrored
mailboxes and exploded PST archives to Knovas (employee JWT; tenant mTLS for
ingestion).

- Document metadata at ingest (author, language, dates, type, status, source kind) — `RC_SEND_DOCUMENT_METADATA`; optional matter assignment from the path (`RC_MATTER_PATH_RULE`)
- Formats: `.xlsx` (worksheets as tables) and `.pptx` (slides as pages) in addition to `.md .txt .docx .pdf .eml .msg`
- Mailbox mirror via Microsoft Graph (`MAILBOX_*`), PST exploder and queue (`RC_PST_INBOX`, `RC_PST_STAGING`)
- OCR: Italian language pack; default `RC_TESSERACT_LANG=deu+fra+ita+eng`; extraction metrics; synthetic DE/FR/IT OCR benchmark and on-prem runbook
- Index-status verification for migrations (`content_sha256`, `index_status`, `indexed_at` in the state DB)
- Deploy: [RemoteController/docs/SETUP.md](RemoteController/docs/SETUP.md)
- Connectors: [RemoteController/docs/connectors.md](RemoteController/docs/connectors.md) · Migration: [RemoteController/docs/migration.md](RemoteController/docs/migration.md)
- Changelog: [RemoteController/CHANGELOG.md](RemoteController/CHANGELOG.md)

## Office add-ins

Outlook and Word add-ins served by the Platform at `/addins/*` (HTTPS only).
Outlook: "In Knovas ablegen" files an e-mail with attachments to a matter in two
clicks, with dedup by message id. Word: search from Word, open the original,
insert a citation.

- Deploy: [KnovasPlatform/docs/integration/office-add-ins.md](KnovasPlatform/docs/integration/office-add-ins.md)
- Component: [KnovasPlatform/components/knovas_office_addins/](KnovasPlatform/components/knovas_office_addins/)

## Prerequisites (from Knovas)

- Tenant mTLS certificates — each component expects different filenames in a different directory; see [docs/certificates.md](docs/certificates.md). The add-ins and the connectors need no additional certificate (see the note there)
- Documents indexed in Knovas (via RemoteController or your ingestion pipeline); metadata backfill run once for documents indexed before this release
- Tenant knowledge graph enabled; seat count set (drives the query budget — [docs/product-statements.md §3](docs/product-statements.md))
- For RemoteController: instance token, registered public URL (remote-operator mode)

## Prerequisites (from the customer)

- (Mailbox mirror) Microsoft Entra app with `Mail.Read` application permission and admin consent; mailbox allow-list
- (PST migration) `.pst` files delivered to the inbox volume; staging disk per [docs/hosting-requirements.md](docs/hosting-requirements.md)
- (Zefix) firm credentials for the Zefix public REST API; egress to `www.zefix.admin.ch`
- (Office add-ins) Platform TLS certificate trusted by Office; Centralized Deployment or sideload
- (Fristen in Outlook) Person nodes with an `email` identifier for responsible lawyer and deputy
```

- [ ] **Step 3: Create `KnovasPlatform/CHANGELOG.md`**

```markdown
## Changelog

## Unreleased

- Search: filter rail (Akte, Praxisgebiet, Dokumenttyp, Autor, Zeitraum, Sprache, Status, Quelle), sort selector, real "Weitere Treffer" via `offset`, facet chips, hit metaline (type · date · author · language · version badge), "KI-Zusammenfassung" label on `auto_summary` chunks, honest empty state (`no_strong_matches`, API status), persistent "Beispieldaten" banner under `SEARCH_USE_TEST_RESULTS`; UI-only keys are no longer forwarded to the API; API-rejected filters are surfaced.
- Document dialog: version list with `changed_by` and "aktuelle Version" badge; "Ähnliche Dokumente" and, on the matter page, "Ähnliche Akten"; table rendering in `markdown.js`; metadata edit via `PATCH …/metadata`.
- Viewer: vendored pdf.js (`static/js/vendor/pdfjs/`), route `/viewer?doc=&path=&page=&snippet=`, `openEvidence()` helper shared by search hits, Cortex evidence, fact evidence and conflict hits; CSP `worker-src 'self'`.
- Parteien (`/parteien`): register with kind-aware identifier search, identifier editor with kinds, duplicates queue and merge sheet (guarded action), Zefix button on organisations (`src/zefix_client.py`, `ZEFIX_USERNAME`/`ZEFIX_PASSWORD`).
- Konfliktprüfung (`/konfliktpruefung`): form, result page with `withheld_count` and `degraded` rendered prominently, decisions with note, history, printable protocol, lateral-hire CSV/XLSX import.
- Fristen (`/fristen`): Vorschläge / Zur Bestätigung / Bestätigt tabs, adopt/reject, four-eyes-aware confirm button, overdue banner, per-matter widget; per-user ICS feed `GET /feeds/deadlines.ics?token=` (`src/ics_feed.py`).
- Posteingang (`/posteingang`): background events poller with advisory-lock leader (`src/events_poller.py`, platform-db tables `events`, `event_cursor`), sidebar badge, "Ereignisprotokoll" CSV export; upload screens poll transmission status.
- Cortex: `ONTOLOGY_SOURCE=graph` as the deploy-bundle default once cassettes exist; "Demo-Daten" badge on fixture; "Akten-Kompass" ego graph; "Warum?" panel; `trust_chip` macro (tier + scope + signals); `/berichte`; `/import` wizard with dry-run diff; type-level Vorgaben in graph mode; filter `503` states rendered as "kann gerade nicht bewerten — bitte später".
- Arbeitstag-Journal (`/mein-tag`, opt-in per user): platform-db `activity_journal`, per-day matter blocks, CSV export, `JOURNAL_RETENTION_DAYS` (default 90).
- Office add-in host: `/addins/*` static taskpane, `POST /api/filing/email`, `GET /api/filing/suggest`, platform-db `filed_emails`.
- Client: `knovas_client.py` gains `search_documents(query, *, filters, limit, offset, sort, facets, scope)`, `document_versions`, `similar_documents`, `update_document_metadata`, `identifiers_search`, `node_duplicates`, `merge_nodes`, `conflict_check_run/list/get/decide`, `facts_list`, `fact_adopt`, `fact_propose`, `node_ego`, `graph_import`, `events_poll`, `transmission_status`, `graph_job`, `export_graph`, `export_documents`; typed dataclasses `Identifier(kind)`, `ConflictCheck`, `ConflictHit`, `Decision`, `EgoGraph`, `Event` in `graph_model.py`.
- Docs: `docs/features/*.md` (one per screen family, each with its status label), `docs/integration/office-add-ins.md`, `docs/integration/graph-api.md`, `docs/integration/events.md`.

## 1.0.0 — 2026-07-30

- First customer deploy bundle: search UI, document open, Cortex/Wissensnetz on fixture data. History before this file lives in the repository log and in the GitHub Release `v1.0.0`.
```

- [ ] **Step 4: RemoteController `## Unreleased` — append entries (after line 8)**

```markdown
- Document metadata at ingest: `ExtractionPayload`/`ExtractedDocument` carry `author`, `language`, `created`, `modified`, `document_type`, `document_status`, `document_date`, `extra`; `src/sync/document_metadata.py::build_document_metadata` assembles the `metadata` object for `init_document_transmission` (`RC_SEND_DOCUMENT_METADATA`, default on); language fallback via `py3langid` (`RC_LANGUAGE_DETECT`); optional matter assignment from the relative path (`RC_MATTER_PATH_RULE`).
- Formats: `.xlsx` and `.pptx` via `src/sync/office_extractors.py` (`XlsxExtractor`, `PptxExtractor`) registered into `knovas_extract.dispatch.MIME_REGISTRY`; one `SYNCABLE_EXTENSIONS` source of truth for every allow-list; provenance stamped `remote-controller-office`.
- Mailbox mirror via Microsoft Graph (`src/mailbox_mirror/`, `MAILBOX_*`): per-folder delta queries with full-walk fallback, messages as `.eml` with attachments beside them, no cursor advance while downloads fail, no prune on incomplete enumeration.
- PST: `scripts/explode_pst.py` (libpst `readpst` as a separate process) and `src/sync/pst_queue.py` (one archive per cycle, resumable); volumes `RC_PST_INBOX`, `RC_PST_STAGING`.
- OCR: `tesseract-ocr-ita` in the image; default `RC_TESSERACT_LANG=deu+fra+ita+eng`; per-document `ocr_used` and warnings; metrics `knovas_rc_documents_extracted_total{ext,ocr}`, `knovas_rc_extract_errors_total{reason}`; `benchmarks/ocr/` synthetic DE/FR/IT benchmark and on-prem runbook; documented re-queue for `skip:unconvertible`.
- State DB: `content_sha256`, `index_status`, `indexed_at`; lazy polling of `GET /secured/transmissions/<key>/status` so a migration can be verified ("N Dokumente eingereicht, N indexiert").
- Contract: `sync_response.schema.json` gains `rate_limit` and `subfolder_progress`.
- Docs: `docs/connectors.md`, `docs/migration.md`; `docs/configuration.md` formats, OCR languages and metadata keys.
```

- [ ] **Step 5: Verify**

Run:

```bash
head -1 RELEASE_NOTES.md
grep -c "^## " RELEASE_NOTES.md
grep -n "^## " KnovasPlatform/CHANGELOG.md
sed -n '3,16p' RemoteController/CHANGELOG.md | grep -c "^- "
```

Expected: `# v1.1.0 (draft …)`; `5` sections; `## Unreleased` and `## 1.0.0 — 2026-07-30`; `11` bullets under RemoteController Unreleased (4 existing + 7 new).

- [ ] **Step 6: Commit**

```bash
git add RELEASE_NOTES.md KnovasPlatform/CHANGELOG.md RemoteController/CHANGELOG.md
git commit -m "docs(release): v1.1.0 draft release notes with Office add-ins section; create KnovasPlatform CHANGELOG; RemoteController Unreleased entries"
```

When the last phase merges: replace `(draft — …)` in the H1 with nothing, move `## Unreleased` bullets under `## 1.1.0 — <date>` in both changelogs, and tag `git tag -a v1.1.0 -m "Knovas Components v1.1.0" && git push origin v1.1.0`.

---

---

### Task KC-G-8: The design lives in both repositories — commit the copies and add the plans pointer

**Requirements:** process (design §12 item 14: "this design in both"); G9 (engineering intent findable next to the plans)
**Files:**
- Commit (already present, untracked): `E:/Knovas/KnovasComponents/docs/superpowers/specs/2026-08-15-pflichtenheft-d-j-design.md`
- Commit (already present, untracked): `E:/Knovas/KnowledgeBase/docs/superpowers/specs/2026-08-15-pflichtenheft-d-j-design.md`
- Create: `E:/Knovas/KnovasComponents/docs/superpowers/plans/README.md`
- Create: `E:/Knovas/KnowledgeBase/docs/superpowers/plans/README.md`
**Interfaces:**
- Consumes: the plan file names the orchestrator writes: `docs/superpowers/plans/2026-08-15-pflichtenheft-d-j-knowledgebase.md` (KB parts, in KnowledgeBase) and `docs/superpowers/plans/2026-08-15-pflichtenheft-d-j-components.md` (KC parts, in KnovasComponents) — if the assembled plan files carry different suffixes, use those names in the README rows.
- Produces: byte-identical design copies committed in both repositories; a `plans/README.md` in each repo that names the sibling repository's half.

- [ ] **Step 1: Verify the two copies are byte-identical (the copy command, should it ever be needed again)**

Run:

```bash
cmp /e/Knovas/KnowledgeBase/docs/superpowers/specs/2026-08-15-pflichtenheft-d-j-design.md \
    /e/Knovas/KnovasComponents/docs/superpowers/specs/2026-08-15-pflichtenheft-d-j-design.md && echo IDENTICAL
```

Expected: `IDENTICAL`. (Both files exist as untracked files on 2026-08-15. If they ever diverge, the KnovasComponents copy is the one edited by hand and the copy command is `cp /e/Knovas/KnovasComponents/docs/superpowers/specs/2026-08-15-pflichtenheft-d-j-design.md /e/Knovas/KnowledgeBase/docs/superpowers/specs/2026-08-15-pflichtenheft-d-j-design.md`.)

- [ ] **Step 2: Create `docs/superpowers/plans/README.md` in KnovasComponents**

```markdown
## Implementation plans

Task-checkboxed plans derived from the designs in `../specs/`. Each plan opens
with the agentic-worker banner and a `**Spec:**` link; steps use `- [ ]`.

| Plan | Design | Notes |
| --- | --- | --- |
| `2026-07-26-preview-feedback-branding.md` | `../specs/2026-07-26-preview-feedback-branding-design.md` | shipped |
| `2026-07-30-trefferliste.md` | `../specs/2026-07-30-trefferliste-design.md` | shipped |
| `2026-08-04-wissensnetz-ontology-mvp.md` | `../specs/2026-08-04-wissensnetz-ontology-mvp-design.md` | shipped |
| `2026-08-08-cortex-verbindungen-zeichnen.md` | `../specs/2026-08-08-cortex-verbindungen-zeichnen-design.md` | shipped |
| `2026-08-14-matters-and-typed-nodes.md` | `../specs/2026-08-14-matters-and-typed-nodes-design.md` | Part A in KnowledgeBase, Parts B/C here |
| `2026-08-15-pflichtenheft-d-j-components.md` | `../specs/2026-08-15-pflichtenheft-d-j-design.md` | **KnovasComponents half** of the D–J plan (Platform, RemoteController, add-ins, docs). The **KnowledgeBase half** is `KnowledgeBase/docs/superpowers/plans/2026-08-15-pflichtenheft-d-j-knowledgebase.md`. The design file is identical in both repositories — edit here, copy there. |

The section-B identity plan lives on the `feat/section-b-buildout` branch of both
repositories (`2026-08-14-section-b-buildout.md`).
```

- [ ] **Step 3: Create `docs/superpowers/plans/README.md` in KnowledgeBase**

Create `E:/Knovas/KnowledgeBase/docs/superpowers/plans/README.md`:

```markdown
## Implementation plans

Task-checkboxed plans derived from the designs in `../specs/` (and, for
infrastructure work, from the audits in `../audits/`).

Cross-repository plans — the customer-hosted half lives in
`KnovasComponents/docs/superpowers/plans/`:

| Plan (this repository) | Design | Sibling half |
| --- | --- | --- |
| `2026-08-15-pflichtenheft-d-j-knowledgebase.md` | `../specs/2026-08-15-pflichtenheft-d-j-design.md` (identical copy in KnovasComponents; edited there, copied here) | `KnovasComponents/docs/superpowers/plans/2026-08-15-pflichtenheft-d-j-components.md` |
| Part A of `KnovasComponents/docs/superpowers/plans/2026-08-14-matters-and-typed-nodes.md` | `KnovasComponents/docs/superpowers/specs/2026-08-14-matters-and-typed-nodes-design.md` | Parts B/C in KnovasComponents |

Everything else in this folder is single-repository.
```

- [ ] **Step 4: Commit in KnovasComponents**

```bash
cd /e/Knovas/KnovasComponents
git add docs/superpowers/specs/2026-08-15-pflichtenheft-d-j-design.md docs/superpowers/plans/README.md
git commit -m "Design: Pflichtenheft D–H, J — integration, product surface, declarations (copy shared with KnowledgeBase)"
```

- [ ] **Step 5: Commit in KnowledgeBase**

```bash
cd /e/Knovas/KnowledgeBase
git add docs/superpowers/specs/2026-08-15-pflichtenheft-d-j-design.md docs/superpowers/plans/README.md
git commit -m "docs(design): add Pflichtenheft D–H, J design (identical copy of KnovasComponents/docs/superpowers/specs/2026-08-15-pflichtenheft-d-j-design.md) and plans index"
```

- [ ] **Step 6: Verify both trees are clean for these paths**

Run: `cd /e/Knovas/KnowledgeBase && git status --short docs/superpowers; cd /e/Knovas/KnovasComponents && git status --short docs/superpowers`
Expected: no output from either.

---

---

### Task KC-G-9: `docs/certificates.md` — who needs the bundle (add-ins and connectors do not) and the symlink sentence

**Requirements:** H2, F2 (E5 feed, mailbox mirror), G9 (an honest "not needed")
**Files:**
- Modify: `docs/certificates.md:1` (title), `:6-9` (the "do not symlink" sentence), new section after "Per-component placement" (after line 43)
**Interfaces:**
- Consumes: `scripts/setup.sh:30-38` (the unified installer's Platform symlinks inside one `certs/` directory); the add-in architecture (taskpane on the Platform origin, `POST /api/filing/email` on the Platform — design §6.11); the mailbox mirror inside RemoteController (design §7.2); the ICS feed served by the Platform (design §6.8); Zefix called by the Platform (design §6.7).
- Produces: the paragraph the release notes prerequisites (KC-G-7) and `office-add-ins.md` (add-in part) point at: `docs/certificates.md#who-needs-the-bundle`.

- [ ] **Step 1: Record the state**

Run: `sed -n '1p;6,9p' docs/certificates.md; grep -n "add-in\|Add-in\|mailbox\|Zefix" docs/certificates.md || echo "no statement about add-ins or connectors"`
Expected: title `# mTLS certificates — one bundle, three components`; the sentence `Copy the bundle to each component that needs it — do not symlink between them …`; `no statement about add-ins or connectors`.

- [ ] **Step 2: Fix the title and the symlink sentence (lines 1 and 6-9)**

Replace line 1 with:

```markdown
## mTLS certificates — one bundle, two components that hold it
```

Replace lines 6-9 with:

```markdown
Each component expects those files under a **different name, in a different
directory**. That is the single most common setup failure, so this page is the
source of truth. Copy the bundle to each component that needs it and do not
assume one component's paths work in another. (The unified installer
`scripts/setup.sh` keeps a single `certs/` directory and links the Platform
spellings `client.crt`, `client.key`, `ca.crt` to the RemoteController files
inside that one directory — that is fine; hand-made links *between* the two
component directories are not.)
```

- [ ] **Step 3: Add the section "Who needs the bundle" after "Per-component placement" (after line 43)**

Insert:

```markdown
## Who needs the bundle

Only the two components that talk to the Knovas API directly:

| Component | Holds the tenant bundle? | Why |
|---|---|---|
| RemoteController | **Yes** | Uploads documents over mTLS. The **mailbox mirror** and the **PST importer** run *inside* RemoteController and reuse its certificate — they need nothing of their own; their credentials are Microsoft Entra (Graph), not Knovas |
| KnovasPlatform | **Yes** | Search, graph, events, export over mTLS. The **ICS deadline feed**, the **Zefix enrichment** and the **add-in filing endpoint** are Platform routes and reuse its certificate |
| Office add-ins (Outlook, Word) | **No** | The taskpane is a web page served by the Platform at `https://<fqdn>/addins/*` and calls Platform routes (`/api/search`, `/api/filing/email`, …) with the Platform session cookie. It never contacts the Knovas API and never sees the bundle. What it needs is the Platform's **TLS server certificate** to be trusted by Office on the client PC — a different certificate, issued by your CA, see `hosting-requirements.md` |
| Employee browsers | **No** | Same as the add-ins: Platform TLS only |

If a fourth component ever needs the bundle, add a column to the filename
table above in the same commit that introduces it.
```

- [ ] **Step 4: Verify**

Run: `grep -n "^## Who needs the bundle\|two components that hold it\|hand-made links" docs/certificates.md`
Expected: three matches.

- [ ] **Step 5: Commit**

```bash
git add docs/certificates.md
git commit -m "docs(certs): state that add-ins and connectors need no tenant bundle; reconcile the symlink sentence with scripts/setup.sh"
```

---

**Part KC-G is complete** when: `docs/product-statements.md` exists and is linked from `README.md`, `docs/README.md`, `specifications.md` §7 and `hosting-requirements.md`; `python scripts/check_devkit_mirror.py --kit-dir ../KnowledgeBase/docs/Knovas_Developer_Kit/api` exits 0 on the branch; `python -m pytest scripts/tests -q` passes; the CI workflow lists the `devkit-mirror` job; the design is committed in both repositories with a `plans/README.md` beside it; and the link check of KC-G-2 Step 3 reports only documents owned by other parts of this plan.

---

---

## Appendix: tasks specified but not yet expanded

These task IDs are part of the plan's structure (Part Overview, traceability table, neighbouring **Interfaces** blocks) but their step-by-step bodies are **not yet written**. Each line is the task's brief — precise enough to expand, not licence to improvise. Expand one to the same standard as the tasks above (failing test → run → minimal implementation → run → commit, real code in every step) before starting it.

**KC-A (search UI — KC-A-1, 6, 7, 8 are written)**

| ID | Scope |
| --- | --- |
| KC-A-2 | `src/search_filters.py`: split the UI filter payload into "forward to the API" (`filters`, `sort`, `limit`, `offset`, `facets`, `scope`) and "apply locally" (`exact_match` and the other refinements `_apply_search_refinement` already implements); an allowed-filter list under `web.search.filters` in `config.yaml`; rework `app.py::search()` so UI-only keys are never forwarded (today every unknown key goes to `/secured/query` and logs a warning on every search) and so an API `400 validation_error` is surfaced with its `field`. Tests over the route contract. |
| KC-A-3 | Filter rail in `index.html` + `static/js/filters.js`: Akte (matter picker → `scope.node_ids` via the section-C `/api/graph/nodes?node_type_id=<Mandat>`), Praxisgebiet (matters whose `semantic_role=practice_area` fact matches → `scope`), Dokumenttyp, Autor, Zeitraum, Sprache, Status, Quelle, sort selector, and a real "Weitere Treffer" using `offset` instead of re-running the query with a doubled limit. `app.js` sends the typed payload; facet chips render from the response. |
| KC-A-4 | Hit card and honesty: metaline `Typ · Datum · Autor · Sprache` plus a version badge; `auto_summary` chunk hits labelled "KI-Zusammenfassung"; the empty state renders `no_strong_matches` and `semantix.status` instead of ignoring them; a persistent "Beispieldaten" banner whenever `SEARCH_USE_TEST_RESULTS` is on. |
| KC-A-5 | "Wer kennt sich aus?" (D5): a rail action that re-runs the current query with `facets=["author"]` and renders the author facet as a ranked list, each entry linking to the query filtered to that author. |

**KC-B (Parteien, Zefix, Konfliktprüfung — KC-B-1 is written)**

| ID | Scope |
| --- | --- |
| KC-B-2 | `src/web_interface/parties_routes.py` blueprint: `/parteien` page and `/api/parties`, `/api/parties/search`, `/api/parties/<id>`, `/api/parties/duplicates`, `/api/parties/merge`; party node types from `web.graph.party_node_types`; identifier editor with kinds; Dubletten queue with a merge sheet stating "Quelle bleibt als Verweis erhalten"; merge is a section-B guarded action (`ApprovalService` kind `party_merge`, admin bypass recorded, `audit.record` on execute). Templates + `static/js/parties.js` + tests incl. CSRF and the approval branch. |
| KC-B-3 | `src/zefix_client.py` + `/api/zefix/lookup`: Zefix public REST from the **customer's** network (`ZEFIX_USERNAME`/`ZEFIX_PASSWORD`, disabled when absent, timeouts, no retry on 4xx); "Aus Zefix übernehmen" uploads a generated `Zefix-Auszug <UID> <Datum>` document through the Platform upload path with `metadata` and `graph_assign`, then creates UID / Sitz / Rechtsform / Status facts with that document's chunk as evidence. State in the UI and the docs that signatories and group structure are **not** available from the cantonal extract. |
| KC-B-4 | `src/web_interface/conflicts_routes.py`: `/konfliktpruefung` form (names + role + context), `POST /api/conflict-checks` with `actor_ref` = the section-B user id, result page grouped by Parteien / Akten / Dokumente with `withheld_count` and `degraded` as prominent callouts, decision form, history list, and a printable `templates/conflict_protocol.html` (print CSS; check id, actor, time, queries, hits, decision, result hash). |
| KC-B-5 | Lateral-hire import (D4): `POST /api/conflict-checks/import` accepting CSV/XLSX (`openpyxl`) with columns client / counterparty / matter / period → validation → one check per row under a bundle context `lateral:<uuid>` → summary table with per-row status and protocol links. Sample fixtures in the tests. |
| KC-B-6 | Sidebar entries and `active_nav` values for both screens; fixture-mode state ("Wissensnetz-Modus erforderlich") wherever the graph is required. |
| KC-B-7 | Docs: `KnovasPlatform/docs/features/matters-and-parties.md` (register, identifier kinds, merge semantics, Zefix scope) and `conflicts-check.md` (workflow, what the evidentiary record contains, the wall policy = counted withheld hits, `degraded`, the protocol, the D4 import format); rows in `KnovasPlatform/docs/integration/graph-api.md`; RELEASE_NOTES lines. |

**KC-C (Fristen, Posteingang — KC-C-1 and KC-C-2a are written)**

| ID | Scope |
| --- | --- |
| KC-C-2b | Finish the deadlines routes: the three tabs (Vorschläge / Zur Bestätigung / Bestätigt) wired to `facts_list`, `fact_adopt`, confirm and reject; the confirm button disabled server-side for the user who entered the fact (compare the ledger's last human actor from the fact history); permanent-rejection copy; the per-matter widget include for the section-C `matter.html`. |
| KC-C-3 | `src/ics_feed.py` + `GET /feeds/deadlines.ics?token=`: RFC 5545 output, one VEVENT per confirmed deadline (`DTSTART` honouring `precision` — a month-precision fact is never drawn on a specific day), `ORGANIZER`/`ATTENDEE` from the matter's `responsible`/`deputy` entity_ref facts (Person nodes with an `email` identifier), `VALARM` −P7D and −P1D, `UID` = fact id, `X-KNOVAS-FACT-ID`; `feed_tokens` table (`0005_feed_tokens.sql`) with create/revoke in settings; golden-text test. |
| KC-C-4 | `src/events_poller.py`: leader election via a `platform-db` advisory lock, `events_poll(after=cursor)` every `EVENTS_POLL_SECONDS` (15), rows into `events` + `event_cursor` (`0002_events.sql`), started from `create_app` when `EVENTS_POLL_ENABLED`; safe under two gunicorn workers; tests with a fake client. |
| KC-C-5 | `src/web_interface/inbox_routes.py`: `/posteingang` grouped by kind (sort proposals, deadline proposals, pending confirmations, contradictions, job completions, conflict checks) with deep links, mark-read, and `/api/inbox/unread-count` for the sidebar badge. |
| KC-C-6 | Ingestion/upload screens poll `transmission_status` for pending keys and show indexed/failed; `Ereignisprotokoll` CSV export at `/api/inbox/export`. |
| KC-C-7 | Overdue escalation banner driven by `graph.fact.confirmation_overdue` events. |
| KC-C-8 | Docs: `features/deadlines.md` (E1 cross-link, the proposal → adopt → confirm chain, four-eyes semantics incl. `actor_kind` honesty, Outlook subscription steps, what a PMS integrator consumes instead), `features/reports-and-inbox.md` (inbox half), `integration/events.md`, `.env.example` keys, RELEASE_NOTES lines. |

**KC-D (Cortex live — KC-D-1..3 are written)**

| ID | Scope |
| --- | --- |
| KC-D-4 | G3 "Warum?" drawer in graph mode: facts with tier chips, evidence rows (pointer, page, quote) opening the viewer through the shared `openEvidence(...)` helper from KC-A-7; reachable from the search Trefferliste for a hit's assigned matter. |
| KC-D-5 | G4 `templates/_trust_chip.html` macro (German tier label, scope tag "firmenweit" / "Ihre Sicht", signals popover: independent sources, supporting links, contradiction pressure, curation status, validity elapsed) + CSS, reused by chronology, dossier, facts and the why-panel. |
| KC-D-6 | G5 `src/web_interface/reports_routes.py`: `/berichte` rendering contradictions and completeness with a node-type filter, paging, deep links to node/fact/evidence, and a CSV export. |
| KC-D-7 | G6 `/import` wizard: CSV upload → column mapping (matter number, client, counterparties, responsible lawyer, practice area, status, opened date) → build the `POST /secured/graph/imports` payload (identifiers with kinds, facts by `semantic_role`, Person nodes with email identifiers) → dry-run diff → apply → progress via `graph_job`; cross-link to the section-C file-structure bootstrap. |
| KC-D-8 | G7 `GraphOntologySource.create_type_relation` implemented on `target_node_type_id` (section-C A4/A5/B4) and `summary()` returning declared relations with `count: 0` so a dashed Vorgabe survives a reload; enable the type→type path in `ontology_connect.js` in graph mode. |
| KC-D-9 | G8 `GraphFilterEngine` wired to `filters/evaluate|apply|placements|reject|restore` in graph mode; `503 filter_embedding_model_stale` / `relevance_calibration_missing` rendered as "kann gerade nicht bewerten — bitte später" (never as "keine Treffer"); apply progress via `graph_job`; replace `_locate`'s scan over every node with the server-side node filters. |
| KC-D-10 | Docs: `features/import-and-bootstrap.md`, the reports half of `features/reports-and-inbox.md`, the ego section of `features/matters-and-parties.md`, a "Cortex live vs Demo" section in `KnovasPlatform/docs/README.md`, `docs/specifications.md` §2.5 (`ONTOLOGY_*`) and §2.3 (`/secured/graph/*`). |

**KC-E (add-ins and journal — KC-E-1 is written)**

| ID | Scope |
| --- | --- |
| KC-E-2 | `src/web_interface/filing_routes.py`: `POST /api/filing/email` (`{mime_base64|msg_base64, node_id, include_attachments}`, session auth + CSRF, 25 MB body limit with the matching nginx `client_max_body_size` and gunicorn timeout notes, `audit.record`) and `POST /api/filing/suggest` (`{from, to, subject}` → `identifiers_search` → ranked matters, recent matters from the journal when available). |
| KC-E-3 | `KnovasPlatform/components/knovas_office_addins/`: `manifest.outlook.xml` (Mailbox 1.8, `ReadWriteMailbox`, ribbon button "In Knovas ablegen"), `manifest.word.xml`, `taskpane/` (`index.html`, `common.js` login + CSRF, `outlook.js` — MIME via `makeEwsRequestAsync` `GetItem` `IncludeMimeContent` → `POST /api/filing/email`, matter picker with suggestions, toast; `word.js` — search over `/api/search`, "Öffnen" via `client-path` UNC or the companion token, "Zitat einfügen" via `setSelectedDataAsync`), Knovas design tokens in `styles.css`. |
| KC-E-4 | `src/web_interface/addins_routes.py` serving `/addins/*` over the Platform origin with cache headers and a CSP `frame-ancestors` allowing `outlook.office.com`, `office.live.com`, `*.officeapps.live.com` and localhost for development; a manifest well-formedness test (`xml.etree` parse + required elements) and route tests. |
| KC-E-5 | `src/journal.py`: `record(kind, *, user_id, matter_node_id, pointer, page, format, query_hash)` into `activity_journal` (`0003_journal.sql`), opt-in per user in `settings`, retention purge (`JOURNAL_RETENTION_DAYS`, default 90), `day_view(user_id, day)` splitting blocks on gaps > 20 minutes, `csv_export(user_id, from, to)`; hooks in `app.py::search()`, the document-open routes, the matter page and the viewer. |
| KC-E-6 | `src/web_interface/journal_routes.py`: `/mein-tag`, `/api/journal/day`, `/api/journal/export.csv`, `/api/journal/settings`; a user sees only their own rows and admins have no per-person view (works-council-friendly by construction); `/api/journal/format-stats` returns aggregate open-counts by format, which is the measurement the search backlog's pdf.js precondition asked for. |
| KC-E-7 | Docs: `features/activity-journal.md` (consent text, what is recorded and what is not, retention, export columns, PMS import hint) and the `integration/office-add-ins.md` page (architecture + sequence diagram, hosting on the Platform origin over HTTPS, permissions, central deployment vs sideload, on-prem Exchange note, troubleshooting); rows in `KnovasPlatform/components/README.md`, `docs/specifications.md` §2.8, `hosting-requirements.md`; RELEASE_NOTES section. |

**KC-F (RemoteController — KC-F-1 and KC-F-2 are written)**

| ID | Scope |
| --- | --- |
| KC-F-3 | One source of truth for `SYNCABLE_EXTENSIONS` (`document_text.py:49`) from which `DEFAULT_INCLUDE_GLOBS`, `default_sync_body.py`, the OneDrive `DEFAULT_ALLOWED_EXTENSIONS` and the `sync_request.schema.json` description derive — today the list exists in five places and a partial edit silently half-enables a format. |
| KC-F-4 | `src/sync/office_extractors.py`: `XlsxExtractor` (openpyxl `read_only`, `data_only`; one `Table` per worksheet block, ≤ 64 cols / 5 000 rows, ragged rows padded before `map_extractor_tables` drops them, hidden sheets skipped, sheet name as `title`, `client_table_hint = xlsx_s{i}_t{j}`, plus a flattened text rendering) and `PptxExtractor` (python-pptx; one page per slide, slide title as a section, notes included), registered into `knovas_extract.dispatch.MIME_REGISTRY` at RC import — the documented public hook — with provenance recorded as `remote-controller-office` so nothing is misattributed to the certified extractor. Upstreaming to `knovas-extract` is the named follow-up. |
| KC-F-5 | OCR: `tesseract-ocr-ita` in the Dockerfile, default `RC_TESSERACT_LANG=deu+fra+ita+eng`, `result.warnings` and an `ocr_used` flag kept on `ExtractedDocument`, Prometheus `knovas_rc_documents_extracted_total{ext,ocr}` and `knovas_rc_extract_errors_total{reason}` in `routes/metrics.py`, and `scripts/requeue_skipped.py` for rows parked as `skip:unconvertible` (enabling Italian later does not re-ingest them by itself). |
| KC-F-6 | `benchmarks/ocr/`: `build_corpus.py` renders ground-truth DE/FR/IT legal paragraphs to page images at 200/300 dpi with skew and noise (Pillow) → PDF; `run_ocr_benchmark.py` runs `knovas_extract.extract(use_ocr=True, ocr_language=…)` and reports CER/WER per language and dpi into `results/<ts>/{metrics.json,report.md}`; a README with the on-premise "Nachweis auf eigenen Scans" runbook, because real court scans cannot be published. |
| KC-F-7 | `src/mailbox_mirror/`: `graph_mail.py` (client-credentials auth reusing `onedrive_mirror/graph.py`; `mailFolders`, `messages/delta`, `messages/{id}/$value`, `attachments`), `mirror.py` (mailbox allow-list, folder include/exclude, per-folder delta with full-walk fallback, each message materialised as `.eml` under `<MAILBOX_MIRROR_PATH>/<upn>/<folder>/<sha1(internetMessageId)>.eml` with mtime pinned to `receivedDateTime`, attachments beside it as `<key>.att/<name>`, and the two OneDrive invariants copied verbatim: no cursor advance while downloads fail, no prune on incomplete enumeration), `runner.py`, `MAILBOX_*` env gating so a missing config never fails boot. |
| KC-F-8 | PST: `scripts/explode_pst.py` (`readpst -e -j N -o <staging>`, folder hierarchy preserved, `Message-ID` captured, idempotent, timeout) + `src/sync/pst_queue.py` (one PST per cycle from `RC_PST_INBOX`, resumable, state rows), `pst-utils` in the image, writable `RC_PST_INBOX`/`RC_PST_STAGING` volumes in `docker-compose.yml` and SETUP.md (today `./data` is mounted read-only), tests with a fake `readpst`. |
| KC-F-9 | State DB `content_sha256` + `index_status`/`indexed_at` (additive-migration idiom from `subfolder_queue.py:67-71`); skip or alias an upload whose content hash already exists under another path (a prerequisite for mailbox and PST, where one message appears in several folders); lazy polling of `GET /secured/transmissions/<key>/status` so `/sync/status` can report "N eingereicht, N indexiert"; `sync_response.schema.json` gains `rate_limit` and `subfolder_progress` (both already computed and discarded) and `_build_sync_response` serialises them. |
| KC-F-10 | Docs: `RemoteController/docs/connectors.md` (OneDrive, mailbox, PST, XLSX/PPTX, metadata rules — the OneDrive connector has no prose documentation at all today), `migration.md` (inventory, PST step, throughput settings against the API ceiling, dedup expectations, verification through index status, rollback, the fixed-price rule of thumb), `configuration.md` (format table, OCR languages, metadata env keys, `RC_MATTER_PATH_RULE`), SETUP volumes, CHANGELOG `Unreleased`, `docs/hosting-requirements.md` options C/D + Graph egress, `docs/specifications.md` §1.3/§1.6. |

## Verification

After all parts, on a Platform pointed at the dev tenant with `ONTOLOGY_SOURCE=graph` and section B enabled:

```bash
cd KnovasPlatform/components/docbridge_integration && python -m pytest
python -m pytest tests/test_graph_contract_live.py --knovas-api        # cassette refresh against dev
cd ../../../RemoteController && python -m pytest
python -m benchmarks.ocr.run_ocr_benchmark --dpi 200,300 --languages de,fr,it
```

Then walk the product path once by hand: search with the filter rail → open a hit in the viewer at its page with the snippet highlighted → open the document's versions and similar documents → open *Parteien*, search "Mueller", merge a duplicate → run a *Konfliktprüfung*, print the protocol → open *Fristen*, adopt an extracted deadline as user A, try to confirm as A (disabled), confirm as B, subscribe the ICS feed in Outlook → open *Posteingang* and see the day's events → open a matter's *Akten-Kompass* → open *Berichte* → run the CSV import wizard in dry-run → file an email to a matter from Outlook → read *Mein Tag* → export the journal CSV → in RemoteController, drop a PST into the inbox and watch `/sync/status` report indexed counts.

## Requirement traceability

| Requirement | Tasks |
| --- | --- |
| F3 · filters + pagination (UI half) | KC-A-1..KC-A-4, KC-A-8; RC metadata KC-F-1 |
| F9 · honest empty results (UI) | KC-A-4 |
| D5 · expertise location | KC-A-5; KC-F-1 |
| F6 · version history (UI) | KC-A-6 |
| F8 · similar documents / matters | KC-A-6 (documents), KC-D-3 (matters via ego + `kg_node_ids`) |
| H4 · tables (UI + XLSX) | KC-A-6, KC-F-4 |
| F7 · jump to the hit | KC-A-7 |
| D1 · party register + dedup | KC-B-1, KC-B-2, KC-B-6, KC-B-7 |
| D3 · Zefix/UID enrichment | KC-B-3 |
| D2 · conflicts check as evidence | KC-B-4, KC-B-7 |
| D4 · lateral-hire import | KC-B-5 |
| E3 · four-eyes (UI) | KC-C-1, KC-C-2, KC-C-8 |
| E4 · proposal inbox | KC-C-2, KC-C-5 |
| E5 · deadlines in Outlook with substitutes | KC-C-3, KC-C-8 |
| E6 · eventing consumer (Posteingang, job status) | KC-C-4..KC-C-7 |
| G1 · Cortex on the live graph | KC-D-2 |
| G2 · matter ego graph | KC-D-1, KC-D-3 |
| G3 · every node answers "why?" | KC-D-4 |
| G4 · trust made visible | KC-D-5 |
| G5 · partner's Monday report | KC-D-6 |
| G6 · non-empty graph (import wizard + bootstrap) | KC-D-7 (+ C-plan C11) |
| G7 · draw on the map (Vorgaben live) | KC-D-8 |
| G8 · tireless junior (filters live) | KC-D-9 |
| G9 · honesty labels | KC-G-1, KC-D-2 (badges) |
| H2 · Outlook and Word add-ins | KC-E-1..KC-E-4 |
| J2 · activity hints | KC-E-5, KC-E-6, KC-E-7 |
| J3 · realization reporting (substrate + statement) | KC-E-6, KC-G-1 |
| F1 · OCR accuracy evidence | KC-F-5, KC-F-6 |
| F2 · whole estate (mailbox, XLSX/PPTX, PST) | KC-F-3, KC-F-4, KC-F-7, KC-F-8 |
| H1 · migration incl. PST | KC-F-8, KC-F-9, KC-F-10 |
| F5 · language at ingest | KC-F-1 |
| E1/E2 · deadline strategy declared | KC-G-1 |
| H6 · Justitia 4.0 | KC-G-1 |
| J1/J4 · time capture / invoicing declared | KC-G-1 |
| F4 · throughput statement in customer docs | KC-G-1, KC-G-5 |
| H5 · exit doc + export UI pointers | KC-G-3 (mirror `Export_and_Exit.md`) |
