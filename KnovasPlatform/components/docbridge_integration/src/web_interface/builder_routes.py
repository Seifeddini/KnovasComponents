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
``before_request`` on the blueprint looks up the permission the endpoint needs
(``ENDPOINT_PERMISSIONS``; an endpoint not listed needs ``manage``) and refuses
anyone whose roles -- their Platform roles, or their grant on the workspace the
request is about (step B11, builder.team) -- do not give it. A stage added
later is covered by default instead of by remembering.

Takes the gate and a page-context callable rather than importing ``app.py``,
so it can be exercised against a minimal Flask app, as the console is.
"""
from __future__ import annotations

import copy
import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Mapping, Optional

from flask import (Blueprint, Response, abort, g, jsonify, redirect, render_template, request,
                   session, url_for)
from markupsafe import Markup

from builder import editing, ontology_view, package, processes, publisher, review, tenant_definitions
from builder import tenant_ontology as tenant_reader
from builder import mappings as mapping_rules
from builder import views as brain_views
from builder import operate
from builder import sources as source_kinds
from builder import packs as shipped_packs
from builder import team
from builder import components as component_rules
from builder import protocol as connector_protocol
from builder.adapters import AdapterError
from builder import access as access_rules
from builder import sites as site_rules
from builder.access_edits import draft_tree

logger = logging.getLogger(__name__)

#: Roles that open the builder for every workspace (step B11: admin and the four builder roles).
BUILDER_ROLES = team.BUILDER_ROLES
#: The permission each endpoint needs (builder.team). Not listed: ``manage``.
#: Where components call. The page adds the address its browser reached: behind nginx the server's Host has no port.
PROTOCOL_PATH = "/api/connectors/v1"
ENDPOINT_PERMISSIONS = {
    "home": "view", "stage": "view", "workspace": "view", "pack_update": "view", "export": "view",
    "mapping_page": "view", "published_bundle": "view", "styleguide": "view", "views_preview": "view",
    "comments": "view", "activity": "view",
    "import_preview": "edit", "import_package": "edit", "create_workspace": "edit", "edit_ontology": "edit", "definitions_adopt": "edit", "adopt_definitions": "edit",
    "undo": "edit", "redo": "edit", "views_assistant": "edit", "brain_shape": "edit", "source_credential": "edit", "source_test": "edit", "source_sample": "edit",
    "mapping_preview": "edit", "mapping_dry_run": "edit", "dry_run": "edit",
    "add_comment": "comment", "resolve_comment": "resolve",
    "approve": "review", "record_review": "review",
    "publish": "publish", "link_tenant": "publish",
    "register_component": "manage", "team": "manage", "delete_workspace": "manage", "grant_access": "manage", "revoke_access": "manage",
    "component_page": "view", "enrol_component": "manage", "rotate_component": "manage", "revoke_component": "manage", "delete_component": "manage",
    "component_schedule": "manage", "component_settings": "manage", "component_run": "edit", "component_check": "edit",
    # Automatic apply of a source's clean plans (KG-2): whoever manages the tenant's sites and their schedules.
    "component_auto_apply": "manage",
    "component_pause": "publish", "component_resume": "publish", "component_plan": "review",
    "access_page": "edit", "access_preview": "edit", "access_apply": "edit", "access_rules_add": "edit",
    "access_rule_remove": "edit", "access_members": "manage", "access_explain": "manage", "access_posture": "manage",
    "access_posture_approve": "manage",
    # The connection layer (MVP-23..28): sites and their packages are managed; the connection card and «Freigaben» are
    # read with a Platform role; accepting or rejecting a write proposal needs publish (decision CL-4).
    "site_page": "manage", "create_site": "manage", "site_package": "manage", "site_rotate": "manage",
    "site_probe": "manage", "site_certificate": "manage",
    "connection_page": "view", "approvals_page": "view", "approvals_count": "view",
    "approval_accept": "publish", "approval_reject": "publish",
}
#: Endpoints about the workspace chosen in the session rather than one in the address.
SESSION_ENDPOINTS = frozenset({"stage", "mapping_page", "access_page", "access_preview", "access_apply",
                               "access_rules_add", "access_rule_remove"})
#: Reads that hand out a whole workspace: logged in the access log for everyone, a role holder too (BB-07, B7).
AUDITED_READS = frozenset({"export"})
#: Endpoints that need no workspace: a global role, or any running grant, opens them for reading.
GLOBAL_ENDPOINTS = frozenset({"home", "stage", "styleguide"})
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


def user_may_build(user, grants=()) -> bool:
    """Whether ``user`` holds a builder role, or a running grant on a workspace. ``None`` (nobody signed in) may not."""
    if user is None:
        return False
    from datetime import date

    found = team.access(getattr(user, "roles", ()) or (), list(grants or ()), None, date.today())
    return bool(found.permissions) or bool(found.workspaces)


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
    tenant_bundle: Optional[Callable[[], Any]] = None,
    publish_sink: Optional[Callable[..., Any]] = None,
    views_source: Optional[Callable[[], Any]] = None,
    source_secrets: Optional[Callable[[], Any]] = None,
    reader_factory: Optional[Callable[..., Any]] = None,
    user_directory: Optional[Callable[[], Any]] = None,
    component_secrets: Optional[Callable[[], Any]] = None,
    component_adapter: Optional[Callable[[Mapping[str, Any]], Any]] = None,
    component_limits: Optional[Callable[[], Mapping[str, Any]]] = None,
    issue_certificates: bool = True,
    access_backend: Optional[Callable[[], Any]] = None,
    platform_roles: Optional[Callable[[], list]] = None,
    roles_sink: Optional[Callable[..., Any]] = None,
    roles_source: Optional[Callable[[], Any]] = None,
    client_ca: Optional[Callable[[], Optional[str]]] = None,
    sync_status: Optional[Callable[[], Mapping[str, Any]]] = None,
    assistant_client: Optional[Callable[[], Optional[tuple]]] = None,
    tenant_graph: Optional[Callable[[], tuple]] = None,
    tenant_client: Optional[Callable[[], Any]] = None,
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
    ``tenant_bundle`` returns the definitions bundle the tenant's views read
    (graph_views.service.ViewService.bundle): such a workspace takes the
    tenant's processes, spines, pages and profiles from it. It may raise;
    then the workspace starts with the types alone and says so.

    ``publish_sink(bundle_text, source=, content_hash=)`` hands a published
    bundle to the views: the sync's source (brain_sync.SyncService.adopt), so
    the views read what was published. ``views_source`` returns the source the
    views read now: a configured one wins over the sync's, and the publish says so.

    ``source_secrets`` returns the write-only store of the sources'
    credentials (``platform_settings.SourceSecrets`` for the Platform's
    tenant). ``reader_factory(component, source, secret, workspace_id)``
    returns the reader of a source through the component that serves it
    (``builder.sources``): the only way the builder reads a source.

    ``user_directory`` returns the Platform's accounts (identity's
    UserRepository: ``get_by_email``, ``create``): inviting a consultant who
    has no account makes one (step B11).

    ``component_secrets`` returns the settings registry's store of the sync
    components' token hashes; ``component_adapter(component)`` the adapter
    over what a component that does not speak protocol v1 reports today
    (builder.adapters), or None; ``component_limits()`` the alert limits
    (builder.components.LIMITS) as the settings registry sets them (step B13).

    ``access_backend()`` returns the Brain's access groups and folder rules
    (builder.access_backend.BrainAccess on the Platform's client); the access
    window (step B14) changes the live tree through it.

    ``platform_roles`` returns the Platform's role keys (its role table);
    ``roles_sink(mapping, user)`` writes the published role-to-profile
    mapping to the setting ``views.profile_roles`` (step B9); ``roles_source()``
    reads what that setting holds now, so the dry run shows what publish
    replaces (BB-07, B1). Without it, the published version's mapping is the
    state compared with.

    ``sync_status()`` returns the Platform's sync as the Betrieb stage reads
    it (step B10): ``state`` (brain-runner's state of the mappings), ``runs``
    (the sync's run history) and ``evaluation``; None where nothing syncs.

    ``client_ca()`` returns Knovas's client CA (PEM, public), when configured: a site certificate recorded on the
    site page must be signed by it (BB-07 B3, S-4 M2).

    ``tenant_client()`` returns the Builder's line to the tenant's connection routes (builder.connection_api.
    BrainConnection on the Platform's certificate): write proposals for «Freigaben» and the job counts of the
    «Verbindung» card (connection layer MVP-27, MVP-28).

    ``engine_factory`` returns the engine that compiles, dry-runs and applies
    a version (``builder.publisher.BrainRunnerEngine`` on the Platform's
    certificate). It acts on that certificate's tenant only, so a workspace
    linked to another tenant is refused before anything runs.
    """
    bp = Blueprint("builder", __name__, url_prefix="/builder")

    def _grants_of(user) -> list:
        store = _store()
        if store is None or not hasattr(store, "grants_of"):
            return []
        return store.grants_of(user.id)

    def _access(user, workspace_id: Optional[str]) -> team.Access:
        from datetime import date

        return team.access(getattr(user, "roles", ()) or (), _grants_of(user), workspace_id, date.today())

    def _refuse(message: str, status: int = 403):
        if request.path.startswith("/builder/api/"):
            return jsonify({"success": False, "error": message}), status
        # 403, as the console does: the person is signed in and the builder is
        # not a secret, so saying "not for you" is honest and makes a missing
        # role easy to diagnose.
        return render_template("builder/refused.html", **page_context(), message=message), status

    @bp.before_request
    def _require_builder_role():
        user = gate.current_user()
        if user is None:
            if request.path.startswith("/builder/api/"):
                return jsonify({"success": False, "error": "Nicht angemeldet."}), 401
            return redirect(url_for("login", next=request.full_path or "/builder"))
        endpoint = (request.endpoint or "").rpartition(".")[2]
        needed = ENDPOINT_PERMISSIONS.get(endpoint, "manage")
        workspace_id = (request.view_args or {}).get("workspace_id")
        if workspace_id is None and endpoint in SESSION_ENDPOINTS:
            workspace_id = session.get(SESSION_WORKSPACE)
        found = _access(user, workspace_id)
        if workspace_id and endpoint in SESSION_ENDPOINTS and not found.may("view"):
            # The workspace chosen earlier is no longer open to this person: choose again.
            session.pop(SESSION_WORKSPACE, None)
            workspace_id, found = None, _access(user, None)
        g.builder_access = found
        g.builder_workspace = workspace_id
        if found.may(needed):
            return None
        if endpoint in GLOBAL_ENDPOINTS and needed == "view" and found.workspaces:
            return None   # a consultant: the stages, with only their workspace to choose
        if found.expired is not None:
            return _refuse(f"Ihr Zugang zu diesem Arbeitsbereich ist am {found.expired['ends_on']} abgelaufen.")
        if not found.permissions and not found.workspaces:
            return _refuse("Der Brain Builder braucht eine Builder-Rolle.")
        return _refuse(f"Dafür fehlt Ihnen die Berechtigung «{team.PERMISSION_LABELS[needed]}».")

    @bp.after_request
    def _log_access(response):
        """Every request of a grant's holder, every write of anyone, and every export: who, when, which action, the
        status."""
        found = g.get("builder_access")
        user = gate.current_user() if found is not None else None
        # A refused try on an access that has run out counts as a grant holder's request too.
        expired_try = found is not None and found.expired is not None and response.status_code == 403
        audited_read = (request.endpoint or "").rpartition(".")[2] in AUDITED_READS
        if user is None or (found.via != "grant" and request.method == "GET" and not expired_try and not audited_read):
            return response
        store = _store()
        if store is None or not hasattr(store, "log_access"):
            return response
        try:
            store.log_access(g.get("builder_workspace"), user_id=user.id, email=getattr(user, "email", "") or "",
                             method=request.method, action=(request.endpoint or "").rpartition(".")[2],
                             status=response.status_code, via=found.via or ("grant" if expired_try else "role"))
        except Exception as exc:  # noqa: BLE001 - the log never takes the request with it
            logger.warning("Builder: access not logged (%s)", type(exc).__name__)
        return response

    @bp.app_context_processor
    def _team_context():
        found = g.get("builder_access")
        if found is None:
            return {}
        return {"builder_access": found, "role_labels": team.ROLE_LABELS}

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
                   "processes": "Prozess", "processes.rules": "Prozessregel", "processes.report_rules": "Berichtsregel",
                   "sources": "Quelle", "mappings": "Zuordnung",
                   "spines": "Hierarchie", "pages": "Seite", "profiles": "Profil", "profile_roles": "Rolle",
                   "access.groups": "Zugriffsgruppe", "access.rules": "Ordnerregel"}.get(section)
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

    def _visible(workspaces) -> list:
        found = g.get("builder_access")
        if found is None or found.via == "role":
            return list(workspaces)
        return [w for w in workspaces if w.id in found.workspaces]

    def _render(template: str, **values):
        store = _store()
        workspaces = _visible(store.list()) if store is not None else []
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
            tenant_definitions=tenant_bundle is not None,
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
            return _render("builder/workspaces.html", stage=current, stage_number=number,
                           component_alerts=_component_overview(_store())["alerts"])
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
                               brain=brain_views.build(store.model(workspace.id), roles=_roles()),
                               assistant_ready=_assistant() is not None,
                               shape_ready=tenant_graph is not None and _platform_tenant() is not None)
        if current.slug == "verbinden":
            return _render("builder/components.html", stage=current, stage_number=number,
                           **_components_context(_store()), **_registry_context(_store()))
        if current.slug == "quellen":
            store = _store()
            workspace = _current_workspace(store)
            if workspace is not None:
                return _render("builder/sources.html", stage=current, stage_number=number,
                               current_workspace=workspace, history=_history_counts(workspace.id),
                               sources=_sources_view(store, store.model(workspace.id), workspace.id))
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

    @bp.route("/api/w/<workspace_id>/delete", methods=["POST"])
    def delete_workspace(workspace_id: str):
        """Delete a workspace: gone from every list and page, its history kept. The name, typed again, confirms."""
        store = _store()
        current = _workspace_or_404(store, workspace_id)
        typed = str((request.get_json(silent=True) or {}).get("name") or "").strip()
        if typed != current.name.strip():
            return jsonify({"success": False, "error": "Bitte den Namen des Arbeitsbereichs genau so eingeben."}), 400
        if not store.delete(current.id, gate.current_user().id):
            abort(404)
        logger.info("Builder workspace deleted: %s", current.id)
        if session.get(SESSION_WORKSPACE) == current.id:
            session.pop(SESSION_WORKSPACE, None)
        return jsonify({"success": True, "url": url_for("builder.stage", slug=STAGES[0].slug)})

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

    def _tenant_package(current) -> dict:
        """The package the tenant's definitions bundle makes (builder.tenant_definitions), for this workspace.

        The bundle is this Platform's tenant's, so a workspace linked to another tenant is refused.
        """
        if tenant_bundle is None or _platform_tenant() is None:
            raise editing.EditError("Diese Plattform liest kein Definitionsbündel.", 409)
        if current.tenant and current.tenant != _platform_tenant():
            raise editing.EditError("Der Arbeitsbereich gehört zu einem anderen Mandanten.", 409)
        try:
            bundle = tenant_bundle()
        except Exception as exc:  # noqa: BLE001 - no source set, unreachable or refused: say so
            logger.warning("Builder: the tenant's definitions bundle could not be read: %s", type(exc).__name__)
            raise editing.EditError("Das Definitionsbündel des Mandanten ließ sich nicht lesen.", 502,
                                    [type(exc).__name__])
        try:
            return tenant_definitions.model_from_bundle(current.name, bundle).model
        except package.PackageError as exc:
            raise editing.EditError(exc.message, 422, exc.details)

    def _adoption_counts(changes) -> dict:
        """What an adoption adds or completes, per kind: {"7 Prozesse": ..} as the review page lists them."""
        added: dict[str, int] = {}
        completed: dict[str, int] = {}
        for c in changes:
            section = c["path"].split("/", 1)[0]
            label = review.SECTIONS.get(section, section)
            if c["before"] is None:
                added[label] = added.get(label, 0) + 1
            else:
                completed[label] = completed.get(label, 0) + 1
        return {**added, **{f"{label} ergänzt": n for label, n in completed.items()}}

    @bp.route("/w/<workspace_id>/definitionen")
    def definitions_adopt(workspace_id: str):
        """What adopting the tenant's definitions would add to the draft, for review before it is applied (BB-12)."""
        store, current = _editable_workspace(workspace_id)
        model = store.model(current.id) or {}
        try:
            source = _tenant_package(current)
            changes = editing.apply(copy.deepcopy(model), {"op": "definitions.adopt", "package": source})
            refusal = None
        except editing.EditError as exc:
            changes, refusal = [], exc
        session[SESSION_WORKSPACE] = current.id
        return _render(
            "builder/definitions_adopt.html", stage=STAGES[0], stage_number=1, current_workspace=current,
            diff=review.diff(changes), counts=_adoption_counts(changes), refusal=refusal,
            adopt_url=url_for("builder.adopt_definitions", workspace_id=current.id),
            done_url=url_for("builder.stage", slug="prozesse"),
        )

    @bp.route("/api/w/<workspace_id>/definitions/adopt", methods=["POST"])
    def adopt_definitions(workspace_id: str):
        """Adopt the tenant's definitions into the draft: one edit, logged and undoable, read from the bundle here."""
        store, current = _editable_workspace(workspace_id)
        try:
            source = _tenant_package(current)
            model, changes, batch = store.edit(current.id, gate.current_user().id,
                                               lambda m: editing.apply(m, {"op": "definitions.adopt", "package": source}))
        except editing.EditError as exc:
            return _refused(exc)
        if batch:
            history = _history(current.id)
            history["undo"].append(batch)
            history["redo"] = []
            _save_history(current.id, history)
        return _edited(store, current.id, model, changes, "definitions.adopt")

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
        bound = _site_bound(store, {}, {str(s.get("key")): s for s in result.model.get("sources") or []})
        if bound and not g.builder_access.may(SITE_BINDING_PERMISSION):
            # An import binds its sources as an edit does (BB-07, B2): to a site's component only for who manages sites.
            return jsonify({"success": False, "error": SITE_BINDING_REFUSED, "details": bound}), 403
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
            bundle, bundle_error = None, None
            if tenant_bundle is not None:
                try:
                    bundle = tenant_bundle()
                except Exception as exc:  # noqa: BLE001 - no source set, unreachable or refused: the types still come
                    bundle_error = type(exc).__name__
                    logger.warning("Builder: the tenant's definitions bundle could not be read: %s", bundle_error)
            try:
                node_types, schema_of = tenant_ontology()
                model, counts = tenant_reader.model_from_tenant(name, node_types or [], schema_of, bundle=bundle)
            except Exception as exc:  # noqa: BLE001 - the tenant could not be read; say so
                logger.warning("Builder: the tenant's ontology could not be read: %s", type(exc).__name__)
                return jsonify({"success": False, "error": "Die Ontologie des Mandanten ließ sich nicht lesen.",
                                "details": [type(exc).__name__]}), 502
            source = {"kind": "tenant", **counts}
            if bundle_error:
                source["definitions_error"] = bundle_error
        else:
            model = package.read_package(package.blank_package(name)).model
        chosen = [str(p) for p in request.form.getlist("packs") if str(p).strip()]
        try:
            for pack_id in dict.fromkeys(chosen):
                # The tenant's types do not say which pack they came from: a pack takes over its own by name.
                editing.apply(model, {"op": "pack.apply", "pack": pack_id, "take_over": start == "tenant"})
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
            view = {"sources": _sources_view(store, model, workspace_id)}
        elif wanted == "mapping":
            view = {"mapping": _mapping_view(model, request.args.get("mapping") or "")}
        elif wanted == "views":
            view = {"views": brain_views.build(model, roles=_roles())}
        else:
            view = {"ontology": ontology_view.build(model)}
        # What an edit did beside what was asked, in the same change (a fact taken out of «Zurückschreiben», MVP-26).
        notices = [c["notice"] for c in changes if c.get("notice")]
        return jsonify({"success": True, "changes": len(changes), "notices": notices,
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
            _check_switch_op(store, current, op)
            _check_role_op(op)
            model, changes, batch = store.edit(current.id, gate.current_user().id,
                                               _guard_site_bindings(store, lambda m: editing.apply(m, op)))
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
            model, changes, _ = store.edit(current.id, gate.current_user().id,
                                           _guard_site_bindings(store, lambda m: step(m, recorded)))
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

    def _brain_shape(store, current):
        """The relations and hierarchies the tenant's links show (builder.brain_shape), read through this Platform's
        certificate; a workspace of another tenant is refused (the tenant comes from the certificate only)."""
        from builder import brain_shape

        if tenant_graph is None or _platform_tenant() is None:
            raise editing.EditError("Diese Plattform liest keinen Mandanten.", 409)
        if current.tenant and current.tenant != _platform_tenant():
            raise editing.EditError("Dieser Arbeitsbereich gehört zu einem anderen Mandanten.", 409)
        try:
            node_types, nodes, edges = tenant_graph()
        except Exception as exc:  # noqa: BLE001 - the Brain could not be read: said, not raised
            logger.warning("Builder brain shape: the tenant's graph is not readable (%s)", type(exc).__name__)
            raise editing.EditError("Das Brain des Mandanten ist gerade nicht lesbar.", 502)
        shape = brain_shape.derive(store.model(current.id), node_types or [], nodes or [], edges or [])
        logger.info("Builder brain shape: workspace %s, %d edges, %d relation and %d hierarchy proposals",
                    current.id, shape.counts.get("edges", 0), len(shape.relations), len(shape.hierarchies))
        return shape

    @bp.route("/api/w/<workspace_id>/brain-shape", methods=["GET", "POST"])
    def brain_shape(workspace_id: str):
        """GET: what the tenant's links show, as proposals. POST {chosen: [ids]}: take those over into the draft,
        derived again here (the browser sends ids, never operations), as one batch."""
        from builder import brain_shape as shapes

        store, current = _editable_workspace(workspace_id)
        try:
            shape = _brain_shape(store, current)
        except editing.EditError as exc:
            return _refused(exc)
        if request.method == "GET":
            return jsonify({"success": True, **shapes.as_view(shape)})
        chosen = (request.get_json(silent=True) or {}).get("chosen") or []
        ops = shapes.operations(shape, [str(c) for c in chosen if isinstance(c, str)])
        if not ops:
            return jsonify({"success": False, "error": "Nichts ausgewählt."}), 400
        try:
            model, changes, batch = store.edit(current.id, gate.current_user().id,
                                               lambda m: [c for op in ops for c in editing.apply(m, op)])
        except editing.EditError as exc:
            return _refused(exc)
        if batch:
            history = _history(current.id)
            history["undo"].append(batch)
            history["redo"] = []
            _save_history(current.id, history)
        return jsonify({"success": True, "changes": len(changes), "history": _history_counts(current.id),
                        "views": brain_views.build(model, roles=_roles())})

    def _assistant():
        """(client, model name) of the views assistant, or None: no API key in the settings registry."""
        if assistant_client is None:
            return None
        try:
            return assistant_client()
        except Exception as exc:  # noqa: BLE001 - a broken setting reads as "not set up", said on the page
            logger.warning("Builder: the views assistant cannot be set up (%s)", type(exc).__name__)
            return None

    @bp.route("/api/w/<workspace_id>/views/assistant", methods=["POST"])
    def views_assistant(workspace_id: str):
        """Views by description: Claude tries operations on a copy of the draft until the checks are clean; the
        ones it kept are applied here as one batch (one undo). The description is never logged."""
        from builder import assistant

        store, current = _editable_workspace(workspace_id)
        found = _assistant()
        if not found:
            return jsonify({"success": False, "error": "Der Assistent ist nicht eingerichtet: in den Einstellungen "
                            "fehlt der Schlüssel für die Claude API."}), 503
        client, model_name = found
        body = request.get_json(silent=True) or {}
        try:
            result = assistant.answer(client, model_name, store.model(current.id), str(body.get("message") or ""),
                                      history=body.get("history"), roles=_roles())
        except assistant.AssistantError as exc:
            return jsonify({"success": False, "error": exc.message}), exc.status
        changes, batch = [], None
        if result.operations:
            try:
                model, changes, batch = store.edit(current.id, gate.current_user().id,
                                                   lambda m: assistant.apply_all(m, result.operations))
            except editing.EditError as exc:
                return jsonify({"success": False, "error": "Der Entwurf hat sich inzwischen geändert; "
                                "beschreiben Sie es bitte noch einmal.", "details": [exc.message]}), 409
        else:
            model = store.model(current.id)
        if batch:
            history = _history(current.id)
            history["undo"].append(batch)
            history["redo"] = []
            _save_history(current.id, history)
        logger.info("Builder views assistant: workspace %s, %d rounds, %d operations, %d changes",
                    current.id, result.rounds, len(result.operations), len(changes))
        return jsonify({"success": True, "reply": result.reply, "operations": len(result.operations),
                        "changes": len(changes), "history": _history_counts(current.id),
                        "views": brain_views.build(model, roles=_roles())})

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

    def _roles_effect(model, base) -> Optional[dict]:
        """What publish would write as the role mapping (views.profile_roles, replaced whole) against what the
        Platform holds now (``roles_source``), else against the published version's mapping; None when the draft
        sets no roles."""
        if "profile_roles" not in model:
            return None
        if roles_sink is None:
            return {"error": "Diese Plattform schreibt keine Einstellungen."}
        current, unknown = brain_views.profile_roles(base or {}), False
        if roles_source is not None:
            try:
                found = roles_source()
                if isinstance(found, str):
                    import json as _json

                    found = _json.loads(found) if found.strip() else None
                if isinstance(found, Mapping):
                    current = dict(found)
                elif not found:
                    current = {}            # the setting is empty: every role sees every profile today
                else:
                    unknown = True
            except Exception as exc:  # noqa: BLE001 - compared with the published version, and said so
                logger.warning("Builder: views.profile_roles not readable for the plan (%s)", type(exc).__name__)
                unknown = True
        plan = review.roles_plan(current, brain_views.profile_roles(model))
        if unknown:
            plan["current_unknown"] = True
        return plan

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

    # -------------------------------------------------------- the team (B11)

    def _comment_threads(store, workspace_id: str) -> dict:
        """{target: {"comments": [...], "open": n}} for the review page."""
        threads: dict = {}
        for c in store.comments(workspace_id):
            t = threads.setdefault(c["target"], {"comments": [], "open": 0})
            t["comments"].append(c)
            t["open"] += 0 if c["resolved_at"] else 1
        return threads

    def _comment_view(store, workspace_id: str, target: str) -> list:
        """A target's comments, each saying whether the element changed after it was written (addressed?)."""
        changed = max((c["at"] for c in store.changes(workspace_id, limit=500) if c["path"] == target), default=None)
        return [{**c, "changed_since": bool(changed and changed > c["at"]),
                 "mine": str(c.get("author")) == str(gate.current_user().id)} for c in store.comments(workspace_id, target)]

    def _target(value) -> str:
        target = str(value or "").strip()
        if not 1 <= len(target) <= 300 or any(ch in target for ch in "\n\r\t"):
            raise ValueError("Unbekanntes Element.")
        return target

    @bp.route("/api/w/<workspace_id>/comments")
    def comments(workspace_id: str):
        """The comments on one element of the workspace (a change-set item or a canvas element)."""
        store = _store()
        current = _workspace_or_404(store, workspace_id)
        try:
            target = _target(request.args.get("target"))
        except ValueError as exc:
            return jsonify({"success": False, "error": str(exc)}), 400
        found = g.get("builder_access")
        return jsonify({"success": True, "comments": _comment_view(store, current.id, target),
                        "may": {"comment": found.may("comment"), "resolve": found.may("resolve")}})

    @bp.route("/api/w/<workspace_id>/comments", methods=["POST"])
    def add_comment(workspace_id: str):
        store = _store()
        current = _workspace_or_404(store, workspace_id)
        body = request.get_json(silent=True) or {}
        try:
            target = _target(body.get("target"))
        except ValueError as exc:
            return jsonify({"success": False, "error": str(exc)}), 400
        text = str(body.get("body") or "").strip()
        if not 1 <= len(text) <= 4000:
            return jsonify({"success": False, "error": "Ein Kommentar hat 1 bis 4000 Zeichen."}), 400
        store.add_comment(current.id, target, text, gate.current_user().id)
        logger.info("Builder: comment added, workspace %s", current.id)   # never the text (GI-DATA-02/04)
        return jsonify({"success": True, "comments": _comment_view(store, current.id, target)}), 201

    @bp.route("/api/w/<workspace_id>/comments/<comment_id>/resolve", methods=["POST"])
    def resolve_comment(workspace_id: str, comment_id: str):
        store = _store()
        current = _workspace_or_404(store, workspace_id)
        if not store.resolve_comment(current.id, comment_id, gate.current_user().id):
            return jsonify({"success": False, "error": "Diesen offenen Kommentar gibt es nicht."}), 404
        target = next((c["target"] for c in store.comments(current.id) if c["id"] == comment_id), "")
        logger.info("Builder: comment resolved, workspace %s", current.id)
        return jsonify({"success": True, "comments": _comment_view(store, current.id, target)})

    def _grant_state(grant: Mapping[str, Any]) -> str:
        from datetime import date

        if grant.get("revoked_at"):
            return "revoked"
        return "active" if date.fromisoformat(str(grant["ends_on"])[:10]) >= date.today() else "expired"

    @bp.route("/w/<workspace_id>/team", endpoint="team")
    def team_page(workspace_id: str):
        """Who may work on the workspace: the grants (consultants), and the access log."""
        store = _store()
        current = _workspace_or_404(store, workspace_id)
        grants = [{**gr, "state": _grant_state(gr)} for gr in store.access_grants(current.id)]
        from datetime import date, timedelta

        return _render("builder/team.html", stage=STAGES[0], stage_number=1, current_workspace=current,
                       grants=grants, log=store.access_log(current.id, limit=100), roles=team_roles(),
                       default_end=(date.today() + timedelta(days=30)).isoformat(),
                       accounts=user_directory is not None)

    def team_roles() -> list:
        return [{"key": r, "label": team.ROLE_LABELS[r]} for r in team.ROLES]

    @bp.route("/api/w/<workspace_id>/access", methods=["POST"])
    def grant_access(workspace_id: str):
        """Invite someone by e-mail to this workspace only, with roles and the day the access ends."""
        import secrets as _secrets
        from datetime import date

        store = _store()
        current = _workspace_or_404(store, workspace_id)
        body = request.get_json(silent=True) or {}
        email = str(body.get("email") or "").strip().lower()
        if not re.match(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}$", email):
            return jsonify({"success": False, "error": "E-Mail: eine Adresse wie name@firma.ch."}), 400
        try:
            roles, ends_on = team.check_grant(body.get("roles"), body.get("ends_on"), date.today())
        except ValueError as exc:
            return jsonify({"success": False, "error": str(exc)}), 400
        if user_directory is None:
            return jsonify({"success": False, "error": "Diese Plattform legt keine Konten an."}), 503
        directory = user_directory()
        me = gate.current_user()
        user = directory.get_by_email(email)
        password = None
        if user is None:
            # A new account for the consultant: a one-time password, shown once, changed at the first sign-in.
            password = _secrets.token_urlsafe(18)
            name = str(body.get("name") or "").strip()[:120] or email
            user = directory.create(email=email, display_name=name, password=password, must_change_password=True,
                                    created_by=me.id)
        if str(user.id) == str(me.id):
            return jsonify({"success": False, "error": "Sich selbst lädt man nicht ein."}), 400
        grant_id = store.grant_access(current.id, user_id=user.id, email=email, roles=roles, ends_on=ends_on,
                                      actor_id=me.id)
        logger.info("Builder: access granted on workspace %s, %d roles, new account: %s", current.id, len(roles),
                    password is not None)
        return jsonify({"success": True, "id": grant_id, "email": email, "password": password, "ends_on": ends_on}), 201

    @bp.route("/api/w/<workspace_id>/access/<grant_id>/revoke", methods=["POST"])
    def revoke_access(workspace_id: str, grant_id: str):
        store = _store()
        current = _workspace_or_404(store, workspace_id)
        if not store.revoke_access(current.id, grant_id, gate.current_user().id):
            return jsonify({"success": False, "error": "Diesen Zugang gibt es nicht mehr."}), 404
        logger.info("Builder: access revoked on workspace %s", current.id)
        return jsonify({"success": True})

    @bp.route("/w/<workspace_id>/aktivitaet")
    def activity(workspace_id: str):
        """The workspace's activity: who changed what, when -- edits, runs, approvals, comments, access."""
        store = _store()
        current = _workspace_or_404(store, workspace_id)
        session[SESSION_WORKSPACE] = current.id
        feed = [{"at": c["at"], "who": c.get("actor_name") or "–", "kind": "change", "path": c["path"]}
                for c in store.changes(current.id, limit=200)]
        feed += [{"at": r["at"], "who": r.get("actor_name") or "–", "kind": r["kind"], "status": r["status"],
                  "summary": r.get("summary") or {}} for r in store.runs(current.id, limit=60)]
        feed += [{"at": a["at"], "who": a.get("reviewer_name") or "–", "kind": "approval", "own_changes": a.get("own_changes"),
                  "own_import": a.get("own_import")}
                 for a in store.approval_history(current.id)]
        for c in store.comments(current.id):
            feed.append({"at": c["at"], "who": c.get("author_name") or "–", "kind": "comment", "path": c["target"]})
            if c["resolved_at"]:
                feed.append({"at": c["resolved_at"], "who": c.get("resolved_by_name") or "–", "kind": "resolved",
                             "path": c["target"]})
        for gr in store.access_grants(current.id):
            feed.append({"at": gr["invited_at"], "who": gr.get("invited_by_name") or "–", "kind": "granted",
                         "email": gr["email"], "roles": gr["roles"], "ends_on": gr["ends_on"]})
            if gr.get("revoked_at"):
                feed.append({"at": gr["revoked_at"], "who": gr.get("revoked_by_name") or "–", "kind": "revoked",
                             "email": gr["email"]})
        feed.sort(key=lambda x: str(x.get("at") or ""), reverse=True)
        return _render("builder/activity.html", stage=STAGES[0], stage_number=1, current_workspace=current,
                       feed=feed[:300], role_labels=team.ROLE_LABELS)

    # -------------------------------------------------------- sync components (B13)

    def _limits() -> dict:
        found = dict(component_rules.LIMITS)
        try:
            found.update({k: v for k, v in (component_limits() if component_limits else {}).items() if v})
        except Exception as exc:  # noqa: BLE001 - the defaults hold
            logger.warning("Builder: component limits not readable (%s)", type(exc).__name__)
        return found

    def _component_secrets():
        if component_secrets is None:
            raise connector_protocol.ProtocolError(503, "no_secrets", "Diese Plattform speichert keine Geheimnisse.")
        found = component_secrets()
        if not getattr(found, "available", True):
            raise connector_protocol.ProtocolError(503, "no_secrets", found.reason)
        return found

    def _adapter(component: Mapping[str, Any]):
        if component_rules.on_site(component) or component_adapter is None:
            return None
        try:
            return component_adapter(component)
        except Exception as exc:  # noqa: BLE001 - shown as offline
            logger.warning("Builder: no adapter for a component (%s)", type(exc).__name__)
            return None

    def _observed(component: Mapping[str, Any]) -> tuple[dict, list, list]:
        """The component as the registry shows it, its runs and its held plans: from its own reports (v1), or
        through its adapter (what it reports today), or -- the HTTP/JSON reader, part of the Platform -- online."""
        store = _store()
        if component_rules.on_site(component):
            return dict(component), store.component_runs(component["id"]), store.held_plans(component["id"])
        adapter = _adapter(component)
        now = datetime.now().astimezone().isoformat()
        if adapter is None:
            return {**component, "last_heartbeat_at": now, "reported_state": "online"}, [], []
        seen = adapter.observe()
        merged = {**component, "last_heartbeat_at": seen.get("heartbeat_at") if seen.get("reachable") else None,
                  "reported_state": seen.get("reported_state"), "version": seen.get("version") or component.get("version"),
                  "open_errors": seen.get("open_errors", 0), "lag_seconds": seen.get("lag_seconds"),
                  "paused": bool(seen.get("paused")) or component.get("paused"),
                  "capabilities": seen.get("capabilities") or []}
        return merged, adapter.runs(), adapter.held()

    def _tenant_wide() -> bool:
        """Whether the person may see the tenant's components: a Platform builder role, not a grant on one
        workspace only (a consultant sees their workspace, not the company's whole register)."""
        found = g.get("builder_access")
        return found is not None and found.via == "role"

    def _component_overview(store) -> dict:
        from datetime import timezone

        if not _tenant_wide():
            return {"rows": [], "alerts": [], "limits": _limits()}

        tenant = _platform_tenant()
        if store is None or not tenant or not hasattr(store, "component_runs"):
            return {"rows": [], "alerts": [], "limits": _limits()}
        now = datetime.now(timezone.utc)
        limits = _limits()
        rows, runs, held, seen = [], {}, {}, []
        for c in store.components(tenant, every=True):
            observed, c_runs, c_held = _observed(c)
            seen.append(observed)
            runs[c["id"]], held[c["id"]] = c_runs, c_held
            rows.append(component_rules.row(observed, c_runs, c_held, now, limits))
        return {"rows": rows, "alerts": component_rules.alerts(seen, runs, held, now, limits), "limits": limits}

    def _registry_context(store) -> dict:
        overview = _component_overview(store)
        state, kind = request.args.get("zustand") or None, request.args.get("art") or None
        return {"registry": component_rules.registry(overview["rows"], state=state, kind=kind),
                "registry_all": len(overview["rows"]), "component_alerts": overview["alerts"],
                "limits": overview["limits"], "state_filter": state, "kind_filter": kind,
                "registry_states": component_rules.STATES,
                "enrol_kinds": {k: v for k, v in component_rules.KINDS.items() if not v.get("site_only")},
                "protocol_path": PROTOCOL_PATH, "issue_certificates": issue_certificates,
                "sites": _site_rows(store), "site_options": site_rules.OPTIONS}

    def _component_or_404(component_id: str) -> dict:
        tenant = _platform_tenant()
        store = _store()
        found = store.component(tenant, component_id, any_state=True) if store is not None and tenant else None
        if found is None:
            abort(404)
        return found

    def _protocol_refused(exc):
        return jsonify({"success": False, "error": exc.message}), exc.status

    @bp.route("/komponenten/<component_id>")
    def component_page(component_id: str):
        """One component: what it serves and runs, its schedule, runs, held plans, last events and commands."""
        from datetime import timezone

        store = _store()
        component = _component_or_404(component_id)
        observed, runs, held = _observed(component)
        now = datetime.now(timezone.utc)
        limits = _limits()
        row = component_rules.row(observed, runs, held, now, limits)
        stage = STAGES_BY_SLUG["verbinden"]
        return _render("builder/component.html", stage=stage, stage_number=STAGES.index(stage) + 1,
                       component=observed, row=row, runs=runs[:30], held=held, served=store.served_by(component_id),
                       checks=component_rules.check_rows(observed, store.served_by(component_id)["sources"]),
                       events=store.events(component_id, 50) if component_rules.on_site(component) else [],
                       commands=store.commands(component_id) if component_rules.on_site(component) else [],
                       alerts=component_rules.alerts([observed], {component_id: runs}, {component_id: held}, now, limits),
                       chart=_run_chart(runs[:30]), kinds=component_rules.KINDS, limits=limits,
                       protocol_path=PROTOCOL_PATH,
                       days=component_rules.DAYS, sync=_component_sync(store, observed),
                       may_manage=g.builder_access.may("manage"))

    def _component_sync(store, component) -> list:
        """The sync status of each source the component serves (KG-2): reported by Knovas Link for a component
        behind it, by the component itself in direct mode."""
        if not component_rules.on_site(component):
            return []
        status = (component.get("telemetry") or {}).get("sources") or []
        if component.get("transport") == "link" and component.get("parent_id"):
            parent = store.component(_platform_tenant(), component["parent_id"], any_state=True)
            status = ((parent or {}).get("telemetry") or {}).get("sources") or []
        sources = [{**src, "component_id": component["id"], "component_name": component.get("name")}
                   for src in store.served_by(component["id"])["sources"]]
        return component_rules.sync_rows(sources, status, {component["id"]: component.get("schedule") or {}},
                                         {component["id"]: component_rules.auto_apply_of(component)})

    def _run_chart(runs: list) -> dict:
        """The run history as bars, oldest left: duration high, items and errors marked."""
        ordered = list(reversed(runs))
        longest = max([int(r.get("duration_ms") or 0) for r in ordered] + [1])
        most = max([int(r.get("added") or 0) + int(r.get("changed") or 0) + int(r.get("retired") or 0) for r in ordered] + [1])
        bars = []
        for i, r in enumerate(ordered):
            items = int(r.get("added") or 0) + int(r.get("changed") or 0) + int(r.get("retired") or 0)
            bars.append({"x": 8 + i * 22, "height": max(2, round(100 * int(r.get("duration_ms") or 0) / longest)),
                         "items_y": 112 - round(100 * items / most), "items": items, "errors": int(r.get("errors") or 0),
                         "status": r.get("status"), "kind": r.get("kind"), "at": r.get("started_at"),
                         "seconds": round(int(r.get("duration_ms") or 0) / 1000, 1)})
        return {"bars": bars, "width": max(240, 16 + 22 * len(bars)), "longest_s": round(longest / 1000, 1), "most": most}

    @bp.route("/api/components/enrol", methods=["POST"])
    def enrol_component():
        """The wizard: a component that speaks protocol v1, and its one-time enrolment token (shown once, 24 h)."""
        from datetime import timedelta, timezone

        store = _store()
        tenant = _platform_tenant()
        if store is None or not tenant:
            return jsonify({"success": False, "error": "Diese Plattform kennt ihren Mandanten nicht."}), 409
        body = request.get_json(silent=True) or {}
        name = str(body.get("name") or "").strip()
        if not 1 <= len(name) <= 120:
            return jsonify({"success": False, "error": "Name: 1 bis 120 Zeichen."}), 400
        now = datetime.now(timezone.utc)
        try:
            component_id, token = connector_protocol.issue_enrolment(
                store, _component_secrets(), tenant, kind=str(body.get("kind") or ""), name=name,
                site=str(body.get("site") or "").strip()[:120], owner=str(body.get("owner") or "").strip()[:120],
                actor=gate.current_user(), now=now)
        except connector_protocol.ProtocolError as exc:
            return _protocol_refused(exc)
        logger.info("Builder: enrolment token issued for component %s", component_id)
        return jsonify({"success": True, "id": component_id, "token": token,
                        "expires_at": (now + timedelta(hours=component_rules.ENROL_HOURS)).isoformat(),
                        "url": url_for("builder.component_page", component_id=component_id)}), 201

    @bp.route("/api/components/<component_id>/rotate", methods=["POST"])
    def rotate_component(component_id: str):
        from datetime import timezone

        _component_or_404(component_id)
        try:
            token = connector_protocol.rotate_enrolment(_store(), _component_secrets(), _platform_tenant(), component_id,
                                                        gate.current_user(), datetime.now(timezone.utc))
        except connector_protocol.ProtocolError as exc:
            return _protocol_refused(exc)
        logger.info("Builder: component %s rotated", component_id)
        return jsonify({"success": True, "token": token})

    @bp.route("/api/components/<component_id>/revoke", methods=["POST"])
    def revoke_component(component_id: str):
        from datetime import timezone

        component = _component_or_404(component_id)
        if component.get("transport") != "v1":
            _store().update_component(component_id, state="revoked")
        else:
            try:
                connector_protocol.revoke(_store(), _component_secrets(), _platform_tenant(), component_id,
                                          gate.current_user(), datetime.now(timezone.utc))
            except connector_protocol.ProtocolError as exc:
                return _protocol_refused(exc)
        logger.info("Builder: component %s revoked", component_id)
        return jsonify({"success": True})

    @bp.route("/api/components/<component_id>/delete", methods=["POST"])
    def delete_component(component_id: str):
        """Remove a component nobody runs: one that never enrolled, or one already revoked. Its name is free again. One
        still running must be revoked first, so that deleting it cannot leave it working unseen; a source that still
        reads through it must go first, so that no source points at nothing."""
        component = _component_or_404(component_id)
        if component.get("state") not in ("pending", "revoked"):
            return jsonify({"success": False, "error": "Diese Komponente läuft noch: zuerst widerrufen, dann löschen."}), 409
        store = _store()

        def bound(cid: str) -> list:
            # What it serves (published), and what a draft binds to it: neither may be left pointing at nothing.
            found = list(store.served_by(cid)["sources"])
            for w in store.list():
                found += [s for s in (store.model(w.id) or {}).get("sources") or [] if s.get("component") == cid]
            return found

        sources = bound(component_id)
        if component_rules.is_link(component) and hasattr(store, "children"):
            # Knovas Link's components go with it (their rows cascade): a source read through one of them must go first.
            for child in store.children(component_id):
                sources += bound(child["id"])
        if sources:
            names = ", ".join(sorted({str(s.get("name")) for s in sources}))
            return jsonify({"success": False, "error": f"Diese Quellen lesen noch über sie; zuerst entfernen: {names}."}), 409
        if component_secrets is not None:
            secrets = component_secrets()
            for which in ("enrol", "control"):
                secrets.clear(connector_protocol.secret_key(component_id, which), gate.current_user())
        store.delete_component(_platform_tenant(), component_id)
        logger.info("Builder: component %s deleted", component_id)
        return jsonify({"success": True, "url": url_for("builder.stage", slug="verbinden")})

    def _command(component: Mapping[str, Any], command: str, args: dict, event: str) -> None:
        store = _store()
        store.add_command(component["id"], command, args, gate.current_user().id)
        store.add_event(component["id"], event, {})

    @bp.route("/api/components/<component_id>/run", methods=["POST"])
    def component_run(component_id: str):
        """Run now: a dry run, or -- for whoever may publish -- an apply after a dry run."""
        component = _component_or_404(component_id)
        kind = (request.get_json(silent=True) or {}).get("kind")
        if kind not in ("dry_run", "apply"):
            return jsonify({"success": False, "error": "Probelauf oder Anwenden?"}), 400
        if kind == "apply" and not g.builder_access.may("publish"):
            return jsonify({"success": False, "error": "Anwenden darf, wer veröffentlicht."}), 403
        if component.get("state") in ("revoked", "pending") or component.get("paused"):
            return jsonify({"success": False, "error": "Die Komponente läuft nicht (pausiert, widerrufen oder nicht angemeldet)."}), 409
        if component_rules.on_site(component):
            if kind == "apply" and not any(r["kind"] == "dry_run" and r["status"] in ("ok", "held")
                                           for r in _store().component_runs(component_id, 5)):
                return jsonify({"success": False, "error": "Erst ein Probelauf: Angewendet wird, was ein Probelauf gezeigt hat."}), 409
            _command(component, "run", {"kind": kind}, "run.requested")
            return jsonify({"success": True, "queued": True})
        adapter = _adapter(component)
        if adapter is None:
            return jsonify({"success": False, "error": "Diese Komponente lässt sich hier nicht starten."}), 409
        try:
            adapter.run(kind, gate.current_user())
        except AdapterError as exc:
            return jsonify({"success": False, "error": str(exc)}), 409
        except Exception as exc:  # noqa: BLE001 - the sync's own refusal
            return jsonify({"success": False, "error": str(exc)[:300] or type(exc).__name__}), 409
        return jsonify({"success": True, "queued": False})

    @bp.route("/api/components/<component_id>/check", methods=["POST"])
    def component_check(component_id: str):
        """Ask a component to report whether it can read its sources: reads nothing into the Platform, writes nothing."""
        component = _component_or_404(component_id)
        if not component_rules.on_site(component) or component.get("state") != "enrolled":
            return jsonify({"success": False, "error": "Das geht bei einer angemeldeten Komponente."}), 409
        _command(component, "check", {}, "check.requested")
        return jsonify({"success": True, "queued": True})

    def _pause(component_id: str, paused: bool):
        component = _component_or_404(component_id)
        if component_rules.on_site(component):
            _store().update_component(component_id, paused=paused)
            _command(component, "pause" if paused else "resume", {}, "paused" if paused else "resumed")
            return jsonify({"success": True})
        adapter = _adapter(component)
        try:
            if adapter is None:
                raise AdapterError("Diese Komponente lässt sich hier nicht pausieren.")
            adapter.pause(gate.current_user()) if paused else adapter.resume(gate.current_user())
        except AdapterError as exc:
            return jsonify({"success": False, "error": str(exc)}), 409
        _store().update_component(component_id, paused=paused)
        return jsonify({"success": True})

    @bp.route("/api/components/<component_id>/pause", methods=["POST"])
    def component_pause(component_id: str):
        return _pause(component_id, True)

    @bp.route("/api/components/<component_id>/resume", methods=["POST"])
    def component_resume(component_id: str):
        return _pause(component_id, False)

    @bp.route("/api/components/<component_id>/schedule", methods=["POST"])
    def component_schedule(component_id: str):
        component = _component_or_404(component_id)
        if component_rules.is_link(component):
            # Since protocol 1.2 each component behind Knovas Link follows its own schedule (KG-2).
            return jsonify({"success": False, "error": "Knovas Link führt nichts aus: den Zeitplan auf der Seite der "
                                                       "Komponente am Standort setzen."}), 409
        try:
            schedule = component_rules.check_schedule((request.get_json(silent=True) or {}).get("schedule"))
        except ValueError as exc:
            return jsonify({"success": False, "error": str(exc)}), 400
        known = component.get("capabilities") or []
        if schedule["mode"] == "commits" and known and component_rules.POLL_CAPABILITY not in known:
            return jsonify({"success": False, "error": "Diese Komponente prüft noch nicht auf neue Commits: das Abbild "
                                                       "aktualisieren (Sync-Komponente 0.3.0 oder neuer)."}), 409
        _store().update_component(component_id, schedule=schedule)
        _store().add_event(component_id, "schedule.changed", {})
        if component_rules.on_site(component):
            _command(component, "reload_config", {}, "config.changed")
        return jsonify({"success": True, "schedule": schedule, "text": component_rules.schedule_text(schedule)})

    @bp.route("/api/components/<component_id>/auto_apply", methods=["POST"])
    def component_auto_apply(component_id: str):
        """«Automatisch anwenden, wenn nichts zur Freigabe ansteht», per source (KG-2). The component then applies a
        plan of that source right after it is made, only when the plan holds nothing a person must decide; a guard
        hold, drift or a retire entry still waits for a person. Off by default; whoever manages the sites switches it,
        and every change is an event on the component and a line in the access log."""
        component = _component_or_404(component_id)
        body = request.get_json(silent=True) or {}
        on, workspace, key = body.get("on"), str(body.get("workspace") or ""), str(body.get("source") or "")
        if not isinstance(on, bool):
            return jsonify({"success": False, "error": "An oder aus?"}), 400
        if not component_rules.on_site(component) or component.get("state") != "enrolled":
            return jsonify({"success": False, "error": "Das geht bei einer angemeldeten Komponente, die bei der Firma "
                                                       "läuft."}), 409
        served = _store().served_by(component_id)["sources"]
        if not any(s.get("workspace") == workspace and s.get("key") == key for s in served):
            return jsonify({"success": False, "error": "Diese Quelle liest die Komponente nicht (veröffentlichte "
                                                       "Fassung)."}), 404
        known = component.get("capabilities") or []
        if on and component_rules.AUTO_APPLY_CAPABILITY not in known:
            return jsonify({"success": False, "error": "Diese Komponente wendet noch nicht selbst an: das Abbild "
                                                       "aktualisieren (Sync-Komponente 0.3.0 oder neuer)."}), 409
        ref = component_rules.source_ref(workspace, key)
        if (ref in component_rules.auto_apply_of(component)) != on:
            _store().update_component(component_id, settings=component_rules.with_auto_apply(component, ref, on))
            _store().add_event(component_id, "auto_apply.on" if on else "auto_apply.off", {})
            _command(component, "reload_config", {}, "config.changed")
            logger.info("Builder: automatic apply %s for a source of component %s", "on" if on else "off", component_id)
        return jsonify({"success": True, "on": on})

    @bp.route("/api/components/<component_id>/settings", methods=["POST"])
    def component_settings(component_id: str):
        """Name, site, owner (and a connector's address). What it runs -- sources, mappings, folder rules -- changes
        in a workspace draft and goes through the review (step B5)."""
        component = _component_or_404(component_id)
        body = request.get_json(silent=True) or {}
        name = str(body.get("name") or component["name"]).strip()
        if not 1 <= len(name) <= 120:
            return jsonify({"success": False, "error": "Name: 1 bis 120 Zeichen."}), 400
        fields = {"name": name, "site": str(body.get("site", component.get("site")) or "").strip()[:120],
                  "owner": str(body.get("owner", component.get("owner")) or "").strip()[:120]}
        _store().update_component(component_id, **fields)
        _store().add_event(component_id, "config.changed", {})
        if component_rules.on_site(component):
            _command(component, "reload_config", {}, "config.changed")
        return jsonify({"success": True})

    @bp.route("/api/components/<component_id>/plans/<plan_id>", methods=["POST"])
    def component_plan(component_id: str, plan_id: str):
        """Approve or reject a plan the deletion guard held, as brain-runner holds them; a reason is required."""
        component = _component_or_404(component_id)
        body = request.get_json(silent=True) or {}
        approve = bool(body.get("approve"))
        reason = str(body.get("reason") or "").strip()[:500]
        if approve and not reason:
            return jsonify({"success": False, "error": "Warum dürfen diese Knoten stillgelegt werden? Ein Grund ist nötig."}), 400
        if approve and not g.builder_access.may("publish"):
            # The approval lets the component write -- retire nodes -- on the tenant: as an apply does.
            return jsonify({"success": False, "error": "Freigeben darf, wer veröffentlicht."}), 403
        store = _store()
        if component_rules.on_site(component):
            plan = next((h for h in store.held_plans(component_id) if h["id"] == plan_id and h["state"] == "held"), None)
            if plan is None or not store.decide_held(component_id, plan_id, approve=approve, actor_id=gate.current_user().id,
                                                     reason=reason):
                return jsonify({"success": False, "error": "Diesen zurückgehaltenen Plan gibt es nicht mehr."}), 404
            _command(component, "approve_plan" if approve else "reject_plan",
                     {"plan": plan_id, "mapping": plan["mapping"], "reason": reason},
                     "plan.approved" if approve else "plan.rejected")
            return jsonify({"success": True})
        adapter = _adapter(component)
        held_now = adapter.held() if adapter else []
        plan = next((h for h in held_now if h["id"] == plan_id), None)
        if plan is None:
            return jsonify({"success": False, "error": "Diesen zurückgehaltenen Plan gibt es nicht mehr."}), 404
        # brain-runner releases the guard for the whole dry run: every mapping it held, not this one alone.
        together = sorted(h["mapping"] for h in held_now if h.get("run") == plan.get("run"))
        if approve and len(together) > 1 and sorted(str(m) for m in body.get("confirm_mappings") or []) != together:
            return jsonify({"success": False, "error": "Die Freigabe gilt für alle in diesem Probelauf zurückgehaltenen "
                            "Zuordnungen; bestätigen Sie alle.", "details": together}), 409
        try:
            adapter.decide(plan["mapping"], approve, reason, gate.current_user())
        except AdapterError as exc:
            return jsonify({"success": False, "error": str(exc)}), 409
        except Exception as exc:  # noqa: BLE001 - the sync's own refusal
            return jsonify({"success": False, "error": str(exc)[:300] or type(exc).__name__}), 409
        return jsonify({"success": True})

    # -------------------------------------------------------- sites and Knovas Link (connection layer MVP-23)

    def _site_rows(store) -> list:
        """The tenant's sites for the «Verbinden» page, each with its Link's state."""
        from datetime import timezone

        tenant = _platform_tenant()
        if not _tenant_wide() or store is None or not tenant or not hasattr(store, "sites"):
            return []
        now, limits = datetime.now(timezone.utc), _limits()
        rows = []
        for s in store.sites(tenant):
            link = site_rules.site_link(store, tenant, s["id"])
            state = component_rules.state_of(link, now, limits) if link else "pending"
            rows.append({**s, "url": url_for("builder.site_page", site_id=s["id"]), "state": state,
                         "state_label": component_rules.STATES[state],
                         "options_text": ", ".join(site_rules.OPTIONS[o]["label"] for o in s["options"]
                                                   if o in site_rules.OPTIONS) or "nur Knovas Link",
                         "sync": _site_sync(store, link)})
        return rows

    def _site_sync(store, link) -> list:
        """Per source a site's components serve: the last synced commit, when, what the last apply wrote, the next
        check, the schedule and whether it applies on its own (KG-2), from Link's heartbeat."""
        if link is None or not hasattr(store, "children"):
            return []
        children = store.children(link["id"])
        sources = []
        for child in children:
            sources += [{**src, "component_id": child["id"], "component_name": child.get("name")}
                        for src in store.served_by(child["id"])["sources"]]
        return component_rules.sync_rows(sources, (link.get("telemetry") or {}).get("sources") or [],
                                         {c["id"]: c.get("schedule") or {} for c in children},
                                         {c["id"]: component_rules.auto_apply_of(c) for c in children})

    def _site_or_404(site_id: str) -> dict:
        tenant = _platform_tenant()
        store = _store()
        found = store.site(tenant, site_id) if store is not None and tenant and hasattr(store, "site") else None
        if found is None:
            abort(404)
        return found

    def _ensure_link(site: Mapping[str, Any]) -> dict:
        """The site's Knovas Link row; made when it is missing (a site whose Link could not be made at first)."""
        from datetime import timezone

        store, tenant = _store(), _platform_tenant()
        link = site_rules.site_link(store, tenant, site["id"])
        if link is None:
            connector_protocol.issue_enrolment(store, _component_secrets(), tenant, kind="knovas_link",
                                               name=site_rules.link_name(site["name"]), site=site["name"],
                                               owner=site.get("owner") or "", actor=gate.current_user(),
                                               now=datetime.now(timezone.utc), site_id=site["id"])
            link = site_rules.site_link(store, tenant, site["id"])
        return link

    @bp.route("/standorte/<site_id>")
    def site_page(site_id: str):
        """A site: the one command, the certificate step, the setup checklist and the package."""
        from datetime import timezone

        site = _site_or_404(site_id)
        store, tenant = _store(), _platform_tenant()
        link = site_rules.site_link(store, tenant, site["id"])
        children = store.children(link["id"]) if link else []
        now, limits = datetime.now(timezone.utc), _limits()
        stage = STAGES_BY_SLUG["verbinden"]
        return _render("builder/site.html", stage=stage, stage_number=STAGES.index(stage) + 1, site=site, link=link,
                       link_state=component_rules.state_of(link, now, limits) if link else "pending",
                       children=[component_rules.row(c, [], [], now, limits) for c in children],
                       checklist=site_rules.checklist(site, link, children, now, limits),
                       containers=[{"name": c, **site_rules.CONTAINERS[c]} for c in site_rules.containers_of(site["options"])],
                       options=site_rules.OPTIONS, command=site_rules.COMMAND, env_file=site_rules.LINK_ENV_FILE,
                       sources_env_file=site_rules.SOURCES_ENV_FILE,
                       certificate_step=site_rules.CERTIFICATE_STEP, certificate_check=site_rules.CERTIFICATE_CHECK,
                       certificate_fingerprint_command=site_rules.CERTIFICATE_FINGERPRINT, protocol_path=PROTOCOL_PATH,
                       compose_preview=site_rules.compose_file(site, site["options"]),
                       states=component_rules.STATES, sync=_site_sync(store, link))

    @bp.route("/api/sites", methods=["POST"])
    def create_site():
        """«Standort anlegen»: the site and its Knovas Link, waiting for the package to be installed."""
        store, tenant = _store(), _platform_tenant()
        if store is None or not tenant:
            return jsonify({"success": False, "error": "Diese Plattform kennt ihren Mandanten nicht."}), 409
        body = request.get_json(silent=True) or {}
        try:
            name = site_rules.check_name(body.get("name"))
            options = site_rules.check_options(body.get("options"))
        except ValueError as exc:
            return jsonify({"success": False, "error": str(exc)}), 400
        owner = str(body.get("owner") or "").strip()[:120]
        site_id = store.create_site(tenant, name=name, owner=owner, options=options, actor_id=gate.current_user().id)
        if site_id is None:
            return jsonify({"success": False, "error": f"Einen Standort «{name}» gibt es schon."}), 409
        try:
            _ensure_link(store.site(tenant, site_id))
        except connector_protocol.ProtocolError as exc:
            # The site stays; its page makes Link again when the package is asked for.
            logger.warning("Builder: Knovas Link of a new site not made (%s)", exc.code)
            return jsonify({"success": False, "error": exc.message,
                            "url": url_for("builder.site_page", site_id=site_id)}), exc.status
        logger.info("Builder: site created, %d options", len(options))
        return jsonify({"success": True, "id": site_id, "url": url_for("builder.site_page", site_id=site_id)}), 201

    _PLATFORM_URL = re.compile(r"^https?://[A-Za-z0-9.-]+(:\d{1,5})?" + re.escape(PROTOCOL_PATH) + "$")

    @bp.route("/api/sites/<site_id>/package", methods=["POST"])
    def site_package(site_id: str):
        """«Installationspaket herunterladen»: docker-compose.yml, knovas-link.env and knovas-sources.env. While Link
        has not enrolled, each
        download carries a new one-time token and the one before stops working; once it runs, the package carries none."""
        from datetime import timedelta, timezone

        site = _site_or_404(site_id)
        body = request.get_json(silent=True) or {}
        platform_url = str(body.get("platform_url") or "").strip()
        if platform_url and not _PLATFORM_URL.match(platform_url):
            return jsonify({"success": False, "error": "Adresse der Plattform: http(s)://Host[:Port]" + PROTOCOL_PATH}), 400
        # The page sends the address its browser reached: behind nginx the server's own Host has no port.
        platform_url = platform_url or request.url_root.rstrip("/") + PROTOCOL_PATH
        try:
            link = _ensure_link(site)
            secrets = _component_secrets()
        except connector_protocol.ProtocolError as exc:
            return _protocol_refused(exc)
        token = None
        if link.get("state") == "pending":
            now = datetime.now(timezone.utc)
            token, digest = component_rules.new_token("kce")
            secrets.put(connector_protocol.secret_key(link["id"], "enrol"), digest, gate.current_user())
            _store().update_component(link["id"], enrol_expires_at=(now + timedelta(hours=component_rules.ENROL_HOURS)).isoformat())
            _store().add_event(link["id"], "enrol.token_issued", {})
        elif link.get("state") != "enrolled":
            return jsonify({"success": False, "error": "Knovas Link dieses Standorts ist widerrufen."}), 409
        data = site_rules.package_zip(site, platform_url=platform_url, token=token)
        logger.info("Builder: site package made, site %s, with token: %s", site["id"], token is not None)
        name = re.sub(r"[^a-z0-9]+", "-", site["name"].lower()).strip("-") or "standort"
        return Response(data, mimetype="application/zip", headers={
            "Content-Disposition": f'attachment; filename="knovas-site-{name}.zip"', "Cache-Control": "no-store"})

    @bp.route("/api/sites/<site_id>/rotate", methods=["POST"])
    def site_rotate(site_id: str):
        """«Token erneuern»: Link enrols again; its control token stops at once. The next package carries the token."""
        from datetime import timezone

        site = _site_or_404(site_id)
        try:
            link = _ensure_link(site)
            if link.get("state") != "pending":
                connector_protocol.rotate_enrolment(_store(), _component_secrets(), _platform_tenant(), link["id"],
                                                    gate.current_user(), datetime.now(timezone.utc))
        except connector_protocol.ProtocolError as exc:
            return _protocol_refused(exc)
        logger.info("Builder: the token of a site's Knovas Link renewed, site %s", site["id"])
        return jsonify({"success": True})

    @bp.route("/api/sites/<site_id>/certificate", methods=["POST"])
    def site_certificate(site_id: str):
        """«Zertifikat erfassen» (DEC-0056 A1; BB-07 B3, S-4 M2): the certificate Knovas's client CA signed from this
        site's request. Taken only for the key of the request Link enrolled with, for this tenant, for client
        authentication, valid now and signed by the client CA where it is configured; its fingerprint is then what
        BUILDER_CONNECTOR_REQUIRE_CLIENT_CERT makes every call of this Link carry."""
        from datetime import timezone

        site = _site_or_404(site_id)
        link = site_rules.site_link(_store(), _platform_tenant(), site["id"])
        pem = str((request.get_json(silent=True) or {}).get("certificate") or "")
        if len(pem) > 64 * 1024:
            return jsonify({"success": False, "error": "Ein Zertifikat ist wenige Kilobyte gross."}), 413
        try:
            ca = client_ca() if client_ca is not None else None
        except Exception as exc:  # noqa: BLE001 - checked without it; Link still checks the issuer
            logger.warning("Builder: the client CA is not readable (%s)", type(exc).__name__)
            ca = None
        try:
            found = connector_protocol.record_certificate(_store(), _platform_tenant(), link, pem,
                                                          datetime.now(timezone.utc), client_ca=ca)
        except connector_protocol.ProtocolError as exc:
            logger.info("Builder: a site certificate was refused (%s), site %s", exc.code, site["id"])
            return jsonify({"success": False, "error": exc.message, "code": exc.code}), exc.status
        logger.info("Builder: the certificate of a site's Knovas Link recorded, site %s", site["id"])
        return jsonify({"success": True, **found})

    @bp.route("/api/sites/<site_id>/probe", methods=["POST"])
    def site_probe(site_id: str):
        """«Jetzt prüfen»: Link goes round the work channel now and reports the round trip with its next beat."""
        site = _site_or_404(site_id)
        link = site_rules.site_link(_store(), _platform_tenant(), site["id"])
        if link is None or link.get("state") != "enrolled":
            return jsonify({"success": False, "error": "Knovas Link dieses Standorts ist nicht angemeldet."}), 409
        _command(link, "probe", {}, "probe.requested")
        return jsonify({"success": True, "queued": True})

    # -------------------------------------------------------- «Freigaben»: write proposals (MVP-27, decision CL-4)

    def _tenant_client():
        """The Builder's line to the tenant's connection routes, or None where the Platform has none."""
        if tenant_client is None:
            return None
        try:
            return tenant_client()
        except Exception as exc:  # noqa: BLE001 - no certificate here: the page says so
            logger.warning("Builder: the tenant's connection routes are not reachable (%s)", type(exc).__name__)
            return None

    def _tenant_wide_or_refuse():
        """«Freigaben» and «Verbindung» are the tenant's: a Platform role sees them, a grant on one workspace does not."""
        if not _tenant_wide():
            return _refuse("Das sieht, wer eine Builder-Rolle der Plattform hat.")
        return None

    def _workspace_models(store) -> list:
        if store is None:
            return []
        tenant = _platform_tenant()
        return [store.model(w.id) or {} for w in store.list() if not w.tenant or w.tenant == tenant]

    @bp.route("/betrieb/freigaben")
    def approvals_page():
        """«Freigaben»: the open write proposals and what became of the decided ones."""
        refusal = _tenant_wide_or_refuse()
        if refusal:
            return refusal
        from builder import approvals as approval_rules
        from builder.connection_api import ConnectionApiError

        client, error, found = _tenant_client(), None, {"open": [], "decided": []}
        if client is None:
            error = "Diese Plattform liest keine Änderungsvorschläge des Mandanten (kein Zertifikat)."
        else:
            try:
                found = approval_rules.rows(client.source_writes("proposed"), client.source_writes(),
                                            approval_rules.mapping_writes(_workspace_models(_store())))
            except ConnectionApiError as exc:
                logger.warning("Builder: write proposals not read (HTTP %s, %s)", exc.status, exc.code or "-")
                error = f"Das Brain hat die Vorschläge nicht geliefert ({codes_text(exc.code) or f'HTTP {exc.status}'})."
            except Exception as exc:  # noqa: BLE001 - the page says it could not read
                logger.warning("Builder: write proposals not read (%s)", type(exc).__name__)
                error = f"Das Brain war nicht erreichbar ({type(exc).__name__})."
        logger.info("Builder Freigaben: %d open, %d decided shown", len(found["open"]), len(found["decided"]))
        stage = STAGES_BY_SLUG["betrieb"]
        return _render("builder/approvals.html", stage=stage, stage_number=STAGES.index(stage) + 1,
                       proposals=found, error=error, may_decide=g.builder_access.may("publish"))

    def codes_text(code) -> Optional[str]:
        from builder import codes

        return codes.describe(code)["title"] if code else None

    @bp.route("/api/freigaben/count")
    def approvals_count():
        """The badge: how many proposals wait. Zero where nothing can be read."""
        refusal = _tenant_wide_or_refuse()
        if refusal:
            return refusal
        client = _tenant_client()
        try:
            return jsonify({"open": len(client.source_writes("proposed")) if client is not None else 0})
        except Exception as exc:  # noqa: BLE001 - the badge stays empty
            logger.info("Builder: open proposals not counted (%s)", type(exc).__name__)
            return jsonify({"open": None})

    def _decide(proposal_id: str, accept: bool):
        refusal = _tenant_wide_or_refuse()
        if refusal:
            return refusal
        from builder import approvals as approval_rules
        from builder import codes
        from builder.connection_api import ConnectionApiError

        client = _tenant_client()
        if client is None:
            return jsonify({"success": False, "error": "Diese Plattform erreicht die Vorschläge des Mandanten nicht."}), 503
        reason = str((request.get_json(silent=True) or {}).get("reason") or "").strip()
        if not accept and not 3 <= len(reason) <= 500:
            return jsonify({"success": False, "error": "Warum wird abgelehnt? Ein Grund ist nötig (3 bis 500 Zeichen)."}), 400
        # The person deciding, as the declared actor; the Brain records it (verified only with I2).
        actor = {"kind": "user", "id": str(gate.current_user().id)}
        try:
            result = client.accept_source_write(proposal_id, actor) if accept else \
                client.reject_source_write(proposal_id, actor, reason)
        except ConnectionApiError as exc:
            logger.info("Builder: write proposal %s not %s (HTTP %s, %s)", proposal_id,
                        "accepted" if accept else "rejected", exc.status, exc.code or "-")
            found = codes.describe(exc.code) if exc.code else None
            text = f"{found['title']}: {found['explanation']} {found['fix']}" if found else f"Das Brain hat abgelehnt (HTTP {exc.status})."
            return jsonify({"success": False, "error": text, "code": exc.code}), exc.status if 400 <= exc.status < 500 else 502
        except Exception as exc:  # noqa: BLE001 - said on the page
            logger.warning("Builder: write proposal decision failed (%s)", type(exc).__name__)
            return jsonify({"success": False, "error": "Das Brain war nicht erreichbar."}), 502
        status = result.get("status") or ("queued" if accept else "rejected")
        logger.info("Builder: write proposal %s %s, now %s", proposal_id, "accepted" if accept else "rejected", status)
        return jsonify({"success": True, "status": status, "status_text": approval_rules.status_text(status),
                        "pull_request": result.get("result_ref") if str(result.get("result_ref") or "").startswith("https://") else None})

    @bp.route("/api/freigaben/<proposal_id>/accept", methods=["POST"])
    def approval_accept(proposal_id: str):
        return _decide(proposal_id, True)

    @bp.route("/api/freigaben/<proposal_id>/reject", methods=["POST"])
    def approval_reject(proposal_id: str):
        return _decide(proposal_id, False)

    # -------------------------------------------------------- «Verbindung»: the sites at work (MVP-28)

    def _site_sources(store, children: list) -> list:
        """The sources read through a site's components, each with how many mappings read it and the shortest
        freshness window among them."""
        out = []
        models: dict = {}
        for child in children:
            served = store.served_by(child["id"])
            for s in served["sources"]:
                ws = s["workspace"]
                # What the site runs: the published version, as served_by reads it (BB-07, B2).
                model = models.setdefault(ws, store.published_model(ws) or {})
                ids = {m["id"] for m in served["mappings"] if m["workspace"] == ws and m["source"] == s["key"]}
                windows = [((m.get("definition") or {}).get("freshness") or {}).get("window")
                           for m in model.get("mappings") or [] if (m.get("definition") or {}).get("id") in ids]
                windows = [w for w in windows if component_rules._window(w) is not None]
                out.append({"workspace": ws, "key": s["key"], "name": s.get("name"), "component": child["id"],
                            "mappings": len(ids),
                            "freshness": min(windows, key=component_rules._window) if windows else None})
        return out

    def _connection_cards() -> tuple[list, Optional[str]]:
        from datetime import timedelta, timezone

        from builder import connection as connection_rules

        store, tenant = _store(), _platform_tenant()
        if store is None or not tenant or not hasattr(store, "sites"):
            return [], "Diese Plattform kennt ihren Mandanten nicht."
        now, limits = datetime.now(timezone.utc), _limits()
        client, stats, proposals, note = _tenant_client(), None, None, None
        if client is None:
            note = "Diese Plattform liest die Zahlen des Mandanten nicht (kein Zertifikat): die letzten 24 Stunden sind unbekannt."
        else:
            try:
                stats = client.job_stats((now - timedelta(hours=connection_rules.WINDOW_HOURS)).isoformat())
            except Exception as exc:  # noqa: BLE001 - shown as unbekannt
                logger.info("Builder Verbindung: job counts not read (%s)", type(exc).__name__)
            try:
                proposals = client.source_writes()
            except Exception as exc:  # noqa: BLE001 - shown as unbekannt
                logger.info("Builder Verbindung: proposals not read (%s)", type(exc).__name__)
        cards = []
        for site in store.sites(tenant):
            link = site_rules.site_link(store, tenant, site["id"])
            children = store.children(link["id"]) if link else []
            cards.append(connection_rules.site_card(site, link, children, sources=_site_sources(store, children),
                                                    stats=stats, proposals=proposals, now=now, limits=limits,
                                                    proposals_url=url_for("builder.approvals_page")))
        logger.info("Builder Verbindung: %d sites, %d alerts", len(cards), sum(len(c["alerts"]) for c in cards))
        return cards, note

    @bp.route("/betrieb/verbindung")
    def connection_page():
        """«Verbindung»: per site whether everything works, the last 24 hours, and what to do about what does not."""
        refusal = _tenant_wide_or_refuse()
        if refusal:
            return refusal
        from builder import connection as connection_rules

        cards, note = _connection_cards()
        if request.args.get("teil") == "karten":
            return render_template("builder/_connection_cards.html", cards=cards)
        stage = STAGES_BY_SLUG["betrieb"]
        return _render("builder/connection.html", stage=stage, stage_number=STAGES.index(stage) + 1, cards=cards,
                       note=note, refresh_seconds=connection_rules.REFRESH_SECONDS)

    # -------------------------------------------------------- the access window (B14)

    class _DraftAccess:
        """The access design of the chosen workspace's draft, through the same face as the Brain's."""

        def __init__(self, store, workspace):
            self.store, self.workspace = store, workspace

        def model(self):
            return self.store.model(self.workspace.id)

        def tree(self):
            return draft_tree(self.model())

        def rules(self):
            return [{"rule_id": r["key"], "pointer_prefix": r["prefix"], "access_groups": list(r.get("groups") or []),
                     "source": r.get("source")} for r in (self.model().get("access") or {}).get("rules") or []]

        def _edit(self, op):
            self.store.edit(self.workspace.id, gate.current_user().id, lambda m: editing.apply(m, op))

        def create(self, name, parent, acting_as):
            self._edit({"op": "access.group.create", "name": name, "parent": parent})
            return self.tree().by_name(name)

        def rename(self, gid, name):
            self._edit({"op": "access.group.rename", "key": gid, "name": name})

        def move(self, gid, parent):
            self._edit({"op": "access.group.move", "key": gid, "parent": parent})

        def delete(self, gid):
            self._edit({"op": "access.group.remove", "key": gid})

        def create_rule(self, prefix, groups, acting_as):
            self._edit({"op": "access.rule.add", "prefix": prefix, "groups": groups})
            return {"pointer_prefix": prefix}

        def delete_rule(self, rule_id):
            self._edit({"op": "access.rule.remove", "key": rule_id})

        def documents(self, gid):
            return 0, False

        def node_types(self):
            return []

        def posture(self):
            return None

    def _access_mode() -> str:
        return "draft" if request.args.get("entwurf") else "live"

    def _access_backend(mode: str):
        """The tree the request works on: the tenant's live one (administrators), or the draft's design."""
        if mode == "draft":
            store = _store()
            workspace = _current_workspace(store)
            if workspace is None:
                raise access_rules.AccessError("Wählen Sie zuerst einen Arbeitsbereich.", 409)
            return _DraftAccess(store, workspace)
        if not g.builder_access.may("manage"):
            raise access_rules.AccessError("Den Baum des Mandanten ändern Administratoren; Berater entwerfen ihn im Entwurf.", 403)
        if access_backend is None:
            raise access_rules.AccessError("Diese Plattform liest die Zugriffsgruppen des Brains nicht.", 503)
        return access_backend()

    def _access_refused(exc):
        return jsonify({"success": False, "error": exc.message, "code": exc.code}), exc.status

    def _people() -> list:
        directory = user_directory() if user_directory is not None else None
        if directory is None or not hasattr(directory, "list_all"):
            return []
        out = []
        for u in directory.list_all():
            grants = directory.access_grants_of(u.id) if hasattr(directory, "access_grants_of") else []
            out.append({"id": str(u.id), "name": u.display_name or u.email, "email": u.email,
                        "roles": sorted(getattr(u, "roles", ()) or ()), "grants": grants, "user": u})
        return out

    def _running(grants: list) -> list:
        from datetime import date

        today = date.today().isoformat()
        return [gr["group_id"] for gr in grants if not gr.get("ends_on") or gr["ends_on"] >= today]

    def _assigned(tree, rules, node_types, backend, *, with_documents: bool) -> dict:
        counts = {}
        for gid in tree.groups:
            docs, more = backend.documents(gid) if with_documents else (0, False)
            counts[gid] = {"folder_rules": sum(1 for r in rules if gid in (r.get("access_groups") or [])),
                           "node_types": sum(1 for t in node_types if gid in (t.get("required_groups") or [])),
                           "documents": docs, "documents_more": more}
        return counts

    def _access_view(mode: str) -> dict:
        backend = _access_backend(mode)
        tree = backend.tree()
        rules = backend.rules()
        node_types = backend.node_types()
        people = _people() if mode == "live" else []
        members: dict = {}
        for person in people:
            for gid in _running(person["grants"]):
                members.setdefault(gid, []).append(person["id"])
        counts = _assigned(tree, rules, node_types, backend, with_documents=mode == "live")
        for gid in tree.groups:
            counts[gid]["members"] = len(members.get(gid, []))
        names = {p["id"]: p["name"] for p in people}
        chosen = request.args.get("gruppe")
        if chosen not in tree.groups:
            chosen = next((k for k in tree.groups if not tree.is_admin(k)), next(iter(tree.groups), None))
        person = next((p for p in people if p["id"] == request.args.get("person")), None)
        lit = access_rules.effective(tree, _running(person["grants"])) if person else []
        detail = None
        if chosen:
            above = [{"group": tree.name(a), "people": [names.get(u, u) for u in members.get(a, [])]} for a in tree.ancestors(chosen)]
            detail = {"id": chosen, "name": tree.name(chosen), "is_admin": tree.is_admin(chosen),
                      "parent": tree.parent(chosen), "path": [tree.name(a) for a in reversed(tree.ancestors(chosen))],
                      "members": [{"id": u, "name": names.get(u, u),
                                   "ends_on": next((gr.get("ends_on") for p in people if p["id"] == u
                                                    for gr in p["grants"] if gr["group_id"] == chosen), None)}
                                  for u in members.get(chosen, [])],
                      "above": [a for a in above if a["people"]],
                      "rules": [r for r in rules if chosen in (r.get("access_groups") or [])],
                      "node_types": [t.get("name") for t in node_types if chosen in (t.get("required_groups") or [])],
                      "counts": counts.get(chosen, {})}
        tenant = _platform_tenant()
        store = _store()
        workspace = _current_workspace(store) if store is not None else None
        folders = []
        if workspace is not None:
            for src in (store.model(workspace.id) or {}).get("sources") or []:
                folder = (src.get("settings") or {}).get("folder")
                if folder:
                    folders.append({"prefix": "/" + str(folder).strip("/"), "source": src.get("name")})
        return {"mode": mode, "tree": tree.nested(counts, lit), "detail": detail, "groups": [
                    {"id": k, "name": tree.name(k), "path": " / ".join([tree.name(a) for a in reversed(tree.ancestors(k))] + [tree.name(k)])}
                    for k in tree.groups],
                "people": [{k: v for k, v in p.items() if k != "user"} for p in people],
                "person": person["id"] if person else None, "lit": lit, "folders": folders,
                "posture": backend.posture(), "posture_requests": store.posture_requests(tenant) if mode == "live" and tenant and hasattr(store, "posture_requests") else [],
                "me": str(gate.current_user().id), "workspace": workspace.name if workspace else None}

    @bp.route("/zugriff")
    def access_page():
        """The access window: the group tree (left), a group (middle), the people (right), explain and posture."""
        mode = _access_mode()
        try:
            view = _access_view(mode)
        except access_rules.AccessError as exc:
            if mode == "live" and exc.status == 403:
                return redirect(url_for("builder.access_page", entwurf=1))
            view = {"mode": mode, "error": exc.message}
        stage = STAGES[0]
        return _render("builder/access.html", stage=stage, stage_number=1, view=view)

    def _access_change(body: Mapping[str, Any]) -> dict:
        op = body.get("op")
        if op not in ("create", "rename", "move", "delete"):
            raise access_rules.AccessError("Anlegen, umbenennen, verschieben oder löschen?")
        return {"op": op, "group": body.get("group"), "parent": body.get("parent"), "name": body.get("name")}

    def _preview(backend, change: dict, mode: str) -> dict:
        tree = backend.tree()
        rules = backend.rules()
        node_types = backend.node_types()
        op = change["op"]
        if op == "create":
            access_rules.check_create(tree, change["name"], change["parent"])
        elif op == "rename":
            access_rules.check_rename(tree, change["group"], change["name"])
        elif op == "move":
            access_rules.check_move(tree, change["group"], change["parent"])
        counts = _assigned(tree, rules, node_types, backend, with_documents=mode == "live" and op in ("move", "delete"))
        if op == "delete":
            people = _people() if mode == "live" else []
            assigned = dict(counts.get(change["group"], {}))
            assigned.pop("documents_more", None)
            assigned["members"] = sum(1 for p in people if change["group"] in _running(p["grants"]))
            access_rules.check_delete(tree, change["group"], assigned)
        people = _people() if mode == "live" else []
        members: dict = {}
        for person in people:
            for gid in _running(person["grants"]):
                members.setdefault(gid, []).append(person["id"])
        # What people see: documents and node types (a folder rule is how documents get there, not something seen).
        items = {gid: c["documents"] + c["node_types"] for gid, c in counts.items()}
        found = access_rules.impact(tree, change, members, items) if op in ("move", "delete") else \
            {"gains": {}, "losses": {}, "items": 0, "people_gaining": 0, "people_losing": 0}
        names = {p["id"]: p["name"] for p in people}
        what = {"create": f"Neue Gruppe «{change['name']}» unter {tree.name(change['parent'])}",
                "rename": f"{tree.name(change['group'])} heißt künftig «{change['name']}»",
                "move": f"{tree.name(change['group'])} hängt künftig unter {tree.name(change['parent'])} statt unter "
                        f"{tree.name(tree.parent(change['group']))}",
                "delete": f"{tree.name(change['group'])} wird gelöscht"}[op]
        return {"what": what, "gains": [{"name": names.get(u, u), "items": n} for u, n in sorted(found["gains"].items())],
                "losses": [{"name": names.get(u, u), "items": n} for u, n in sorted(found["losses"].items()) if n],
                "items": found["items"], "nobody": not found["gains"] and not any(found["losses"].values())}

    @bp.route("/api/access/preview", methods=["POST"])
    def access_preview():
        """What a structural change would do -- who gains or loses sight of how many items -- before it is made."""
        mode = _access_mode()
        try:
            backend = _access_backend(mode)
            change = _access_change(request.get_json(silent=True) or {})
            return jsonify({"success": True, "impact": _preview(backend, change, mode)})
        except access_rules.AccessError as exc:
            return _access_refused(exc)

    @bp.route("/api/access/apply", methods=["POST"])
    def access_apply():
        """The change, after its preview was shown and confirmed; checked again, then made."""
        mode = _access_mode()
        body = request.get_json(silent=True) or {}
        if body.get("confirmed") is not True:
            return jsonify({"success": False, "error": "Erst die Wirkung ansehen und bestätigen."}), 409
        try:
            backend = _access_backend(mode)
            change = _access_change(body)
            _preview(backend, change, mode)
            if change["op"] == "create":
                made = backend.create(access_rules.check_create(backend.tree(), change["name"], change["parent"]),
                                      change["parent"], ())
                logger.info("Access window: group created (%s)", mode)
                return jsonify({"success": True, "group": made})
            if change["op"] == "rename":
                backend.rename(change["group"], access_rules.check_rename(backend.tree(), change["group"], change["name"]))
            elif change["op"] == "move":
                backend.move(change["group"], change["parent"])
            else:
                backend.delete(change["group"])
        except access_rules.AccessError as exc:
            return _access_refused(exc)
        except editing.EditError as exc:
            return _refused(exc)
        logger.info("Access window: group %s (%s)", change["op"], mode)
        return jsonify({"success": True})

    @bp.route("/api/access/rules", methods=["POST"])
    def access_rules_add():
        """A folder (a path prefix from a sync component's source) assigned to a group."""
        mode = _access_mode()
        body = request.get_json(silent=True) or {}
        prefix = str(body.get("prefix") or "").strip()
        if not prefix.startswith("/") or ".." in prefix.split("/"):
            return jsonify({"success": False, "error": "Ordner: ein Pfad wie /Mandate/Meier, ohne «..»."}), 400
        try:
            backend = _access_backend(mode)
            tree = backend.tree()
            group = str(body.get("group") or "")
            if group not in tree.groups:
                raise access_rules.AccessError("Diese Gruppe gibt es nicht.", 404)
            backend.create_rule(prefix, [group], ())
        except access_rules.AccessError as exc:
            return _access_refused(exc)
        except editing.EditError as exc:
            return _refused(exc)
        logger.info("Access window: folder rule added (%s)", mode)
        return jsonify({"success": True}), 201

    @bp.route("/api/access/rules/remove", methods=["POST"])
    def access_rule_remove():
        mode = _access_mode()
        try:
            _access_backend(mode).delete_rule(str((request.get_json(silent=True) or {}).get("rule") or ""))
        except access_rules.AccessError as exc:
            return _access_refused(exc)
        except editing.EditError as exc:
            return _refused(exc)
        return jsonify({"success": True})

    @bp.route("/api/access/members", methods=["POST"])
    def access_members():
        """Put a person in a group (until a day, if given), or take them out: the Platform asserts it to the Brain."""
        from datetime import date

        body = request.get_json(silent=True) or {}
        directory = user_directory() if user_directory is not None else None
        if directory is None:
            return jsonify({"success": False, "error": "Diese Plattform verwaltet keine Konten."}), 503
        try:
            tree = _access_backend("live").tree()
        except access_rules.AccessError as exc:
            return _access_refused(exc)
        group, user_id = str(body.get("group") or ""), str(body.get("user") or "")
        person = next((p for p in _people() if p["id"] == user_id), None)
        if group not in tree.groups or person is None:
            return jsonify({"success": False, "error": "Diese Person oder Gruppe gibt es nicht."}), 404
        if body.get("remove"):
            directory.revoke_access_group(user_id, group)
            logger.info("Access window: a person left a group")
            return jsonify({"success": True})
        ends_on = str(body.get("ends_on") or "").strip() or None
        if ends_on:
            try:
                if date.fromisoformat(ends_on) < date.today():
                    raise ValueError
            except ValueError:
                return jsonify({"success": False, "error": "Bis: ein Datum ab heute, wie 2026-12-31."}), 400
        directory.grant_access_group(user_id, group, ends_on=ends_on, by=gate.current_user().id)
        logger.info("Access window: a person joined a group, ends: %s", bool(ends_on))
        return jsonify({"success": True})

    @bp.route("/api/access/explain", methods=["POST"])
    def access_explain():
        """Why a person sees a document, or why not: the Brain's answer for that person, and the path through the tree."""
        body = request.get_json(silent=True) or {}
        pointer = str(body.get("pointer") or "").strip()
        person = next((p for p in _people() if p["id"] == str(body.get("user") or "")), None)
        if person is None or not pointer:
            return jsonify({"success": False, "error": "Wer, und welches Dokument (sein Pfad)?"}), 400
        try:
            backend = _access_backend("live")
            tree = backend.tree()
            rules = backend.rules()
            acl = backend.document_access(pointer) or {}
            readable = backend.readable_as(person["user"], pointer)
        except access_rules.AccessError as exc:
            return _access_refused(exc)
        groups = [str(x) for x in acl.get("access_groups") or []]
        rule = acl.get("rule") if isinstance(acl.get("rule"), Mapping) else None
        if rule is None:
            matching = [r for r in rules if pointer.startswith(str(r.get("pointer_prefix") or "\0"))]
            rule = max(matching, key=lambda r: len(str(r.get("pointer_prefix"))), default=None)
        if not groups and rule is not None:
            groups = [str(x) for x in rule.get("access_groups") or []]
        governor = f"die Ordnerregel {rule.get('pointer_prefix')}" if rule is not None else None
        answer = access_rules.explain(tree, person["name"], _running(person["grants"]), groups, governor=governor,
                                      readable=readable, item=f"«{pointer.rsplit('/', 1)[-1]}»")
        logger.info("Access window: access explained (readable: %s)", readable)
        return jsonify({"success": True, "answer": {**answer, "path": [tree.name(x) for x in answer["path"]]}})

    @bp.route("/api/access/posture", methods=["POST"])
    def access_posture():
        """Ask to switch the tenant's RBAC enforcement; a second administrator approves."""
        mode = (request.get_json(silent=True) or {}).get("mode")
        if mode not in ("disabled", "enforcing"):
            return jsonify({"success": False, "error": "Durchsetzung: aus oder an?"}), 400
        tenant = _platform_tenant()
        if not tenant:
            return jsonify({"success": False, "error": "Diese Plattform kennt ihren Mandanten nicht."}), 409
        _store().request_posture(tenant, mode, gate.current_user().id)
        logger.info("Access window: enforcement switch requested (%s)", mode)
        return jsonify({"success": True}), 201

    @bp.route("/api/access/posture/<request_id>/approve", methods=["POST"])
    def access_posture_approve(request_id: str):
        tenant = _platform_tenant()
        store = _store()
        found = next((r for r in store.posture_requests(tenant or "") if r["id"] == request_id), None)
        if found is None or found.get("approved_at"):
            return jsonify({"success": False, "error": "Diese Anfrage gibt es nicht mehr."}), 404
        if str(found.get("requested_by")) == str(gate.current_user().id):
            return jsonify({"success": False, "error": "Freigeben muss eine zweite Person."}), 403
        posture = None
        try:
            posture = _access_backend("live").posture()
        except access_rules.AccessError:
            pass
        if found["mode"] == "enforcing" and posture is not None and not posture.get("backfill_complete"):
            return jsonify({"success": False, "error": "Die ACL-Nachführung ist noch nicht abgeschlossen; erst dann durchsetzen."}), 409
        outcome = "freigegeben; Knovas schaltet den Mandanten um (interne Schnittstelle)"
        if not store.approve_posture(tenant, request_id, gate.current_user().id, outcome):
            return jsonify({"success": False, "error": "Freigeben muss eine zweite Person."}), 403
        logger.info("Access window: enforcement switch approved")
        return jsonify({"success": True, "outcome": outcome})

    def _access_effect(model) -> Optional[dict]:
        """What publish would write of the draft's access design onto the live tenant (builder.review.access_plan),
        read now and written nowhere; None when the draft designs no access. An unreadable tenant is the plan's
        ``error``: the dry run shows it, and the approval holds it like everything else."""
        design = model.get("access") or {}
        if not design.get("groups") and not design.get("rules"):
            return None
        if access_backend is None:
            return {"error": "Diese Plattform liest die Zugriffsgruppen des Brains nicht."}
        try:
            backend = access_backend()
            return review.access_plan(design, backend.tree(), [str(r.get("pointer_prefix")) for r in backend.rules()])
        except Exception as exc:  # noqa: BLE001 - the dry run says so
            logger.warning("Builder: the tenant's access groups were not read for the plan (%s)", type(exc).__name__)
            return {"error": getattr(exc, "message", type(exc).__name__)}

    def _publish_access(model, plan: Optional[Mapping[str, Any]]) -> dict:
        """The access design onto the tenant as the approved plan says (``plan``, _access_effect): the groups it
        makes, by name under their parent, then the folder rules not there yet. Never deletes or moves anything
        live, and writes nothing the plan does not name."""
        design = model.get("access") or {}
        if not design.get("groups") and not design.get("rules"):
            return {"written": False, "why": "Der Entwurf entwirft keine Zugriffsgruppen."}
        if access_backend is None or not plan or plan.get("error"):
            return {"written": False, "why": (plan or {}).get("error")
                    or "Diese Plattform liest die Zugriffsgruppen des Brains nicht."}
        groups = {str(gr.get("key")): gr for gr in design.get("groups") or [] if isinstance(gr, Mapping)}
        try:
            backend = access_backend()
            tree = backend.tree()
            admin = next((k for k in tree.roots() if tree.is_admin(k)), None)
            ids, made = {}, 0
            for planned in plan.get("groups") or []:          # parents before their children
                parent_key = groups.get(planned["key"], {}).get("parent")
                parent = ids.get(parent_key) if parent_key else admin
                existing = tree.by_name(planned["name"])
                ids[planned["key"]] = existing or backend.create(planned["name"], parent, ())
                if not existing:
                    made += 1
                    tree = backend.tree()
            rules = 0
            for planned, r in zip(plan.get("rules") or [], [r for r in design.get("rules") or [] if isinstance(r, Mapping)]):
                if not planned["exists"]:
                    backend.create_rule(r["prefix"], [ids[gid] for gid in r.get("groups") or [] if gid in ids], ())
                    rules += 1
        except Exception as exc:  # noqa: BLE001 - reported, the publish stands
            logger.warning("Builder: the access design was not written (%s)", type(exc).__name__)
            return {"written": False, "why": getattr(exc, "message", type(exc).__name__)}
        return {"written": True, "groups": made, "rules": rules}

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
            "component_alerts": _component_overview(store)["alerts"],
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
        found = store.components(tenant) if store is not None and tenant and _tenant_wide() else []
        return {"components": found, "component_kinds": source_kinds.COMPONENT_KINDS,
                "planned_components": source_kinds.PLANNED_COMPONENTS}

    def _secrets():
        return source_secrets() if source_secrets is not None else None

    def _sources_view(store, model, workspace_id: Optional[str] = None) -> dict:
        tenant = _platform_tenant()
        components = {c["id"]: c for c in (store.components(tenant) if tenant else [])}
        secrets = _secrets()
        found = []
        if workspace_id is None:
            workspace = _current_workspace(store)
            workspace_id = workspace.id if workspace is not None else None
        published = {s.get("key"): s for s in ((store.published_model(workspace_id) if workspace_id and
                                                hasattr(store, "published_model") else None) or {}).get("sources") or []}
        for s in model.get("sources") or []:
            c = components.get(s.get("component"))
            kind = source_kinds.SOURCE_KINDS.get(s.get("kind"), {})
            on_site = bool(c and c.get("site_id"))
            found.append({**s, "kind_label": kind.get("label", s.get("kind")),
                          "access": source_kinds.access_of(s),
                          "access_label": source_kinds.ACCESS.get(source_kinds.access_of(s), source_kinds.access_of(s)),
                          "component_name": c["name"] if c else None,
                          "component_kind": c["kind"] if c else None,
                          "credential_label": kind.get("credential"),
                          "credential_required": bool(kind.get("credential_required")),
                          # A source on a site (MVP-24): its credential is a line of knovas-sources.env, never a value here.
                          "on_site": on_site,
                          # Its switches (MVP-25): as in the draft, as published, and why one is not offered.
                          "fetch": source_kinds.fetch_of(s), "write": source_kinds.write_of(s),
                          "fetch_published": source_kinds.fetch_of(published.get(s.get("key")) or {}),
                          "write_published": source_kinds.write_of(published.get(s.get("key")) or {}),
                          "fetch_reason": source_kinds.fetch_offered(s.get("kind")),
                          "write_reason": source_kinds.write_offered(s.get("kind"), c),
                          # Connector protocol 2.0 (draft, BUILDER_PROTOCOL_V2): the actions the component declared.
                          "v2_actions": _v2_actions(s.get("kind"), c),
                          **({"credential_line": site_rules.credential_line(s.get("key")), "site_name": c.get("site"),
                              "check_url": url_for("builder.component_check", component_id=c["id"])} if on_site else {})})
        kinds = {k: {"label": v["label"], "component": v["component"], "components": [v["component"], *v.get("also_on", ())],
                     "credential": v["credential"],
                     "credential_required": v["credential_required"],
                     "access": [{"value": m, "label": source_kinds.ACCESS[m]} for m in source_kinds.access_modes(k)],
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
            kind = source_kinds.SOURCE_KINDS.get(op.get("kind"), {})
            wanted = kind.get("component")
            if wanted and component["kind"] not in (wanted, *kind.get("also_on", ())):
                raise editing.EditError(f"Eine Quelle dieser Art liest nur ein {source_kinds.COMPONENT_KINDS[wanted]['label']}.")

    #: Who may bind a source to a component at a site: whoever manages the tenant's sites (BB-07, B2). A grant on one
    #: workspace never gives it (grants hold builder roles only), so a consultant cannot point a site at a source.
    SITE_BINDING_PERMISSION = "manage"
    SITE_BINDING_REFUSED = ("Eine Quelle an eine Komponente eines Standorts binden darf nur, wer die Standorte des "
                            "Mandanten verwaltet (Berechtigung «Zugänge verwalten»).")

    def _site_bound(store, sources_before: Mapping[str, Any], sources_after: Mapping[str, Any]) -> list:
        """The keys of sources that ``sources_after`` binds to a component at a site ({key: source}) and
        ``sources_before`` did not bind to that component."""
        tenant = _platform_tenant()
        out = []
        for key, after in sources_after.items():
            target = (after or {}).get("component")
            if not target or target == ((sources_before.get(key) or {}).get("component")):
                continue
            component = store.component(tenant, str(target), any_state=True) if tenant else None
            if component is not None and component_rules.on_site(component):
                out.append(key)
        return sorted(out)

    def _guard_site_bindings(store, mutate):
        """``mutate`` for store.edit, refused (403, nothing written) when its changes bind a source to a component
        at a site and the person may not manage the tenant's sites."""
        def run(model):
            changes = mutate(model)
            before = {str((c.get("before") or {}).get("key")): c.get("before") for c in changes
                      if str(c.get("path", "")).startswith("sources/") and c.get("before")}
            after = {str((c.get("after") or {}).get("key")): c.get("after") for c in changes
                     if str(c.get("path", "")).startswith("sources/") and c.get("after")}
            if _site_bound(store, before, after) and not g.builder_access.may(SITE_BINDING_PERMISSION):
                raise editing.EditError(SITE_BINDING_REFUSED, 403)
            return changes
        return run

    def _v2_actions(kind, component) -> list:
        from builder import protocol_v2
        return protocol_v2.actions_of(kind, component) if protocol_v2.enabled() else []

    def _check_switch_op(store, current, op) -> None:
        """Writing back is switched on only where the component serving the source can write to its kind (MVP-25)."""
        if not isinstance(op, Mapping) or op.get("op") != "source.switches" or op.get("access") != "read_write":
            return
        source = next((s for s in (store.model(current.id) or {}).get("sources") or [] if s.get("key") == op.get("key")), None)
        if source is None:
            return          # the edit itself says that the source does not exist
        if not source_kinds.SOURCE_KINDS.get(source.get("kind"), {}).get("write"):
            return          # the edit itself refuses a kind that cannot be written
        tenant = _platform_tenant()
        component = store.component(tenant, str(source.get("component"))) if tenant else None
        why = source_kinds.write_offered(source.get("kind"), component)
        if why:
            raise editing.EditError(why, 409)

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
        # «Zugriff» and «Zurückschreiben» (MVP-26): what is chosen, what may be written back and why not.
        verdicts = mapping_rules.invertible(defn, model)
        from builder import codes as code_texts

        writes = defn.get("writes") or {}
        write_types = [{"type": t, "facts": [{"name": f, "writable": ok, "reason": reason,
                                              # «Nicht umkehrbar: <explanation>», with the sub-reason's fix.
                                              "reason_title": code_texts.not_invertible(reason)["text"] if reason else None,
                                              "reason_text": code_texts.not_invertible(reason)["fix"] if reason else None,
                                              "on": f in (writes.get(t) or {}),
                                              "field": (writes.get(t) or {}).get(f, {}).get("field")
                                              or mapping_rules.write_field(defn, t, f),
                                              "allowed": (writes.get(t) or {}).get(f, {}).get("allowed")}
                                             for f, (ok, reason) in sorted(facts.items())]}
                       for t, facts in verdicts.items() if facts]
        access_panel = {"value": defn.get("access"), "groups": [{"key": g.get("key"), "name": g.get("name") or g.get("key")}
                                                                for g in (model.get("access") or {}).get("groups") or []]}
        write_panel = {"enabled": bool(src) and source_kinds.write_of(src), "types": write_types,
                       "source_name": (src or {}).get("name")}
        return {"id": defn.get("id"), "file": entry.get("file"), "description": defn.get("description") or "",
                "access": access_panel, "writes": write_panel, "explanation": mapping_rules.describe(model, entry),
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

    def _served_on_site(store, model, mapping_id: str) -> Optional[dict]:
        """The enrolled component that reads this mapping's source, if one does."""
        entry = _mapping_entry(model, mapping_id)
        source = next((s for s in model.get("sources") or []
                       if entry and s.get("key") == (entry.get("builder") or {}).get("source")), None)
        tenant = _platform_tenant()
        component = store.component(tenant, source.get("component")) if source and tenant else None
        return component if component and component_rules.on_site(component) else None

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
        current = _workspace_or_404(store, workspace_id)
        model = store.model(current.id)
        served = _served_on_site(store, model, mapping_id)
        if served is not None:
            return jsonify({"success": False, "error": "Diese Quelle liest eine Komponente bei der Firma; der Probelauf läuft dort.",
                            "url": url_for("builder.component_page", component_id=served["id"])}), 409
        # Nothing is published here: it plans the current state, so a mapping already published can be run again.
        ready, refusal = _connected(current)
        if refusal:
            return refusal
        tenant, engine = ready
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
        draft = store.draft(current.id)
        store.record_run(current.id, kind="dry_run", actor_id=gate.current_user().id, tenant=tenant,
                         content_hash=draft["content_hash"] if draft else package.content_hash(model),
                         bundle_hash=None, status=status,
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
        tenant = _platform_tenant()
        component = store.component(tenant, source.get("component")) if tenant and source.get("component") else None
        if component is not None and component.get("site_id"):
            # MVP-24: no credential of a source on a site is typed into the Platform.
            return jsonify({"success": False, "error": "Der Zugang dieser Quelle gehört an den Standort: die Zeile "
                            f"{site_rules.credential_line(key)}… in {site_rules.SOURCES_ENV_FILE} eintragen, danach neu starten und "
                            "Verbindung prüfen. In die Plattform wird er nicht eingegeben."}), 409
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
        return jsonify({"success": True, "history": _history_counts(current.id), "sources": _sources_view(store, model, current.id)})

    def _reader(store, current, source):
        from platform_settings import source_secret_key

        tenant = _platform_tenant()
        component = store.component(tenant, source.get("component")) if tenant else None
        if component is None:
            raise source_kinds.ReaderError("Die Komponente dieser Quelle ist nicht registriert.")
        secrets = _secrets()
        secret = secrets.get(source_secret_key(current.id, source["key"])) if secrets and source.get("credential_set") else None
        kind = source_kinds.SOURCE_KINDS.get(source.get("kind"), {})
        if component_rules.on_site(component):
            # Read at the company. The Platform reads nothing from a company's network; what it can design from is the
            # shape of a file, or a public source with a credential of its own.
            if source.get("kind") == "knovas_connector":
                return source_kinds.OnSiteFiles((component.get("source_checks") or {}).get(f"{current.id}/{source['key']}"))
            problem = source_kinds.on_site_problem(source.get("kind"), secret, site=bool(component.get("site_id")))
            if problem:
                raise source_kinds.ReaderError(problem)
        if reader_factory is None:
            raise source_kinds.ReaderError("Diese Plattform liest keine Quellen.")
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

    def _origin(rows) -> Optional[dict]:
        """The row the draft started from (an import or a creation), or None (a later draft starts from a publish)."""
        return next(({"kind": r["path"], "actor": str(r["actor"]) if r.get("actor") else None,
                      "actor_name": r.get("actor_name"), "at": r.get("at")}
                     for r in rows if r["path"] in review.ORIGINS), None)

    def _base(store, workspace_id: str) -> tuple[Optional[dict], Optional[str]]:
        """The newest published model and its content hash: what the draft is compared with (None, None before the
        first publish)."""
        base = store.published_model(workspace_id) if hasattr(store, "published_model") else None
        return base, (package.content_hash(base) if base is not None else None)

    def _effects(model, base) -> dict:
        """What publish writes beside the ontology, planned now: the access design on the tenant, the role mapping
        on the Platform (BB-07, B1)."""
        effects = {"access": _access_effect(model), "roles": _roles_effect(model, base)}
        rows = review.effect_rows(effects)
        counts = {s: sum(1 for r in rows if r["status"] == s) for s in ("create", "update", "refused", "not_configured")}
        return {**effects, "rows": rows, "counts": counts}

    def _destructive(rows, diff) -> list[dict]:
        """Retirements and removals: those of the change log (since the draft's start) and those against the published
        version. Each needs its own confirmation."""
        found = {g["path"]: g for g in review.destructive(review.change_set(rows))}
        for g in review.destructive(diff):
            found.setdefault(g["path"], g)
        return [found[p] for p in sorted(found)]

    def _latest_dry_run(runs, content_hash) -> Optional[dict]:
        """The newest dry run of this draft (not a mapping's: B8 plans one mapping, not the publish)."""
        return next((r for r in runs if r["kind"] == "dry_run" and r["content_hash"] == content_hash
                     and not (r.get("summary") or {}).get("mapping")), None)

    def _review_context(store, workspace) -> dict:
        draft = store.draft(workspace.id)
        rows = store.draft_changes(workspace.id) if draft else []
        author = request.args.get("autor") or None
        base, _ = _base(store, workspace.id)
        model = store.model(workspace.id) if draft else None
        # Everything a publish changes, against the published version (BB-07, B1): an import's whole content too.
        everything = review.model_diff(base, model, rows) if draft else review.change_set([])
        changes = review.model_diff(base, model, rows, author=author) if draft and author else everything
        runs = store.runs(workspace.id)
        content = draft["content_hash"] if draft else None
        dry_run = _latest_dry_run(runs, content) if draft else None
        reviewed = (dry_run or {}).get("summary", {}).get("review_hash") if dry_run else None
        mine = [a for a in store.approvals(workspace.id) if a["content_hash"] == content]
        approvals = [a for a in mine if reviewed and a.get("review_hash") == reviewed]
        me = str(gate.current_user().id)
        origin = _origin(rows)
        engine = _engine()
        return {
            "draft": draft, "changes": changes, "author": author, "authors": everything["authors"],
            "destructive": _destructive(rows, everything) if draft else [], "dry_run": dry_run, "approvals": approvals,
            "stale_approvals": [a for a in mine if a not in approvals], "review_hash": reviewed,
            "origin": origin, "is_origin_author": bool(origin and origin["actor"] == me),
            "base_number": (store.published_bundle(workspace.id) or {}).get("number") if base is not None else None,
            "runs": runs[:10], "is_author": me in _authors(rows),
            "may_self_approve": me in _authors(rows) and "admin" in set(gate.current_user().roles or ()),
            "engine_missing": engine.missing() if engine is not None else "Keine Veröffentlichung in dieser Umgebung.",
            "engine_version": engine.version() if engine is not None else None,
            "published": store.published_bundle(workspace.id),
            "comments": _comment_threads(store, workspace.id),
        }

    def _publishable(store, workspace_id: str):
        """The workspace, its draft and the tenant it publishes to -- or a refusal."""
        current = _workspace_or_404(store, workspace_id)
        draft = store.draft(current.id)
        if draft is None:
            return None, (jsonify({"success": False, "error": "Es gibt keinen Entwurf zu veröffentlichen."}), 409)
        ready, refusal = _connected(current)
        if refusal:
            return None, refusal
        return (current, draft, *ready), None

    def _connected(current):
        """(tenant, engine) of a workspace linked to this Platform's tenant -- or a refusal."""
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
        return (tenant, engine), None

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
        # Beside the ontology: everything else a publish changes (BB-07, B1) -- the model's diff against the published
        # version, the access design it writes on the tenant, the role mapping it writes on the Platform -- and the
        # digest of all of it, which an approval holds.
        base, base_hash = _base(store, current.id)
        diff = review.model_diff(base, model, store.draft_changes(current.id))
        effects = _effects(model, base)
        digest = review.review_hash(base_hash=base_hash, content_hash=draft["content_hash"], plan_rows=table["rows"],
                                    effects=effects)
        status = "refused" if table["counts"]["refused"] or effects["counts"]["refused"] else "ok"
        extra = {"effects": effects, "diff": review.diff_summary(diff), "diff_total": diff["total"],
                 "review_hash": digest, "base_hash": base_hash}
        store.record_run(current.id, kind="dry_run", actor_id=gate.current_user().id, tenant=tenant,
                         content_hash=draft["content_hash"], bundle_hash=compiled.bundle_hash, status=status,
                         summary={**table, "bundle_content_hash": compiled.content_hash,
                                  "engine": engine.version(), **extra})
        logger.info("Builder dry run: workspace %s, %s, beside the ontology %s", current.id, table["counts"],
                    effects["counts"])
        return jsonify({"success": True, "status": status, **table, **extra})

    @bp.route("/api/w/<workspace_id>/approve", methods=["POST"])
    def approve(workspace_id: str):
        """A reviewer approves the draft as dry-run; never one of its authors."""
        store = _store()
        current = _workspace_or_404(store, workspace_id)
        draft = store.draft(current.id)
        body = request.get_json(silent=True) or {}
        if draft is None or _hash_of(body) != draft["content_hash"]:
            return jsonify({"success": False, "error": "Der Entwurf hat sich seit dem Laden geändert."}), 409
        user = gate.current_user()
        own_changes, reason = str(user.id) in _authors(store.draft_changes(current.id)), None
        if own_changes:
            # Four eyes: an author does not approve. An administrator may, deliberately and with a reason; the
            # approval says so wherever it is shown.
            if "admin" not in set(user.roles or ()):
                return jsonify({"success": False, "error": "Freigeben kann nur, wer an diesem Entwurf nichts geändert hat."}), 403
            if body.get("self_approve") is not True:
                return jsonify({"success": False, "error": "Sie haben an diesem Entwurf mitgearbeitet. Als Administrator "
                                                          "geben Sie ihn als Selbstfreigabe mit Begründung frei."}), 403
            reason = str(body.get("reason") or "").strip()
            if not 3 <= len(reason) <= 500:
                return jsonify({"success": False, "error": "Eine Selbstfreigabe braucht eine Begründung (3 bis 500 Zeichen)."}), 400
        still_open = [c for c in store.comments(current.id) if not c["resolved_at"]]
        if still_open:
            return jsonify({"success": False, "error": f"Erst die offenen Kommentare klären ({len(still_open)})."}), 409
        # The approval holds the digest of what the newest dry run of this draft showed (BB-07, B1): the whole diff and
        # what publish writes beside the ontology. A page that sends the digest it showed must show the newest.
        dry = _latest_dry_run(store.runs(current.id), draft["content_hash"])
        digest = ((dry or {}).get("summary") or {}).get("review_hash") if dry and dry["status"] != "failed" else None
        if not digest:
            return jsonify({"success": False, "error": "Erst nach einem Probelauf dieses Stands."}), 409
        if body.get("review_hash") not in (None, "", digest):
            return jsonify({"success": False, "error": "Seit dem Laden lief ein neuer Probelauf mit anderem Ergebnis; "
                                                      "die Seite neu laden und ihn prüfen."}), 409
        # Self-approval of an import stays allowed (decision of 2026-11-06); it is recorded and shown as such.
        origin = _origin(store.draft_changes(current.id))
        own_import = bool(origin and origin["actor"] == str(user.id))
        if not store.approve(current.id, user.id, draft["content_hash"], own_changes=own_changes, reason=reason,
                             review_hash=digest, own_import=own_import):
            return jsonify({"success": False, "error": "Der Entwurf hat sich seit dem Laden geändert."}), 409
        logger.info("Builder approval: workspace %s%s%s", current.id,
                    ", own changes (administrator)" if own_changes else "",
                    ", by the person whose import or creation the draft started from" if own_import else "")
        return jsonify({"success": True, "review_hash": digest, "own_import": own_import})

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
        approved = [a for a in store.approvals(current.id) if a["content_hash"] == content]
        if not approved:
            return jsonify({"success": False, "error": "Dieser Stand ist nicht freigegeben."}), 409
        rows = store.draft_changes(current.id)
        model = store.model(current.id)
        base, base_hash = _base(store, current.id)
        destructive = sorted(g["path"] for g in _destructive(rows, review.model_diff(base, model, rows)))
        confirmed = sorted(str(p) for p in body.get("confirm_destructive") or [])
        if destructive and confirmed != destructive:
            return jsonify({"success": False, "error": "Stilllegungen brauchen eine eigene Bestätigung.",
                            "details": destructive}), 409
        try:
            compiled = engine.compile(model)
            plan = engine.plan(model, tenant)
        except Exception as exc:  # noqa: BLE001 - the engine's failure is the run's result
            logger.warning("Builder apply failed: workspace %s, %s", current.id, type(exc).__name__)
            store.record_run(current.id, kind="apply", actor_id=gate.current_user().id, tenant=tenant,
                             content_hash=content, bundle_hash=None, status="failed",
                             summary={"error": type(exc).__name__})
            return jsonify({"success": False, "error": "Das Veröffentlichen ist fehlgeschlagen.",
                            "details": [str(exc)[:500]]}), 502
        # The approval binds to exactly what the dry run showed (BB-07, B1): planned again now, the whole of what this
        # publish changes must have the digest an approval holds. A changed published state, tenant (its types, access
        # groups, folder rules) or role mapping makes it another publish, which nobody approved.
        effects = _effects(model, base)
        digest = review.review_hash(base_hash=base_hash, content_hash=content,
                                    plan_rows=publisher.summarise_plan(plan)["rows"], effects=effects)
        approved = [a for a in approved if a.get("review_hash") == digest]
        if not approved:
            return jsonify({"success": False, "error": "Was das Veröffentlichen jetzt ändert, ist nicht mehr das, was "
                            "freigegeben wurde (der Mandant, seine Zugriffsgruppen, die Rollen oder der veröffentlichte "
                            "Stand haben sich geändert). Erst einen neuen Probelauf prüfen und freigeben."}), 409
        try:
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
        # Who imported (or created) the draft's start and who approved it, for the audit (BB-07, B1).
        origin = _origin(rows)
        summary["audit"] = {
            "origin": origin["kind"] if origin else None, "imported_by": origin["actor"] if origin else None,
            "imported_by_name": origin["actor_name"] if origin else None,
            "approved_by": [{"id": a["reviewer"], "name": a.get("reviewer_name"), "own_import": a.get("own_import"),
                             "own_changes": a.get("own_changes")} for a in approved],
            "approved_by_importer": bool(origin and origin["actor"]
                                         and any(a["reviewer"] == origin["actor"] for a in approved)),
            "published_by": str(gate.current_user().id), "review_hash": digest}
        if number is not None:
            summary["run_event"] = _run_event(engine, model, number, status, summary)
            summary["views"] = _hand_to_views(compiled, number)
            summary["roles"] = _publish_roles(model)
            summary["access"] = _publish_access(model, effects.get("access"))
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
        # A source configured in the settings wins over the sync's, so this version is kept but not what they read.
        if views_source is not None and str(views_source() or "").strip() not in ("sync", "sync:"):
            return {"adopted": False, "elsewhere": True}
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
