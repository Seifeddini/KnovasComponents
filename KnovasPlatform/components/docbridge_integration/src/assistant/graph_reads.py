"""<<Wissensgraph>>: the reads behind the Knovas Assistent's graph tools (PMVP-20 brain-graph).

With ``assistant.mode`` ``brain_graph`` Claude answers with tools that read the tenant's knowledge graph (the Brain)
through the Core's ``/secured/graph`` routes, executed here by the Platform (``assistant.brain_graph`` runs the tool
loop). This module holds the reads and the tools' logic, and nothing of Claude:

- ``GraphPort``: the few read-only calls over the Platform's ``KnovasAPIClient`` (the tenant's mTLS certificate;
  inside ``AsTheAssistant`` every call carries the assistant's claim for the signed-in person): ``GET /node-types``,
  ``GET /nodes``, ``GET /edges``, ``GET /node-types/<id>/schema``, ``GET /nodes/<id>/facts`` and ``POST
  /facts/batch`` (a read; the ids travel in the body). Every call is bounded by the answer's time
  (``client.within_time``), paced by a token bucket kept under the tenant's rate limit (a facts batch costs one token
  per node it names: the Core admits it as one metadata read per node), and a 429 or 503 is waited out a few times,
  never past the time the answer needs for its last request to Claude. No call is retried otherwise; nothing is
  logged here (the service logs counts).
- ``GraphSession``: one answer's view of the graph. The topology (node types, node headers, edges) is read once, on
  the first tool call that needs it, in three requests; facts are read only for the nodes a tool needs, at most
  ``MAX_FACT_NODES`` per answer, and kept for the answer. Nothing is kept beyond one answer (the reads are the asking
  person's, so no cache is shared between people).
- The tools (``describe_graph``, ``find_nodes``, ``read_node``, ``neighbours``, ``list_nodes``): each returns the JSON
  Claude reads, and every node it returns becomes a ``Passage`` of the answer (``PassageBook``) whose text is the
  node's type, name, description and facts as plain lines (``node_text``) and whose pointer is ``brain:<node id>``
  (``node_pointer``), so the unchanged ``check.check_answer`` can find a quote in it. A count is a passage too
  (``count_text``: the query in words, "Anzahl der Treffer: N" and the names), so a number can be quoted; what
  each count asked is kept as a ``CountRead`` (``GraphSession.counts``) for the graph under the answer
  (``assistant.answer_graph``).

The code is generic: no type, relation or fact name of any tenant is written here. Types and relations come from the
graph at run time; the only fact names the code knows are the brain-runner's own bookkeeping (``key`` is shown
first, ``BOOKKEEPING_FACTS`` are left out of passages).

This module imports neither Flask nor the app. It is ASCII-only: umlauts and guillemets are ``\\u`` escapes.
"""

from __future__ import annotations

import contextlib
import datetime
import json
import re
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import quote

import requests

from assistant.conversation import Seen
from assistant.service import Passage

# ---------------------------------------------------------------------------
# Knobs
# ---------------------------------------------------------------------------

#: The pointer of a node's passage: ``brain:<node id>``. A chat opens it as the node's dossier (brain views on).
NODE_POINTER_PREFIX = "brain:"
#: The pointer of a list's count passage (no node of its own): never opened.
QUERY_POINTER_PREFIX = "brain:abfrage:"
#: The most nodes and edges one answer's topology may have; a larger graph refuses the tools (``GraphTooLarge``).
MAX_TOPOLOGY_NODES = 50_000
MAX_TOPOLOGY_EDGES = 250_000
#: The most nodes whose facts one answer reads (each costs one rate-limit token): about 40 s at the pace below.
MAX_FACT_NODES = 300
#: Node ids per ``POST /facts/batch``: the tenant's metadata burst is 20.
FACTS_BATCH = 20
#: The pace of the reads: 8 a second, under the tenant's 600 a minute and its metadata class (10 a second, burst 20);
#: the views, the loader and other workers share them, and a 429 is waited out.
RATE_PER_MINUTE = 480
RATE_BURST = 20
#: A 429 or 503 is waited out this many times, these many seconds each, then the tool answers busy.
BACKOFF_SECONDS = (1.0, 2.0, 4.0)
#: Node passages one list or neighbours result returns (default and most); the count passage names more.
DEFAULT_RESULT_NODES = 15
MAX_RESULT_NODES = 25
#: The names a count passage lists; beyond them it says how many more.
MAX_LISTED_NAMES = 100
#: The matches ``find_nodes`` returns (default and most).
DEFAULT_FOUND = 10
MAX_FOUND = 25
#: Candidates whose facts ``find_nodes`` reads when no name matches (a key, a number): only with a type.
MAX_FIND_FACT_NODES = 200
#: A node passage holds at most this many facts, each value cut to this many characters.
MAX_FACTS_PER_NODE = 40
MAX_FACT_CHARS = 300
#: A full node passage names at most this many neighbours per relation, and returns at most this many ids in all.
MAX_NEIGHBOURS_PER_RELATION = 20
MAX_NEIGHBOUR_IDS = 80
#: The most conditions of a filter, relation conditions and hops of a ``via`` path.
MAX_CONDITIONS = 6
MAX_PATH_HOPS = 3
#: The most passages, and characters of passages, of one answer; beyond them a node is returned without a number
#: (it cannot be cited).
MAX_PASSAGES = 150
MAX_PASSAGE_CHARS_TOTAL = 200_000
#: The most characters of one tool result's JSON.
MAX_RESULT_CHARS = 40_000
#: The brain-runner's bookkeeping facts (KnowledgeBase ``knovas-software`` brain-runner, common attributes of every
#: bundle): they say where a node came from, not what it is, and are left out of passages. ``key`` is shown first.
BOOKKEEPING_FACTS = frozenset({"source", "source_key", "mapping", "in_source", "last_seen", "source_version",
                               "provenance"})
KEY_FACT = "key"

