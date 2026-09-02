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
from typing import Any, Mapping

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
    return IngestionProfile(
        identifier_prefix=str(form.get("identifier_prefix", "") or "").strip(),
        sources=sources,
        file_types=[t for t in (lists.get("file_types") or []) if t] or ["documents"],
        schedule=schedule,
        throughput=throughput,
        max_document_age_days=int(age) if age.isdigit() else None,
        description=str(form.get("description", "") or "").strip(),
    )


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


def apply_profile(payload: Mapping[str, Any], actor, *, conn, rc_client) -> dict:
    """Save a new version and push it. The executor for ingestion_profile_change,
    used both when the actor may act alone and after a second person confirms."""
    profile = profile_from_json(payload["profile"])
    compiled = compile_profile(profile)
    repo = IngestionProfileRepository(conn)
    version = repo.save_new_version(profile, by=actor)
    rc_client.push(compiled)
    repo.mark_pushed(version.id)
    audit.record(conn, action="ingestion.profile_pushed", actor=actor,
                 target_type="ingestion_profile", target_id=f"default v{version.version}",
                 detail={"folders": len(profile.sources), "schedule": profile.schedule})
    return {"version": version.version}


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
            return _page(dict(request.form), error=str(exc), status=400)
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
            return _page(dict(request.form), error=str(exc), status=400)
        me = gate.current_user()
        payload = {"profile": profile_to_json(profile)}
        try:
            outcome = run_guarded(
                _approvals(), me, kind=KIND, target_ref="ingestion_profile:default",
                payload=payload,
                execute=lambda: apply_profile(payload, me, conn=gate.connection(),
                                              rc_client=rc_client_factory()),
            )
        except (RemoteControllerError, PermissionError) as exc:
            return _page(form_from_profile(profile),
                         error=f"RemoteController hat das Profil nicht uebernommen: {exc}", status=502)
        if outcome.queued:
            return _page(form_from_profile(profile), notice=_queued_notice(outcome.request))
        return _page(notice=f"Profil gespeichert und uebertragen (Version {outcome.result['version']}).")

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
        payload = {"profile": profile_to_json(old.profile)}
        try:
            outcome = run_guarded(
                _approvals(), me, kind=KIND, target_ref=f"ingestion_profile:default@v{version}",
                payload=payload,
                execute=lambda: apply_profile(payload, me, conn=gate.connection(),
                                              rc_client=rc_client_factory()),
            )
        except (RemoteControllerError, PermissionError) as exc:
            return _page(error=f"Wiederherstellen fehlgeschlagen: {exc}", status=502)
        if outcome.queued:
            return _page(notice=_queued_notice(outcome.request))
        return _page(notice=f"Version {version} wiederhergestellt als Version {outcome.result['version']}.")

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

        def _halt():
            rc_client_factory().stop()
            audit.record(gate.connection(), action="ingestion.stopped", actor=me,
                         target_type="remote_controller", target_id="sync", detail={})
            return {"stopped": True}

        try:
            outcome = run_guarded(_approvals(), me, kind=KIND, target_ref="remote_controller:stop",
                                  payload={"action": "stop"}, execute=_halt)
        except (RemoteControllerError, PermissionError) as exc:
            return _page(error=f"Stopp fehlgeschlagen: {exc}", status=502)
        if outcome.queued:
            return _page(notice=_queued_notice(outcome.request))
        return _page(notice="Abgleich angehalten.")

    return bp
