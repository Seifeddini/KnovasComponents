"""Local Cortex page for the current star view.

Serves the templates and static files from this checkout, with a cache-busting
query so a refresh always loads the scripts just saved. The ontology API is
not on this port; the star view falls back to its built-in systems, which is
enough to drag nodes while developing the picture.
"""
from __future__ import annotations

import re
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "src" / "web_interface" / "static"
TEMPLATE = ROOT / "src" / "web_interface" / "templates" / "ontology.html"


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


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path in ("/", "/ontology"):
            body = render_page()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path.startswith("/static/"):
            file_path = (STATIC / path[len("/static/"):]).resolve()
            if not str(file_path).startswith(str(STATIC.resolve())) or not file_path.is_file():
                self.send_error(404)
                return
            data = file_path.read_bytes()
            kind = "text/css" if file_path.suffix == ".css" else "text/javascript"
            if file_path.suffix == ".svg":
                kind = "image/svg+xml"
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_error(404)

    def log_message(self, fmt: str, *args) -> None:
        print(fmt % args)


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 8766), Handler)
    print("Cortex demo at http://127.0.0.1:8766/ontology")
    server.serve_forever()
