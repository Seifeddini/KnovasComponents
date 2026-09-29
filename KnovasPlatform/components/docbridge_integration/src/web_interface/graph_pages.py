"""Pages for the knowledge graph: directories, entry cards, the console tabs.

Each page is a shell -- sidebar, header, the root element -- and the screen is
drawn by static/js/graph_*.js from /api/graph/*. The shell carries what the
server already knows (the directory's title, the type id, whether the
deployment runs in graph mode) so a page never flashes an empty header.

* ``/verzeichnis/<slug>``            a directory: every entry of one type
* ``/verzeichnis/<slug>/<node_id>``  an entry's card, reached from its directory
* ``/eintrag/<node_id>``             the same card, for a type without directory
* ``/admin/wissenstypen[/<type_id>[/karte]]``  types, their fields, card layout
* ``/admin/verzeichnisse``           which types have a directory, and in what order

Reading is any signed-in person (the backend ACL narrows it); the console is
administrators only, enforced here and again on every /api/graph route.
"""
from __future__ import annotations

import functools
import logging

from flask import Blueprint, abort, redirect, render_template, request, url_for

logger = logging.getLogger(__name__)


def create_graph_pages(gate, *, graph_mode, directories, page_context):
    bp = Blueprint("graph_pages", __name__)

    def signed_in(view):
        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            if gate.current_user() is None:
                return redirect(url_for("login", next=request.full_path or request.path))
            return view(*args, **kwargs)
        return wrapped

    def admin_only(view):
        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            user = gate.current_user()
            if user is None:
                return redirect(url_for("login", next=request.full_path or request.path))
            if "admin" not in user.roles:
                abort(403)
            return view(*args, **kwargs)
        return wrapped

    def _render(template, status=200, **values):
        user = gate.current_user()
        return render_template(template, graph_mode=graph_mode(), me=user,
                               is_admin=bool(user and "admin" in user.roles),
                               **page_context(), **values), status

    def _view(slug):
        if not graph_mode():
            return None
        try:
            return directories().view_by_slug(slug)
        except Exception as exc:  # noqa: BLE001 - the page says so instead of a 500
            logger.warning("Verzeichnis %s nicht lesbar: %s", slug, exc)
            return None

    @bp.route("/verzeichnis/<slug>")
    @signed_in
    def directory(slug):
        view = _view(slug)
        missing = graph_mode() and view is None
        return _render("graph_user.html", status=404 if missing else 200,
                       kg_page="directory", slug=slug, node_id="", view=view,
                       missing=missing, active_nav=f"dir:{slug}",
                       page_title=(view or {}).get("title") or "Verzeichnis")

    @bp.route("/verzeichnis/<slug>/<node_id>")
    @signed_in
    def entry_in_directory(slug, node_id):
        view = _view(slug)
        return _render("graph_user.html", kg_page="entry", slug=slug, node_id=node_id,
                       view=view, missing=False, active_nav=f"dir:{slug}",
                       page_title=(view or {}).get("title") or "Eintrag")

    @bp.route("/eintrag/<node_id>")
    @signed_in
    def entry(node_id):
        return _render("graph_user.html", kg_page="entry", slug="", node_id=node_id,
                       view=None, missing=False, active_nav="", page_title="Eintrag")

    @bp.route("/admin/wissenstypen")
    @admin_only
    def types():
        return _render("graph_admin.html", kg_page="types", type_id="", admin_tab="types",
                       active_nav="admin", page_title="Wissenstypen",
                       page_hint="Ein Wissenstyp ist eine Art von Eintrag — „Mandat“, „Person“, "
                                 "„Frist“. Sie beschreiben hier seine Felder, den Aufbau seiner "
                                 "Karte und die Liste, unter der seine Einträge erreichbar sind.")

    @bp.route("/admin/wissenstypen/<type_id>")
    @admin_only
    def type_workshop(type_id):
        return _render("graph_admin.html", kg_page="type", type_id=type_id, admin_tab="types",
                       active_nav="admin", page_title="Wissenstyp", page_hint="")

    @bp.route("/admin/wissenstypen/<type_id>/karte")
    @admin_only
    def card_designer(type_id):
        return _render("graph_admin.html", kg_page="card", type_id=type_id, admin_tab="types",
                       active_nav="admin", page_title="Karte gestalten", page_hint="")

    @bp.route("/admin/verzeichnisse")
    @admin_only
    def directories_admin():
        return _render("graph_admin.html", kg_page="dirs", type_id="", admin_tab="dirs",
                       active_nav="admin", page_title="Verzeichnisse",
                       page_hint="Ein Verzeichnis ist eine eigene Seite: alle Einträge eines "
                                 "Wissenstyps, durchsuchbar, mit den gewählten Feldern als "
                                 "Spalten. Es erscheint links in der Navigation, für alle "
                                 "angemeldeten Personen. Was jemand dort sieht, entscheidet "
                                 "weiterhin Knovas: das Verzeichnis zeigt nur, was die Freigaben "
                                 "dieser Person ohnehin hergeben.")

    return bp
