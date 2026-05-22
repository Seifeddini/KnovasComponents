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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
