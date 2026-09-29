"""The /api/graph/* namespace: node types, fields, entries, directories, cards.

A separate module rather than more routes in app.py, which is already ~1900
lines. Follows the blueprint-factory shape admin.py established: the app's own
helpers are passed in, so this module imports nothing from app.py and can be
tested against a minimal Flask app.

Authorisation is on the route, never on whether the UI draws the control:

* reading is any signed-in person, narrowed by the backend ACL -- the Platform
  never adds a second answer to "may I see this?";
* writing an entry is its owner, its editors, or an administrator (node_grants);
* types, fields, directories, card layouts and suggestions are the console's,
  administrators only.

Thin by design: parse, authorise, delegate to ``graph_workbench``, serialise.

Plan: docs/superpowers/plans/2026-09-02-typed-node-workbench-components.md;
screens: the "Wissenstypen & Verzeichnisse" artifact.
"""
from __future__ import annotations

import functools
import logging
from typing import Any, Optional

from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

_GENERIC_ERROR = "Ein Fehler ist aufgetreten."
_FIXTURE_MODE_ERROR = "Wissensnetz-Modus erforderlich"
_NO_WRITE = "Keine Bearbeitungsrechte für diesen Eintrag."


class _NoDirectories:
    """Stand-in when no platform DB is wired (the gate tests' minimal app)."""

    def views(self):
        return []

    def view_for_type(self, _type_id):
        return None

    def view_by_slug(self, _slug):
        return None

    def card(self, _type_id):
        return None

    def dismissed(self, _ids):
        return set()


