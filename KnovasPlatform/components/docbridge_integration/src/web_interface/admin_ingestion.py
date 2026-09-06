"""The Ingestion tab: what to index, when, how fast, behind which wall.

One profile, one form, one write (section B plan, "Ingestion administration").
The form edits an IngestionProfile; compile_profile is the only thing that
produces RemoteController documents; RemoteControllerClient.push is the only
thing that sends them. Saving is a guarded action, because a profile change
can widen or halt coverage (KC-B5-2).

Plan: docs/superpowers/plans/2026-09-02-admin-ingestion-tab.md
"""
from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any, Mapping
from uuid import UUID

from flask import render_template, request

from identity import audit
from identity.approvals import ApprovalService
from identity.ingestion_compiler import (
    IngestionProfile,
    ProfileError,
    SourceFolder,
    compile_profile,
    redact_for_support,
)
from identity import ingestion_presets as presets
from identity.ingestion_profiles import (
    IngestionProfileRepository,
    profile_from_json,
    profile_to_json,
)
from remote_controller_client import RemoteControllerError
from web_interface.guarded import run_guarded

logger = logging.getLogger(__name__)

MAX_FOLDER_ROWS = 12
KIND = "ingestion_profile_change"

#: Who can actually carry an approved profile change out. RemoteController's
#: gate admits `admin` and `ingestion_manager` only (RC/src/auth/
#: platform_principal.py::ADMIN_ROLES), while APPROVER_ROLES is
#: {approver, admin} -- so a pure approver can confirm the request and then
#: cannot execute it. Say that before a version row is written, not after
#: RemoteController answers 403.
EXECUTOR_ROLES = frozenset({"admin", "ingestion_manager"})

# German labels for the presets; the preset ids stay the compiler's.
LABELS = {
    "continuous": ("Laufend", "Neue und geaenderte Dokumente innerhalb weniger Minuten, den ganzen Tag."),
    "nightly": ("Nachts, ausserhalb der Buerozeiten", "Nur zwischen 19:00 und 06:00. Nichts laeuft, waehrend gearbeitet wird."),
    "manual": ("Nur wenn ich starte", "Laeuft einmal, wenn Sie auf Start druecken, und stoppt dann."),
    "gentle": ("Schonend", "Etwa 300 Dokumente pro Stunde. Keine spuerbare Last auf dem Dateiserver."),
    "normal": ("Normal", "Etwa 1'800 Dokumente pro Stunde. Die richtige Wahl fuer die meisten Kanzleien."),
    "fast": ("Schnell", "Etwa 7'200 Dokumente pro Stunde. Fuer den ersten Import, danach zuruecksetzen."),
    "documents": ("Dokumente", "Word, PDF, Text und Markdown."),
    "email": ("E-Mail", "Aus Outlook gespeicherte Nachrichten."),
}


# What "uebertragen" actually got the firm, in the words the tab uses. A push
# reaches RemoteController in three different states and only one of them is
# "the new folder list is being indexed right now"; saying so is the whole
# point of RemoteControllerClient.push returning an outcome (C2).
APPLIED_CLAUSES = {
    "started": "Abgleich gestartet.",
    "next_cycle": "wird beim naechsten Durchlauf wirksam.",
    "stored": "der Abgleich wird von Hand gestartet.",
}


def _requester(requested_by: str | None, actor) -> SimpleNamespace | None:
    """The person who asked, as something ``save_new_version`` can take.

    ``None`` when nobody else asked -- the actor requested and executed in
    one click -- or when the id is not a UUID, in which case attributing the
    row to the executor is the honest fallback.
    """
    if not requested_by or str(requested_by) == str(getattr(actor, "id", "")):
        return None
    try:
        return SimpleNamespace(id=UUID(str(requested_by)))
    except (TypeError, ValueError):
        logger.warning("requested_by ist keine UUID (%r); Version wird dem Ausfuehrenden "
                       "zugeschrieben.", requested_by)
        return None