OPS = ("equals", "not_equals", "contains", "present", "missing", "before", "after")
DIRECTIONS = ("out", "in")
_DIRECTION_WORDS = {"out": "ausgehend", "in": "eingehend"}
_OP_WORDS = {"equals": "=", "not_equals": "\u2260", "contains": "enth\u00e4lt", "present": "vorhanden",
             "missing": "fehlt", "before": "vor", "after": "nach"}
_NODE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_LAQUO, _RAQUO = "\u00ab", "\u00bb"


# ---------------------------------------------------------------------------
# Errors: each is one plain message to Claude (a tool_result with is_error), never a Core text
# ---------------------------------------------------------------------------


class GraphError(Exception):
    """A tool could not answer. ``code`` is a word of the code's (for the log line), ``str()`` the message to
    Claude."""

    code = "graph_error"

    def __init__(self, message: str, code: Optional[str] = None) -> None:
        super().__init__(message)
        if code:
            self.code = code


class ToolInputError(GraphError):
    code = "bad_input"


class GraphBusy(GraphError):
    code = "busy"


class GraphOutOfTime(GraphError):
    code = "out_of_time"


class GraphUnavailable(GraphError):
    code = "unavailable"


class GraphTooLarge(GraphError):
    code = "too_large"


# ---------------------------------------------------------------------------
# The port: the reads, paced, bounded and waited out
# ---------------------------------------------------------------------------


class TokenBucket:
    """Tokens at ``rate_per_minute`` with ``burst`` at most; ``take(n)`` waits for them (graph_views.pacing's
    bucket, with a count: a facts batch takes one per node)."""

    def __init__(self, rate_per_minute: float = RATE_PER_MINUTE, burst: int = RATE_BURST,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep) -> None:
        self._rate = max(float(rate_per_minute), 1.0) / 60.0
        self._capacity = float(max(int(burst), 1))
        self._tokens = self._capacity
        self._clock, self._sleep = clock, sleep
        self._last = clock()
        self._lock = threading.Lock()

    def wait_for(self, count: int) -> float:
        """Seconds until ``count`` tokens are there (0 when they are)."""
        with self._lock:
            self._refill()
            return max(0.0, (min(float(count), self._capacity) - self._tokens) / self._rate)

    def take(self, count: int = 1) -> None:
        for _ in range(max(1, int(count))):
            while True:
                with self._lock:
                    self._refill()
                    if self._tokens >= 1.0:
                        self._tokens -= 1.0
                        break
                    wait = (1.0 - self._tokens) / self._rate
                self._sleep(wait)

    def _refill(self) -> None:
        now = self._clock()
        self._tokens = min(self._capacity, self._tokens + (now - self._last) * self._rate)
        self._last = now


_SHARED_BUCKET: Optional[TokenBucket] = None
_SHARED_LOCK = threading.Lock()


def shared_bucket() -> TokenBucket:
    """The process-wide bucket of the assistant's graph reads: the limit is the tenant's, not a person's."""
    global _SHARED_BUCKET
    with _SHARED_LOCK:
        if _SHARED_BUCKET is None:
            _SHARED_BUCKET = TokenBucket()
        return _SHARED_BUCKET


class GraphPort:
    """The read-only calls of the graph tools over ``KnovasAPIClient`` (``graph_read``, ``graph_facts_batch``).

    ``time_left()`` gives the seconds the answer has left; no read starts with less than ``reserve_seconds`` left
    (the time the last request to Claude needs), and every read runs inside ``client.within_time(time_left)``.
    ``bucket`` paces the reads (``shared_bucket()`` by default); ``sleep`` waits out a 429 or 503.
    """

    def __init__(self, client: Any, *, time_left: Callable[[], float], reserve_seconds: float = 0.0,
                 bucket: Optional[TokenBucket] = None, sleep: Callable[[float], None] = time.sleep) -> None:
        self._client = client
        self._time_left = time_left
        self._reserve = float(reserve_seconds)
        self._bucket = bucket if bucket is not None else shared_bucket()
        self._sleep = sleep
        self.calls = 0
        self.busy = 0
        self.nodes_with_facts = 0

    def _ensure_time(self, need: float = 0.0) -> None:
        if self._time_left() - need < self._reserve:
            raise GraphOutOfTime("Die Zeit f\u00fcr Abfragen ist um: Antworten Sie jetzt mit dem, was Sie haben.")

    def _within(self):
        bound = getattr(self._client, "within_time", None)
        return bound(self._time_left) if callable(bound) else contextlib.nullcontext()

    def _paced(self, tokens: int) -> None:
        self._ensure_time(self._bucket.wait_for(tokens))
        self._bucket.take(tokens)

    def _wait_out(self, attempt: int) -> bool:
        """Wait before attempt ``attempt + 1`` after a 429 or 503; False when no attempt or no time is left."""
        self.busy += 1
        if attempt >= len(BACKOFF_SECONDS):
            return False
        wait = BACKOFF_SECONDS[attempt]
        if self._time_left() - wait < self._reserve:
            return False
        self._sleep(wait)
        return True

    def get(self, path: str) -> Any:
        """``GET /secured/graph<path>``: the payload, or None for a 404 (unknown, hidden, or the graph off)."""
        attempt = 0
        while True:
            self._paced(1)
            self.calls += 1
            try:
                with self._within():
                    status, payload = self._client.graph_read(path)
            except requests.exceptions.Timeout:
                raise GraphOutOfTime("Der Wissensgraph hat nicht rechtzeitig geantwortet.") from None
            except PermissionError:
                raise GraphUnavailable("Niemand ist angemeldet: der Wissensgraph wird nicht gelesen.",
                                       "no_principal") from None
            except requests.exceptions.RequestException:
                raise GraphUnavailable("Der Wissensgraph ist gerade nicht erreichbar.", "transport") from None
            if status in (429, 503):
                if self._wait_out(attempt):
                    attempt += 1
                    continue
                raise GraphBusy("Der Wissensgraph ist gerade ausgelastet.")
            if status == 404:
                return None
            if not isinstance(status, int) or status >= 400:
                raise GraphUnavailable("Der Wissensgraph hat die Abfrage nicht beantwortet.", f"http_{status}")
            return payload

    def facts(self, node_ids: Sequence[str]) -> Dict[str, Optional[List[Dict[str, Any]]]]:
        """The fact rows of each node (None for a node the answer leaves out), ``FACTS_BATCH`` ids a request; node
        by node when the Core has no batch route (404, 405, 501)."""
        out: Dict[str, Optional[List[Dict[str, Any]]]] = {}
        ids = list(dict.fromkeys(node_ids))
        for start in range(0, len(ids), FACTS_BATCH):
            chunk = ids[start:start + FACTS_BATCH]
            out.update(self._facts_chunk(chunk))
            self.nodes_with_facts += len(chunk)
        return out

    def _facts_chunk(self, chunk: List[str]) -> Dict[str, Optional[List[Dict[str, Any]]]]:
        attempt = 0
        while True:
            self._paced(len(chunk))
            self.calls += 1
            try:
                with self._within():
                    status, payload = self._client.graph_facts_batch(chunk)
            except PermissionError:
                raise GraphUnavailable("Niemand ist angemeldet: der Wissensgraph wird nicht gelesen.",
                                       "no_principal") from None
            if status in (429, 503):
                if self._wait_out(attempt):
                    attempt += 1
                    continue
                raise GraphBusy("Der Wissensgraph ist gerade ausgelastet.")
            if status in (404, 405, 501):
                return {node_id: _list(self.get(f"/nodes/{quote(node_id, safe='')}/facts"), "facts", none=True)
                        for node_id in chunk}
            if status == 599:
                raise GraphUnavailable("Der Wissensgraph ist gerade nicht erreichbar.", "transport")
            if not isinstance(status, int) or status >= 400 or not isinstance(payload, Mapping):
                raise GraphUnavailable("Der Wissensgraph hat die Abfrage nicht beantwortet.", f"http_{status}")
            data = payload.get("data") if isinstance(payload.get("data"), Mapping) else payload
            by_node = data.get("facts_by_node") if isinstance(data.get("facts_by_node"), Mapping) else {}
            return {node_id: ([f for f in by_node[node_id] if isinstance(f, Mapping)]
                              if isinstance(by_node.get(node_id), list) else None) for node_id in chunk}


