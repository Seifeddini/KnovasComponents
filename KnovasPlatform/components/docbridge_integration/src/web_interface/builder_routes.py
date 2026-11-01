"""The Brain Builder's shell: /builder, its eight stages and the workspaces.

Plan: "Knovas Brain Builder -- build plan" (Claude Docs), steps B0 and B1
(workspaces: list, import of a configuration package, overview, export), B2 and
B3 (the ontology, read and edited on the canvas, with undo and redo). The builder
is the Platform as a consultant's tool: a consultant models a company's
ontology, processes, sources and views, then publishes them to the company's
Brain. This module draws the frame every later step fills in -- the stage
navigation, the workspace switcher, an empty state per stage -- and the style
guide at /builder/_styleguide.

Every route is authorised on the route, not on whether the link is drawn: a
``before_request`` on the blueprint refuses anyone without a builder role, so
a stage added later is covered by default instead of by remembering. Until the
builder's own roles exist (step B11: author, reviewer, publisher) the only
builder role is ``admin``.

Takes the gate and a page-context callable rather than importing ``app.py``,
so it can be exercised against a minimal Flask app, as the console is.
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Mapping, Optional

from flask import (Blueprint, Response, abort, jsonify, redirect, render_template, request,
                   session, url_for)
from markupsafe import Markup

from builder import editing, ontology_view, package, processes, publisher, review, tenant_ontology as tenant_reader
from builder import mappings as mapping_rules
from builder import views as brain_views
from builder import operate
from builder import sources as source_kinds
from builder import packs as shipped_packs

logger = logging.getLogger(__name__)

#: Roles that open the builder. Step B11 replaces this with the builder roles.
BUILDER_ROLES = frozenset({"admin"})
#: The Platform's own roles when the app does not say which it has (identity's built-in four).
PLATFORM_ROLES = ("admin", "approver", "ingestion_manager", "member")


@dataclass(frozen=True)
class Stage:
    slug: str
    label: str
    summary: str
    empty_title: str
    empty_text: str
    #: Whether the stage works on a chosen workspace. Only the first one does
    #: not: it is where a workspace is made.
    needs_workspace: bool = True


#: The consultant's eight stages, in the order they work through them.
STAGES: tuple[Stage, ...] = (
    Stage(
        "arbeitsbereich", "Arbeitsbereich",
        "Den Arbeitsbereich einer Firma anlegen, mit ihrem Mandanten verbinden, "
        "Startpakete wählen und das Team einladen.",
        "Noch kein Arbeitsbereich",
        "Ein Arbeitsbereich hält das Modell einer Firma: Ontologie, Prozesse, "
        "Quellen und Ansichten. Legen Sie den ersten an, um zu beginnen.",
        needs_workspace=False,
    ),
    Stage(
        "verbinden", "Verbinden",
        "Die Synchronisationskomponenten der Firma anlegen und in der Plattform "
        "registrieren: Dateiablage, DMS, Datenbank, GitHub.",
        "Noch keine Komponente verbunden",
        "Alles, was das Brain erhält, kommt über eine registrierte Komponente.",
    ),
    Stage(
        "ontologie", "Ontologie",
        "Typen, Beziehungen und Attribute modellieren. Die Prüfung läuft beim "
        "Bearbeiten mit.",
        "Noch keine Typen",
        "Typen erscheinen hier als Karten, Beziehungen als Pfeile zwischen ihnen.",
    ),
    Stage(
        "prozesse", "Prozesse",
        "Abläufe mit Schritten, Übergängen und einem Abschlusskriterium je "
        "Schritt modellieren.",
        "Noch keine Prozesse",
        "Ein Prozess legt fest, welche Schritte ein Vorgang durchläuft und wer "
        "ihn verantwortet.",
    ),
    Stage(
        "quellen", "Quellen & Zuordnung",
        "Für jede Komponente festlegen, was sie liest, und ihre Felder Typen, "
        "Attributen und Beziehungen zuordnen.",
        "Noch keine Quellen",
        "Quellen kommen aus den verbundenen Komponenten. Verbinden Sie zuerst "
        "eine Komponente.",
    ),
    Stage(
        "ansichten", "Ansichten & Rollen",
        "Hierarchien, die Abschnitte jeder Typseite und wer welche Ansicht sieht.",
        "Noch keine Ansichten",
        "Ansichten bauen auf den Typen der Ontologie auf.",
    ),
    Stage(
        "pruefen", "Prüfen & Veröffentlichen",
        "Alle Änderungen mit der veröffentlichten Fassung vergleichen, als "
        "Probelauf prüfen, freigeben lassen und veröffentlichen.",
        "Nichts zu prüfen",
        "Sobald ein Editor etwas ändert, erscheint die Änderung hier.",
    ),
    Stage(
        "betrieb", "Betrieb",
        "Zustand je Typ, Zuordnungen und Komponenten, Laufhistorie und Ordnung "
        "im Modell.",
        "Noch nichts veröffentlicht",
        "Der Betrieb zeigt, wie es dem Brain nach der ersten Veröffentlichung geht.",
    ),
)

STAGES_BY_SLUG: Mapping[str, Stage] = {stage.slug: stage for stage in STAGES}

STYLEGUIDE_VIEWS = ("beide", "hell", "dunkel")


def user_may_build(user) -> bool:
    """Whether ``user`` holds a builder role. ``None`` (nobody signed in) may not."""
    return user is not None and bool(BUILDER_ROLES & set(getattr(user, "roles", ()) or ()))


#: Where the chosen workspace is remembered between pages.
SESSION_WORKSPACE = "builder_workspace"

#: builder_change.path values that are events, not places in the model.
CHANGE_LABELS = {"import": "Paket importiert", "create": "Angelegt"}
#: Session key of the undo and redo stacks: {workspace id: {"undo": [batch], "redo": [batch]}}.
SESSION_HISTORY = "builder_history"
#: How many edits a session can take back.
HISTORY_DEPTH = 30


def _download_name(name: str) -> str:
    """A file name for the export that every browser and file system takes."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower().translate(str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}))).strip("-")
    return f"{slug or 'arbeitsbereich'}-konfiguration.zip"