def _applied_clause(result: Mapping[str, Any] | None) -> str:
    """The half-sentence that follows the version number in a save notice."""
    result = result or {}
    applied = str(result.get("applied") or "stored")
    text = "; " + APPLIED_CLAUSES.get(applied, APPLIED_CLAUSES["stored"])
    start_error = result.get("start_error")
    if start_error:
        text += f" Start fehlgeschlagen: {start_error}"
    return text


def _labelled(table: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, str]]:
    return {key: {"label": LABELS.get(key, (key, ""))[0],
                  "description": LABELS.get(key, (key, ""))[1]} for key in table}


def profile_from_form(form: Mapping[str, str], lists: Mapping[str, list[str]]) -> IngestionProfile:
    """Build the profile the form describes. Raises ProfileError with a
    sentence a person can act on; compile_profile validates the rest."""
    schedule = str(form.get("schedule", "") or "").strip()
    throughput = str(form.get("throughput", "") or "").strip()
    if schedule not in presets.SCHEDULE_PRESETS:
        raise ProfileError("Bitte einen der angebotenen Zeitplaene waehlen.")
    if throughput not in presets.THROUGHPUT_PRESETS:
        raise ProfileError("Bitte eine der angebotenen Geschwindigkeiten waehlen.")
    sources: list[SourceFolder] = []
    for n in range(MAX_FOLDER_ROWS):
        path = str(form.get(f"folder-{n}-path", "") or "").strip()
        if not path:
            continue
        sources.append(SourceFolder(
            path=path,
            recursive=str(form.get(f"folder-{n}-recursive", "") or "") == "1",
            access_groups=tuple(g for g in (lists.get(f"folder-{n}-groups") or []) if g),
        ))
    if not sources:
        raise ProfileError("Mindestens ein Ordner muss angegeben sein.")
    age = str(form.get("max_document_age_days", "") or "").strip()
    if age and not age.isdigit():
        raise ProfileError("Bitte die Altersgrenze als ganze Zahl in Tagen angeben.")
    return IngestionProfile(
        identifier_prefix=str(form.get("identifier_prefix", "") or "").strip(),
        sources=sources,
        file_types=[t for t in (lists.get("file_types") or []) if t] or ["documents"],
        schedule=schedule,
        throughput=throughput,
        max_document_age_days=int(age) if age else None,
        description=str(form.get("description", "") or "").strip(),
    )


def form_from_request(form: Mapping[str, str], lists: Mapping[str, list[str]]) -> dict[str, Any]:
    """Rebuild the template's form structure from the raw, unvalidated request.

    Used to re-render a person's own input after ``profile_from_form`` or
    ``compile_profile`` rejects it, so a validation error does not erase the
    folder rows and checkbox choices they were in the middle of fixing.
    ``dict(request.form)`` cannot do this: it keeps only the first value of a
    repeated field (``file_types``, ``folder-N-groups``), and the template
    expects ``form.folders`` as a list of rows, which the raw form never has.
    """
    folders: list[dict[str, Any]] = []
    for n in range(MAX_FOLDER_ROWS):
        path = str(form.get(f"folder-{n}-path", "") or "").strip()
        if not path:
            continue
        folders.append({
            "path": path,
            "recursive": str(form.get(f"folder-{n}-recursive", "") or "") == "1",
            "groups": [g for g in (lists.get(f"folder-{n}-groups") or []) if g],
        })
    return {
        "identifier_prefix": str(form.get("identifier_prefix", "") or "").strip(),
        "description": str(form.get("description", "") or "").strip(),
        "schedule": str(form.get("schedule", "") or "").strip(),
        "throughput": str(form.get("throughput", "") or "").strip(),
        "file_types": [t for t in (lists.get("file_types") or []) if t],
        "max_document_age_days": str(form.get("max_document_age_days", "") or "").strip(),
        "folders": folders,
    }


