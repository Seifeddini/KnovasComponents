"""The two files of a run.

``report.json`` is for a pull request, a ticket or a later comparison: numbers, ids, kinds, statuses and class
names, and no customer text (no question, answer, document name or exception message).

``judging.md`` is for the people who judge (plan section 11: the automatic verdict is a first pass). It holds the
questions, the answers and the document names, and it is the only place of a run that does. It is written in
German, the judges' language. Keep it out of the repository and out of tickets.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Tuple

from pmvp_harness.pointers import normalise
from pmvp_harness.runner import MVP_SET_SIZE, MVP_TARGET_OK, Report, Row, status_label
from pmvp_harness.scoring import ANSWERING_KINDS, passages_of, refs_of, sentences_of

logger = logging.getLogger(__name__)

REPORT_FORMAT = 1


def _json_row(row: Row) -> Dict[str, Any]:
    marks = row.score
    out: Dict[str, Any] = {
        "id": row.question.id,
        "kind": row.question.kind,
        "in_mvp_set": row.question.in_mvp_set,
        "ok": row.ok,
        "error": row.error,
        "status": None if marks is None else status_label(marks.status),
    }
    if marks is not None:
        out.update({
            "sentences": marks.sentences_shown,
            "passages": marks.passages,
            "refs_shown": marks.refs_shown,
            "refs_matching": marks.refs_matching,
            "unreferenced_shown": marks.unreferenced_shown,
            "source_hit": marks.source_hit,
            "points_found": list(marks.points_found),
            "leaked": len(marks.leaked_pointers),
            "unknown_pointers": marks.unknown_pointers,
            "removed": marks.removed,
        })
    return out


def report_data(report: Report) -> Dict[str, Any]:
    """What ``report.json`` holds."""
    return {"format": REPORT_FORMAT, "summary": report.summary(), "rows": [_json_row(row) for row in report.rows]}


# --- the judging sheet ------------------------------------------------------------------------------


def _line(text: object) -> str:
    """Customer text on one line, otherwise as it is."""
    return " ".join(str(text or "").split())


def _code(text: object) -> str:
    """A document name as a code span, so an underscore or a star in it stays what it is."""
    value = _line(text)
    return f"`` {value} ``" if "`" in value else f"`{value}`"


def _quote(text: object) -> List[str]:
    lines = str(text or "").splitlines() or [""]
    return [f"> {line}".rstrip() for line in lines]


#: The file's and the contract's words, said for a judge; the word itself follows in brackets.
_KIND_WORDS = {"fact": "Tatsache", "document": "Dokument", "process": "Ablauf",
               "negative": "ohne Antwort in den Quellen", "rights": "Rechte"}
_STATUS_WORDS = {"answered": "beantwortet", "no_answer": "keine Antwort gefunden", "busy": "ausgelastet",
                 "failed": "fehlgeschlagen", "other": "unbekannt"}


def _answer(row: Row) -> List[str]:
    reply, marks = row.reply, row.score
    status = status_label(marks.status)
    out = [f"**Gezeigte Antwort** (Status: {_STATUS_WORDS[status]} [{status}])", ""]
    sentences = sentences_of(reply)
    for number, sentence in enumerate(sentences, start=1):
        refs = "".join(f"[{ref}]" for ref in refs_of(sentence)) or "(ohne Verweis)"
        out.append(f"{number}. {_line(getattr(sentence, 'text', ''))} {refs}")
    message = getattr(reply, "message", None)
    if message:
        if sentences:
            out.append("")
        out += _quote(message)
    if not sentences and not message:
        out.append("(nichts gezeigt)")
    raw = getattr(reply, "raw_answer", None)
    if raw:
        out += ["", "**Ungeprüfte Antwort des Modells** (vor der Prüfung der Verweise; zeigt, was die "
                    "Prüfung entfernt hat)", ""]
        out += _quote(raw)
    return out


def _passages(row: Row, pointer_prefix: str) -> List[str]:
    passages = passages_of(row.reply)
    if not passages:
        return ["**Stellen:** keine"]
    cited = {ref for sentence in sentences_of(row.reply) for ref in refs_of(sentence)}
    supporting = {normalise(pointer) for pointer in row.question.supporting_pointers}
    leaked = {normalise(pointer) for pointer in row.score.leaked_pointers}
    out = ["**Stellen**", "", "| Nr. | Dokument | Seite | Hinweis |", "|---|---|---|---|"]
    for passage in passages:
        number = getattr(passage, "number", "")
        pointer = getattr(passage, "pointer", "")
        spelled = normalise(pointer, pointer_prefix)
        notes = []
        if number in cited:
            notes.append("zitiert")
        if not spelled:
            notes.append("**ohne Dokument, nicht prüfbar**")
        elif spelled in leaked:
            notes.append("**nicht erlaubt**")
        elif spelled in supporting:
            notes.append("Belegdokument")
        page = getattr(passage, "page", None)
        document = _code(pointer).replace('|', chr(92) + '|') if spelled else "(keines genannt)"
        out.append(f"| {number} | {document} | {'' if page is None else page} | {', '.join(notes)} |")
    return out


def _marks(row: Row) -> List[str]:
    marks = row.score
    out = [f"**Automatische Prüfung: {'ok' if marks.ok else 'nicht ok'}**", "",
           f"- Verweise: {marks.refs_shown} gezeigt, {marks.refs_matching} passend",
           f"- Sätze ohne Verweis: {marks.unreferenced_shown}"]
    if row.question.kind in ANSWERING_KINDS:
        out.append(f"- Belegdokument zitiert: {'ja' if marks.source_hit else 'nein'}")
    out.append(f"- Von der Prüfung entfernte Sätze (removed): {marks.removed}")
    if marks.leaked_pointers:
        leaked = ", ".join(_code(pointer) for pointer in marks.leaked_pointers)
    else:
        leaked = "keine"
    out.append(f"- Nicht erlaubte Dokumente unter den Stellen: {leaked}")
    out.append(f"- Stellen aus Dokumenten, die die Wahrheitsdateien nicht kennen: {marks.unknown_pointers} von "
               f"{marks.passages}")
    return out


def _question_block(position: int, row: Row, pointer_prefix: str) -> List[str]:
    question = row.question
    asked_as = question.asked_as or "ohne bestimmte Person"
    out = [f"## {position}. {question.id}", "",
           f"Art: {_KIND_WORDS.get(question.kind, 'unbekannt')} [{question.kind}] · "
           f"im Messsatz: {'ja' if question.in_mvp_set else 'nein'} · "
           f"gefragt als: {asked_as}", "",
           f"**Frage:** {_line(question.question)}", ""]
    if question.expected_points:
        found = row.score.points_found if row.score is not None else ()
        out.append("**Erwartet** (Hinweis für das Urteil, wörtlich in der Antwort gesucht)")
        out.append("")
        for index, point in enumerate(question.expected_points):
            mark = "gefunden" if index < len(found) and found[index] else "nicht gefunden"
            out.append(f"- {_line(point)} ({mark})")
        out.append("")
    if question.supporting_pointers:
        label = "Dokumente, die diese Person nicht sehen darf" if question.kind == "rights" else "Belegdokumente"
        out.append(f"**{label}**")
        out.append("")
        out += [f"- {_code(pointer)}" for pointer in question.supporting_pointers]
        out.append("")
    if row.score is None:
        out += [f"**Keine Antwort erhalten.** Fehlerklasse: {row.error or 'unbekannt'}", "",
                "**Automatische Prüfung: nicht ok**", ""]
    else:
        out += _answer(row) + [""] + _passages(row, pointer_prefix) + [""] + _marks(row) + [""]
    out += ["**Urteil:** [ ] richtig   [ ] falsch   [ ] unklar", "", "Bemerkung:", "", "---", ""]
    return out


def judging_sheet(report: Report) -> str:
    """The judging sheet as Markdown. Holds customer text."""
    summary = report.summary()
    measured = summary["mvp_set"]
    out = [
        "# Beurteilungsbogen Knovas Assistent", "",
        f"Fragen: {summary['questions']} · automatisch ok: {summary['ok']} · ohne Antwort wegen Fehler: "
        f"{summary['errors']} · Messsatz: {measured['ok']} von {measured['questions']} ok "
        f"(Ziel des Plans: mindestens {MVP_TARGET_OK} von {MVP_SET_SIZE})", "",
        "Die automatische Prüfung ist ein erster Durchgang. Sie prüft Verweise, Belegdokumente und Rechte, "
        "nicht ob die Antwort stimmt. Das entscheiden Sie: bitte bei jeder Frage ein Urteil ankreuzen.", "",
        "Dieser Bogen enthält Fragen, Antworten und Dokumentnamen. Er gehört weder ins Repository noch in "
        "ein Ticket.", "", "---", "",
    ]
    for position, row in enumerate(report.rows, start=1):
        out += _question_block(position, row, report.pointer_prefix)
    return "\n".join(out)


def write_report(report: Report, dest) -> Tuple[Path, Path]:
    """Write ``report.json`` and ``judging.md`` into the folder ``dest`` (made when missing); return both paths."""
    folder = Path(dest)
    folder.mkdir(parents=True, exist_ok=True)
    report_path, sheet_path = folder / "report.json", folder / "judging.md"
    report_path.write_text(json.dumps(report_data(report), ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8", newline="\n")
    sheet_path.write_text(judging_sheet(report), encoding="utf-8", newline="\n")
    logger.info("pmvp harness: report written, rows=%d", len(report.rows))
    return report_path, sheet_path
