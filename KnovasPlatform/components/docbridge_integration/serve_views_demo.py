"""The Ansichten (views) of a fictional client, offline: Lumora AG.

    python serve_views_demo.py [--port 5077] [--host 127.0.0.1]

then open http://127.0.0.1:5077/views. No Knovas server, certificate, database or login: the
real views blueprint reads ``demo_data/lumora/graph.json`` through ``graph_views/offline.py``
and the definitions bundle ``demo_data/lumora/bundle.json``. Lumora is a software company run
the way Knovas runs itself (the same seven processes, roles, value streams and spines); every
person, customer and record in it is invented (``demo_data/lumora/company.py``). Rebuild the
data with ``demo_data/lumora/build.py --kb <KnowledgeBase checkout>``.

Saved views, change requests and node grants need the platform database and are off here; the
visitor is the deployment's operator (as with identity off), so the Definition tab shows its
errors, gaps and differences.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from flask import Flask, redirect  # noqa: E402

from graph_views.offline import OfflineClient, OfflineGraph  # noqa: E402
from graph_views.service import ViewService  # noqa: E402
from graph_views.settings import ViewSettings  # noqa: E402
from web_interface.views_routes import create_views_blueprint  # noqa: E402

DEMO = ROOT / "demo_data" / "lumora"
WEB = ROOT / "src" / "web_interface"


class _NoWait:
    """The graph is a local file: no pacing of fact reads."""

    def acquire(self) -> None:
        return None


def create_app(demo: Path = DEMO) -> Flask:
    client = OfflineClient(OfflineGraph.load(demo / "graph.json"))
    service = ViewService(lambda: client, ViewSettings(config_source=f"file:{demo / 'bundle.json'}"), bucket=_NoWait())
    app = Flask(__name__, template_folder=str(WEB / "templates"), static_folder=str(WEB / "static"))
    app.register_blueprint(create_views_blueprint(
        None, service, graph_mode=lambda: True,
        page_context=lambda: {"brand": "Lumora AG (Demo)", "app_title": "Lumora AG (Demo)", "csrf_token": "",
                              "asset_version": "demo", "is_admin": True}))

    @app.route("/")
    def index():
        return redirect("/views")

    @app.route("/ontology", endpoint="ontology_page")
    def ontology_page():        # the page's logo links back to the Cortex, which this demo does not serve
        return redirect("/views")

    return app


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5077)
    args = ap.parse_args(argv)
    print(f"Lumora AG demo: http://{args.host}:{args.port}/views")
    create_app().run(host=args.host, port=args.port, debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
