"""The ask page of the Knovas Assistent (PMVP-22a, plan FEAT-2026-11-19-platform-mvp W-01, W-03, W-04).

``POST /fragen`` takes one question from the box on the search page, asks the assistant service once for the
signed-in person (``assistant.service.AssistantService``: it searches as that person and checks every statement)
and renders ``ask.html`` from the shared page parts:

- ``answered``: each statement with its numbered reference; the reference opens the statement's quote right under
  it (the document's title and page, the quote, a link that opens the document); then the passages;
- ``no_answer``: ``NO_ANSWER_TEXT`` and the closest passages;
- ``busy`` / ``failed``: the service's plain message and nothing else (POST-06);
- the number of statements the check removed, when it is not 0, never their text.

What the page shows of the reply (statement, quote, title) reaches it as text: autoescape is on and nothing here or
in the template is marked safe, so ``<script>``, ``**bold**`` or ``[x](http://...)`` come out as those characters
(FU-11). Every address on the page is built with ``url_for`` (FU-34); none is taken from the reply.

The switch: the installation setting ``assistant.enabled`` (PMVP-25; ``ASSISTANT_ENABLED`` in the environment pins
it), read through the settings' Resolver on every request by ``assistant_enabled``, the one place the page reads
it. Off, ``/fragen`` answers 404 and the search page's box offers only searching. The service comes from PMVP-25's
factory (``assistant.core.make_service``), which reads the same setting and the provider the customer chose.

The form: the box posts a hidden ``csrf_token`` field and this route checks it, as the other form posts of the app
do (login, account password, the console); app.py exempts the endpoint from its header gate for that reason, as it
does for those. A refused token is the form's usual 400 ("Formular ist abgelaufen").

Logs: one line per question with the status and counts. Never the question, a statement, a quote, a title or a
pointer (POST-04).

This file is ASCII-only (scripts/check_ascii_py.py): umlauts are ``\\u`` escapes.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from flask import Blueprint, Flask, abort, current_app, redirect, render_template, request, url_for

from assistant.service import MAX_QUESTION_BYTES, QUESTION_TOO_LONG_TEXT, STATUSES, Passage, Reply

logger = logging.getLogger(__name__)

#: The environment variable that pins the setting ``assistant.enabled`` (platform_settings.REGISTRY).
SWITCH_ENV = "ASSISTANT_ENABLED"

#: The key of ``app.extensions`` that holds the page's wiring (``service``: the service for this request, or
#: ``assistant.core.OFF``).
EXTENSION = "assistant"

EXPIRED_TEXT = "Formular ist abgelaufen. Bitte erneut versuchen."


def assistant_enabled(settings: Any = None) -> bool:
    """The switch, read on every request: the setting ``assistant.enabled`` through the settings' Resolver.

    ``settings`` defaults to the app's Resolver (``app.extensions["platform_settings"]``): the deployment's
    ``ASSISTANT_ENABLED`` pins it, else the settings page's value, else off. Whatever cannot be read counts as off.
    """
    from assistant import providers

    if settings is None:
        settings = current_app.extensions.get("platform_settings")
    if settings is None:
        return False
    try:
        return providers.is_enabled(settings.value(providers.SETTING_ENABLED))
    except Exception as exc:  # noqa: BLE001 - a switch that cannot be read is off
        logger.warning("Fragen: the switch is not readable (%s)", type(exc).__name__)
        return False


def removed_line(count: int) -> str:
    """The small line under an answer: how many statements the check left out (counts only, never their text)."""
    if count <= 0:
        return ""
    if count == 1:
        return "1 Aussage ohne Beleg ausgelassen"
    return f"{count} Aussagen ohne Beleg ausgelassen"


#: The stops that close a statement. The reference stands before them and is kept on their line (FU-35); the words
#: before it wrap as any text does.
_STOPS = frozenset(".!?:;\u2026")


def split_tail(text: str) -> Tuple[str, str]:
    """``(head, stop)`` of a statement: the reference goes between them.

    ``stop`` is the run of closing stops at the end (trailing white space dropped). A plain scan from the end, so
    its time grows with the length of that run and nothing else.
    """
    end = len(text.rstrip())
    start = end
    while start > 0 and text[start - 1] in _STOPS:
        start -= 1
    return text[:start], text[start:end]


@dataclass(frozen=True)
class ShownPassage:
    """A passage as the page shows it: its number, title and page, and the address that opens its document."""

    number: int
    title: str
    page: Optional[int]
    href: str


@dataclass(frozen=True)
class ShownStatement:
    """A statement as the page shows it, its reference, the id of its quote and the passage the quote is from."""

    head: str
    stop: str
    number: int
    quote: str
    quote_id: str
    passage: Optional[ShownPassage]


def _open_href(passage: Passage, pdf_preview: bool) -> str:
    """The address that opens the passage's document, built with url_for only (FU-34).

    As a search hit opens it: a PDF in the browser's own viewer at the passage's page, the passage marked (``s=``,
    its sentence number); anything else, or a PDF while the inline preview is off, as the file itself. The pointer is
    the path too (``Passage.pointer`` is the row's ``doc_id``, which the file routes resolve as the hit's path), so the
    file routes' check that the path belongs to the pointer holds. No question goes into the address.
    """
    pointer = passage.pointer
    if pdf_preview and pointer.lower().endswith(".pdf"):
        args: Dict[str, Any] = {"doc_id": pointer, "path": pointer}
        if passage.sentence is not None:
            args["s"] = str(passage.sentence)
        if passage.page is not None and passage.page > 0:
            args["_anchor"] = f"page={passage.page}"
        return url_for("preview_document", **args)
    return url_for("download_document", doc_id=pointer, path=pointer)


def shown_reply(reply: Reply, pdf_preview: bool) -> Dict[str, Any]:
    """What ``ask.html`` draws of a reply: plain values, the addresses built here."""
    passages = [ShownPassage(number=p.number, title=p.title, page=p.page, href=_open_href(p, pdf_preview))
                for p in reply.passages]
    by_number = {p.number: p for p in passages}
    statements: List[ShownStatement] = []
    for index, sentence in enumerate(reply.sentences, start=1):
        number = sentence.refs[0] if sentence.refs else 0
        head, stop = split_tail(sentence.text)
        statements.append(ShownStatement(head=head, stop=stop, number=number, quote=sentence.quote,
                                         quote_id=f"zitat-{index}", passage=by_number.get(number)))
    return {"statements": statements, "passages": passages, "removed": removed_line(reply.removed)}


def _plain(status: str, message: str) -> Reply:
    return Reply(status=status, message=message)


def create_assistant_blueprint(*, csrf_valid: Callable[[str], bool], grant: Callable[[List[Dict[str, Any]]], None],
                               page_context: Callable[[], Mapping[str, Any]],
                               pdf_preview: Callable[[], bool]) -> Blueprint:
    """The ask page's blueprint.

    Args:
        csrf_valid: the app's check of a submitted form token.
        grant: the app's ``_grant_for_current_user``: the passages the page shows may then be opened by this person
            through the file routes, as a search hit may.
        page_context: the template values of the Platform's layout (sidebar, brand, csrf token, asset version).
        pdf_preview: whether PDFs open in the browser's own viewer (``open.pdf_inline_in_browser``).
    """
    bp = Blueprint("assistant", __name__)

    @bp.app_context_processor
    def _assistant_box() -> Dict[str, Any]:
        # A function, so the switch is read only by a page that asks (the search page's box), not by every page.
        return {"assistant_box_on": assistant_enabled}

    @bp.route("/fragen", methods=["POST"])
    def ask():
        if not assistant_enabled():
            abort(404)
        context = dict(page_context())
        if not csrf_valid(str(request.form.get("csrf_token", "") or "")):
            logger.info("Fragen: status=expired")
            return _render(context, "", _plain("failed", EXPIRED_TEXT)), 400
        question = str(request.form.get("question", "") or "").strip()
        if not question:
            return redirect(url_for("index"), code=303)
        if len(question.encode("utf-8")) > MAX_QUESTION_BYTES:
            # Refused before the service (assistant/README.md), and not repeated as the page head either.
            reply = _plain("failed", QUESTION_TOO_LONG_TEXT)
            question = ""
        else:
            from assistant.core import OFF

            service = current_app.extensions[EXTENSION]["service"]()
            if service is OFF:  # switched off since the check above
                abort(404)
            reply = service.answer(question)
        if reply.status not in STATUSES:  # pragma: no cover - the service gives one of the four
            reply = _plain("failed", "")
        if reply.passages:
            grant([{"doc_id": p.pointer} for p in reply.passages])
        logger.info("Fragen: status=%s statements=%d passages=%d removed=%d",
                    reply.status, len(reply.sentences), len(reply.passages), reply.removed)
        return _render(context, question, reply)

    def _render(context: Dict[str, Any], question: str, reply: Reply):
        shown = shown_reply(reply, bool(pdf_preview())) if reply.status in ("answered", "no_answer") else {
            "statements": [], "passages": [], "removed": ""}
        return render_template(
            "ask.html",
            active_nav="fragen",
            question=question,
            status=reply.status,
            message=reply.message,
            **shown,
            **context,
        )

    return bp


def install_assistant(app: Flask, *, client: Any, identity_gate: Any, split: Callable[..., Any],
                      csrf_valid: Callable[[str], bool], grant: Callable[[List[Dict[str, Any]]], None],
                      page_context: Callable[[], Mapping[str, Any]], pdf_preview: Callable[[], bool]) -> None:
    """Register the ask page and the factory of its service (one line in app.py).

    The service is built per request, inside it, by PMVP-25's ``make_service`` on the settings' Resolver: so the
    switch and the provider apply without a restart, and the client signs every call for the signed-in person
    (``assistant/README.md``, "Wiring"). A test replaces ``app.extensions["assistant"]["service"]``.
    """

    def build_service():
        from assistant.core import make_service
        from web_interface import doc_fields_routes

        return make_service(app.extensions["platform_settings"], client, split=split,
                            user_key=lambda: doc_fields_routes.user_key_for(identity_gate))

    app.extensions[EXTENSION] = {"service": build_service}
    app.register_blueprint(create_assistant_blueprint(
        csrf_valid=csrf_valid, grant=grant, page_context=page_context, pdf_preview=pdf_preview))


__all__: Sequence[str] = (
    "EXPIRED_TEXT", "SWITCH_ENV", "assistant_enabled", "create_assistant_blueprint", "install_assistant",
    "removed_line", "shown_reply", "split_tail",
)