def create_builder_blueprint(
    gate,
    *,
    page_context: Callable[[], Mapping[str, Any]],
    store_factory: Optional[Callable[[], Any]] = None,
    tenant_provider: Optional[Callable[[], Optional[str]]] = None,
    engine_factory: Optional[Callable[[], Any]] = None,
    tenant_ontology: Optional[Callable[[], tuple]] = None,
    publish_sink: Optional[Callable[..., Any]] = None,
    source_secrets: Optional[Callable[[], Any]] = None,
    reader_factory: Optional[Callable[..., Any]] = None,
    platform_roles: Optional[Callable[[], list]] = None,
    roles_sink: Optional[Callable[..., Any]] = None,
    sync_status: Optional[Callable[[], Mapping[str, Any]]] = None,
):
    """Build the /builder blueprint.

    ``page_context`` returns what every builder page needs from the app:
    ``app_title``, ``brand``, ``asset_version``, ``platform_url`` (where
    "Zur Plattform" leads) and ``csrf_token`` (the import dialog sends it as
    the X-CSRF-Token header the app's gate checks).

    ``store_factory`` returns the workspace store for this request
    (``builder.store.WorkspaceStore`` on the request's connection). Without
    one there are no workspaces and the import is refused.

    ``tenant_provider`` returns the tenant this Platform belongs to (from its
    certificate). A new workspace can be linked to it; a tenant named in a
    request is never taken.

    ``tenant_ontology`` returns the tenant's node types and a function that
    reads one type's schema (the Platform's own client, on its certificate):
    a new workspace can start from what the tenant already has.

    ``publish_sink(bundle_text, source=, content_hash=)`` hands a published
    bundle to the views: the sync's source (brain_sync.SyncService.adopt), so
    the views read what was published.

    ``source_secrets`` returns the write-only store of the sources'
    credentials (``platform_settings.SourceSecrets`` for the Platform's
    tenant). ``reader_factory(component, source, secret, workspace_id)``
    returns the reader of a source through the component that serves it
    (``builder.sources``): the only way the builder reads a source.

    ``platform_roles`` returns the Platform's role keys (its role table);
    ``roles_sink(mapping, user)`` writes the published role-to-profile
    mapping to the setting ``views.profile_roles`` (step B9).

    ``sync_status()`` returns the Platform's sync as the Betrieb stage reads
    it (step B10): ``state`` (brain-runner's state of the mappings), ``runs``
    (the sync's run history) and ``evaluation``; None where nothing syncs.

    ``engine_factory`` returns the engine that compiles, dry-runs and applies
    a version (``builder.publisher.BrainRunnerEngine`` on the Platform's
    certificate). It acts on that certificate's tenant only, so a workspace
    linked to another tenant is refused before anything runs.
    """
    bp = Blueprint("builder", __name__, url_prefix="/builder")

    @bp.before_request
    def _require_builder_role():
        user = gate.current_user()
        if user is None:
            if request.path.startswith("/builder/api/"):
                return jsonify({"success": False, "error": "Nicht angemeldet."}), 401
            return redirect(url_for("login", next=request.full_path or "/builder"))
        if not user_may_build(user):
            # 403, as the console does: the person is signed in and the
            # builder is not a secret, so saying "not for you" is honest and
            # makes a missing role easy to diagnose.
            abort(403)
        return None

    @bp.app_template_filter("b_datetime")
    def _datetime(value):
        """ISO 8601 -> "30.10.2026, 14:03" in the server's time zone."""
        if not value:
            return "–"
        try:
            moment = datetime.fromisoformat(str(value))
        except ValueError:
            return str(value)
        if moment.tzinfo is not None:
            moment = moment.astimezone()
        return moment.strftime("%d.%m.%Y, %H:%M")

    @bp.app_template_filter("b_change_label")
    def _change_label(path):
        section, _, key = str(path).partition("/")
        element = {"ontology.types": "Typ", "ontology.relations": "Beziehung", "packs": "Paket",
                   "processes": "Prozess", "processes.rules": "Prozessregel", "sources": "Quelle", "mappings": "Zuordnung",
                   "spines": "Hierarchie", "pages": "Seite", "profiles": "Profil", "profile_roles": "Rolle"}.get(section)
        return f"{element} {key}" if element and key else CHANGE_LABELS.get(path, path)

    @bp.app_template_filter("b_version_tag")
    def _version_tag(workspace):
        parts = []
        if workspace.draft_number is not None:
            parts.append(Markup('<span class="b-tag b-tag--warning"><span class="b-tag-dot"></span>Entwurf {}</span>')
                         .format(workspace.draft_number))
        if workspace.published_number is not None:
            parts.append(Markup('<span class="b-tag b-tag--good"><span class="b-tag-dot"></span>Veröffentlicht {}</span>')
                         .format(workspace.published_number))
        return Markup(" ").join(parts) if parts else Markup('<span class="b-tag">Leer</span>')

    def _store():
        return store_factory() if store_factory is not None else None

    def _current_workspace(store):
        workspace_id = session.get(SESSION_WORKSPACE)
        if not workspace_id or store is None:
            return None
        workspace = store.get(workspace_id)
        if workspace is None:
            session.pop(SESSION_WORKSPACE, None)
        return workspace

    def _render(template: str, **values):
        store = _store()
        workspaces = store.list() if store is not None else []
        values.setdefault("current_workspace", _current_workspace(store))
        return render_template(
            template,
            **page_context(),
            stages=STAGES,
            me=gate.current_user(),
            workspaces=[{"id": w.id, "name": w.name,
                         "url": url_for("builder.workspace", workspace_id=w.id)} for w in workspaces],
            workspace_list=workspaces,
            import_enabled=store is not None,
            term_packs=[_pack_card(p) for p in shipped_packs.term_packs()],
            platform_tenant=_platform_tenant(),
            tenant_start=tenant_ontology is not None and _platform_tenant() is not None,
            **values,
        )

    def _platform_tenant() -> Optional[str]:
        try:
            tenant = tenant_provider() if tenant_provider is not None else None
        except Exception:  # noqa: BLE001 - no certificate here: the workspace is linked later
            logger.info("Builder: the Platform's tenant is not known")
            return None
        return str(tenant) if tenant else None

    def _pack_card(pack: Mapping[str, Any]) -> dict:
        return {"id": pack["id"], "version": str(pack["version"]), "name": dict(pack.get("name") or {}),
                "description": dict(pack.get("description") or {}), "types": len(pack.get("types") or []),
                "relations": len(pack.get("relations") or [])}

    @bp.route("/")
    def home():
        return redirect(url_for("builder.stage", slug=STAGES[0].slug))

    @bp.route("/<slug>")
    def stage(slug: str):
        current = STAGES_BY_SLUG.get(slug)
        if current is None:
            abort(404)
        number = STAGES.index(current) + 1
        if current is STAGES[0]:
            return _render("builder/workspaces.html", stage=current, stage_number=number)
        if current.slug == "pruefen":
            store = _store()
            workspace = _current_workspace(store)
            if workspace is not None:
                return _render("builder/review.html", stage=current, stage_number=number,
                               current_workspace=workspace, **_review_context(store, workspace))
        if current.slug == "betrieb":
            store = _store()
            workspace = _current_workspace(store)
            if workspace is not None and store.published_bundle(workspace.id) is not None:
                return _render("builder/operate.html", stage=current, stage_number=number,
                               current_workspace=workspace, **_operate_context(store, workspace))
        if current.slug == "ansichten":
            store = _store()
            workspace = _current_workspace(store)
            if workspace is not None:
                return _render("builder/views.html", stage=current, stage_number=number,
                               current_workspace=workspace, history=_history_counts(workspace.id),
                               brain=brain_views.build(store.model(workspace.id), roles=_roles()))
        if current.slug == "verbinden":
            return _render("builder/components.html", stage=current, stage_number=number,
                           **_components_context(_store()))
        if current.slug == "quellen":
            store = _store()
            workspace = _current_workspace(store)
            if workspace is not None:
                return _render("builder/sources.html", stage=current, stage_number=number,
                               current_workspace=workspace, history=_history_counts(workspace.id),
                               sources=_sources_view(store, store.model(workspace.id)))
        if current.slug == "prozesse":
            store = _store()
            workspace = _current_workspace(store)
            if workspace is not None:
                return _render("builder/processes.html", stage=current, stage_number=number,
                               current_workspace=workspace, history=_history_counts(workspace.id),
                               processes=processes.build(store.model(workspace.id)))
        if current.slug == "ontologie":
            store = _store()
            workspace = _current_workspace(store)
            if workspace is not None:
                return _render("builder/ontology.html", stage=current, stage_number=number,
                               current_workspace=workspace, history=_history_counts(workspace.id),
                               ontology=ontology_view.build(store.model(workspace.id)))
        return _render("builder/stage.html", stage=current, stage_number=number)

    # -------------------------------------------------------- workspaces

    def _workspace_or_404(store, workspace_id):
        workspace = store.get(workspace_id) if store is not None else None
        if workspace is None:
            abort(404)
        return workspace

    @bp.route("/w/<workspace_id>")
    def workspace(workspace_id: str):
        store = _store()
        current = _workspace_or_404(store, workspace_id)
        session[SESSION_WORKSPACE] = current.id
        model = store.model(current.id) or {}
        names = shipped_packs.names()
        return _render(
            "builder/workspace.html",
            stage=STAGES[0], stage_number=1, current_workspace=current,
            changes=store.changes(current.id, limit=10),
            workspace_packs=[{**entry, "name": names.get(entry["key"], {})} for entry in model.get("packs") or []],
            pack_updates={u["key"]: u for u in editing.pack_updates(model)},
        )

    @bp.route("/w/<workspace_id>/pakete/<pack_id>")
    def pack_update(workspace_id: str, pack_id: str):
        """What updating a pack would change, for review before it is applied."""
        store = _store()
        current = _workspace_or_404(store, workspace_id)
        model = store.model(current.id) or {}
        pack = shipped_packs.term_pack(pack_id)
        if pack is None:
            abort(404)
        taken = next((p for p in model.get("packs") or [] if p["key"] == pack_id), None)
        try:
            changes = editing.pack_changes(model, pack_id)
            refusal = None
        except editing.EditError as exc:
            changes, refusal = [], exc
        session[SESSION_WORKSPACE] = current.id
        return _render(
            "builder/pack_update.html", stage=STAGES[0], stage_number=1, current_workspace=current,
            pack=_pack_card(pack), taken=taken, diff=review.diff(changes), refusal=refusal,
            edit_url=url_for("builder.edit_ontology", workspace_id=current.id),
            done_url=url_for("builder.stage", slug="ontologie"),
        )

    @bp.route("/w/<workspace_id>/export")
    def export(workspace_id: str):
        store = _store()
        current = _workspace_or_404(store, workspace_id)
        data = package.zip_files(package.write_package(store.model(current.id)))
        logger.info("Builder export: workspace %s, %d bytes, sha256 %s",
                    current.id, len(data), hashlib.sha256(data).hexdigest()[:16])
        response = Response(data, mimetype="application/zip")
        response.headers["Content-Disposition"] = f'attachment; filename="{_download_name(current.name)}"'
        return response

    def _uploaded_package():
        """The uploaded zip read into the model, or a JSON refusal."""
        upload = request.files.get("package")
        if upload is None or not upload.filename:
            return None, (jsonify({"success": False, "error": "Bitte ein ZIP-Archiv wählen."}), 400)
        data = upload.read()
        try:
            result = package.read_package(package.files_from_zip(data))
        except package.PackageError as exc:
            # The message is ours; details may quote paths from the archive,
            # so they go to the person who uploaded it, never into the log.
            logger.info("Builder import refused: %d bytes, %s", len(data), type(exc).__name__)
            return None, (jsonify({"success": False, "error": exc.message, "details": exc.details}), 422)
        return (result, data), None

    @bp.route("/api/import/preview", methods=["POST"])
    def import_preview():
        if _store() is None:
            return jsonify({"success": False, "error": "Arbeitsbereiche sind hier nicht verfügbar."}), 503
        uploaded, refusal = _uploaded_package()
        if refusal:
            return refusal
        result, _ = uploaded
        return jsonify({"success": True, "summary": result.summary,
                        "suggested_name": result.summary.get("company") or ""})

    @bp.route("/api/import", methods=["POST"])
    def import_package():
        store = _store()
        if store is None:
            return jsonify({"success": False, "error": "Arbeitsbereiche sind hier nicht verfügbar."}), 503
        name = str(request.form.get("name", "") or "").strip()
        if not 1 <= len(name) <= 120:
            return jsonify({"success": False, "error": "Der Name braucht 1 bis 120 Zeichen."}), 400
        uploaded, refusal = _uploaded_package()
        if refusal:
            return refusal
        result, data = uploaded
        workspace_id = store.create_from_import(
            name=name, model=result.model, actor_id=gate.current_user().id,
            source={"kind": "zip", "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                    "types": result.summary["types"], "relations": result.summary["relations"]},
        )
        logger.info("Builder import: workspace %s from %d bytes, %d types, %d relations",
                    workspace_id, len(data), result.summary["types"], result.summary["relations"])
        session[SESSION_WORKSPACE] = workspace_id
        return jsonify({"success": True, "url": url_for("builder.workspace", workspace_id=workspace_id)}), 201

    @bp.route("/api/workspaces", methods=["POST"])
    def create_workspace():
        """A new, empty workspace: the company's name, no types yet."""
        store = _store()
        if store is None:
            return jsonify({"success": False, "error": "Arbeitsbereiche sind hier nicht verfügbar."}), 503
        name = str(request.form.get("name", "") or "").strip()
        if not 1 <= len(name) <= 120:
            return jsonify({"success": False, "error": "Der Name braucht 1 bis 120 Zeichen."}), 400
        start = request.form.get("start") or "blank"
        source = {"kind": "blank"}
        if start == "tenant":
            if tenant_ontology is None or _platform_tenant() is None:
                return jsonify({"success": False, "error": "Diese Plattform kennt ihren Mandanten nicht."}), 409
            try:
                node_types, schema_of = tenant_ontology()
                model, counts = tenant_reader.model_from_tenant(name, node_types or [], schema_of)
            except Exception as exc:  # noqa: BLE001 - the tenant could not be read; say so
                logger.warning("Builder: the tenant's ontology could not be read: %s", type(exc).__name__)
                return jsonify({"success": False, "error": "Die Ontologie des Mandanten ließ sich nicht lesen.",
                                "details": [type(exc).__name__]}), 502
            source = {"kind": "tenant", **counts}
        else:
            model = package.read_package(package.blank_package(name)).model
        chosen = [str(p) for p in request.form.getlist("packs") if str(p).strip()]
        try:
            for pack_id in dict.fromkeys(chosen):
                editing.apply(model, {"op": "pack.apply", "pack": pack_id})
        except editing.EditError as exc:
            return _refused(exc)
        # A workspace read from the tenant belongs to it.
        tenant = _platform_tenant() if start == "tenant" or request.form.get("tenant") == "link" else None
        workspace_id = store.create(name=name, model=model, actor_id=gate.current_user().id,
                                    source={**source, "packs": model.get("packs") or []}, tenant=tenant)
        logger.info("Builder workspace created: %s, %d packs, tenant linked: %s",
                    workspace_id, len(model.get("packs") or []), tenant is not None)
        session[SESSION_WORKSPACE] = workspace_id
        return jsonify({"success": True, "url": url_for("builder.workspace", workspace_id=workspace_id)}), 201

    # -------------------------------------------------------- editing

    def _history(workspace_id: str) -> dict:
        stacks = session.get(SESSION_HISTORY) or {}
        entry = stacks.get(workspace_id) or {}
        return {"undo": list(entry.get("undo") or []), "redo": list(entry.get("redo") or [])}

    def _save_history(workspace_id: str, history: dict) -> None:
        stacks = dict(session.get(SESSION_HISTORY) or {})
        stacks[workspace_id] = {"undo": history["undo"][-HISTORY_DEPTH:], "redo": history["redo"][-HISTORY_DEPTH:]}
        session[SESSION_HISTORY] = stacks

    def _history_counts(workspace_id: str) -> dict:
        history = _history(workspace_id)
        return {"undo": len(history["undo"]), "redo": len(history["redo"])}

    def _edited(store, workspace_id: str, model: dict, changes: list, what: str):
        logger.info("Builder edit: workspace %s, %s, %d elements", workspace_id, what, len(changes))
        # The page that edits says which view it redraws: the ontology (B3) or the processes (B6).
        wanted = request.args.get("view")
        if wanted == "processes":
            view = {"processes": processes.build(model)}
        elif wanted == "sources":
            view = {"sources": _sources_view(store, model)}
        elif wanted == "mapping":
            view = {"mapping": _mapping_view(model, request.args.get("mapping") or "")}
        elif wanted == "views":
            view = {"views": brain_views.build(model, roles=_roles())}
        else:
            view = {"ontology": ontology_view.build(model)}
        return jsonify({"success": True, "changes": len(changes),
                        "history": _history_counts(workspace_id), **view})

    def _refused(exc: editing.EditError):
        return jsonify({"success": False, "error": exc.message, "details": exc.details}), exc.status

    def _editable_workspace(workspace_id: str):
        store = _store()
        if store is None:
            abort(503)
        return store, _workspace_or_404(store, workspace_id)

    @bp.route("/api/w/<workspace_id>/ontology", methods=["POST"])
    def edit_ontology(workspace_id: str):
        """One edit of the draft's ontology (builder.editing), written with actor, before and after."""
        store, current = _editable_workspace(workspace_id)
        op = request.get_json(silent=True)
        try:
            _check_source_op(store, op)
            _check_role_op(op)
            model, changes, batch = store.edit(current.id, gate.current_user().id,
                                               lambda m: editing.apply(m, op))
        except editing.EditError as exc:
            return _refused(exc)
        if batch:
            history = _history(current.id)
            history["undo"].append(batch)
            history["redo"] = []
            _save_history(current.id, history)
        _forget_moved_credentials(current.id, changes)
        return _edited(store, current.id, model, changes, str(op.get("op")))

    def _step(workspace_id: str, take: str, give: str, step):
        store, current = _editable_workspace(workspace_id)
        history = _history(current.id)
        if not history[take]:
            return jsonify({"success": False, "error": "Es gibt nichts zurückzunehmen." if take == "undo"
                            else "Es gibt nichts wiederherzustellen."}), 409
        batch = history[take][-1]
        recorded = store.batch(current.id, batch)
        try:
            model, changes, _ = store.edit(current.id, gate.current_user().id, lambda m: step(m, recorded))
        except editing.EditError as exc:
            # The draft moved on under this edit (another session): it cannot be taken back as recorded.
            history[take].pop()
            _save_history(current.id, history)
            return _refused(exc)
        history[take].pop()
        history[give].append(batch)
        _save_history(current.id, history)
        return _edited(store, current.id, model, changes, take)

    # -------------------------------------------------------- views and roles (B9)

    def _roles() -> list:
        try:
            found = platform_roles() if platform_roles is not None else None
        except Exception as exc:  # noqa: BLE001 - the role table could not be read: the built-in four
            logger.warning("Builder: the Platform's roles are not readable (%s)", type(exc).__name__)
            found = None
        return sorted(str(r) for r in found) if found else list(PLATFORM_ROLES)

    def _check_role_op(op) -> None:
        """A role mapping names a role the Platform has."""
        if isinstance(op, Mapping) and op.get("op") == "roles.set" and op.get("role") not in _roles():
            raise editing.EditError(f"Die Rolle «{op.get('role')}» gibt es auf der Plattform nicht.", 404)

    @bp.route("/api/w/<workspace_id>/views/preview", methods=["POST"])
    def views_preview(workspace_id: str):
        """A profile's start view and one of its pages, drawn from sample data made from the workspace."""
        store, current = _editable_workspace(workspace_id)
        body = request.get_json(silent=True) or {}
        result = brain_views.preview(store.model(current.id), str(body.get("profile") or ""),
                                     view_id=body.get("view") or None, page_key=body.get("page") or None)
        if result is None:
            return jsonify({"success": False, "error": "Dieses Profil gibt es nicht."}), 404
        return jsonify({"success": True, "preview": result})

    def _publish_roles(model) -> dict:
        """The published role mapping becomes views.profile_roles; its failure is reported, the publish stands."""
        if "profile_roles" not in model:
            return {"written": False, "why": "Der Arbeitsbereich legt keine Rollen fest."}
        if roles_sink is None:
            return {"written": False, "why": "Diese Plattform schreibt keine Einstellungen."}
        try:
            roles_sink(brain_views.profile_roles(model), gate.current_user())
        except Exception as exc:  # noqa: BLE001
            logger.warning("Builder: views.profile_roles not written: %s", type(exc).__name__)
            return {"written": False, "why": type(exc).__name__}
        return {"written": True}

    # -------------------------------------------------------- operate (B10)

    def _editor_links(model) -> dict:
        emitted_by: dict[str, str] = {}
        for m in model.get("mappings") or []:
            d = m.get("definition") or {}
            for e in d.get("emits") or []:
                emitted_by.setdefault(str(e.get("type")), str(d.get("id")))
        ontology_url = url_for("builder.stage", slug="ontologie")
        views_url = url_for("builder.stage", slug="ansichten")
        return {"type": lambda key: f"{ontology_url}#type:{key}",
                "page": lambda key: f"{views_url}#page:{key}",
                "spine": lambda key: f"{views_url}#spine:{key}",
                "mapping": lambda type_key: url_for("builder.mapping_page", mapping_id=emitted_by[type_key])
                if type_key in emitted_by else None}

    def _sync() -> dict:
        try:
            found = sync_status() if sync_status is not None else None
        except Exception as exc:  # noqa: BLE001 - the screen says the sync could not be read
            logger.warning("Builder: the sync's status is not readable (%s)", type(exc).__name__)
            return {"error": type(exc).__name__}
        return dict(found or {})

    def _operate_context(store, workspace) -> dict:
        from datetime import timezone

        model = store.model(workspace.id)
        published = store.published_bundle(workspace.id)
        now = datetime.now(timezone.utc)
        tenant = _platform_tenant()
        engine = _engine()
        snapshot, read_error = {"nodes": [], "edges": []}, None
        if not workspace.tenant or workspace.tenant != tenant:
            read_error = "Der Arbeitsbereich ist nicht mit dem Mandanten dieser Plattform verbunden."
        elif engine is None or engine.missing():
            read_error = engine.missing() if engine is not None else "Diese Umgebung liest den Mandanten nicht."
        else:
            try:
                snapshot = engine.read(model, operate.read_types(model))
            except Exception as exc:  # noqa: BLE001 - the screen says the tenant could not be read
                logger.warning("Builder: the tenant could not be read for the Betrieb stage (%s)", type(exc).__name__)
                read_error = f"Der Mandant ließ sich nicht lesen ({type(exc).__name__})."
        graph = operate.Graph(snapshot)
        sync = _sync()
        runs = store.runs(workspace.id, limit=60)
        sync_runs = list(sync.get("runs") or [])
        lint = operate.lint(model, graph, now=now, links=_editor_links(model))
        logger.info("Builder Betrieb: workspace %s, %d nodes read, %d lint items", workspace.id, len(graph.nodes),
                    sum(v["count"] for v in lint.values()))
        return {
            "published": published, "read_error": read_error, "taken_at": graph.taken_at, "node_count": len(graph.nodes),
            "health": operate.health(model, graph, now=now, reviews=store.reviews(workspace.id),
                                     published_at=published.get("published_at"), evaluation=sync.get("evaluation")),
            "status": operate.mappings_status(model, state=sync.get("state"), sync_runs=sync_runs, builder_runs=runs),
            "sync": {"available": sync_status is not None and "error" not in sync, "error": sync.get("error")},
            "history": operate.history(runs, sync_runs, graph),
            "lint": lint,
            "coverage": operate.coverage(model, graph, request.args.get("hierarchie"), now=now),
        }

    @bp.route("/api/w/<workspace_id>/reviews", methods=["POST"])
    def record_review(workspace_id: str):
        """The owner has reviewed a type's health: its next review is due review_every later."""
        store = _store()
        current = _workspace_or_404(store, workspace_id)
        node_type = str((request.get_json(silent=True) or {}).get("type") or "")
        if node_type not in ((store.model(current.id).get("health") or {}).get("types") or {}):
            return jsonify({"success": False, "error": "Diesen Typ führt der Gesundheitsteil nicht."}), 404
        store.record_review(current.id, node_type, gate.current_user().id)
        logger.info("Builder: health review recorded, workspace %s", current.id)
        return jsonify({"success": True})

    # -------------------------------------------------------- components and sources (B7)

    def _components_context(store) -> dict:
        tenant = _platform_tenant()
        found = store.components(tenant) if store is not None and tenant else []
        return {"components": found, "component_kinds": source_kinds.COMPONENT_KINDS,
                "planned_components": source_kinds.PLANNED_COMPONENTS}

    def _secrets():
        return source_secrets() if source_secrets is not None else None

    def _sources_view(store, model) -> dict:
        tenant = _platform_tenant()
        components = {c["id"]: c for c in (store.components(tenant) if tenant else [])}
        secrets = _secrets()
        found = []
        for s in model.get("sources") or []:
            c = components.get(s.get("component"))
            kind = source_kinds.SOURCE_KINDS.get(s.get("kind"), {})
            found.append({**s, "kind_label": kind.get("label", s.get("kind")),
                          "component_name": c["name"] if c else None,
                          "component_kind": c["kind"] if c else None,
                          "credential_label": kind.get("credential"),
                          "credential_required": bool(kind.get("credential_required"))})
        kinds = {k: {"label": v["label"], "component": v["component"], "credential": v["credential"],
                     "credential_required": v["credential_required"],
                     "settings": [{"name": n, "label": l, "hint": h} for n, l, h in v["settings"]]}
                 for k, v in source_kinds.SOURCE_KINDS.items()}
        mapping_list = []
        for m in model.get("mappings") or []:
            d = m.get("definition") or {}
            mapping_list.append({"id": d.get("id"), "description": d.get("description") or "",
                                 "types": [e.get("type") for e in d.get("emits") or []],
                                 "source": (m.get("builder") or {}).get("source"),
                                 "extractor": (d.get("source") or {}).get("extractor"),
                                 "problems": len(mapping_rules.problems(model, m)),
                                 "url": url_for("builder.mapping_page", mapping_id=d.get("id"))})
        types = [{"key": t["key"], "name": (t.get("label") or {}).get("de") or t["key"]}
                 for t in (model.get("ontology") or {}).get("types") or [] if not t.get("retired")]
        return {"sources": found, "kinds": kinds, "planned": source_kinds.PLANNED_SOURCES, "mappings": mapping_list,
                "types": sorted(types, key=lambda t: t["key"]),
                "components": list(components.values()), "extractors": source_kinds.FILE_EXTRACTORS,
                "secrets": {"available": bool(secrets and secrets.available),
                            "reason": "" if secrets and secrets.available else
                            (secrets.reason if secrets else "Diese Plattform speichert keine Geheimnisse.")}}

    def _check_source_op(store, op) -> None:
        """A source names a component of this tenant's register that serves its kind."""
        if not isinstance(op, Mapping) or op.get("op") not in ("source.create", "source.update"):
            return
        component_id = op.get("component") if op["op"] == "source.create" else (op.get("fields") or {}).get("component")
        if component_id is None:
            return
        tenant = _platform_tenant()
        component = store.component(tenant, str(component_id)) if tenant else None
        if component is None:
            raise editing.EditError("Diese Komponente ist für den Mandanten nicht registriert.", 404)
        if op["op"] == "source.create":
            wanted = source_kinds.SOURCE_KINDS.get(op.get("kind"), {}).get("component")
            if wanted and wanted != component["kind"]:
                raise editing.EditError(f"Eine Quelle dieser Art liest nur ein {source_kinds.COMPONENT_KINDS[wanted]['label']}.")

    def _forget_moved_credentials(workspace_id: str, changes) -> None:
        """A source that moved to another address or repository loses its credential in the registry too."""
        from platform_settings import source_secret_key

        secrets = _secrets()
        for c in changes:
            before, after = c.get("before") or {}, c.get("after")
            if (secrets is not None and c["path"].startswith("sources/") and before.get("credential_set")
                    and (after is None or not after.get("credential_set"))):
                secrets.clear(source_secret_key(workspace_id, before["key"]), gate.current_user())

    # -------------------------------------------------------- mappings (B8)

    def _mapping_entry(model, mapping_id: str):
        return next((m for m in model.get("mappings") or [] if (m.get("definition") or {}).get("id") == mapping_id), None)

    def _mapping_view(model, mapping_id: str) -> dict:
        entry = _mapping_entry(model, mapping_id)
        if entry is None:
            return {"missing": True, "id": mapping_id}
        onto = mapping_rules.Ontology(model)
        defn = entry["definition"]
        builder_meta = entry.get("builder") or {}
        steps = builder_meta.get("steps") or {}
        var = str((defn.get("source") or {}).get("as") or mapping_rules.VAR)

        def target(emit_index, name, label, expr, kind, **extra):
            meta = steps.get(f"{emit_index}:{name}")
            return {"target": name, "label": label, "expression": expr or "", "kind": kind,
                    "field": (meta or {}).get("field") or mapping_rules.simple_field(expr, var),
                    "steps": (meta or {}).get("steps") or [], "custom": bool(expr) and not meta
                    and mapping_rules.simple_field(expr, var) is None, **extra}

        emits = []
        for i, em in enumerate(defn.get("emits") or []):
            t = em.get("type")
            facts = em.get("facts") or {}
            attrs = [a for a in onto.attributes(t)] if onto.live_type(str(t)) else []
            targets = [target(i, "key", "Schlüssel", em.get("key"), "key"),
                       target(i, "name", "Name", em.get("name"), "name"),
                       target(i, "description", "Beschreibung", em.get("description"), "description")]
            for a in attrs:
                spec = facts.get(a["name"]) or {}
                targets.append(target(i, f"fact:{a['name']}", a["name"], spec.get("value"), "fact",
                                      datatype=a.get("datatype") or "text", priority=spec.get("priority"),
                                      policy=spec.get("policy")))
            for name, spec in facts.items():
                if name not in {a["name"] for a in attrs}:
                    targets.append(target(i, f"fact:{name}", name, spec.get("value"), "fact", unknown=True))
            relations = [dict(target(i, f"relation:{j}", r.get("relation"), r.get("to_key"), "relation"),
                              relation=r.get("relation"), to_type=r.get("to_type"), direction=r.get("direction", "out"),
                              to_match=bool(r.get("to_match")))
                         for j, r in enumerate(em.get("relations") or [])]
            offered = [{"relation": r["key"], "direction": d, "to_type": other}
                       for r in onto.relations.values() if not r.get("retired")
                       for d, here, there in (("out", r.get("domain") or [], r.get("range") or []),
                                              ("in", r.get("range") or [], r.get("domain") or []))
                       if "*" in here or t in here
                       for other in (there if "*" not in there else [])]
            emits.append({"index": i, "type": t, "identify": em.get("identify") or [], "targets": targets,
                          "relations": relations, "offered": offered[:200],
                          "attributes": [a["name"] for a in attrs]})
        src = next((s for s in model.get("sources") or [] if s.get("key") == builder_meta.get("source")), None)
        return {"id": defn.get("id"), "file": entry.get("file"), "description": defn.get("description") or "",
                "source_spec": defn.get("source") or {}, "var": var, "source": src,
                "sources": [{"key": s["key"], "name": s["name"], "kind": s["kind"]} for s in model.get("sources") or []],
                "collection": builder_meta.get("collection"), "priority": builder_meta.get("priority", 10),
                "edit_policy": defn.get("edit_policy"), "deletion_guard": defn.get("deletion_guard") or {},
                "freshness": (defn.get("freshness") or {}).get("window"), "emits": emits,
                "problems": mapping_rules.problems(model, entry),
                "yaml": package.dump_yaml(mapping_rules._written(defn)).decode("utf-8"),
                "policies": mapping_rules.EDIT_POLICIES, "steps": mapping_rules.STEPS,
                "types": [{"key": k, "name": (v.get("label") or {}).get("de") or k}
                          for k, v in sorted(onto.types.items()) if not v.get("retired")]}

    @bp.route("/zuordnung/<mapping_id>")
    def mapping_page(mapping_id: str):
        store = _store()
        workspace = _current_workspace(store) if store is not None else None
        if workspace is None:
            return redirect(url_for("builder.stage", slug="quellen"))
        view = _mapping_view(store.model(workspace.id), mapping_id)
        if view.get("missing"):
            abort(404)
        stage = STAGES_BY_SLUG["quellen"]
        return _render("builder/mapping.html", stage=stage, stage_number=STAGES.index(stage) + 1,
                       current_workspace=workspace, history=_history_counts(workspace.id), mapping=view)

    def _mapping_run(store, current, model, mapping_id: str):
        entry = _mapping_entry(model, mapping_id)
        if entry is None:
            abort(404)
        source = next((s for s in model.get("sources") or []
                       if s.get("key") == (entry.get("builder") or {}).get("source")), None)
        if source is None:
            raise source_kinds.ReaderError("Wählen Sie die Quelle, über die die Zuordnung liest.")
        reader = _reader(store, current, source)
        return entry, reader

    @bp.route("/api/w/<workspace_id>/mappings/<mapping_id>/preview", methods=["POST"])
    def mapping_preview(workspace_id: str, mapping_id: str):
        """The fields of the items the mapping reads, and the first nodes, facts and edges it would make."""
        store, current = _editable_workspace(workspace_id)
        model = store.model(current.id)
        try:
            entry, reader = _mapping_run(store, current, model, mapping_id)
            items = mapping_rules.items(entry["definition"], reader)
            problems = mapping_rules.problems(model, entry)
            run = mapping_rules.run(entry["definition"], reader) if not [p for p in problems if p["severity"] == "critical"] else None
        except source_kinds.ReaderError as exc:
            return jsonify({"success": False, "error": str(exc)}), 502
        except Exception as exc:  # noqa: BLE001 - an expression brain-runner refuses at run time
            logger.info("Builder: mapping preview failed, %s", type(exc).__name__)
            return jsonify({"success": False, "error": f"Die Zuordnung lässt sich so nicht ausführen ({type(exc).__name__}).",
                            "details": [str(exc)[:300]]}), 422
        fields = source_kinds.profile(items)
        logger.info("Builder: mapping preview, %d items", len(items))
        return jsonify({"success": True, "fields": fields, "problems": problems,
                        "preview": mapping_rules.preview(run) if run is not None else None})

    @bp.route("/api/w/<workspace_id>/mappings/<mapping_id>/dry-run", methods=["POST"])
    def mapping_dry_run(workspace_id: str, mapping_id: str):
        """brain-runner's plan of the mapping against the tenant: added, modified, retired, drift, the deletion guard."""
        store = _store()
        ready, refusal = _publishable(store, workspace_id)
        if refusal:
            return refusal
        current, draft, tenant, engine = ready
        model = store.model(current.id)
        try:
            entry, reader = _mapping_run(store, current, model, mapping_id)
            if [p for p in mapping_rules.problems(model, entry) if p["severity"] == "critical"]:
                return jsonify({"success": False, "error": "Die Zuordnung hat noch Probleme; zuerst beheben."}), 409
            run = mapping_rules.run(entry["definition"], reader)
            plan = engine.mapping_plan(model, mapping_id, run)
        except source_kinds.ReaderError as exc:
            return jsonify({"success": False, "error": str(exc)}), 502
        except Exception as exc:  # noqa: BLE001 - the engine's failure is the run's result
            logger.warning("Builder mapping dry run failed: %s", type(exc).__name__)
            return jsonify({"success": False, "error": "Der Probelauf ist fehlgeschlagen.", "details": [str(exc)[:300]]}), 502
        summary = mapping_rules.plan_summary(plan, mapping_id)
        status = "refused" if summary["guard"]["tripped"] else "ok"
        store.record_run(current.id, kind="dry_run", actor_id=gate.current_user().id, tenant=tenant,
                         content_hash=draft["content_hash"], bundle_hash=None, status=status,
                         summary={"mapping": mapping_id, **{k: summary[k] for k in
                                                            ("added", "modified", "retired", "unchanged", "drift")},
                                  "guard_tripped": summary["guard"]["tripped"]})
        logger.info("Builder mapping dry run: %s added, %s modified, %s retired", summary["added"],
                    summary["modified"], summary["retired"])
        return jsonify({"success": True, "status": status, "summary": summary})

    @bp.route("/api/components", methods=["POST"])
    def register_component():
        """Register a sync component for the Platform's tenant (enrolment with its own certificate: B13)."""
        store = _store()
        if store is None:
            abort(503)
        tenant = _platform_tenant()
        if not tenant:
            return jsonify({"success": False, "error": "Diese Plattform kennt ihren Mandanten nicht."}), 409
        body = request.get_json(silent=True) or {}
        kind = body.get("kind")
        if kind not in source_kinds.COMPONENT_KINDS:
            return jsonify({"success": False, "error": "Art: eine der angebotenen; CRM- und Datenbank-Connectoren sind geplant."}), 400
        name = str(body.get("name") or "").strip()
        if not 1 <= len(name) <= 120:
            return jsonify({"success": False, "error": "Name: 1 bis 120 Zeichen."}), 400
        settings = {}
        base_url = str((body.get("settings") or {}).get("base_url") or "").strip()
        if base_url:
            if kind != "knovas_connector" or not re.match(r"^https?://[A-Za-z0-9.-]+(:\d+)?(/[\w./-]*)?$", base_url):
                return jsonify({"success": False, "error": "Adresse: http(s)://Host[:Port] des Connectors."}), 400
            settings["base_url"] = base_url
        new_id = store.register_component(tenant, kind=kind, name=name, site=str(body.get("site") or "").strip()[:120],
                                          owner=str(body.get("owner") or "").strip()[:120], settings=settings,
                                          actor_id=gate.current_user().id)
        if new_id is None:
            return jsonify({"success": False, "error": f"Eine Komponente «{name}» gibt es schon."}), 409
        logger.info("Builder: component registered, kind %s", kind)
        return jsonify({"success": True, "id": new_id, **_components_context(store)}), 201

    def _source_of(workspace_id: str, key: str):
        store, current = _editable_workspace(workspace_id)
        model = store.model(current.id)
        source = next((s for s in model.get("sources") or [] if s.get("key") == key), None)
        if source is None:
            abort(404)
        return store, current, model, source

    @bp.route("/api/w/<workspace_id>/sources/<key>/credential", methods=["POST"])
    def source_credential(workspace_id: str, key: str):
        """Write (or remove, with an empty value) a source's credential; it is never sent back."""
        from platform_settings import SettingError, source_secret_key

        store, current, _, source = _source_of(workspace_id, key)
        secrets = _secrets()
        if secrets is None:
            return jsonify({"success": False, "error": "Diese Plattform speichert keine Geheimnisse."}), 409
        value = str((request.get_json(silent=True) or {}).get("value") or "").strip()
        user = gate.current_user()
        try:
            if value:
                secrets.put(source_secret_key(current.id, key), value, user)
            else:
                secrets.clear(source_secret_key(current.id, key), user)
        except SettingError as exc:
            return jsonify({"success": False, "error": str(exc)}), getattr(exc, "status", 400)
        model, changes, _ = store.edit(current.id, user.id, lambda m: editing.apply(
            m, {"op": "source.credential", "key": key, "set": bool(value)}))
        logger.info("Builder: source credential %s", "set" if value else "removed")
        return jsonify({"success": True, "history": _history_counts(current.id), "sources": _sources_view(store, model)})

    def _reader(store, current, source):
        from platform_settings import source_secret_key

        tenant = _platform_tenant()
        component = store.component(tenant, source.get("component")) if tenant else None
        if component is None:
            raise source_kinds.ReaderError("Die Komponente dieser Quelle ist nicht registriert.")
        if reader_factory is None:
            raise source_kinds.ReaderError("Diese Plattform liest keine Quellen.")
        secrets = _secrets()
        secret = secrets.get(source_secret_key(current.id, source["key"])) if secrets and source.get("credential_set") else None
        kind = source_kinds.SOURCE_KINDS.get(source.get("kind"), {})
        if kind.get("credential_required") and not secret:
            raise source_kinds.ReaderError("Für diese Quelle ist noch kein Zugang gesetzt.")
        try:
            return reader_factory(component, source, secret, current.id)
        except source_kinds.ReaderError:
            raise
        except ImportError as exc:
            # The GitHub runner reads through brain-runner; an image built without it cannot.
            logger.warning("Builder: source reader unavailable, kind %s, %s", source.get("kind"), type(exc).__name__)
            raise source_kinds.ReaderError("Diese Installation kann die Quelle nicht lesen: brain-runner ist nicht "
                                           "installiert (das Image wurde ohne den Build-Schlüssel kb_read_token gebaut).") from None
        except Exception as exc:  # noqa: BLE001 - said on the page, never an HTML error page
            logger.warning("Builder: source reader not opened, kind %s, %s", source.get("kind"), type(exc).__name__)
            raise source_kinds.ReaderError(f"Die Quelle ließ sich nicht öffnen ({type(exc).__name__}).") from None

    def _read_failed(source, exc: Exception, what: str):
        """A read that failed, as the JSON the page shows; the log carries the kind and the error's type only."""
        if isinstance(exc, source_kinds.ReaderError):
            logger.info("Builder: source %s failed, kind %s", what, source.get("kind"))
            return jsonify({"success": False, "error": str(exc)}), 502
        logger.warning("Builder: source %s failed, kind %s, %s", what, source.get("kind"), type(exc).__name__)
        return jsonify({"success": False, "error": f"Die Quelle ließ sich nicht lesen ({type(exc).__name__})."}), 502

    @bp.route("/api/w/<workspace_id>/sources/<key>/test", methods=["POST"])
    def source_test(workspace_id: str, key: str):
        """Reach the source through its component and list what it holds, with counts."""
        store, current, _, source = _source_of(workspace_id, key)
        try:
            result = _reader(store, current, source).test()
        except Exception as exc:  # noqa: BLE001 - every failure is answered as JSON
            return _read_failed(source, exc, "test")
        # Counts only: what the source holds goes to the page, never into the log (GI-DATA-02/04).
        logger.info("Builder: source test, kind %s, %d collections", source.get("kind"), len(result["collections"]))
        return jsonify({"success": True, "result": result})

    @bp.route("/api/w/<workspace_id>/sources/<key>/sample", methods=["POST"])
    def source_sample(workspace_id: str, key: str):
        """Up to 50 items of one collection, with the profile of their fields."""
        store, current, _, source = _source_of(workspace_id, key)
        collection = str((request.get_json(silent=True) or {}).get("collection") or "")
        if not collection:
            return jsonify({"success": False, "error": "Welche Auswahl?"}), 400
        try:
            items = _reader(store, current, source).sample(collection, source_kinds.SAMPLE_LIMIT)
        except Exception as exc:  # noqa: BLE001 - every failure is answered as JSON
            return _read_failed(source, exc, "sample")
        logger.info("Builder: source sample, kind %s, %d items", source.get("kind"), len(items))
        return jsonify({"success": True, "collection": collection, "profile": source_kinds.profile(items)})

    @bp.route("/api/w/<workspace_id>/undo", methods=["POST"])
    def undo(workspace_id: str):
        return _step(workspace_id, "undo", "redo", editing.undo)

    @bp.route("/api/w/<workspace_id>/redo", methods=["POST"])
    def redo(workspace_id: str):
        return _step(workspace_id, "redo", "undo", editing.redo)

    # -------------------------------------------------------- review and publish (B5)

    def _engine():
        return engine_factory() if engine_factory is not None else None

    def _authors(rows) -> set:
        """Who made the draft's changes: its import or creation does not count."""
        return {str(r["actor"]) for r in rows if r.get("actor") and r["path"] not in review.ORIGINS}

    def _review_context(store, workspace) -> dict:
        draft = store.draft(workspace.id)
        rows = store.draft_changes(workspace.id) if draft else []
        author = request.args.get("autor") or None
        changes = review.change_set(rows, author=author)
        everything = review.change_set(rows) if author else changes
        runs = store.runs(workspace.id)
        content = draft["content_hash"] if draft else None
        # A mapping's dry run (B8) is not the draft's: it planned one mapping's items, not the publish.
        dry_run = next((r for r in runs if r["kind"] == "dry_run" and r["content_hash"] == content
                        and not (r.get("summary") or {}).get("mapping")), None)
        approvals = [a for a in store.approvals(workspace.id) if a["content_hash"] == content]
        me = str(gate.current_user().id)
        engine = _engine()
        return {
            "draft": draft, "changes": changes, "author": author, "authors": everything["authors"],
            "destructive": review.destructive(everything), "dry_run": dry_run, "approvals": approvals,
            "runs": runs[:10], "is_author": me in _authors(rows),
            "engine_missing": engine.missing() if engine is not None else "Keine Veröffentlichung in dieser Umgebung.",
            "engine_version": engine.version() if engine is not None else None,
            "published": store.published_bundle(workspace.id),
        }

    def _publishable(store, workspace_id: str):
        """The workspace, its draft and the tenant it publishes to -- or a refusal."""
        current = _workspace_or_404(store, workspace_id)
        draft = store.draft(current.id)
        if draft is None:
            return None, (jsonify({"success": False, "error": "Es gibt keinen Entwurf zu veröffentlichen."}), 409)
        tenant = _platform_tenant()
        if not current.tenant:
            return None, (jsonify({"success": False, "error": "Der Arbeitsbereich ist mit keinem Mandanten verbunden."}), 409)
        if current.tenant != tenant:
            # The engine acts with this Platform's certificate: it reaches only that tenant.
            return None, (jsonify({"success": False, "error": "Der Arbeitsbereich gehört zu einem anderen Mandanten "
                                   "als diese Plattform."}), 409)
        engine = _engine()
        missing = engine.missing() if engine is not None else "Keine Veröffentlichung in dieser Umgebung."
        if missing:
            return None, (jsonify({"success": False, "error": missing}), 503)
        return (current, draft, tenant, engine), None

    def _hash_of(body) -> str:
        return str((body or {}).get("content_hash") or "")

    @bp.route("/api/w/<workspace_id>/tenant", methods=["POST"])
    def link_tenant(workspace_id: str):
        """Link the workspace to this Platform's tenant (from its certificate, never from the request)."""
        store = _store()
        current = _workspace_or_404(store, workspace_id)
        tenant = _platform_tenant()
        if not tenant:
            return jsonify({"success": False, "error": "Diese Plattform kennt ihren Mandanten nicht."}), 409
        store.link_tenant(current.id, tenant)
        logger.info("Builder: workspace %s linked to the Platform's tenant", current.id)
        return jsonify({"success": True})

    @bp.route("/api/w/<workspace_id>/dry-run", methods=["POST"])
    def dry_run(workspace_id: str):
        """Compile the draft and plan it against the tenant; nothing is written there."""
        store = _store()
        ready, refusal = _publishable(store, workspace_id)
        if refusal:
            return refusal
        current, draft, tenant, engine = ready
        model = store.model(current.id)
        try:
            compiled = engine.compile(model)
            plan = engine.plan(model, tenant)
        except Exception as exc:  # noqa: BLE001 - the engine's failure is the run's result
            logger.warning("Builder dry run failed: workspace %s, %s", current.id, type(exc).__name__)
            store.record_run(current.id, kind="dry_run", actor_id=gate.current_user().id, tenant=tenant,
                             content_hash=draft["content_hash"], bundle_hash=None, status="failed",
                             summary={"error": type(exc).__name__})
            return jsonify({"success": False, "error": "Der Probelauf ist fehlgeschlagen.",
                            "details": [str(exc)[:500]]}), 502
        table = publisher.summarise_plan(plan)
        status = "refused" if table["counts"]["refused"] else "ok"
        store.record_run(current.id, kind="dry_run", actor_id=gate.current_user().id, tenant=tenant,
                         content_hash=draft["content_hash"], bundle_hash=compiled.bundle_hash, status=status,
                         summary={**table, "bundle_content_hash": compiled.content_hash,
                                  "engine": engine.version()})
        logger.info("Builder dry run: workspace %s, %s", current.id, table["counts"])
        return jsonify({"success": True, "status": status, **table})

    @bp.route("/api/w/<workspace_id>/approve", methods=["POST"])
    def approve(workspace_id: str):
        """A reviewer approves the draft as dry-run; never one of its authors."""
        store = _store()
        current = _workspace_or_404(store, workspace_id)
        draft = store.draft(current.id)
        body = request.get_json(silent=True) or {}
        if draft is None or _hash_of(body) != draft["content_hash"]:
            return jsonify({"success": False, "error": "Der Entwurf hat sich seit dem Laden geändert."}), 409
        me = str(gate.current_user().id)
        if me in _authors(store.draft_changes(current.id)):
            return jsonify({"success": False, "error": "Freigeben kann nur, wer an diesem Entwurf nichts geändert hat."}), 403
        if not any(r["kind"] == "dry_run" and r["content_hash"] == draft["content_hash"] and r["status"] != "failed"
                   for r in store.runs(current.id)):
            return jsonify({"success": False, "error": "Erst nach einem Probelauf dieses Stands."}), 409
        if not store.approve(current.id, gate.current_user().id, draft["content_hash"]):
            return jsonify({"success": False, "error": "Der Entwurf hat sich seit dem Laden geändert."}), 409
        logger.info("Builder approval: workspace %s", current.id)
        return jsonify({"success": True})

    @bp.route("/api/w/<workspace_id>/publish", methods=["POST"])
    def publish(workspace_id: str):
        """Apply the approved draft to the tenant and keep its bundle as the published version."""
        store = _store()
        ready, refusal = _publishable(store, workspace_id)
        if refusal:
            return refusal
        current, draft, tenant, engine = ready
        body = request.get_json(silent=True) or {}
        content = draft["content_hash"]
        if _hash_of(body) != content:
            return jsonify({"success": False, "error": "Der Entwurf hat sich seit dem Laden geändert."}), 409
        if not any(a["content_hash"] == content for a in store.approvals(current.id)):
            return jsonify({"success": False, "error": "Dieser Stand ist nicht freigegeben."}), 409
        destructive = sorted(g["path"] for g in review.destructive(review.change_set(store.draft_changes(current.id))))
        confirmed = sorted(str(p) for p in body.get("confirm_destructive") or [])
        if destructive and confirmed != destructive:
            return jsonify({"success": False, "error": "Stilllegungen brauchen eine eigene Bestätigung.",
                            "details": destructive}), 409
        model = store.model(current.id)
        try:
            compiled = engine.compile(model)
            plan = engine.plan(model, tenant)
            result = engine.apply(model, tenant, plan)
        except Exception as exc:  # noqa: BLE001 - the engine's failure is the run's result
            logger.warning("Builder apply failed: workspace %s, %s", current.id, type(exc).__name__)
            store.record_run(current.id, kind="apply", actor_id=gate.current_user().id, tenant=tenant,
                             content_hash=content, bundle_hash=None, status="failed",
                             summary={"error": type(exc).__name__})
            return jsonify({"success": False, "error": "Das Veröffentlichen ist fehlgeschlagen.",
                            "details": [str(exc)[:500]]}), 502
        errors = list(result.get("errors") or [])
        status = "failed" if errors else ("refused" if result.get("refused") else "ok")
        summary = {"written": result.get("written", 0), "refused": list(result.get("refused") or []),
                   "skipped": list(result.get("skipped") or [])[:200], "errors": [str(e)[:300] for e in errors][:50],
                   "plan": publisher.summarise_plan(plan)["counts"], "bundle_content_hash": compiled.content_hash,
                   "engine": engine.version()}
        number = None
        if status != "failed":
            number = store.publish(current.id, gate.current_user().id, content, compiled.bundle, compiled.bundle_hash)
            summary["published_number"] = number
        if number is not None:
            summary["run_event"] = _run_event(engine, model, number, status, summary)
            summary["views"] = _hand_to_views(compiled, number)
            summary["roles"] = _publish_roles(model)
        store.record_run(current.id, kind="apply", actor_id=gate.current_user().id, tenant=tenant,
                         content_hash=content, bundle_hash=compiled.bundle_hash, status=status, summary=summary)
        logger.info("Builder publish: workspace %s, status %s, %d written, %d refused, version %s",
                    current.id, status, summary["written"], len(summary["refused"]), number)
        return jsonify({"success": status != "failed", "status": status, "result": summary,
                        "error": "Das Brain hat Fehler gemeldet." if status == "failed" else None}), \
            (200 if status != "failed" else 502)

    def _run_event(engine, model, number, status, summary) -> dict:
        """The publish's RunEvent on the tenant; its failure does not undo the publish."""
        try:
            return engine.run_event(
                model, version=number, condition="Ready" if status == "ok" else "Degraded",
                reason="Published" if status == "ok" else "Refused",
                message=(f"{len(summary['refused'])} refused: " + ", ".join(summary["refused"][:20]))
                if summary["refused"] else "",
                observed=f"{summary['written']} written; {len(summary['refused'])} refused; "
                         f"{len(summary['skipped'])} skipped")
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            logger.warning("Builder RunEvent not written: %s", type(exc).__name__)
            return {"written": False, "key": None, "errors": [type(exc).__name__], "why": None}

    def _hand_to_views(compiled, number) -> dict:
        """Make the published bundle the views' source; a failure is reported, the publish stands."""
        if publish_sink is None:
            return {"adopted": False, "why": "Die Ansichten dieser Plattform lesen keinen Sync."}
        try:
            publish_sink(compiled.bundle_bytes.decode("utf-8"), source=f"builder-v{number}",
                         content_hash=compiled.content_hash)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Builder: the views did not take the bundle: %s", type(exc).__name__)
            return {"adopted": False, "why": type(exc).__name__}
        return {"adopted": True}

    @bp.route("/api/w/<workspace_id>/bundle")
    def published_bundle(workspace_id: str):
        """The bundle of the newest published version, as the views read it."""
        store = _store()
        current = _workspace_or_404(store, workspace_id)
        published = store.published_bundle(current.id)
        if published is None:
            abort(404)
        return jsonify(published["bundle"])

    @bp.route("/_styleguide")
    def styleguide():
        # ?ansicht=hell|dunkel shows one theme; the screenshots use it.
        view = request.args.get("ansicht", "beide")
        if view not in STYLEGUIDE_VIEWS:
            view = "beide"
        return _render("builder/styleguide.html", stage=None, stage_number=None, view=view)

    return bp