def _list(payload: Any, key: str, *, none: bool = False) -> Any:
    if isinstance(payload, Mapping) and isinstance(payload.get(key), list):
        return [item for item in payload[key] if isinstance(item, Mapping)]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, Mapping)]
    return None if none else []


# ---------------------------------------------------------------------------
# Text: folding, values, the passages
# ---------------------------------------------------------------------------


_UMLAUTS = {"\u00e4": "ae", "\u00f6": "oe", "\u00fc": "ue"}


def _folded(text: Any, umlauts: bool) -> str:
    value = unicodedata.normalize("NFC", str(text if text is not None else "").casefold().replace("\u00df", "ss"))
    if umlauts:
        value = "".join(_UMLAUTS.get(char, char) for char in value)
    value = unicodedata.normalize("NFD", value)
    return " ".join("".join(char for char in value if unicodedata.category(char) != "Mn").split())


def fold(text: Any) -> str:
    """``text`` as names and values are compared: case, accents and white space ignored, an umlaut written as
    ae/oe/ue ("B\u00e4r" is "baer")."""
    return _folded(text, True)


def folds(text: Any) -> Tuple[str, ...]:
    """Both ways a German name may be typed: the umlaut as ae/oe/ue and as the bare vowel ("R\u00fcegg" is "rueegg"
    and "ruegg")."""
    return tuple(dict.fromkeys((_folded(text, True), _folded(text, False))))


def same(left: Any, right: Any) -> bool:
    return bool(set(folds(left)) & set(folds(right)))


def holds_text(haystack: Any, needle: Any) -> bool:
    """Whether ``needle`` stands in ``haystack``, folded either way."""
    return any(n in h for n in folds(needle) for h in folds(haystack))


_WORD = re.compile(r"[^\W_]+")


def starts_a_word(haystack: Any, needle: Any) -> bool:
    """Whether ``needle`` (folded either way) starts at the beginning of a word of ``haystack``: "baer" in "Dr.
    Markus B\u00e4r", not "bar" in "Lombardi"."""
    for h in folds(haystack):
        starts = {m.start() for m in _WORD.finditer(h)}
        for n in folds(needle):
            n = n.strip()
            if not n:
                continue
            index = h.find(n)
            while index >= 0:
                if index in starts or not n[0].isalnum():
                    return True
                index = h.find(n, index + 1)
    return False


def _clean(value: Any) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


