"""The pages of the Knovas Assistent (PMVP-22a, PMVP-23; plan FEAT-2026-11-19-platform-mvp W-01, W-03, W-04, W-06,
W-07; contract L2-PLATFORM-ASSISTANT).

The addresses (blueprint ``assistant``):

- ``POST /fragen`` (``ask``): one question from the box on the search page. The service answers once for the
  signed-in person (``assistant.service.AssistantService``: it searches as that person and checks every statement).
  ``answered`` and ``no_answer`` become that person's chat (``identity.chats.ChatStore``): a new chat titled with the
  question, the question and the answer stored in one transaction, the reply's passages granted, and a 303 to the
  chat at its newest turn. ``busy`` and ``failed`` store nothing: the page shows the plain message and the question
  stands in the box again.
- ``GET /fragen/<chat id>`` (``chat``): the chat, every turn in order, each answer drawn as it was when new; the box
  "Nachfragen oder etwas Neues fragen ..." and "Gespraech loeschen".
- ``POST /fragen/<chat id>`` (``follow_up``): a follow-up in that chat (W-06, SPEC-05). The service gets the chat's
  turns as history (a question as typed, an answer as its statements' text, one per line: never a quote, a passage
  or the stored JSON; POST-02) and searches the chat's last question and the follow-up together (decision D1).
  Stored and answered with a 303 as a question is; ``busy`` / ``failed``: the chat as it was, the message under the
  last turn, the question in the box, nothing stored.
- ``POST /fragen/<chat id>/loeschen`` (``delete_chat``): deletes the chat with its messages and references; 303 to
  the search page.
- ``GET /fragen/<chat id>/quelle/<turn>/<number>`` (``source``): opens the document of reference ``number`` of answer
  ``turn`` (1 is the first answer). It asks the Core whether the person may read that document
  (``document_readable``, as the person), grants that one document, and redirects where ``_open_href`` sends a
  passage (the dossier's pattern, PMVP-30a). A stored chat outlives the grants of its answer (an hour).
- ``GET /fragen/verlauf`` (``history``): every chat of the person, 50 a page, newest first.
- the sidebar's block (``assistant_chats``, for ``_sidebar.html``): "+ Neue Frage" and the person's newest 20
  chats, grouped by their last change; "Alle Gespraeche" when there are more. A failed read draws no block.

How an answer is stored (no migration): the assistant message's text is one JSON object, the answer as shown
(``stored_answer``); its references are the reply's passages with the passage text as search returned it
(decision D2: read by its owner only, deleted with the chat). A message that does not parse is drawn as one plain
paragraph without chips. Before anything is stored, NUL characters are removed, a page outside 1 to 2^31-1 becomes
no page and a reference without a pointer is left out (FU-01); a question and its answer (and a new chat) go into
one transaction, and a failed write shows the reply once, unsaved, with ``NOT_SAVED_TEXT`` (never a 500).

Owner only: every address reads and writes through ``ChatStore`` with the signed-in person as the owner; no route
takes an owner from the request. Another person's chat, a missing one and a malformed id give the same 404, before
anything is asked or granted, and the same log line.

Text stays text: autoescape is on, nothing here or in the templates is marked safe, so ``<script>``, ``**bold**`` or
``[x](http://...)`` come out as those characters (FU-11). Every address is built with ``url_for`` (FU-34); none is
taken from a reply or a title.

The switch: the installation setting ``assistant.enabled`` (PMVP-25; ``ASSISTANT_ENABLED`` pins it), read through the
settings' Resolver on every request by ``assistant_enabled``. Off, every address here answers 404 and no chat list is
drawn; the service comes from PMVP-25's factory (``assistant.core.make_service``).

The forms: each form post carries a hidden ``csrf_token`` field, checked here before anything is read; app.py exempts
these endpoints from its header gate for that reason. A refused token is the form's usual 400 ("Formular ist
abgelaufen").

Logs: one line per request with the status and counts. Never a question, a title, a statement, a quote, a pointer or
a passage (POST-04).

Deletion after 90 days (D3): ``install_assistant`` starts ``identity.chats.ChatRetentionJob`` whenever personal
accounts are on, whatever the switch says.

This file is ASCII-only (scripts/check_ascii_py.py): umlauts are ``\\u`` escapes.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from flask import Blueprint, Flask, abort, current_app, g, make_response, redirect, render_template, request, url_for

from assistant.service import (MAX_QUESTION_BYTES, NO_ANSWER_TEXT, QUESTION_TOO_LONG_TEXT, STATUSES, Passage, Reply,
                               Sentence, Turn, cut_to_bytes)

logger = logging.getLogger(__name__)

#: The environment variable that pins the setting ``assistant.enabled`` (platform_settings.REGISTRY).
SWITCH_ENV = "ASSISTANT_ENABLED"

#: The key of ``app.extensions`` that holds the page's wiring (``service``: the service for this request, or
#: ``assistant.core.OFF``; ``retention``: the running ``ChatRetentionJob`` or None).
EXTENSION = "assistant"

EXPIRED_TEXT = "Formular ist abgelaufen. Bitte erneut versuchen."
#: Shown under a reply whose chat could not be written (FU-01): the reply is shown once, unsaved.
NOT_SAVED_TEXT = "Dieses Gespr\u00e4ch konnte nicht gespeichert werden."
#: The page of all chats when the store cannot be read.
LIST_UNAVAILABLE_TEXT = ("Die Gespr\u00e4che k\u00f6nnen gerade nicht geladen werden. "
                         "Bitte versuchen Sie es in einem Moment noch einmal.")

#: A chat's page when the chat cannot be read (the database is down; PMVP-24): status 503, never a 500.
CHAT_UNAVAILABLE_TEXT = ("Das Gespr\u00e4ch kann gerade nicht geladen werden. "
                         "Bitte versuchen Sie es in einem Moment noch einmal.")

#: The sidebar lists the newest this many chats (D4); "Alle Gespraeche" when there are more.
SIDEBAR_CHATS = 20
#: The page of all chats shows this many at a time.
HISTORY_PAGE_SIZE = 50
#: The last page number the page of all chats accepts (an offset beyond any person's chats).
MAX_HISTORY_PAGE = 100_000
#: The version of the stored answer's JSON.
STORED_VERSION = 1
#: The statuses that become a chat; ``busy`` and ``failed`` store nothing.
STORED_STATUSES = ("answered", "no_answer")
#: A page number outside 1 to this is stored as no page (FU-01: the column is a 32-bit integer).
MAX_STORED_INT = 2 ** 31 - 1
ELLIPSIS = "\u2026"
NUL = "\x00"

try:  # Dates the way people in the firm read them (as experiments/indexer.py); UTC when tzdata is missing.
    from zoneinfo import ZoneInfo

    LOCAL_TZ: Any = ZoneInfo("Europe/Zurich")
except Exception:  # noqa: BLE001
    LOCAL_TZ = timezone.utc

GROUP_TODAY = "Heute"
GROUP_WEEK = "Diese Woche"
GROUP_EARLIER = "Fr\u00fcher"


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


@dataclass(frozen=True)
class ShownTurn:
    """One question and its answer as ``ask.html`` draws them.

    ``status``: one of ``STATUSES``, ``"stored"`` (a stored answer that does not parse: ``message`` is its text, one
    plain paragraph) or ``""`` (a question without an answer). ``anchor``: the element id the 303 lands on.
    ``notice``: a line under the answer (``NOT_SAVED_TEXT``).
    """

    question: str
    status: str
    message: str = ""
    statements: List[ShownStatement] = field(default_factory=list)
    passages: List[ShownPassage] = field(default_factory=list)
    removed: str = ""
    anchor: str = ""
    notice: str = ""


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


def shown_reply(reply: Reply, pdf_preview: bool, *, turn: Optional[int] = None,
                href: Optional[Callable[[Passage], str]] = None) -> Dict[str, Any]:
    """What ``ask.html`` draws of a reply: plain values, the addresses built here.

    ``turn``: the answer's place in its chat (1 is the first); the quote ids are then ``zitat-<turn>-<i>``, unique
    on a page of several answers. ``href``: the address of a passage's document (a stored chat's ``source`` route);
    without it, ``_open_href``.
    """
    open_href = href or (lambda passage: _open_href(passage, pdf_preview))
    passages = [ShownPassage(number=p.number, title=p.title, page=p.page, href=open_href(p)) for p in reply.passages]
    by_number = {p.number: p for p in passages}
    statements: List[ShownStatement] = []
    for index, sentence in enumerate(reply.sentences, start=1):
        number = sentence.refs[0] if sentence.refs else 0
        head, stop = split_tail(sentence.text)
        quote_id = f"zitat-{index}" if turn is None else f"zitat-{turn}-{index}"
        statements.append(ShownStatement(head=head, stop=stop, number=number, quote=sentence.quote,
                                         quote_id=quote_id, passage=by_number.get(number)))
    return {"statements": statements, "passages": passages, "removed": removed_line(reply.removed)}


def _plain(status: str, message: str) -> Reply:
    return Reply(status=status, message=message)


# --- what is stored ------------------------------------------------------------------------------------------------


def _clean(text: Any) -> str:
    """Text without NUL characters (FU-01: PostgreSQL's text refuses them)."""
    return str(text if text is not None else "").replace(NUL, "")


def _storable_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= MAX_STORED_INT


def chat_title(question: str) -> str:
    """The chat's title: the question as one line, cut at a word to at most 200 characters with an ellipsis (FU-02).

    A single word longer than that is cut inside it.
    """
    from identity.chats import TITLE_MAX

    line = " ".join(_clean(question).split())
    if len(line) <= TITLE_MAX:
        return line
    head = line[:TITLE_MAX - 1]
    if line[TITLE_MAX - 1] != " " and " " in head:
        head = head[:head.rindex(" ")]
    return head.rstrip() + ELLIPSIS


def stored_answer(reply: Reply) -> Tuple[str, List[Dict[str, Any]]]:
    """The assistant message of an ``answered`` or ``no_answer`` reply as it is stored: ``(text, references)``.

    ``text`` is one JSON object, the answer as shown: ``{"v": 1, "status", "statements": [{"text", "source",
    "quote"}], "removed", "sentences": {"<passage number>": <sentence number>}}``. ``references`` are the reply's
    passages: number, pointer, page, title and the passage text as search returned it (D2). Cleaned first (FU-01):
    no NUL anywhere, a page outside 1 to 2^31-1 as no page, a reference without a pointer left out.
    """
    statements = [{"text": _clean(s.text), "source": (s.refs[0] if s.refs else 0), "quote": _clean(s.quote)}
                  for s in reply.sentences]
    references: List[Dict[str, Any]] = []
    sentences: Dict[str, int] = {}
    for passage in reply.passages:
        pointer = _clean(passage.pointer)
        if not pointer or not _storable_int(passage.number):
            continue
        references.append({"number": passage.number, "pointer": pointer,
                           "page": passage.page if _storable_int(passage.page) else None,
                           "title": _clean(passage.title), "text": _clean(passage.text)})
        if _storable_int(passage.sentence):
            sentences[str(passage.number)] = passage.sentence
    text = json.dumps({"v": STORED_VERSION, "status": reply.status, "statements": statements,
                       "removed": max(0, int(reply.removed or 0)), "sentences": sentences}, ensure_ascii=False)
    return text, references


def _int_value(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError("not an integer")
    return value


def stored_reply(message: Any) -> Reply:
    """The reply a stored assistant message holds, as the page drew it when new. Raises ValueError, TypeError or
    KeyError when the text is not the shape ``stored_answer`` writes."""
    data = json.loads(message.text)
    if not isinstance(data, dict) or data.get("v") != STORED_VERSION or data.get("status") not in STORED_STATUSES:
        raise ValueError("not a stored answer")
    statements = data["statements"]
    sentence_of = data.get("sentences", {})
    if not isinstance(statements, list) or not isinstance(sentence_of, dict):
        raise TypeError("not a stored answer")
    sentences = []
    for item in statements:
        if not isinstance(item, dict) or not isinstance(item["text"], str) or not isinstance(item["quote"], str):
            raise TypeError("not a statement")
        sentences.append(Sentence(text=item["text"], refs=(_int_value(item["source"]),), quote=item["quote"]))
    numbers = {int(key): _int_value(value) for key, value in sentence_of.items()}
    passages = [Passage(number=ref["number"], pointer=ref["pointer"], title=ref.get("title") or "",
                        page=ref.get("page"), text=ref.get("text") or "", sentence=numbers.get(ref["number"]))
                for ref in message.references]
    status = data["status"]
    return Reply(status=status, sentences=sentences if status == "answered" else [], passages=passages,
                 removed=max(0, _int_value(data.get("removed", 0))),
                 message=NO_ANSWER_TEXT if status == "no_answer" else "")


def _read_stored(message: Any) -> Optional[Reply]:
    try:
        return stored_reply(message)
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        logger.warning("Fragen: a stored answer is not in the stored shape (%s)", type(exc).__name__)
        return None


def history_of(chat: Any) -> List[Turn]:
    """The chat's turns for the service, oldest first: a question as typed; an answer as its statements' text, one
    per line; a ``no_answer`` as ``NO_ANSWER_TEXT``. No quote, no passage, no JSON (POST-02); an answer that does not
    parse is left out."""
    turns: List[Turn] = []
    for message in chat.messages:
        if message.role == "user":
            turns.append(Turn(role="user", text=message.text))
            continue
        reply = _read_stored(message)
        if reply is None:
            continue
        text = "\n".join(s.text for s in reply.sentences) if reply.status == "answered" else NO_ANSWER_TEXT
        if text.strip():
            turns.append(Turn(role="assistant", text=text))
    return turns


def follow_up_search_text(last_question: str, follow_up: str) -> str:
    """What a follow-up searches (D1): the chat's last question and the follow-up, within ``MAX_QUESTION_BYTES``.

    The follow-up is never cut; the last question gives way to it.
    """
    room = MAX_QUESTION_BYTES - len(follow_up.encode("utf-8")) - 1
    head = cut_to_bytes(last_question.strip(), room) if room > 0 else ""
    return (head + "\n" + follow_up).strip()


def _answers(chat: Any) -> List[Any]:
    return [m for m in chat.messages if m.role == "assistant"]


# --- the sidebar ---------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SidebarChat:
    """One chat in the sidebar: its address, its title (the person's text) and whether it is the open one."""

    href: str
    title: str
    current: bool = False


@dataclass(frozen=True)
class SidebarChats:
    """What ``_sidebar.html`` draws of the person's chats: the address of a new question, the groups in order
    (label, chats), and the address of all chats when there are more than the sidebar holds ("" otherwise)."""

    new_href: str
    groups: List[Tuple[str, List[SidebarChat]]]
    more_href: str = ""


def group_label(moment: datetime, today: date) -> str:
    """The group of a chat in the sidebar by the day of its last change in the installation's zone: "Heute",
    "Diese Woche" (since Monday) or "Frueher"."""
    day = moment.astimezone(LOCAL_TZ).date() if moment.tzinfo else moment.date()
    if day >= today:
        return GROUP_TODAY
    if day >= today - timedelta(days=today.weekday()):
        return GROUP_WEEK
    return GROUP_EARLIER


def _local_today() -> date:
    return datetime.now(LOCAL_TZ).date()


def _when(moment: datetime) -> str:
    local = moment.astimezone(LOCAL_TZ) if moment.tzinfo else moment
    return local.strftime("%d.%m.%Y, %H:%M")


# --- the pages -----------------------------------------------------------------------------------------------------


def create_assistant_blueprint(*, csrf_valid: Callable[[str], bool], grant: Callable[[List[Dict[str, Any]]], None],
                               page_context: Callable[[], Mapping[str, Any]],
                               pdf_preview: Callable[[], bool], gate: Any = None, client: Any = None) -> Blueprint:
    """The assistant's blueprint.

    Args:
        csrf_valid: the app's check of a submitted form token.
        grant: the app's ``_grant_for_current_user``: the passages the page shows may then be opened by this person
            through the file routes, as a search hit may.
        page_context: the template values of the Platform's layout (sidebar, brand, csrf token, asset version).
        pdf_preview: whether PDFs open in the browser's own viewer (``open.pdf_inline_in_browser``).
        gate: the identity gate (the signed-in person and the request's database connection). Without it there are
            no chats: ``POST /fragen`` answers as in PMVP-22a and the chat addresses answer 404.
        client: the app's Knovas client, for ``document_readable`` on the click route (as the person).
    """
    bp = Blueprint("assistant", __name__)

    def store():
        from identity.chats import ChatStore

        return ChatStore(gate.connection())

    def refuse_unless_open():
        """The chat addresses: 404 while the switch is off, without personal accounts, or with nobody signed in."""
        if gate is None or not assistant_enabled():
            abort(404)
        person = gate.current_user()
        if person is None:
            abort(404)
        return person

    def not_found():
        # One line for another person's chat, a missing one and a malformed id: the log does not tell them apart.
        logger.info("Fragen: no such chat of this person")
        abort(404)

    def unavailable(exc: BaseException):
        """The plain page for a chat that cannot be read or deleted now (PMVP-24): 503, the error's type logged,
        nothing of it on the page."""
        logger.warning("Fragen: the chat is not readable (%s)", type(exc).__name__)
        page, _ = render([ShownTurn(question="", status="failed", message=CHAT_UNAVAILABLE_TEXT)])
        abort(make_response(page, 503))

    def read_chat(chat_id: str, person: Any):
        from identity.chats import ChatNotFound

        failure: Optional[BaseException] = None
        try:
            return store().get_chat(chat_id, owner=person.id)
        except ChatNotFound:
            not_found()
        except Exception as exc:  # noqa: BLE001 - the database is down: a plain 503, never a 500
            failure = exc
        unavailable(failure)

    def token_refused() -> bool:
        return not csrf_valid(str(request.form.get("csrf_token", "") or ""))

    def sidebar_chats(open_chat: Any = None) -> Optional[SidebarChats]:
        """The sidebar's block, or None: switched off, nobody signed in, or the list cannot be read (logged by the
        error's type; the page is drawn without it)."""
        try:
            if gate is None or not assistant_enabled():
                return None
            person = gate.current_user()
            if person is None:
                return None
            rows = store().list_chats(owner=person.id, limit=SIDEBAR_CHATS + 1)
            today = _local_today()
            groups: List[Tuple[str, List[SidebarChat]]] = []
            for row in rows[:SIDEBAR_CHATS]:
                label = group_label(row.updated_at, today)
                if not groups or groups[-1][0] != label:
                    groups.append((label, []))
                groups[-1][1].append(SidebarChat(href=url_for("assistant.chat", chat_id=str(row.id)),
                                                 title=row.title,
                                                 current=open_chat is not None and str(row.id) == str(open_chat)))
            return SidebarChats(new_href=url_for("index"), groups=groups,
                                more_href=url_for("assistant.history") if len(rows) > SIDEBAR_CHATS else "")
        except Exception as exc:  # noqa: BLE001 - the list never breaks a page
            logger.warning("Fragen: the chat list is not readable (%s)", type(exc).__name__)
            return None

    @bp.app_context_processor
    def _assistant_context() -> Dict[str, Any]:
        # Functions, so the switch and the list are read only by a page that asks (the search page's box, the
        # Workspace's sidebar), not by every page.
        return {"assistant_box_on": assistant_enabled, "assistant_chats": sidebar_chats}

    def render(turns: Sequence[ShownTurn], *, question_box: str = "", chat_id: Optional[str] = None,
               status: str = "", code: int = 200):
        return render_template(
            "ask.html",
            active_nav="fragen",
            view="turns",
            turns=list(turns),
            status=status or (turns[-1].status if turns else ""),
            chat_id=chat_id,
            open_chat=chat_id,
            box_value=question_box,
            **dict(page_context()),
        ), code

    def shown_turn(question: str, reply: Reply, *, turn: int, href: Optional[Callable[[Passage], str]] = None,
                   anchor: str = "", notice: str = "") -> ShownTurn:
        if reply.status in STORED_STATUSES:
            shown = shown_reply(reply, bool(pdf_preview()), turn=turn, href=href)
        else:
            shown = {}
        return ShownTurn(question=question, status=reply.status, message=reply.message, anchor=anchor,
                         notice=notice, **shown)

    def chat_turns(chat: Any) -> List[ShownTurn]:
        """The stored chat as the page draws it; every document link goes through the click route."""
        turns: List[ShownTurn] = []
        question: Optional[str] = None
        number = 0
        for message in chat.messages:
            if message.role == "user":
                if question is not None:
                    turns.append(ShownTurn(question=question, status=""))
                question = message.text
                continue
            number += 1
            anchor = f"runde-{number}"
            reply = _read_stored(message)
            if reply is None:
                turns.append(ShownTurn(question=question or "", status="stored", message=message.text,
                                       anchor=anchor))
            else:
                def href(passage: Passage, turn: int = number) -> str:
                    return url_for("assistant.source", chat_id=str(chat.id), turn=turn, number=passage.number)

                turns.append(shown_turn(question or "", reply, turn=number, href=href, anchor=anchor))
            question = None
        if question is not None:
            turns.append(ShownTurn(question=question, status=""))
        return turns

    def save(person: Any, question: str, reply: Reply, chat_id: Any = None) -> Optional[str]:
        """Store the question and its answer (and a new chat) in one transaction: the chat's id, or None when the
        write failed (logged by the error's type; nothing is left half stored)."""
        text, references = stored_answer(reply)
        try:
            chats = store()
            with gate.connection().transaction():
                if chat_id is None:
                    chat_id = chats.create_chat(owner=person.id, title=chat_title(question))
                chats.add_message(chat_id, owner=person.id, role="user", text=question)
                chats.add_message(chat_id, owner=person.id, role="assistant", text=text, references=references)
        except Exception as exc:  # noqa: BLE001 - shown once, unsaved; never a 500
            logger.warning("Fragen: the chat was not stored (%s)", type(exc).__name__)
            return None
        return str(chat_id)

    def start_the_clock() -> None:
        """The answer's deadline starts with the request (PMVP-24): the service built for it reads it from ``g``."""
        from assistant.core import Deadline

        g.assistant_deadline = Deadline()

    def service_or_404():
        from assistant.core import OFF

        service = current_app.extensions[EXTENSION]["service"]()
        if service is OFF:  # switched off since the check
            abort(404)
        return service

    def granted(reply: Reply) -> None:
        """The reply's passages may be opened by this person, as a search hit may (only a shown reply's)."""
        pointers = [_clean(p.pointer) for p in reply.passages] if reply.status in STORED_STATUSES else []
        if any(pointers):
            grant([{"doc_id": pointer} for pointer in pointers if pointer])

    @bp.route("/fragen", methods=["POST"])
    def ask():
        start_the_clock()
        if not assistant_enabled():
            abort(404)
        if token_refused():
            logger.info("Fragen: status=expired")
            return render([ShownTurn(question="", status="failed", message=EXPIRED_TEXT)], code=400)
        question = _clean(request.form.get("question", "")).strip()
        if not question:
            return redirect(url_for("index"), code=303)
        if len(question.encode("utf-8")) > MAX_QUESTION_BYTES:
            # Refused before the service (assistant/README.md), and repeated neither as the page head nor in the box.
            logger.info("Fragen: status=failed too_long=1")
            return render([ShownTurn(question="", status="failed", message=QUESTION_TOO_LONG_TEXT)])
        reply = service_or_404().answer(question)
        if reply.status not in STATUSES:  # pragma: no cover - the service gives one of the four
            reply = _plain("failed", "")
        granted(reply)
        person = gate.current_user() if gate is not None else None
        saved = save(person, question, reply) if reply.status in STORED_STATUSES and person is not None else None
        logger.info("Fragen: status=%s stored=%s statements=%d passages=%d removed=%d",
                    reply.status, "yes" if saved else "no", len(reply.sentences), len(reply.passages), reply.removed)
        if saved:
            return redirect(url_for("assistant.chat", chat_id=saved, _anchor="runde-1"), code=303)
        notice = NOT_SAVED_TEXT if reply.status in STORED_STATUSES and person is not None else ""
        box = "" if reply.status in STORED_STATUSES else question
        return render([shown_turn(question, reply, turn=1, notice=notice)], question_box=box)

    @bp.route("/fragen/verlauf")
    def history():
        person = refuse_unless_open()
        raw = str(request.args.get("seite", "1") or "1").strip()
        page = int(raw) if raw.isdigit() and 0 < len(raw) <= 6 else 1
        page = min(max(page, 1), MAX_HISTORY_PAGE)
        try:
            rows = store().list_chats(owner=person.id, limit=HISTORY_PAGE_SIZE + 1,
                                      offset=(page - 1) * HISTORY_PAGE_SIZE)
        except Exception as exc:  # noqa: BLE001 - a plain page, never a 500
            logger.warning("Fragen: the list of chats is not readable (%s)", type(exc).__name__)
            return render_template("ask.html", active_nav="fragen", view="verlauf", chats=[], unavailable=True,
                                   newer_page=None, older_page=None, status="failed",
                                   unavailable_text=LIST_UNAVAILABLE_TEXT, **dict(page_context())), 503
        chats = [{"id": str(row.id), "title": row.title, "when": _when(row.updated_at)}
                 for row in rows[:HISTORY_PAGE_SIZE]]
        logger.info("Fragen: list page=%d chats=%d", page, len(chats))
        return render_template("ask.html", active_nav="fragen", view="verlauf", chats=chats, unavailable=False,
                               newer_page=page - 1 if page > 1 else None,
                               older_page=page + 1 if len(rows) > HISTORY_PAGE_SIZE else None,
                               status="", **dict(page_context()))

    @bp.route("/fragen/<chat_id>", methods=["GET"])
    def chat(chat_id: str):
        person = refuse_unless_open()
        found = read_chat(chat_id, person)
        turns = chat_turns(found)
        logger.info("Fragen: chat shown turns=%d", len(_answers(found)))
        return render(turns, chat_id=str(found.id))

    @bp.route("/fragen/<chat_id>", methods=["POST"])
    def follow_up(chat_id: str):
        start_the_clock()
        person = refuse_unless_open()
        if token_refused():
            logger.info("Fragen: follow-up status=expired")
            return render([ShownTurn(question="", status="failed", message=EXPIRED_TEXT)], code=400)
        found = read_chat(chat_id, person)
        here = str(found.id)
        question = _clean(request.form.get("question", "")).strip()
        if not question:
            return redirect(url_for("assistant.chat", chat_id=here), code=303)
        turns = chat_turns(found)
        if len(question.encode("utf-8")) > MAX_QUESTION_BYTES:
            logger.info("Fragen: follow-up status=failed too_long=1")
            return render(turns + [ShownTurn(question="", status="failed", message=QUESTION_TOO_LONG_TEXT)],
                          chat_id=here)
        history = history_of(found)
        last_question = next((t.text for t in reversed(history) if t.role == "user"), "")
        reply = service_or_404().answer(question, history,
                                        search_text=follow_up_search_text(last_question, question))
        if reply.status not in STATUSES:  # pragma: no cover - the service gives one of the four
            reply = _plain("failed", "")
        granted(reply)
        saved = save(person, question, reply, chat_id=found.id) if reply.status in STORED_STATUSES else None
        logger.info("Fragen: follow-up status=%s stored=%s turns=%d history=%d statements=%d passages=%d removed=%d",
                    reply.status, "yes" if saved else "no", len(_answers(found)), len(history),
                    len(reply.sentences), len(reply.passages), reply.removed)
        number = len(_answers(found)) + 1
        if saved:
            return redirect(url_for("assistant.chat", chat_id=here, _anchor=f"runde-{number}"), code=303)
        if reply.status in STORED_STATUSES:  # the write failed: the reply once, unsaved
            return render(turns + [shown_turn(question, reply, turn=number, anchor=f"runde-{number}",
                                              notice=NOT_SAVED_TEXT)], chat_id=here)
        # busy, failed: the chat as it was, the message under the last turn, the question back in the box.
        return render(turns + [ShownTurn(question="", status=reply.status, message=reply.message)],
                      question_box=question, chat_id=here)

    @bp.route("/fragen/<chat_id>/loeschen", methods=["POST"])
    def delete_chat(chat_id: str):
        from identity.chats import ChatNotFound

        person = refuse_unless_open()
        if token_refused():
            logger.info("Fragen: delete status=expired")
            return render([ShownTurn(question="", status="failed", message=EXPIRED_TEXT)], code=400)
        failure: Optional[BaseException] = None
        try:
            store().delete_chat(chat_id, owner=person.id)
        except ChatNotFound:
            not_found()
        except Exception as exc:  # noqa: BLE001 - the database is down: a plain 503, never a 500
            failure = exc
        if failure is not None:
            unavailable(failure)
        logger.info("Fragen: chat deleted")
        return redirect(url_for("index"), code=303)

    @bp.route("/fragen/<chat_id>/quelle/<int:turn>/<int:number>")
    def source(chat_id: str, turn: int, number: int):
        person = refuse_unless_open()
        found = read_chat(chat_id, person)
        answers = _answers(found)
        if not 1 <= turn <= len(answers):
            not_found()
        message = answers[turn - 1]
        reference = next((r for r in message.references if r.get("number") == number), None)
        if reference is None:
            not_found()
        try:
            readable = client is not None and client.document_readable(reference["pointer"]) is True
        except Exception as exc:  # noqa: BLE001 - the door stays shut
            logger.warning("Fragen: the readability check failed (%s)", type(exc).__name__)
            readable = False
        if not readable:
            logger.info("Fragen: a stored source was not opened")
            not_found()
        grant([{"doc_id": reference["pointer"]}])
        stored = _read_stored(message)
        sentence = next((p.sentence for p in stored.passages if p.number == number), None) if stored else None
        passage = Passage(number=number, pointer=reference["pointer"], title=reference.get("title") or "",
                          page=reference.get("page"), text="", sentence=sentence)
        logger.info("Fragen: a stored source was opened")
        return redirect(_open_href(passage, bool(pdf_preview())))

    return bp


class AsTheAssistant:
    """The service, answering with the Knovas Assistent's own claim for the signed-in person (RB-6).

    The whole answer runs inside ``client.asserting_for(AgentPrincipal(<the assistant>, <the person>))``, so every
    Core call of it (the search, the document-fields probe inside it, ``/secured/llm/generate``) carries the claim
    ``AgentStore.claim_for`` gives the assistant for that person (its roles' groups and functions, narrowed to what
    the person holds) and declares ``{"kind": "agent", "id": <the assistant>, "on_behalf_of": {"kind": "user",
    "id": <the person>}}``. It fails closed: a client that cannot assert for someone else, no signed-in person or no
    assistant answers ``failed`` and nothing is searched as the person instead. A follow-up's ``search_text``
    (PMVP-23) is passed on.
    """

    def __init__(self, service: Any, *, client: Any, identity_gate: Any) -> None:
        self._service = service
        self._client = client
        self._gate = identity_gate

    def answer(self, question: str, history: Sequence[Any] = (), *, search_text: Optional[str] = None) -> Reply:
        from assistant.service import FAILED_TEXT
        from identity.agents import AgentPrincipal, AgentStore

        asserting = getattr(self._client, "asserting_for", None)
        try:
            person = self._gate.current_user() if self._gate is not None else None
            if not callable(asserting) or person is None:
                raise LookupError("no principal for the assistant")
            principal = AgentPrincipal(agent_id=AgentStore(self._gate.connection()).assistant().id,
                                       on_behalf_of=person.id)
        except Exception as exc:  # noqa: BLE001 - fail closed: no claim, no answer
            logger.warning("Fragen: the assistant's claim is not available (%s)", type(exc).__name__)
            return _plain("failed", FAILED_TEXT)
        with asserting(principal):
            if search_text is None:
                return self._service.answer(question, history)
            return self._service.answer(question, history, search_text=search_text)


def install_assistant(app: Flask, *, client: Any, identity_gate: Any, split: Callable[..., Any],
                      csrf_valid: Callable[[str], bool], grant: Callable[[List[Dict[str, Any]]], None],
                      page_context: Callable[[], Mapping[str, Any]], pdf_preview: Callable[[], bool],
                      start_retention: bool = True) -> None:
    """Register the assistant's pages, the factory of its service and the chats' deletion job (one call in app.py).

    The service is built per request, inside it, by PMVP-25's ``make_service`` on the settings' Resolver: so the
    switch and the provider apply without a restart (``assistant/README.md``, "Wiring"). It answers as the Knovas
    Assistent for the signed-in person (``AsTheAssistant``, RB-6): the client signs every call with the assistant's
    claim narrowed to that person. A test replaces ``app.extensions["assistant"]["service"]``.

    With personal accounts on (an identity gate), ``ChatRetentionJob`` runs in this worker whatever the switch says
    (chats stored earlier still expire, D3): its first tick is an hour after the start; it does not start for an
    app that is already in testing, and it ends at its next tick once the app is (a test app is marked after it is
    built).
    """
    from identity.chats import ChatRetentionJob

    def build_service():
        from assistant.core import OFF, make_service
        from web_interface import doc_fields_routes

        service = make_service(app.extensions["platform_settings"], client, split=split,
                               user_key=lambda: doc_fields_routes.user_key_for(identity_gate),
                               deadline=g.get("assistant_deadline"))
        if service is OFF:
            return OFF
        return AsTheAssistant(service, client=client, identity_gate=identity_gate)

    retention = None
    if start_retention and identity_gate is not None and not app.testing:
        retention = ChatRetentionJob(connect=identity_gate.open_connection,
                                     keep_running=lambda: not app.testing).start()
    app.extensions[EXTENSION] = {"service": build_service, "retention": retention}
    app.register_blueprint(create_assistant_blueprint(
        csrf_valid=csrf_valid, grant=grant, page_context=page_context, pdf_preview=pdf_preview,
        gate=identity_gate, client=client))


__all__: Sequence[str] = (
    "EXPIRED_TEXT", "NOT_SAVED_TEXT", "SWITCH_ENV", "AsTheAssistant", "ShownTurn", "SidebarChats",
    "assistant_enabled", "chat_title", "create_assistant_blueprint", "follow_up_search_text", "group_label",
    "history_of", "install_assistant", "removed_line", "shown_reply", "split_tail", "stored_answer", "stored_reply",
)
