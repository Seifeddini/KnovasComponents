"""The Brain Builder's shell: /builder and its eight stages.

Plan: "Knovas Brain Builder -- build plan" (Claude Docs), step B0. The builder
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

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from flask import Blueprint, abort, redirect, render_template, request, url_for

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


def create_builder_blueprint(gate, *, page_context: Callable[[], Mapping[str, Any]]):
    """Build the /builder blueprint.

    ``page_context`` returns what every builder page needs from the app:
    ``app_title``, ``brand``, ``asset_version`` and ``platform_url`` (where
    "Zur Plattform" leads).
    """
    bp = Blueprint("builder", __name__, url_prefix="/builder")

    @bp.before_request
    def _require_builder_role():
        user = gate.current_user()
        if user is None:
            return redirect(url_for("login", next=request.full_path or "/builder"))
        if not user_may_build(user):
            # 403, as the console does: the person is signed in and the
            # builder is not a secret, so saying "not for you" is honest and
            # makes a missing role easy to diagnose.
            abort(403)
        return None

    def _render(template: str, **values):
        user = gate.current_user()
        return render_template(
            template,
            **page_context(),
            stages=STAGES,
            me=user,
            # Workspaces arrive with the workspace model (step B1). Until then
            # the switcher says there is none, which is the truth.
            workspaces=(),
            current_workspace=None,
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
        return _render(
            "builder/stage.html",
            stage=current,
            stage_number=STAGES.index(current) + 1,
        )

    @bp.route("/_styleguide")
    def styleguide():
        # ?ansicht=hell|dunkel shows one theme; the screenshots use it.
        view = request.args.get("ansicht", "beide")
        if view not in STYLEGUIDE_VIEWS:
            view = "beide"
        return _render("builder/styleguide.html", stage=None, stage_number=None, view=view)

    return bp
