"""The Brain Builder's shell: /builder, its eight stages and the workspaces.

Plan: "Knovas Brain Builder -- build plan" (Claude Docs), steps B0 and B1
(workspaces: list, import of a configuration package, overview, export). The builder
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

from builder import ontology_view, package

logger = logging.getLogger(__name__)

#: Roles that open the builder. Step B11 replaces this with the builder roles.
BUILDER_ROLES = frozenset({"admin"})


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


def _download_name(name: str) -> str:
    """A file name for the export that every browser and file system takes."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower().translate(str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}))).strip("-")
    return f"{slug or 'arbeitsbereich'}-konfiguration.zip"


def create_builder_blueprint(
    gate,
    *,
    page_context: Callable[[], Mapping[str, Any]],
    store_factory: Optional[Callable[[], Any]] = None,
):
    """Build the /builder blueprint.

    ``page_context`` returns what every builder page needs from the app:
    ``app_title``, ``brand``, ``asset_version``, ``platform_url`` (where
    "Zur Plattform" leads) and ``csrf_token`` (the import dialog sends it as
    the X-CSRF-Token header the app's gate checks).

    ``store_factory`` returns the workspace store for this request
    (``builder.store.WorkspaceStore`` on the request's connection). Without
    one there are no workspaces and the import is refused.
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
        return CHANGE_LABELS.get(path, path)

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
            **values,
        )

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
        if current.slug == "ontologie":
            store = _store()
            workspace = _current_workspace(store)
            if workspace is not None:
                return _render("builder/ontology.html", stage=current, stage_number=number,
                               current_workspace=workspace,
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
        return _render(
            "builder/workspace.html",
            stage=STAGES[0], stage_number=1, current_workspace=current,
            changes=store.changes(current.id, limit=10),
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
        result = package.read_package(package.blank_package(name))
        workspace_id = store.create(name=name, model=result.model, actor_id=gate.current_user().id,
                                    source={"kind": "blank"})
        logger.info("Builder workspace created: %s", workspace_id)
        session[SESSION_WORKSPACE] = workspace_id
        return jsonify({"success": True, "url": url_for("builder.workspace", workspace_id=workspace_id)}), 201

    @bp.route("/_styleguide")
    def styleguide():
        # ?ansicht=hell|dunkel shows one theme; the screenshots use it.
        view = request.args.get("ansicht", "beide")
        if view not in STYLEGUIDE_VIEWS:
            view = "beide"
        return _render("builder/styleguide.html", stage=None, stage_number=None, view=view)

    return bp