def form_from_profile(profile: IngestionProfile | None) -> dict[str, Any]:
    if profile is None:
        return {"identifier_prefix": "", "description": "", "schedule": "nightly",
                "throughput": "normal", "file_types": ["documents"],
                "max_document_age_days": "", "folders": []}
    return {
        "identifier_prefix": profile.identifier_prefix,
        "description": profile.description,
        "schedule": profile.schedule,
        "throughput": profile.throughput,
        "file_types": list(profile.file_types),
        "max_document_age_days": "" if profile.max_document_age_days is None else str(profile.max_document_age_days),
        "folders": [{"path": s.path, "recursive": bool(s.recursive),
                     "groups": list(s.access_groups)} for s in profile.sources],
    }


def apply_profile(payload: Mapping[str, Any], actor, *, conn, rc_client,
                  requested_by: str | None = None) -> dict:
    """Save (or reuse) a version and push it. Reached through
    ``execute_ingestion_change`` both when the actor may act alone and after
    a second person confirms.

    ``requested_by`` is the id of the person who asked, carried in the
    approval payload. When it differs from ``actor`` the version records
    both: ``created_by`` the requester, ``approved_by`` the executor. The
    audit row keeps ``actor``, because the executor is who acted.

    Never returns a ``failed`` list: every failure here raises, and
    ``_execute`` in admin_approvals then leaves the request approved and
    retryable.

    The version is saved *before* the push is attempted, on purpose: a push
    that fails still leaves a current-but-unpushed row behind, and that row
    is the record of what was attempted, not a bug to route around. What
    would be a bug is inserting a second, identical row on every retry -- so
    a save whose payload matches the current version, while that version is
    still unpushed, reuses it (no insert) and goes straight to push and
    ``mark_pushed`` again, rather than saving a new one.
    """
    profile = profile_from_json(payload["profile"])
    compiled = compile_profile(profile)
    repo = IngestionProfileRepository(conn)
    current = repo.current()
    if (current is not None and current.pushed_at is None
            and profile_to_json(current.profile) == payload["profile"]):
        version = current
    else:
        requester = _requester(requested_by, actor)
        version = repo.save_new_version(
            profile,
            by=actor if requester is None else requester,
            approved_by=None if requester is None else actor,
        )
    pushed = dict(rc_client.push(compiled) or {})
    applied = str(pushed.get("applied") or "stored")
    repo.mark_pushed(version.id)
    audit.record(conn, action="ingestion.profile_pushed", actor=actor,
                 target_type="ingestion_profile", target_id=f"default v{version.version}",
                 detail={"folders": len(profile.sources), "schedule": profile.schedule,
                         "applied": applied, "requested_by": requested_by})
    return {"version": version.version, "applied": applied,
            "start_error": pushed.get("start_error")}


def execute_stop(actor, *, conn, rc_client) -> dict:
    """Halt the sync and say so in the audit log. One implementation, two
    call sites: the ``stop`` route's guarded execute and the executor
    registry. They drifted once already -- the registry's inline lambda
    called ``stop()`` and wrote nothing, so an approved halt appeared only
    as ``approval.executed`` and never as ``ingestion.stopped``.
    """
    rc_client.stop()
    audit.record(conn, action="ingestion.stopped", actor=actor,
                 target_type="remote_controller", target_id="sync", detail={})
    return {"stopped": True}


def execute_ingestion_change(payload: Mapping[str, Any], actor, *, conn, rc_client) -> dict:
    """The executor registered for ``ingestion_profile_change``.

    One named function rather than a conditional lambda in the registry,
    because both branches have to audit and only one of them used to.
    """
    if payload.get("action") == "stop":
        return execute_stop(actor, conn=conn, rc_client=rc_client)
    if not (set(getattr(actor, "roles", ()) or ()) & EXECUTOR_ROLES):
        # Refuse here, before save_new_version: a pure approver would
        # otherwise leave a current-but-unpushed row behind and learn about
        # the role gap from RemoteController's 403.
        raise RemoteControllerError(
            "Die Ausfuehrung braucht die Rolle admin oder ingestion_manager; ein "
            "reiner Pruefer kann das Profil nicht an RemoteController uebertragen.",
            status=None,
        )
    return apply_profile(payload, actor, conn=conn, rc_client=rc_client,
                         requested_by=payload.get("requested_by"))


