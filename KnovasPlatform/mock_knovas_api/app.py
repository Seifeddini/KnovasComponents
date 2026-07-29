from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List
from uuid import uuid4

from flask import Flask, jsonify, request

app = Flask(__name__)

DOCUMENTS: List[Dict[str, Any]] = [
    {
        "doc_id": "demo-001",
        "title": "Lease Agreement - ACME GmbH",
        "path": "contracts/lease_acme.pdf",
        "type": "contract",
        "snippet": "This lease agreement starts on 2026-01-01 and includes renewal options.",
        "timestamp": "2026-01-01T09:00:00Z",
    },
    {
        "doc_id": "demo-002",
        "title": "Employment Contract - Jane Doe",
        "path": "hr/employment_jane_doe.docx",
        "type": "employment",
        "snippet": "The probation period is 6 months with full benefits.",
        "timestamp": "2026-02-10T11:30:00Z",
    },
    {
        "doc_id": "demo-003",
        "title": "Case Notes - Matter 42",
        "path": "cases/matter_42_notes.txt",
        "type": "case_note",
        "snippet": "Initial hearing is scheduled for April with supporting evidence attached.",
        "timestamp": "2026-03-01T14:15:00Z",
    },
]


@app.get("/health")
def health() -> Any:
    return jsonify({"status": "healthy", "mock": True, "timestamp": datetime.now(timezone.utc).isoformat()})


@app.get("/api/search")
def search() -> Any:
    query = (request.args.get("query") or "").strip().lower()
    limit_raw = request.args.get("limit", "20")
    try:
        limit = max(1, int(limit_raw))
    except ValueError:
        limit = 20

    if not query:
        results = DOCUMENTS[:limit]
    else:
        results = [
            doc
            for doc in DOCUMENTS
            if query in (doc.get("title", "").lower() + " " + doc.get("snippet", "").lower())
        ][:limit]

    return jsonify({"success": True, "results": results, "total": len(results), "mock": True})


@app.post("/api/docs/full-sync")
def full_sync() -> Any:
    payload = request.get_json(silent=True) or {}
    documents = payload.get("documents", [])
    accepted = len(documents) if isinstance(documents, list) else 0
    return jsonify(
        {
            "success": True,
            "mock": True,
            "accepted": accepted,
            "sync_id": f"sync-{uuid4()}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    )


@app.post("/api/docs/new")
def new_doc() -> Any:
    payload = request.get_json(silent=True) or {}
    doc_id = payload.get("doc_id", f"new-{uuid4()}")
    return jsonify(
        {
            "success": True,
            "mock": True,
            "doc_id": doc_id,
            "indexed": True,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    )


_STORED_POINTERS: set[str] = set()
_ENGAGEMENT_COUNT = 0


@app.get("/secured/health")
def secured_health() -> Any:
    return jsonify({"status": "success", "message": "healthy", "mock": True})


@app.post("/secured/query")
def secured_query() -> Any:
    payload = request.get_json(silent=True) or {}
    query_input = payload.get("Input")
    queries: List[str] = []
    if isinstance(query_input, list):
        queries = [str(q) for q in query_input if str(q).strip()]
    elif query_input:
        queries = [str(query_input)]

    results = []
    for doc in DOCUMENTS:
        hay = (doc.get("title", "") + " " + doc.get("snippet", "")).lower()
        if not queries or any(q.lower() in hay for q in queries):
            results.append(
                {
                    "pointer": doc["doc_id"],
                    "document_uuid": str(uuid4()),
                    "final_score": 0.9,
                    "cosine_similarity": 0.88,
                    "cosine_distance": 0.12,
                    "ingested_summary": {"present": True, "text": doc.get("snippet", "")},
                    "page_number": 1,
                    "sentence_number": 1,
                    "top_chunks": [],
                }
            )

    return jsonify(
        {
            "status": "success",
            "message": "Query executed successfully",
            "query_session_id": str(uuid4()),
            "pointers": [r["pointer"] for r in results],
            "result_count": len(results),
            "results": results,
            "meta": {"embed_latency_ms": 1, "stage1_latency_ms": 1, "stage2_latency_ms": 1},
            "mock": True,
        }
    )


@app.post("/secured/init_document_transmission")
def secured_init() -> Any:
    payload = request.get_json(silent=True) or {}
    key = str(uuid4())
    pointer = payload.get("identifier")
    if pointer:
        _STORED_POINTERS.add(str(pointer))
    return jsonify(
        {
            "status": "success",
            "message": "Transmission initialized",
            "transmission_key_id": key,
            "mock": True,
        }
    ), 201


@app.post("/secured/transmit_document_part")
def secured_transmit() -> Any:
    payload = request.get_json(silent=True) or {}
    part_count = int(payload.get("part_number", 0))
    complete = part_count >= 0
    return jsonify(
        {
            "status": "success",
            "message": "Success",
            "transmission_complete": complete,
            "mock": True,
        }
    )


@app.delete("/secured/delete_information_object")
def secured_delete() -> Any:
    payload = request.get_json(silent=True) or {}
    pointer = str(payload.get("pointer") or "")
    if pointer not in _STORED_POINTERS:
        return jsonify({"status": "error", "message": "not found"}), 404
    _STORED_POINTERS.discard(pointer)
    return jsonify(
        {
            "status": "success",
            "message": "deleted",
            "document_uuid": str(uuid4()),
            "deleted_sentences": 1,
            "deleted_versions": 1,
            "mock": True,
        }
    )


@app.post("/secured/analytics/engagement")
def secured_engagement() -> Any:
    global _ENGAGEMENT_COUNT
    payload = request.get_json(silent=True) or {}
    events = payload.get("events") or []
    if not payload.get("query_session_id") or not events:
        return jsonify({"status": "error", "message": "bad request"}), 400
    accepted = len(events)
    _ENGAGEMENT_COUNT += accepted
    return jsonify(
        {
            "status": "success",
            "message": "Engagement events accepted",
            "accepted": accepted,
            "mock": True,
        }
    ), 202


@app.post("/secured/sign_certificate")
def secured_sign_certificate() -> Any:
    payload = request.get_json(silent=True) or {}
    csr = payload.get("csr") or ""
    if "BEGIN CERTIFICATE REQUEST" not in str(csr):
        return jsonify({"status": "error", "message": "invalid csr"}), 400
    return jsonify(
        {
            "status": "success",
            "message": "Certificate created successfully",
            "certificate": "-----BEGIN CERTIFICATE-----\nMOCK\n-----END CERTIFICATE-----\n",
            "certificate_chain": "-----BEGIN CERTIFICATE-----\nMOCK-CA\n-----END CERTIFICATE-----\n",
            "serial_number": "123",
            "expires_at": datetime.now(timezone.utc).isoformat(),
            "validity_days": payload.get("validity_days", 365),
            "mock": True,
        }
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
