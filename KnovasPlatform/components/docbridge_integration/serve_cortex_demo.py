"""Local Cortex page fed by a snapshot of the live Knovas ontology API.

The snapshot uses the same JSON shapes as ``/api/ontology/*``:

- summary: ``{success, types: [{id, label, count}], relations: [{src, predicate, dst, count}]}``
- entities: ``{success, entities: [{id, label, type, doc_count}]}``
- detail: ``{success, entity: {id, label, type, description, doc_count},
            relations: [{predicate, direction, target: {id, label, type}}],
            evidence, filters, doc_field_links}``
"""
from __future__ import annotations

import json
import re
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "src" / "web_interface" / "static"
TEMPLATE = ROOT / "src" / "web_interface" / "templates" / "ontology.html"
SNAPSHOT = ROOT / "demo_data" / "knovas_ontology.json"

EMPTY: dict[str, Any] = {"summary": {"success": True, "types": [], "relations": []},
                         "entities": {}, "details": {}}


def load_snapshot() -> dict[str, Any]:
    if not SNAPSHOT.is_file():
        return json.loads(json.dumps(EMPTY))
    data = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    data.setdefault("summary", EMPTY["summary"])
    data.setdefault("entities", {})
    data.setdefault("details", {})
    return data


def save_snapshot(data: dict[str, Any]) -> None:
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


SNAPSHOT_DATA = load_snapshot()


def render_page() -> bytes:
    html = TEMPLATE.read_text(encoding="utf-8")
    html = re.sub(
        r"\{\{\s*url_for\('static',\s*filename='([^']+)'\)\s*\}\}",
        r"/static/\1",
        html,
    )
    html = html.replace("{{ csrf_token }}", "demo")
    html = html.replace("{{ brand }}", "Knovas")
    html = html.replace("{{ app_title }}", "Knovas")
    html = html.replace("{{ company_name[0] | upper }}", "K")
    html = html.replace("{{ company_name }}", "Knovas")
    html = html.replace("?v={{ asset_version }}", "?v=dev")
    html = re.sub(r"\{%-?\s*(if|elif|else|endif|for|endfor).*?-?%\}", "", html)
    html = re.sub(r"\{\{[^}]+\}\}", "", html)
    return html.encode("utf-8")


def entity_by_id(entity_id: str) -> dict[str, Any] | None:
    for rows in SNAPSHOT_DATA["entities"].values():
        for row in rows:
            if row.get("id") == entity_id:
                return row
    detail = SNAPSHOT_DATA["details"].get(entity_id)
    if detail and isinstance(detail.get("entity"), dict):
        return detail["entity"]
    return None


def detail_payload(entity_id: str) -> dict[str, Any] | None:
    stored = SNAPSHOT_DATA["details"].get(entity_id)
    if isinstance(stored, dict) and isinstance(stored.get("entity"), dict):
        entity = stored["entity"]
        return {
            "success": True,
            "entity": {
                "id": entity.get("id") or entity_id,
                "label": entity.get("label") or "",
                "type": entity.get("type") or "",
                "description": entity.get("description") or "",
                "doc_count": entity.get("doc_count") or 0,
            },
            "relations": stored.get("relations") or [],
            "evidence": stored.get("evidence") or [],
            "filters": stored.get("filters") or [],
            "doc_field_links": stored.get("doc_field_links") or [],
        }
    row = entity_by_id(entity_id)
    if row is None:
        return None
    return {
        "success": True,
        "entity": {
            "id": row.get("id") or entity_id,
            "label": row.get("label") or "",
            "type": row.get("type") or "",
            "description": row.get("description") or "",
            "doc_count": row.get("doc_count") or 0,
        },
        "relations": [],
        "evidence": [],
        "filters": [],
        "doc_field_links": [],
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _cors(self) -> None:
        origin = self.headers.get("Origin") or "*"
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Vary", "Origin")

    def _json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path != "/api/ontology/import":
            self._json({"success": False, "error": "Nicht gefunden"}, 404)
            return
        length = int(self.headers.get("Content-Length") or "0")
        raw = self.rfile.read(length)
        try:
            incoming = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            self._json({"success": False, "error": "Kein JSON"}, 400)
            return
        if isinstance(incoming.get("summary"), dict):
            summary = incoming["summary"]
            SNAPSHOT_DATA["summary"] = {
                "success": True,
                "types": summary.get("types") or [],
                "relations": summary.get("relations") or [],
            }
        if isinstance(incoming.get("entities"), dict):
            SNAPSHOT_DATA["entities"].update(incoming["entities"])
        if isinstance(incoming.get("details"), dict):
            SNAPSHOT_DATA["details"].update(incoming["details"])
        save_snapshot(SNAPSHOT_DATA)
        types = SNAPSHOT_DATA["summary"].get("types") or []
        self._json({
            "success": True,
            "types": len(types),
            "relations": len(SNAPSHOT_DATA["summary"].get("relations") or []),
            "entities": sum(len(rows) for rows in SNAPSHOT_DATA["entities"].values()),
            "details": len(SNAPSHOT_DATA["details"]),
        })

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path in ("/", "/ontology"):
            body = render_page()
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/ontology/summary":
            summary = SNAPSHOT_DATA["summary"]
            self._json({
                "success": True,
                "types": summary.get("types") or [],
                "relations": summary.get("relations") or [],
            })
            return
        if path == "/api/ontology/entities":
            type_id = (parse_qs(parsed.query).get("type") or [""])[0]
            rows = SNAPSHOT_DATA["entities"].get(type_id, [])
            self._json({"success": True, "entities": rows})
            return
        prefix = "/api/ontology/entities/"
        if path.startswith(prefix):
            entity_id = path[len(prefix):]
            payload = detail_payload(entity_id)
            if payload is None:
                self._json({"success": False, "error": "Entität nicht gefunden"}, 404)
                return
            self._json(payload)
            return
        if path.startswith("/static/"):
            file_path = (STATIC / path[len("/static/"):]).resolve()
            if (not str(file_path).startswith(str(STATIC.resolve()))
                    or not file_path.is_file()):
                self.send_error(404)
                return
            data = file_path.read_bytes()
            kind = "application/octet-stream"
            if file_path.suffix == ".css":
                kind = "text/css; charset=utf-8"
            elif file_path.suffix == ".js":
                kind = "text/javascript; charset=utf-8"
            elif file_path.suffix == ".svg":
                kind = "image/svg+xml"
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_error(404)

    def log_message(self, fmt: str, *args: Any) -> None:
        print(fmt % args)


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 8766), Handler)
    print("Cortex demo at http://127.0.0.1:8766/ontology")
    server.serve_forever()