def attach_ingestion_routes(bp, gate, *, csrf_valid, csrf_token, page_context,
                            client_factory, rc_client_factory, require_ingestion):
    def _csrf_ok() -> bool:
        return csrf_valid(str(request.form.get("csrf_token", "") or ""))

    def _approvals() -> ApprovalService:
        return ApprovalService(gate.connection(), gate.users())

    def _lists() -> dict[str, list[str]]:
        return {key: request.form.getlist(key) for key in request.form.keys()}

    def _page(form=None, *, error=None, notice=None, status=200, preview=None, support_json=None):
        repo = IngestionProfileRepository(gate.connection())
        current = repo.current()
        rc_status: dict[str, Any] = {}
        try:
            rc_status = rc_client_factory().status()
        except Exception as exc:  # noqa: BLE001
            logger.warning("RemoteController-Status nicht abrufbar: %s", exc)
            rc_status = {"scheduler_state": "unbekannt"}
        groups = []
        try:
            groups = client_factory().access_groups()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Zugriffsgruppen nicht abrufbar: %s", exc)
        return render_template(
            "admin_ingestion.html",
            active_nav="admin",
            **page_context(),
            form=form if form is not None else form_from_profile(current.profile if current else None),
            schedules=_labelled(presets.SCHEDULE_PRESETS),
            throughputs=_labelled(presets.THROUGHPUT_PRESETS),
            file_types=_labelled(presets.FILE_TYPE_PRESETS),
            groups=groups,
            status=rc_status,
            current=current,
            versions=repo.versions(),
            preview=preview,
            support_json=support_json,
            me=gate.current_user(),
            error=error,
            notice=notice,
            csrf_token=csrf_token(),
        ), status

    def _queued_notice(req) -> str:
        return (f"Zur Freigabe eingereicht (Nr. {str(req.id)[:8]}). Das Profil wird erst "
                "nach Bestaetigung durch eine zweite Person uebernommen.")

    @bp.route("/ingestion")
    @require_ingestion
    def ingestion():
        return _page()

    @bp.route("/ingestion/preview", methods=["POST"])
    @require_ingestion
    def preview():
        csrf_ok = _csrf_ok()
        if not csrf_ok:
            return _page(error="Formular ist abgelaufen. Bitte erneut versuchen.", status=400)
        try:
            profile = profile_from_form(request.form, _lists())
            compile_profile(profile)
        except ProfileError as exc:
            return _page(form_from_request(request.form, _lists()), error=str(exc), status=400)
        summary = []
        rc = rc_client_factory()
        for source in profile.sources:
            try:
                found = rc.discover(root=source.path, max_depth=3)
                entries = found.get("entries") or []
                summary.append({
                    "path": source.path,
                    "files": sum(1 for e in entries if e.get("type") == "file"),
                    "folders": sum(1 for e in entries if e.get("type") == "directory"),
                    "truncated": bool(found.get("truncated")),
                    "error": None,
                })
            except (RemoteControllerError, PermissionError) as exc:
                summary.append({"path": source.path, "files": None, "folders": None,
                                "truncated": False, "error": str(exc)})
        return _page(form_from_profile(profile), preview=summary,
                     support_json=redact_for_support(profile),
                     notice="Vorschau erstellt. Noch nichts gespeichert.")

    @bp.route("/ingestion/save", methods=["POST"])
    @require_ingestion
    def save():
        csrf_ok = _csrf_ok()
        if not csrf_ok:
            return _page(error="Formular ist abgelaufen. Bitte erneut versuchen.", status=400)
        try:
            profile = profile_from_form(request.form, _lists())
            compile_profile(profile)
        except ProfileError as exc:
            return _page(form_from_request(request.form, _lists()), error=str(exc), status=400)
        me = gate.current_user()
        # requested_by rides in the payload so an approved change records
        # the person who asked as the version author, not the approver who
        # clicks. The direct-execute path ignores it (it is the same person).
        payload = {"profile": profile_to_json(profile), "requested_by": str(me.id)}
        try:
            outcome = run_guarded(
                _approvals(), me, kind=KIND, target_ref="ingestion_profile:default",
                payload=payload,
                execute=lambda: execute_ingestion_change(payload, me, conn=gate.connection(),
                                                         rc_client=rc_client_factory()),
            )
        except (RemoteControllerError, PermissionError) as exc:
            return _page(form_from_profile(profile),
                         error=f"RemoteController hat das Profil nicht uebernommen: {exc}", status=502)
        if outcome.queued:
            return _page(form_from_profile(profile), notice=_queued_notice(outcome.request))
        return _page(notice=(f"Profil gespeichert und uebertragen "
                             f"(Version {outcome.result['version']})"
                             f"{_applied_clause(outcome.result)}"))

    @bp.route("/ingestion/restore/<int:version>", methods=["POST"])
    @require_ingestion
    def restore(version):
        csrf_ok = _csrf_ok()
        if not csrf_ok:
            return _page(error="Formular ist abgelaufen. Bitte erneut versuchen.", status=400)
        repo = IngestionProfileRepository(gate.connection())
        old = next((v for v in repo.versions() if v.version == version), None)
        if old is None:
            return _page(error=f"Version {version} gibt es nicht.", status=404)
        me = gate.current_user()
        payload = {"profile": profile_to_json(old.profile), "requested_by": str(me.id)}
        try:
            outcome = run_guarded(
                _approvals(), me, kind=KIND, target_ref=f"ingestion_profile:default@v{version}",
                payload=payload,
                execute=lambda: execute_ingestion_change(payload, me, conn=gate.connection(),
                                                         rc_client=rc_client_factory()),
            )
        except (RemoteControllerError, PermissionError) as exc:
            return _page(error=f"Wiederherstellen fehlgeschlagen: {exc}", status=502)
        if outcome.queued:
            return _page(notice=_queued_notice(outcome.request))
        return _page(notice=(f"Version {version} wiederhergestellt als Version "
                             f"{outcome.result['version']}"
                             f"{_applied_clause(outcome.result)}"))

    @bp.route("/ingestion/start", methods=["POST"])
    @require_ingestion
    def start():
        csrf_ok = _csrf_ok()
        if not csrf_ok:
            return _page(error="Formular ist abgelaufen. Bitte erneut versuchen.", status=400)
        me = gate.current_user()
        try:
            rc_client_factory().start()
        except (RemoteControllerError, PermissionError) as exc:
            return _page(error=f"Start fehlgeschlagen: {exc}", status=502)
        audit.record(gate.connection(), action="ingestion.started", actor=me,
                     target_type="remote_controller", target_id="sync", detail={})
        return _page(notice="Abgleich gestartet.")

    @bp.route("/ingestion/stop", methods=["POST"])
    @require_ingestion
    def stop():
        csrf_ok = _csrf_ok()
        if not csrf_ok:
            return _page(error="Formular ist abgelaufen. Bitte erneut versuchen.", status=400)
        me = gate.current_user()
        try:
            outcome = run_guarded(
                _approvals(), me, kind=KIND, target_ref="remote_controller:stop",
                payload={"action": "stop"},
                execute=lambda: execute_stop(me, conn=gate.connection(),
                                             rc_client=rc_client_factory()),
            )
        except (RemoteControllerError, PermissionError) as exc:
            return _page(error=f"Stopp fehlgeschlagen: {exc}", status=502)
        if outcome.queued:
            return _page(notice=_queued_notice(outcome.request))
        return _page(notice="Abgleich angehalten.")

    return bp