def create_graph_blueprint(gate, grant_store, source, *, graph_mode,
                           topology=None, directories=None):
    """Build the blueprint.

    Args:
        gate: the IdentityGate; ``gate.current_user()`` or None.
        grant_store: zero-arg callable -> NodeGrantStore over THIS request's
            connection (``gate.connection()`` is request-scoped).
        source: a callable returning the Knovas client for this request.
        graph_mode: a callable returning True when ONTOLOGY_SOURCE=graph.
        topology: a callable returning the cached graph source Cortex uses
            (``GraphOntologySource``), or None to read the client directly.
        directories: zero-arg callable -> DirectoryStore over this request's
            connection.
    """
    bp = Blueprint("graph_api_ui", __name__, url_prefix="/api/graph")

    def require_graph_mode(view):
        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            if not graph_mode():
                # 409, not 500: nothing is broken. The deployment is in fixture
                # mode and the screen says so rather than inventing data.
                return jsonify({"success": False, "error": _FIXTURE_MODE_ERROR}), 409
            return view(*args, **kwargs)
        return wrapped

    def require_user(view):
        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            if gate is None or gate.current_user() is None:
                return jsonify({"success": False, "error": "Nicht angemeldet."}), 401
            return view(*args, **kwargs)
        return wrapped

    def require_admin(view):
        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            user = gate.current_user() if gate else None
            if user is None:
                return jsonify({"success": False, "error": "Nicht angemeldet."}), 401
            if "admin" not in user.roles:
                # 403, not 404: the caller is authenticated and the schema
                # editor is not a secret. Hiding it would only make a
                # misconfigured account harder to diagnose.
                logger.info("Admin-Gate: %s ohne Rolle admin auf %s",
                            user.id, view.__name__)
                return jsonify({"success": False,
                                "error": "Nur für Administratoren."}), 403
            return view(*args, **kwargs)
        return wrapped

    def require_node_write(view=None, *, param="node_id"):
        """Usable bare (``@require_node_write``) or with the name of the path
        parameter carrying the node id (``@require_node_write(param="id")``).
        """

        def decorate(inner):
            @functools.wraps(inner)
            def wrapped(*args, **kwargs):
                user = gate.current_user() if gate else None
                if user is None:
                    return jsonify({"success": False,
                                    "error": "Nicht angemeldet."}), 401
                if param not in kwargs:
                    # A wiring mistake, and it must be loud. kwargs.get would
                    # hand may_write None, every request would be refused, and
                    # the screen would blame the user's permissions for what is
                    # a mismatched route parameter.
                    raise RuntimeError(
                        f"require_node_write on {inner.__name__}: the route has "
                        f"no <{param}> parameter to authorise against.")
                # Straight from the URL: may_write is responsible for refusing
                # an id that cannot name a node, rather than raising out of the
                # guard and turning an authorisation answer into a 500.
                node_id = kwargs[param]
                if not grant_store().may_write(node_id, user):
                    logger.info("Schreib-Gate: %s ohne Recht an Knoten %r",
                                user.id, node_id)
                    return jsonify({"success": False, "error": _NO_WRITE}), 403
                return inner(*args, **kwargs)
            return wrapped

        return decorate(view) if view is not None else decorate

    bp.require_graph_mode = require_graph_mode
    bp.require_user = require_user
    bp.require_admin = require_admin
    bp.require_node_write = require_node_write

    # ── plumbing ──────────────────────────────────────────────────────────

    def _store():
        return directories() if directories is not None else _NoDirectories()

    def _person(user_id) -> Optional[dict]:
        try:
            user = gate.users().get(str(user_id))
        except Exception:  # noqa: BLE001 - an id that is not a user id is no person
            return None
        if user is None:
            return None
        return {"id": str(user.id), "email": user.email,
                "display_name": user.display_name or user.email}

    def _workbench():
        from graph_workbench import Workbench

        return Workbench(source(), topology() if topology is not None else None, _store(),
                         people=_person)

    def _body() -> dict:
        body = request.get_json(silent=True)
        return body if isinstance(body, dict) else {}

    def _fail(status: int, message: str):
        return jsonify({"success": False, "error": message}), status

    def _translate(exc: Exception):
        """An exception from a layer below, as an answer a screen can show."""
        from graph_workbench import NotFound
        from identity.directories import DirectoryError, SlugTakenError, ViewExistsError
        from knovas_client import GraphError, KnowledgeGraphDisabled

        if isinstance(exc, NotFound):
            return _fail(404, "Nicht gefunden.")
        if isinstance(exc, (SlugTakenError, ViewExistsError)):
            return _fail(409, str(exc))
        if isinstance(exc, DirectoryError):
            return _fail(400, str(exc))
        if isinstance(exc, KnowledgeGraphDisabled):
            return _fail(409, "Der Wissensgraph ist für diesen Mandanten nicht aktiviert.")
        if isinstance(exc, GraphError):
            logger.warning("Knowledge Graph API: %s", exc)
            if exc.status == 429:
                return _fail(503, "Die Knovas-API bittet um eine kurze Pause. "
                                  "Bitte in einigen Sekunden erneut versuchen.")
            if exc.status in (400, 409, 422):
                return _fail(400, f"Die Knovas-API hat abgelehnt: {exc.message or exc.error_code}")
            return _fail(502, "Die Knovas-API hat nicht geantwortet wie erwartet.")
        logger.error("Graph route failed", exc_info=exc)
        return _fail(500, _GENERIC_ERROR)

    def handled(view):
        """Every route below answers a screen, never with a stack trace."""
        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            try:
                return view(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 - translated, logged above
                return _translate(exc)
        return wrapped

    def _is_admin(user) -> bool:
        return user is not None and "admin" in user.roles

    def _may_grant(node_id) -> bool:
        user = gate.current_user()
        if _is_admin(user):
            return True
        # mayGrant (node_grants.als): the owner or an admin, never an editor.
        return user is not None and grant_store().for_node(node_id)["owner"] == str(user.id)

    def _grants_payload(node_id) -> dict:
        current = grant_store().for_node(node_id)
        return {
            "owner": (_person(current["owner"]) or {"id": current["owner"], "email": None,
                                                   "display_name": "Unbekanntes Konto"})
            if current["owner"] else None,
            "editors": [_person(uid) or {"id": uid, "email": None,
                                          "display_name": "Unbekanntes Konto"}
                        for uid in current["editors"]],
        }

    # ── node types and fields (the console) ───────────────────────────────

    @bp.route("/node-types", methods=["GET"])
    @require_graph_mode
    @require_user
    @handled
    def list_node_types():
        return jsonify({"success": True, "node_types": _workbench().node_types()})

    @bp.route("/node-types", methods=["POST"])
    @require_graph_mode
    @require_admin
    @handled
    def create_node_type():
        name = " ".join(str(_body().get("name") or "").split())
        if not name:
            return _fail(400, "Name fehlt.")
        workbench = _workbench()
        if any(t["name"].lower() == name.lower() for t in workbench.node_types()):
            return _fail(409, f"Einen Wissenstyp „{name}“ gibt es bereits.")
        created = workbench.client.graph_create_node_type(name)
        if not created:
            return _fail(400, "Typ nicht anlegbar.")
        workbench.invalidate()
        node_type = created.get("node_type", created)
        return jsonify({"success": True, "node_type": node_type}), 201

    @bp.route("/node-types/<type_id>/schema", methods=["GET"])
    @require_graph_mode
    @require_user
    @handled
    def read_schema(type_id):
        from graph_directory import public_attribute

        wanted = request.args.get("include_deprecated") in ("1", "true")
        attributes = _workbench().schema(type_id, include_deprecated=wanted)
        if attributes is None:
            return _fail(404, "Typ nicht gefunden.")
        ordered = sorted(attributes, key=lambda a: (int(a.get("sort_order") or 0),
                                                     str(a.get("name") or "")))
        return jsonify({"success": True, "attributes": [public_attribute(a) for a in ordered]})

    def _clean_enum(values) -> Optional[list]:
        if not isinstance(values, list):
            return None
        cleaned = []
        for value in values:
            text = " ".join(str(value or "").split())
            if text and text not in cleaned:
                cleaned.append(text)
        return cleaned or None

    @bp.route("/node-types/<type_id>/schema", methods=["POST"])
    @require_graph_mode
    @require_admin
    @handled
    def create_attribute(type_id):
        from graph_directory import live_attributes
        from graph_model import DATATYPES

        payload = _body()
        name = " ".join(str(payload.get("name") or "").split())
        datatype = str(payload.get("datatype") or "").strip()
        if not name:
            return _fail(400, "Name fehlt.")
        if datatype not in DATATYPES:
            return _fail(400, "Unbekannter Datentyp.")
        enum_values = _clean_enum(payload.get("enum_values")) if datatype == "enum" else None
        if datatype == "enum" and not enum_values:
            return _fail(400, "Ein Auswahlfeld braucht mindestens einen Wert — sonst ist es "
                              "ein Feld, das man nicht ausfüllen kann.")
        workbench = _workbench()
        current = workbench.schema(type_id)
        if current is None:
            return _fail(404, "Typ nicht gefunden.")
        live = live_attributes(current)
        if any(str(a.get("name") or "").lower() == name.lower() for a in live):
            return _fail(409, f"Ein Feld „{name}“ gibt es bei diesem Typ schon.")
        target = str(payload.get("target_node_type_id") or "").strip() or None
        if target and (datatype != "entity_ref" or workbench.node_type(target) is None):
            return _fail(400, "Ein Zieltyp gehört zu einer Verbindung und muss ein "
                              "bestehender Wissenstyp sein.")
        next_order = (max((int(a.get("sort_order") or 0) for a in live), default=0) // 10 + 1) * 10
        created = workbench.client.graph_create_schema_attribute(
            type_id, name, datatype=datatype, required=bool(payload.get("required")),
            description=" ".join(str(payload.get("description") or "").split()) or None,
            sort_order=next_order, enum_values=enum_values, target_node_type_id=target)
        if created is None:
            return _fail(404, "Typ nicht gefunden.")
        workbench.invalidate()
        return jsonify({"success": True, "attribute": created.get("attribute", created)}), 201

    @bp.route("/node-types/<type_id>/schema/<attribute_id>", methods=["PATCH"])
    @require_graph_mode
    @require_admin
    @handled
    def update_attribute(type_id, attribute_id):
        from graph_directory import attribute_id as aid_of, is_deprecated

        payload = _body()
        fields: dict = {}
        if "name" in payload:
            fields["name"] = " ".join(str(payload["name"] or "").split())
            if not fields["name"]:
                return _fail(400, "Das Feld braucht einen Namen.")
        if "description" in payload:
            fields["description"] = " ".join(str(payload["description"] or "").split())
        if "required" in payload:
            fields["required"] = bool(payload["required"])
        if "enum_values" in payload:
            fields["enum_values"] = _clean_enum(payload["enum_values"])
            if not fields["enum_values"]:
                return _fail(400, "Ein Auswahlfeld braucht mindestens einen Wert.")
        revive = payload.get("deprecated") is False
        if revive:
            fields["deprecated_at"] = None
        if not fields:
            return _fail(400, "Keine Änderung.")
        workbench = _workbench()
        if revive:
            # A revived field goes to the end, like a new one.
            live = workbench.live_schema(type_id)
            fields["sort_order"] = (max((int(a.get("sort_order") or 0) for a in live),
                                        default=0) // 10 + 1) * 10
        updated = workbench.client.graph_update_schema_attribute(type_id, attribute_id, **fields)
        if updated is None:
            return _fail(404, "Feld nicht gefunden.")
        workbench.invalidate()
        if revive:
            # Read back: an API that ignores deprecated_at on PATCH answers 200
            # all the same, and "wieder aufgenommen" must not be a claim.
            again = next((a for a in workbench.schema(type_id, include_deprecated=True) or []
                          if aid_of(a) == str(attribute_id)), None)
            if again is None or is_deprecated(again):
                return _fail(409, "Die Knovas-API nimmt ein stillgelegtes Feld nicht wieder "
                                  "auf. Legen Sie bei Bedarf ein neues Feld an; die alten "
                                  "Werte bleiben lesbar.")
        return jsonify({"success": True, "attribute": updated.get("attribute", updated)})

    @bp.route("/node-types/<type_id>/schema/<attribute_id>", methods=["DELETE"])
    @require_graph_mode
    @require_admin
    @handled
    def deprecate_attribute(type_id, attribute_id):
        """Deprecation, not deletion: existing facts keep their attribute_id.
        The response says `deprecated` so the UI cannot accidentally word it
        as a delete."""
        workbench = _workbench()
        if workbench.client.graph_deprecate_schema_attribute(type_id, attribute_id) is None:
            return _fail(404, "Feld nicht gefunden.")
        view = workbench.store.view_for_type(type_id)
        if view and str(attribute_id) in view["columns"]:
            workbench.store.update_view(type_id, by=gate.current_user(), columns=[
                c for c in view["columns"] if c != str(attribute_id)])
        workbench.invalidate()
        return jsonify({"success": True, "deprecated": True})

    @bp.route("/node-types/<type_id>/schema/<attribute_id>/move", methods=["POST"])
    @require_graph_mode
    @require_admin
    @handled
    def move_attribute(type_id, attribute_id):
        """Move a field before another (or to the end). One write when there is
        room between the neighbours; otherwise the list is renumbered in tens,
        so the next move fits in between again without touching everything."""
        from graph_directory import attribute_id as aid_of
        from graph_workbench import with_backoff

        workbench = _workbench()
        live = workbench.live_schema(type_id)
        order = [aid_of(a) for a in live]
        if str(attribute_id) not in order:
            return _fail(404, "Feld nicht gefunden.")
        before = str(_body().get("before") or "")
        order.remove(str(attribute_id))
        order.insert(order.index(before) if before in order else len(order), str(attribute_id))
        sort = {aid_of(a): int(a.get("sort_order") or 0) for a in live}
        index = order.index(str(attribute_id))
        prev = sort[order[index - 1]] if index > 0 else None
        nxt = sort[order[index + 1]] if index + 1 < len(order) else None
        if prev is None and nxt is not None and nxt >= 2:
            writes = {str(attribute_id): nxt // 2}
        elif prev is not None and nxt is None:
            writes = {str(attribute_id): prev + 10}
        elif prev is not None and nxt is not None and nxt - prev >= 2:
            writes = {str(attribute_id): (prev + nxt) // 2}
        else:
            writes = {key: (i + 1) * 10 for i, key in enumerate(order) if sort[key] != (i + 1) * 10}
        for key, value in writes.items():
            with_backoff(workbench.client.graph_update_schema_attribute, type_id, key,
                         sort_order=value)
        workbench.invalidate()
        return jsonify({"success": True, "order": order, "writes": len(writes)})

    # ── entries ───────────────────────────────────────────────────────────

    @bp.route("/nodes", methods=["GET"])
    @require_graph_mode
    @require_user
    @handled
    def list_nodes():
        """Every entry this person may see, optionally of one type or matching
        a name. Deliberately NOT narrowed by node_grants: read visibility is
        the backend ACL's answer and it has already been applied."""
        from graph_directory import node_id, node_name, node_type_id

        workbench = _workbench()
        wanted_type = str(request.args.get("type") or "").strip()
        query = str(request.args.get("q") or "").strip().lower()
        nodes = [{"id": node_id(n), "name": node_name(n) or node_id(n),
                  "node_type_id": node_type_id(n)} for n in workbench.nodes()]
        nodes = [n for n in nodes if (not wanted_type or n["node_type_id"] == wanted_type)
                 and (not query or query in n["name"].lower())]
        nodes.sort(key=lambda n: n["name"].lower())
        return jsonify({"success": True, "nodes": nodes})

    def _encode_field(attribute: dict, raw: Any):
        from graph_model import encode

        return encode(str(attribute.get("datatype") or "text"), raw,
                      enum_values=attribute.get("enum_values"))

    def _blank(raw: Any) -> bool:
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            return True
        if isinstance(raw, dict):
            inner = raw.get("value", raw.get("amount", raw.get("node_id")))
            return inner is None or (isinstance(inner, str) and not inner.strip())
        return False

    @bp.route("/nodes", methods=["POST"])
    @require_graph_mode
    @require_user
    @handled
    def create_node():
        """Create an entry and its fields in one request.

        A field the codec refuses is reported, never a reason to refuse the
        entry: schemas make absence visible, they do not block a save.
        """
        from graph_directory import attribute_id, live_attributes
        from graph_model import FactValueError
        from graph_workbench import with_backoff
        from knovas_client import GraphError

        payload = _body()
        name = " ".join(str(payload.get("name") or "").split())
        if not name:
            return _fail(400, "Der Eintrag braucht einen Namen.")
        type_id = str(payload.get("node_type_id") or "").strip() or None
        workbench = _workbench()
        live = live_attributes(workbench.schema(type_id) or []) if type_id else []
        if type_id and workbench.node_type(type_id) is None:
            return _fail(404, "Wissenstyp nicht gefunden.")
        created = workbench.client.graph_create_node(name, node_type_id=type_id)
        if not created:
            return _fail(400, "Eintrag nicht anlegbar.")
        node = created.get("node", created)
        new_id = str(node.get("id"))
        problems = []
        user = gate.current_user()
        try:
            # CreateMechanism: the creator becomes the owner in the same request.
            grant_store().set_owner(new_id, user.id)
        except Exception as exc:  # noqa: BLE001 - the entry exists either way
            logger.warning("Eigentuemer fuer %s nicht eingetragen: %s", new_id, exc)
            problems.append("Die Eigentümerschaft konnte nicht eingetragen werden; "
                            "bearbeiten können den Eintrag vorerst nur Administratoren.")
        values = payload.get("facts") if isinstance(payload.get("facts"), dict) else {}
        by_id = {attribute_id(a): a for a in live}
        for key, raw in values.items():
            attribute = by_id.get(str(key))
            if attribute is None or _blank(raw):
                continue
            try:
                value = _encode_field(attribute, raw)
                with_backoff(workbench.client.graph_create_fact, new_id, value,
                             attribute_id=str(key))
            except FactValueError as exc:
                problems.append(f"{attribute.get('name')}: {exc}")
            except GraphError as exc:
                problems.append(f"{attribute.get('name')}: von der Knovas-API abgelehnt "
                                f"({exc.message or exc.status}).")
        workbench.invalidate()
        return jsonify({"success": True, "node": {"id": new_id, "name": name,
                                                  "node_type_id": type_id},
                        "problems": problems}), 201

    @bp.route("/nodes/<node_id>", methods=["GET"])
    @require_graph_mode
    @require_user
    @handled
    def node_detail(node_id):
        payload = _workbench().node_page(node_id)
        user = gate.current_user()
        payload.update({
            "success": True,
            "grants": _grants_payload(node_id),
            "may_write": grant_store().may_write(node_id, user),
            "may_grant": _may_grant(node_id),
            "is_admin": _is_admin(user),
            "me": str(user.id),
        })
        return jsonify(payload)

    @bp.route("/nodes/<node_id>", methods=["PATCH"])
    @require_graph_mode
    @require_node_write
    @handled
    def update_node(node_id):
        # The name only. Who may SEE an entry is the backend ACL's, set by an
        # administrator in Knovas; an editor renaming a matter must not be able
        # to widen or narrow its audience on the way.
        name = " ".join(str(_body().get("name") or "").split())
        if not name:
            return _fail(400, "Der Eintrag braucht einen Namen.")
        workbench = _workbench()
        updated = workbench.client.graph_update_node(node_id, name=name)
        if updated is None:
            return _fail(404, "Eintrag nicht gefunden.")
        workbench.invalidate()
        return jsonify({"success": True, "node": updated.get("node", updated)})

    @bp.route("/nodes/<node_id>/fields/<attribute_id>", methods=["PUT"])
    @require_graph_mode
    @require_node_write
    @handled
    def write_field(node_id, attribute_id):
        """Set, change or clear one field of an entry.

        The fact to change is looked up on THIS node, never taken from the
        request: a person who may edit one entry must not be able to name a
        fact of another.
        """
        from graph_directory import attribute_id as aid_of, cell, facts_by_attribute, is_deprecated
        from graph_directory import live_attributes, entry_gaps, node_type_id as type_of
        from graph_model import FactValueError

        workbench = _workbench()
        detail = workbench.client.graph_node(node_id)
        if not detail:
            return _fail(404, "Eintrag nicht gefunden.")
        node = detail.get("node", detail)
        facts = detail.get("facts")
        if facts is None:
            facts = workbench.client.graph_facts(node_id) or []
        type_id = type_of(node)
        attributes = workbench.schema(type_id, include_deprecated=True) or [] if type_id else []
        attribute = next((a for a in attributes if aid_of(a) == str(attribute_id)), None)
        if attribute is None:
            return _fail(404, "Feld nicht gefunden.")
        if is_deprecated(attribute):
            return _fail(409, "Das Feld ist stillgelegt; sein Wert wird nur noch gelesen.")
        existing = facts_by_attribute(facts).get(str(attribute_id))
        raw = _body().get("value")
        if _blank(raw):
            if existing is not None:
                workbench.client.graph_delete_fact(str(existing["id"]))
            new_fact = None
        else:
            try:
                value = _encode_field(attribute, raw)
            except FactValueError as exc:
                # The codec's message names the rule; replacing it with a
                # generic error would waste it.
                return _fail(400, f"„{attribute.get('name')}“: {exc}")
            if existing is not None:
                result = workbench.client.graph_update_fact(str(existing["id"]), value=value)
            else:
                result = workbench.client.graph_create_fact(node_id, value,
                                                            attribute_id=str(attribute_id))
            if result is None:
                return _fail(404, "Eintrag nicht gefunden.")
            new_fact = dict(existing or {})
            new_fact.update(result.get("fact") or {})
            new_fact["value"] = value
            new_fact["attribute_id"] = str(attribute_id)
        workbench.invalidate()
        live = live_attributes(attributes)
        others = [f for f in facts if str(f.get("attribute_id")) != str(attribute_id)]
        after = facts_by_attribute(others + ([new_fact] if new_fact else []))
        return jsonify({"success": True,
                        "field": cell(attribute, new_fact, workbench.names()),
                        "gaps": entry_gaps(live, after)})

    @bp.route("/nodes/<node_id>/network", methods=["GET"])
    @require_graph_mode
    @require_user
    @handled
    def node_network(node_id):
        try:
            depth = max(1, min(3, int(request.args.get("depth") or 1)))
        except ValueError:
            depth = 1
        workbench = _workbench()
        detail = workbench.client.graph_node(node_id)
        if not detail:
            return _fail(404, "Eintrag nicht gefunden.")
        network = workbench.network(detail.get("node", detail), depth)
        types = {t["id"]: t for t in workbench.node_types()}
        views = {v["node_type_id"]: v["slug"] for v in workbench.store.views() if v["active"]}
        for node in network["nodes"]:
            node["slug"] = views.get(node["node_type_id"])
        return jsonify({"success": True, "centre": node_id, "types": types, **network})

    @bp.route("/nodes/<node_id>/history", methods=["GET"])
    @require_graph_mode
    @require_user
    @handled
    def node_history(node_id):
        return jsonify({"success": True, **_workbench().history(node_id)})

    # ── editors ───────────────────────────────────────────────────────────

    @bp.route("/nodes/<node_id>/grants", methods=["GET"])
    @require_graph_mode
    @require_user
    @handled
    def read_grants(node_id):
        return jsonify({"success": True, **_grants_payload(node_id),
                        "may_grant": _may_grant(node_id)})

    @bp.route("/nodes/<node_id>/grants", methods=["POST"])
    @require_graph_mode
    @require_user
    @handled
    def add_grant(node_id):
        if not _may_grant(node_id):
            # An editor may edit, not delegate. Otherwise one grant silently
            # becomes the right to hand out every further grant.
            return _fail(403, "Nur Eigentümer oder Administratoren vergeben Bearbeitungsrechte.")
        user_id = str(_body().get("user_id") or "")
        person = _person(user_id) if user_id else None
        if person is None:
            return _fail(404, "Konto nicht gefunden.")
        try:
            grant_store().grant_editor(node_id, person["id"], granted_by=gate.current_user().id)
        except ValueError:
            return _fail(400, "Für diesen Eintrag lassen sich keine Rechte vergeben.")
        return jsonify({"success": True, "editor": person, **_grants_payload(node_id)}), 201

    @bp.route("/nodes/<node_id>/grants/<user_id>", methods=["DELETE"])
    @require_graph_mode
    @require_user
    @handled
    def remove_grant(node_id, user_id):
        from identity.node_grants import OwnerRevokeError

        if not _may_grant(node_id):
            return _fail(403, "Nur Eigentümer oder Administratoren entziehen Bearbeitungsrechte.")
        try:
            grant_store().revoke(node_id, user_id)
        except OwnerRevokeError as exc:
            return _fail(409, str(exc))
        except ValueError:
            return _fail(404, "Eintrag nicht gefunden.")
        return jsonify({"success": True, **_grants_payload(node_id)})

    @bp.route("/people", methods=["GET"])
    @require_graph_mode
    @require_user
    @handled
    def search_people():
        """Colleagues to make editors: active accounts matching a name."""
        query = str(request.args.get("q") or "").strip().lower()
        if len(query) < 2:
            return jsonify({"success": True, "people": []})
        found = [u for u in gate.users().list_all() if u.is_active
                 and (query in (u.display_name or "").lower() or query in u.email.lower())]
        return jsonify({"success": True, "people": [
            {"id": str(u.id), "display_name": u.display_name or u.email, "email": u.email}
            for u in found[:8]]})

    # ── directories ───────────────────────────────────────────────────────

    @bp.route("/directories/<slug>", methods=["GET"])
    @require_graph_mode
    @require_user
    @handled
    def directory(slug):
        from graph_workbench import _public_view

        workbench = _workbench()
        view = workbench.store.view_by_slug(slug)
        if view is None:
            return _fail(404, "Dieses Verzeichnis gibt es nicht.")
        if not view["active"]:
            return jsonify({"success": True, "inactive": True, "view": _public_view(view)})
        return jsonify({"success": True, **workbench.directory(view)})

    @bp.route("/directories/<slug>/rows", methods=["GET"])
    @require_graph_mode
    @require_user
    @handled
    def directory_rows(slug):
        workbench = _workbench()
        view = workbench.store.view_by_slug(slug)
        if view is None or not view["active"]:
            return _fail(404, "Dieses Verzeichnis gibt es nicht.")
        ids = [i for i in str(request.args.get("ids") or "").split(",") if i]
        return jsonify({"success": True, "rows": workbench.directory_rows(view, ids)})

    # ── the console: overview, card layouts, directories, suggestions ─────

    @bp.route("/admin/types", methods=["GET"])
    @require_graph_mode
    @require_admin
    @handled
    def admin_types():
        return jsonify({"success": True, "types": _workbench().admin_types()})

    @bp.route("/admin/types/<type_id>", methods=["GET"])
    @require_graph_mode
    @require_admin
    @handled
    def admin_type(type_id):
        return jsonify({"success": True, **_workbench().admin_type(type_id)})

    @bp.route("/admin/types/<type_id>/card", methods=["GET"])
    @require_graph_mode
    @require_admin
    @handled
    def admin_card(type_id):
        return jsonify({"success": True, **_workbench().admin_card(type_id)})

    @bp.route("/cards/<type_id>", methods=["PUT"])
    @require_graph_mode
    @require_admin
    @handled
    def save_card(type_id):
        from graph_directory import LayoutError, card_layout, clean_layout

        workbench = _workbench()
        if workbench.node_type(type_id) is None:
            return _fail(404, "Wissenstyp nicht gefunden.")
        live = workbench.live_schema(type_id)
        try:
            layout = clean_layout(_body().get("layout"), live)
        except LayoutError as exc:
            return _fail(400, str(exc))
        saved = workbench.store.save_card(type_id, layout, by=gate.current_user())
        return jsonify({"success": True, "layout": card_layout(saved, live)})

    @bp.route("/admin/directories", methods=["GET"])
    @require_graph_mode
    @require_admin
    @handled
    def admin_directories():
        return jsonify({"success": True, **_workbench().admin_directories()})

    def _checked_columns(workbench, type_id, columns) -> list:
        from graph_directory import attribute_id

        known = {attribute_id(a) for a in workbench.live_schema(type_id)}
        return [str(c) for c in columns or [] if str(c) in known]

    @bp.route("/views", methods=["POST"])
    @require_graph_mode
    @require_admin
    @handled
    def create_view():
        from graph_workbench import _public_view

        payload = _body()
        type_id = str(payload.get("node_type_id") or "").strip()
        workbench = _workbench()
        node_type = workbench.node_type(type_id) if type_id else None
        if node_type is None:
            return _fail(404, "Wissenstyp nicht gefunden.")
        title = " ".join(str(payload.get("title") or "").split()) or node_type["name"]
        view = workbench.store.create_view(
            type_id, title=title, subtitle=str(payload.get("subtitle") or ""),
            slug=str(payload.get("slug") or "").strip() or title,
            columns=_checked_columns(workbench, type_id, payload.get("columns")),
            by=gate.current_user())
        return jsonify({"success": True, "view": _public_view(view)}), 201

    @bp.route("/views/<type_id>", methods=["PATCH"])
    @require_graph_mode
    @require_admin
    @handled
    def update_view(type_id):
        from graph_workbench import _public_view

        payload = _body()
        workbench = _workbench()
        fields = {k: payload[k] for k in ("title", "subtitle", "slug", "active") if k in payload}
        if "columns" in payload:
            fields["columns"] = _checked_columns(workbench, type_id, payload.get("columns"))
        if not fields:
            return _fail(400, "Keine Änderung.")
        view = workbench.store.update_view(type_id, by=gate.current_user(), **fields)
        if view is None:
            return _fail(404, "Verzeichnis nicht gefunden.")
        return jsonify({"success": True, "view": _public_view(view)})

    @bp.route("/views/<type_id>", methods=["DELETE"])
    @require_graph_mode
    @require_admin
    @handled
    def delete_view(type_id):
        if not _store().delete_view(type_id):
            return _fail(404, "Verzeichnis nicht gefunden.")
        return jsonify({"success": True})

    @bp.route("/views/<type_id>/move", methods=["POST"])
    @require_graph_mode
    @require_admin
    @handled
    def move_view(type_id):
        try:
            delta = int(_body().get("delta"))
        except (TypeError, ValueError):
            return _fail(400, "Richtung fehlt.")
        if not _store().move_view(type_id, delta):
            return _fail(400, "Das Verzeichnis lässt sich nicht weiter verschieben.")
        return jsonify({"success": True})

    def _suggestion_request():
        payload = _body()
        return (str(payload.get("scope") or ""), str(payload.get("type_id") or ""),
                str(payload.get("id") or ""))

    @bp.route("/suggestions/apply", methods=["POST"])
    @require_graph_mode
    @require_admin
    @handled
    def apply_suggestion():
        scope, type_id, suggestion_id = _suggestion_request()
        if scope not in ("fields", "card", "dirs") or not suggestion_id:
            return _fail(400, "Unbekannter Vorschlag.")
        message = _workbench().apply(scope, type_id, suggestion_id, by=gate.current_user())
        return jsonify({"success": True, "message": message})

    @bp.route("/suggestions/dismiss", methods=["POST"])
    @require_graph_mode
    @require_admin
    @handled
    def dismiss_suggestion():
        scope, type_id, suggestion_id = _suggestion_request()
        if scope not in ("fields", "card", "dirs") or not suggestion_id:
            return _fail(400, "Unbekannter Vorschlag.")
        workbench = _workbench()
        match = next((s for s in workbench.suggestions_for(scope, type_id)
                      if s["id"] == suggestion_id), None)
        if match is None:
            return _fail(404, "Diesen Vorschlag gibt es nicht mehr.")
        if match.get("dismissable") is False:
            return _fail(400, "Dieser Hinweis beschreibt den Bestand und lässt sich nicht "
                              "verwerfen.")
        workbench.store.dismiss(suggestion_id, by=gate.current_user())
        return jsonify({"success": True, "message": "Vorschlag verworfen. Er kommt nicht wieder."})

    return bp