def _cut(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = text[:limit]
    space = head.rfind(" ")
    return (head[:space] if space > limit // 2 else head).rstrip() + " \u2026"


def _value_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "ja" if value else "nein"
    if isinstance(value, (dict, list)):
        try:
            return _clean(json.dumps(value, ensure_ascii=False))
        except (TypeError, ValueError):
            return ""
    return _clean(str(value))


def _valid_now(fact: Mapping[str, Any], today: str) -> bool:
    if fact.get("curation_status") == "rejected":
        return False
    start = str(fact.get("valid_from") or "")[:10]
    end = str(fact.get("valid_to") or "")[:10]
    return (not start or start <= today) and (not end or end > today)


def current_facts(rows: Optional[Iterable[Any]], today: Optional[str] = None) -> Dict[str, List[str]]:
    """``{label: [values]}`` of the facts in force today (not rejected, not closed), in the Core's order, each value
    once."""
    day = today or datetime.date.today().isoformat()
    out: Dict[str, List[str]] = {}
    for fact in rows or ():
        if not isinstance(fact, Mapping) or not _valid_now(fact, day):
            continue
        label = _clean(fact.get("label")) or _clean(fact.get("attribute"))
        value = _value_text(fact.get("value"))
        if not label or not value:
            continue
        values = out.setdefault(label, [])
        if value not in values:
            values.append(value)
    return out


@dataclass(frozen=True)
class Node:
    id: str
    name: str
    type_id: str
    description: str = ""


def node_pointer(node_id: str) -> str:
    return NODE_POINTER_PREFIX + node_id


def node_of_pointer(pointer: Any) -> Optional[str]:
    """The node id of a node passage's pointer (``brain:<id>``), or None for any other pointer (a document's, a
    count's)."""
    if not isinstance(pointer, str) or not pointer.startswith(NODE_POINTER_PREFIX):
        return None
    if pointer.startswith(QUERY_POINTER_PREFIX):
        return None
    node_id = pointer[len(NODE_POINTER_PREFIX):]
    return node_id if _NODE_ID.match(node_id) else None


def node_title(node: Node, type_name: str) -> str:
    return f"{type_name}: {node.name}" if type_name else node.name


def node_text(node: Node, type_name: str, facts: Optional[Mapping[str, List[str]]],
              connections: Sequence[str] = ()) -> str:
    """A node as a passage: its type and name, its description, its facts (``key`` first, the bookkeeping left out),
    then its connections, one plain line each."""
    lines = [node_title(node, type_name)]
    if node.description and fold(node.description) != fold(node.name):
        lines.append("Beschreibung: " + _cut(node.description, MAX_FACT_CHARS))
    shown = 0
    labels = sorted((facts or {}).keys(), key=lambda label: 0 if label == KEY_FACT else 1)
    for label in labels:
        if label in BOOKKEEPING_FACTS:
            continue
        if shown >= MAX_FACTS_PER_NODE:
            lines.append("(weitere Fakten nicht gezeigt)")
            break
        lines.append(f"{label}: " + _cut("; ".join(facts[label]), MAX_FACT_CHARS))
        shown += 1
    if connections:
        lines.append("Verbindungen:")
        lines.extend(connections)
    return "\n".join(lines)


#: The first words of a count's passage (``count_text``).
COUNT_HEAD = "Abfrage im Wissensgraphen: "


def count_text(description: str, total: int, names: Sequence[str], *, incomplete: str = "") -> str:
    """A count as a passage: the query in words, "Anzahl der Treffer: N" and the names (at most
    ``MAX_LISTED_NAMES``)."""
    lines = [COUNT_HEAD + description, f"Anzahl der Treffer: {total}"]
    if names:
        listed = "; ".join(names[:MAX_LISTED_NAMES])
        more = total - min(len(names), MAX_LISTED_NAMES)
        lines.append("Treffer: " + listed + (f" und {more} weitere" if more > 0 else ""))
    if incomplete:
        lines.append("Unvollst\u00e4ndig: " + incomplete)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# The passages of one answer
# ---------------------------------------------------------------------------


@dataclass
class PassageBook:
    """Every passage of one answer, numbered 1..n in the order the tools returned them; the same pointer and text is
    one passage. ``seen``: what the tools returned that is no passage (the graph's description), for the reply's
    check."""

    passages: List[Passage] = field(default_factory=list)
    seen: List[Any] = field(default_factory=list)
    chars: int = 0
    not_numbered: int = 0
    _index: Dict[Tuple[str, str], int] = field(default_factory=dict)

    def add(self, pointer: str, title: str, text: str, page: Optional[int] = None) -> Optional[int]:
        key = (pointer, text)
        if key in self._index:
            return self._index[key]
        if len(self.passages) >= MAX_PASSAGES or self.chars + len(text) > MAX_PASSAGE_CHARS_TOTAL:
            self.not_numbered += 1
            self.seen.append(Seen(title=title, pointer=pointer, text=text))
            return None
        number = len(self.passages) + 1
        self.passages.append(Passage(number=number, pointer=pointer, title=title, page=page, text=text))
        self._index[key] = number
        self.chars += len(text)
        return number

    def numbered(self) -> Dict[int, str]:
        return {p.number: p.text for p in self.passages}

    def number_of(self, pointer: str, text: str) -> Optional[int]:
        """The number of the passage with this pointer and text, or None (no such passage)."""
        return self._index.get((pointer, text))

    @property
    def documents(self) -> int:
        return len({p.pointer for p in self.passages})


# ---------------------------------------------------------------------------
# One answer's graph
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CountRead:
    """What one count passage asked (``GraphSession.counts``, for the graph under the answer, ``answer_graph``):
    the query in words, the total, the node it was asked about (``neighbours``; None for ``list_nodes``), the
    relation and direction it followed from there, the type it counted (when it named one) and every match."""

    description: str
    total: int
    about: Optional[str] = None
    relation: Optional[str] = None
    direction: Optional[str] = None
    type_id: Optional[str] = None
    matched: Tuple[str, ...] = ()


@dataclass
class _Topology:
    type_name: Dict[str, str]
    type_by_fold: Dict[str, str]
    type_description: Dict[str, str]
    nodes: Dict[str, Node]
    by_type: Dict[str, List[str]]
    out: Dict[str, List[Tuple[str, str]]]
    inc: Dict[str, List[Tuple[str, str]]]
    relations: Dict[str, Dict[Tuple[str, str], int]]


class GraphSession:
    """One answer's reads of the graph and the tools over them. ``book`` collects the passages."""

    def __init__(self, port: GraphPort, book: Optional[PassageBook] = None, *, max_fact_nodes: int = MAX_FACT_NODES,
                 today: Optional[str] = None) -> None:
        self.port = port
        self.book = book if book is not None else PassageBook()
        self.max_fact_nodes = int(max_fact_nodes)
        self.today = today
        self._topology: Optional[_Topology] = None
        self._facts: Dict[str, Optional[Dict[str, List[str]]]] = {}
        #: Each numbered count passage's query (its passage number -> ``CountRead``).
        self.counts: Dict[int, CountRead] = {}

    def topology_if_read(self) -> Optional[_Topology]:
        """The topology when a tool has read it in this answer, else None; never reads."""
        return self._topology

    # -- reads -------------------------------------------------------------------------------------------------------

    def topology(self) -> _Topology:
        if self._topology is not None:
            return self._topology
        types = self.port.get("/node-types")
        if types is None:
            raise GraphUnavailable("Der Wissensgraph ist f\u00fcr dieses Unternehmen nicht eingeschaltet.", "graph_off")
        nodes = _list(self.port.get("/nodes"), "nodes")
        edges = _list(self.port.get("/edges"), "edges")
        if len(nodes) > MAX_TOPOLOGY_NODES or len(edges) > MAX_TOPOLOGY_EDGES:
            raise GraphTooLarge("Der Wissensgraph ist zu gross f\u00fcr diese Antwortweise.")
        type_name: Dict[str, str] = {}
        type_description: Dict[str, str] = {}
        for row in _list(types, "node_types"):
            type_id, name = _clean(str(row.get("id") or "")), _clean(row.get("name"))
            if type_id and name:
                type_name[type_id] = name
                type_description[type_id] = _clean(row.get("description"))
        by_fold: Dict[str, str] = {}
        for type_id, name in type_name.items():
            by_fold.setdefault(fold(name), type_id)
        found: Dict[str, Node] = {}
        by_type: Dict[str, List[str]] = {}
        for row in nodes:
            node_id = row.get("id")
            if not isinstance(node_id, str) or not _NODE_ID.match(node_id) or node_id in found:
                continue
            node = Node(id=node_id, name=_clean(row.get("name")) or "(ohne Namen)",
                        type_id=str(row.get("node_type_id") or ""), description=_clean(row.get("description")))
            found[node_id] = node
            by_type.setdefault(node.type_id, []).append(node_id)
        out: Dict[str, List[Tuple[str, str]]] = {}
        inc: Dict[str, List[Tuple[str, str]]] = {}
        relations: Dict[str, Dict[Tuple[str, str], int]] = {}
        seen_edges: set = set()
        for edge in edges:
            lo, hi, relation = edge.get("node_lo"), edge.get("node_hi"), _clean(edge.get("relation"))
            if not (isinstance(lo, str) and isinstance(hi, str) and relation) or lo not in found or hi not in found:
                continue
            key = (lo, relation, hi)
            if key in seen_edges:
                continue
            seen_edges.add(key)
            out.setdefault(lo, []).append((relation, hi))
            inc.setdefault(hi, []).append((relation, lo))
            pair = (found[lo].type_id, found[hi].type_id)
            counts = relations.setdefault(relation, {})
            counts[pair] = counts.get(pair, 0) + 1
        self._topology = _Topology(type_name, by_fold, type_description, found, by_type, out, inc, relations)
        return self._topology

    def facts_of(self, node_ids: Iterable[str]) -> Dict[str, Optional[Dict[str, List[str]]]]:
        """The current facts of each node (None: not readable), read once per answer, within ``max_fact_nodes``."""
        ids = list(dict.fromkeys(node_ids))
        missing = [n for n in ids if n not in self._facts]
        if missing:
            if len(self._facts) + len(missing) > self.max_fact_nodes:
                raise GraphTooLarge(
                    f"Diese Abfrage m\u00fcsste die Fakten von {len(missing)} Knoten lesen; eine Antwort darf "
                    f"h\u00f6chstens {self.max_fact_nodes} lesen (schon gelesen: {len(self._facts)}). Grenzen Sie "
                    "zuerst ein: mit einem kleineren Typ, mit with_relation/without_relation oder mit den Nachbarn "
                    "eines Knotens.")
            rows = self.port.facts(missing)
            for node_id in missing:
                found = rows.get(node_id)
                self._facts[node_id] = None if found is None else current_facts(found, self.today)
        return {n: self._facts.get(n) for n in ids}

    # -- helpers -----------------------------------------------------------------------------------------------------

    def _type_id(self, name: Any, *, required: bool = True) -> Optional[str]:
        topo = self.topology()
        if name is None or (isinstance(name, str) and not name.strip()):
            if required:
                raise ToolInputError("type fehlt.")
            return None
        if not isinstance(name, str):
            raise ToolInputError("type muss ein Text sein.")
        type_id = topo.type_by_fold.get(fold(name))
        if type_id is None:
            known = ", ".join(sorted(topo.type_name.values()))
            raise ToolInputError(f"Den Typ {_LAQUO}{_cut(name, 80)}{_RAQUO} gibt es nicht. Typen: {known}")
        return type_id

    def _node(self, node_id: Any) -> Node:
        topo = self.topology()
        if not isinstance(node_id, str) or node_id not in topo.nodes:
            raise ToolInputError("Diesen Knoten gibt es nicht (oder er ist nicht sichtbar). Nehmen Sie eine id, die "
                                 "ein Werkzeug in dieser Antwort geliefert hat.")
        return topo.nodes[node_id]

    def _relation(self, value: Any) -> str:
        topo = self.topology()
        if not isinstance(value, str) or not value.strip():
            raise ToolInputError("relation fehlt.")
        name = value.strip()
        if name not in topo.relations:
            folded = {fold(r): r for r in topo.relations}
            if fold(name) in folded:
                return folded[fold(name)]
            known = ", ".join(sorted(topo.relations)[:120])
            raise ToolInputError(f"Die Beziehung {_LAQUO}{_cut(name, 80)}{_RAQUO} gibt es nicht. Beziehungen: {known}")
        return name

    @staticmethod
    def _direction(value: Any, *, required: bool = False) -> Optional[str]:
        if value is None or value == "":
            if required:
                raise ToolInputError("direction fehlt (out oder in).")
            return None
        if value not in DIRECTIONS:
            raise ToolInputError("direction ist out (von diesem Knoten weg) oder in (auf diesen Knoten zu).")
        return value

    def type_name(self, node: Node) -> str:
        return self.topology().type_name.get(node.type_id, "")

    def edges_of(self, node_id: str, relation: Optional[str] = None,
                 direction: Optional[str] = None) -> List[Tuple[str, str, str]]:
        """``(relation, direction, other id)`` around a node, in the order of the edges."""
        topo = self.topology()
        out: List[Tuple[str, str, str]] = []
        if direction in (None, "out"):
            out += [(r, "out", other) for r, other in topo.out.get(node_id, ()) if relation in (None, r)]
        if direction in (None, "in"):
            out += [(r, "in", other) for r, other in topo.inc.get(node_id, ()) if relation in (None, r)]
        return out

    def _key(self, node_id: str) -> str:
        facts = self._facts.get(node_id)
        values = (facts or {}).get(KEY_FACT) or []
        return values[0] if values else ""

    def display(self, node_id: str) -> str:
        """A node's name in a list, with its key when the facts were read and the name does not hold it."""
        node = self.topology().nodes[node_id]
        key = self._key(node_id)
        return f"{node.name} ({key})" if key and fold(key) not in fold(node.name) else node.name

    def _passage(self, node_id: str, *, full: bool = False) -> Tuple[Optional[int], str]:
        """The node's passage (short: facts; full: with its connections) and its number (None: not numbered)."""
        node = self.topology().nodes[node_id]
        type_name = self.type_name(node)
        facts = self._facts.get(node_id)
        lines = self._connection_lines(node_id) if full else []
        text = node_text(node, type_name, facts, lines)
        return self.book.add(node_pointer(node_id), node_title(node, type_name), text), text

    def _connection_lines(self, node_id: str) -> List[str]:
        topo = self.topology()
        groups: Dict[Tuple[str, str, str], List[str]] = {}
        for relation, direction, other in self.edges_of(node_id):
            other_type = topo.type_name.get(topo.nodes[other].type_id, "")
            groups.setdefault((relation, direction, other_type), []).append(other)
        lines = []
        for (relation, direction, other_type), others in groups.items():
            arrow = "->" if direction == "out" else "<-"
            names = "; ".join(topo.nodes[o].name for o in others[:MAX_NEIGHBOURS_PER_RELATION])
            more = len(others) - MAX_NEIGHBOURS_PER_RELATION
            lines.append(f"{relation} {arrow} {other_type} ({len(others)}): {names}"
                         + (f" und {more} weitere" if more > 0 else ""))
        return lines

    def _entry(self, node_id: str, number: Optional[int], text: str) -> Dict[str, Any]:
        node = self.topology().nodes[node_id]
        entry: Dict[str, Any] = {"passage": number, "node_id": node_id, "type": self.type_name(node), "text": text}
        if number is None:
            entry["note"] = "nicht nummeriert (Grenze erreicht): nicht zitierbar"
        return entry

    # -- conditions ----------------------------------------------------------------------------------------------------

    def _conditions(self, where: Any) -> List[Tuple[str, str, str]]:
        if where in (None, []):
            return []
        if not isinstance(where, list) or len(where) > MAX_CONDITIONS:
            raise ToolInputError(f"where ist eine Liste von h\u00f6chstens {MAX_CONDITIONS} Bedingungen.")
        out = []
        for item in where:
            if not isinstance(item, Mapping):
                raise ToolInputError("Jede Bedingung ist ein Objekt mit fact, op und value.")
            fact, op = _clean(item.get("fact")), item.get("op") or "equals"
            if not fact or op not in OPS:
                raise ToolInputError("Eine Bedingung braucht fact und op (" + ", ".join(OPS) + ").")
            value = _value_text(item.get("value"))
            if op not in ("present", "missing") and not value:
                raise ToolInputError(f"Die Bedingung {fact} {op} braucht value.")
            out.append((fact, op, value))
        return out

    def _hops(self, items: Any, what: str, *, limit: int = MAX_CONDITIONS) -> List[Tuple[str, str, Optional[str]]]:
        if items in (None, []):
            return []
        if not isinstance(items, list) or len(items) > limit:
            raise ToolInputError(f"{what} ist eine Liste von h\u00f6chstens {limit} Eintr\u00e4gen.")
        out = []
        for item in items:
            if not isinstance(item, Mapping):
                raise ToolInputError(f"Jeder Eintrag von {what} ist ein Objekt mit relation und direction.")
            relation = self._relation(item.get("relation"))
            direction = self._direction(item.get("direction"), required=True)
            out.append((relation, direction, self._type_id(item.get("type"), required=False)))
        return out

    @staticmethod
    def _holds(facts: Mapping[str, List[str]], condition: Tuple[str, str, str]) -> bool:
        fact, op, wanted = condition
        values = facts.get(fact)
        if values is None:
            folded_label = fold(fact)
            values = next((v for label, v in facts.items() if fold(label) == folded_label), [])
        present = [v for v in values if v.strip()]
        if op == "present":
            return bool(present)
        if op == "missing":
            return not present
        if op == "equals":
            return any(same(v, wanted) for v in present)
        if op == "not_equals":
            return not any(same(v, wanted) for v in present)
        if op == "contains":
            return any(holds_text(v, wanted) for v in present)
        return any(_compare(fold(v), fold(wanted), op) for v in present)

    def _contains(self, node_id: str, text: str) -> bool:
        node = self.topology().nodes[node_id]
        if holds_text(node.name, text) or holds_text(node.description, text):
            return True
        return any(holds_text(v, text) for values in (self._facts.get(node_id) or {}).values() for v in values)

    def _related(self, node_id: str, hop: Tuple[str, str, Optional[str]]) -> List[str]:
        relation, direction, type_id = hop
        topo = self.topology()
        return [o for _, _, o in self.edges_of(node_id, relation, direction)
                if type_id is None or topo.nodes[o].type_id == type_id]

    def _filter(self, candidates: List[str], conditions: List[Tuple[str, str, str]], contains: str) -> Tuple[
            List[str], int]:
        """The candidates whose facts hold every condition and that contain ``contains``; and how many could not
        be read."""
        if not conditions and not contains:
            return candidates, 0
        facts = self.facts_of(candidates)
        kept, unread = [], 0
        for node_id in candidates:
            found = facts.get(node_id)
            if found is None:
                unread += 1
                continue
            if all(self._holds(found, c) for c in conditions) and (not contains or self._contains(node_id, contains)):
                kept.append(node_id)
        return kept, unread

    def _result(self, description: str, matched: List[str], limit: Any, *, pointer: str, unread: int = 0,
                extra: Optional[Dict[str, Any]] = None, asked: Optional[CountRead] = None) -> Dict[str, Any]:
        """The answer of a list or neighbours tool: a count passage and up to ``limit`` node passages."""
        size = _limit(limit, DEFAULT_RESULT_NODES, MAX_RESULT_NODES)
        shown = matched[:size]
        if shown:
            try:
                self.facts_of(shown)
            except GraphTooLarge:
                pass  # the fact budget is spent: the nodes are shown with their names and descriptions only
        incomplete = (f"Die Fakten von {unread} Knoten waren nicht lesbar; sie sind nicht mitgez\u00e4hlt."
                      if unread else "")
        names = [self.display(n) for n in matched[:MAX_LISTED_NAMES]]
        text = count_text(description, len(matched), names, incomplete=incomplete)
        number = self.book.add(pointer, "Abfrage: " + _cut(description, 160), text)
        if number is not None and asked is not None:
            self.counts.setdefault(number, CountRead(description=description, total=len(matched), about=asked.about,
                                                     relation=asked.relation, direction=asked.direction,
                                                     type_id=asked.type_id, matched=tuple(matched)))
        result: Dict[str, Any] = {"count": len(matched), "count_passage": {"passage": number, "text": text}}
        if unread:
            result["incomplete"] = True
        result["nodes"] = [self._entry(n, *self._passage(n)) for n in shown]
        if len(matched) > len(shown):
            result["note"] = (f"{len(matched) - len(shown)} weitere Treffer nur im count_passage genannt; limit "
                              f"h\u00f6her setzen (h\u00f6chstens {MAX_RESULT_NODES}) oder eingrenzen.")
        if extra:
            result.update(extra)
        return result

    # -- the tools -------------------------------------------------------------------------------------------------

    def describe_graph(self, type: Any = None) -> Dict[str, Any]:  # noqa: A002 - the tool's own parameter name
        """The graph's vocabulary: each type with its description and count, each relation with the types it joins
        and its count; with ``type``, that type's fields from its schema."""
        topo = self.topology()
        types = [{"type": name, "count": len(topo.by_type.get(type_id, ())),
                  **({"description": topo.type_description[type_id]} if topo.type_description.get(type_id) else {})}
                 for type_id, name in sorted(topo.type_name.items(), key=lambda item: item[1].casefold())]
        relations = []
        for relation in sorted(topo.relations):
            for (lo, hi), count in sorted(topo.relations[relation].items(), key=lambda item: -item[1]):
                relations.append({"relation": relation, "from": topo.type_name.get(lo, ""),
                                  "to": topo.type_name.get(hi, ""), "count": count})
        result: Dict[str, Any] = {"types": types, "relations": relations,
                                  "directions": "out: vom Knoten weg (from -> to); in: auf den Knoten zu"}
        if type not in (None, ""):
            type_id = self._type_id(type)
            schema = self.port.get(f"/node-types/{quote(type_id, safe='')}/schema")
            fields = []
            for attribute in _list(schema, "attributes") or _list(schema, "schema"):
                name = _clean(attribute.get("name"))
                if not name or name in BOOKKEEPING_FACTS:
                    continue
                item = {"fact": name}
                for key in ("datatype", "description"):
                    if _clean(attribute.get(key)):
                        item[key] = _clean(attribute.get(key))
                if isinstance(attribute.get("enum_values"), list):
                    item["values"] = [_value_text(v) for v in attribute["enum_values"][:40]]
                fields.append(item)
            result["facts_of_type"] = {"type": topo.type_name[type_id], "facts": fields}
        self.book.seen.append(Seen(title="", pointer="", text=json.dumps(result, ensure_ascii=False)))
        return result

    def find_nodes(self, text: Any, type: Any = None, limit: Any = None) -> Dict[str, Any]:  # noqa: A002
        """Nodes whose name or description holds every word of ``text`` (case, accents ignored); with a type and
        no such name, the nodes of that type with a fact equal to ``text`` (a key, a number)."""
        topo = self.topology()
        wanted = fold(text) if isinstance(text, str) else ""
        if not wanted:
            raise ToolInputError("text fehlt.")
        type_id = self._type_id(type, required=False)
        pool = topo.by_type.get(type_id, []) if type_id is not None else list(topo.nodes)
        words = str(text).split()
        scored = []
        for node_id in pool:
            node = topo.nodes[node_id]
            haystack = node.name + " " + node.description
            if all(holds_text(haystack, word) for word in words):
                if same(node.name, text):
                    rank = 0
                elif all(starts_a_word(node.name, word) for word in words):
                    rank = 1
                elif all(starts_a_word(haystack, word) for word in words):
                    rank = 2
                else:
                    rank = 3          # only inside a word ("bar" in "Lombardi")
                scored.append((rank, node.name.casefold(), node_id))
        scored.sort()
        found = [node_id for _, _, node_id in scored]
        by_fact = False
        if not found and type_id is not None and len(pool) <= MAX_FIND_FACT_NODES:
            facts = self.facts_of(pool)
            found = [n for n in pool if any(same(text, v) for values in (facts.get(n) or {}).values()
                                            for v in values)]
            by_fact = True
        size = _limit(limit, DEFAULT_FOUND, MAX_FOUND)
        shown = found[:size]
        result: Dict[str, Any] = {"total_matches": len(found),
                                  "nodes": [self._entry(n, *self._passage(n)) for n in shown]}
        if by_fact:
            result["matched_by"] = "fact"
        if len(found) > size:
            result["note"] = f"{len(found) - size} weitere Treffer; genauer suchen oder einen type angeben."
        if not found:
            result["note"] = ("Kein Knoten gefunden. Anders schreiben, einen Teil des Namens suchen, einen type angeben "
                              "oder list_nodes mit contains nutzen.")
        return result

    def read_node(self, node_id: Any) -> Dict[str, Any]:
        """A node with all its facts and its connections grouped by relation, as one passage; the neighbours' ids."""
        node = self._node(node_id)
        self.facts_of([node.id])
        number, text = self._passage(node.id, full=True)
        topo = self.topology()
        neighbours = [{"relation": relation, "direction": direction, "node_id": other,
                       "type": topo.type_name.get(topo.nodes[other].type_id, ""), "name": topo.nodes[other].name}
                      for relation, direction, other in self.edges_of(node.id)]
        result = {"node": self._entry(node.id, number, text), "neighbours": neighbours[:MAX_NEIGHBOUR_IDS]}
        if len(neighbours) > MAX_NEIGHBOUR_IDS:
            result["note"] = (f"{len(neighbours) - MAX_NEIGHBOUR_IDS} weitere Nachbarn ohne id: mit neighbours "
                              "nach Beziehung und Typ abfragen.")
        return result

    def neighbours(self, node_id: Any, relation: Any = None, direction: Any = None, type: Any = None,  # noqa: A002
                   where: Any = None, contains: Any = None, limit: Any = None) -> Dict[str, Any]:
        """The nodes joined to one node, filtered by relation, direction, type, facts and text, with their count."""
        node = self._node(node_id)
        name = self._relation(relation) if relation not in (None, "") else None
        way = self._direction(direction)
        type_id = self._type_id(type, required=False)
        conditions = self._conditions(where)
        text = _clean(contains) if isinstance(contains, str) else ""
        topo = self.topology()
        candidates = list(dict.fromkeys(o for _, _, o in self.edges_of(node.id, name, way)
                                        if type_id is None or topo.nodes[o].type_id == type_id))
        matched, unread = self._filter(candidates, conditions, text)
        parts = [f"verbunden mit {self.type_name(node)} {_LAQUO}{node.name}{_RAQUO}"]
        if name:
            parts.append(f"\u00fcber {name}" + (f" ({_DIRECTION_WORDS[way]})" if way else ""))
        elif way:
            parts.append(_DIRECTION_WORDS[way])
        if type_id is not None:
            parts.append("Typ " + topo.type_name[type_id])
        parts += _condition_words(conditions, text)
        asked = CountRead(description="", total=0, about=node.id, relation=name, direction=way, type_id=type_id)
        return self._result(", ".join(parts), matched, limit, pointer=node_pointer(node.id), unread=unread,
                            asked=asked)

    def list_nodes(self, type: Any, where: Any = None, contains: Any = None,  # noqa: A002
                   with_relation: Any = None, without_relation: Any = None, via: Any = None,
                   limit: Any = None) -> Dict[str, Any]:
        """The nodes of one type that hold every condition, with their count; with ``via``, the nodes those reach
        along a path of relations (a join), with their count."""
        type_id = self._type_id(type)
        conditions = self._conditions(where)
        text = _clean(contains) if isinstance(contains, str) else ""
        having = self._hops(with_relation, "with_relation")
        lacking = self._hops(without_relation, "without_relation")
        path = self._hops(via, "via", limit=MAX_PATH_HOPS)
        topo = self.topology()
        candidates = [n for n in topo.by_type.get(type_id, [])
                      if all(self._related(n, hop) for hop in having)
                      and not any(self._related(n, hop) for hop in lacking)]
        matched, unread = self._filter(candidates, conditions, text)
        parts = [topo.type_name[type_id]]
        parts += [f"mit {relation} ({_DIRECTION_WORDS[way]}" + (f", {topo.type_name[t]})" if t else ")")
                  for relation, way, t in having]
        parts += [f"ohne {relation} ({_DIRECTION_WORDS[way]}" + (f", {topo.type_name[t]})" if t else ")")
                  for relation, way, t in lacking]
        parts += _condition_words(conditions, text)
        description = ", ".join(parts)
        pointer = QUERY_POINTER_PREFIX + type_id
        if not path:
            return self._result(description, sorted(matched, key=lambda n: topo.nodes[n].name.casefold()), limit,
                                pointer=pointer, unread=unread, asked=CountRead("", 0, type_id=type_id))
        reached = matched
        for hop in path:
            reached = list(dict.fromkeys(o for n in reached for o in self._related(n, hop)))
        hops = " und ".join(f"{relation} ({_DIRECTION_WORDS[way]}" + (f", {topo.type_name[t]})" if t else ")")
                            for relation, way, t in path)
        description = f"erreicht von {len(matched)} Knoten ({description}) \u00fcber {hops}"
        return self._result(description, sorted(reached, key=lambda n: topo.nodes[n].name.casefold()), limit,
                            pointer=pointer, unread=unread, extra={"start_count": len(matched)},
                            asked=CountRead("", 0, type_id=path[-1][2]))


def _condition_words(conditions: Sequence[Tuple[str, str, str]], contains: str) -> List[str]:
    out = [f"{fact} {_OP_WORDS[op]}" + (f" {_LAQUO}{value}{_RAQUO}" if op not in ("present", "missing") else "")
           for fact, op, value in conditions]
    if contains:
        out.append(f"enth\u00e4lt {_LAQUO}{contains}{_RAQUO}")
    return out


def _number(text: str) -> Optional[float]:
    try:
        return float(text.replace("'", "").replace(" ", "").replace(",", "."))
    except ValueError:
        return None


def _compare(value: str, target: str, op: str) -> bool:
    left, right = _number(value), _number(target)
    if left is not None and right is not None:
        return left < right if op == "before" else left > right
    return value < target if op == "before" else value > target


def _limit(value: Any, default: int, most: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return max(1, min(int(value), most))


def fit(result: Mapping[str, Any]) -> str:
    """A tool's answer as the JSON Claude reads, cut to ``MAX_RESULT_CHARS``: node entries are dropped from the end
    (their passages stay numbered and citable from the count passage's names)."""
    data = dict(result)
    text = json.dumps(data, ensure_ascii=False)
    while len(text) > MAX_RESULT_CHARS and isinstance(data.get("nodes"), list) and data["nodes"]:
        data["nodes"] = data["nodes"][:-1]
        data["cut"] = True
        text = json.dumps(data, ensure_ascii=False)
    if len(text) > MAX_RESULT_CHARS and isinstance(data.get("neighbours"), list):
        data["neighbours"] = data["neighbours"][:20]
        data["cut"] = True
        text = json.dumps(data, ensure_ascii=False)
    return text[:MAX_RESULT_CHARS] if len(text) > MAX_RESULT_CHARS else text
