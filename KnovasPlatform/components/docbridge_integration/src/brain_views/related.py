"""The dossier's related things by kind (PMVP-30b, plan B-01, B-03): what is connected to one thing, counted per
kind and listed behind a tab, read as the signed-in person.

One starting point (PRE-01): one node id. The reads are per thing (POST-03) and go through the Platform's one Knovas
client, which signs every call as the person signed in on this request (as the dossier's reads do):

1. ``GET /secured/graph/nodes/<id>/neighbors?depth=1&include_edges=true`` (``graph_neighbors``): the things one hop
   away that this person may see, and the edges among them and the starting thing. The Core walks the person's
   visible subgraph only (graph_api.node_neighbors), so a related thing the person may not see is neither counted
   nor listed; nothing here filters by rights. The starting thing itself (hop 0), a row whose hop is not 1 and a row
   without a usable id are left out.
2. ``GET /secured/graph/node-types/<type>`` for the distinct kinds (``graph_get_many``, one round, each call signed as
   the person): the kind's name. At most ``MAX_NAMED_KINDS`` kinds are named, the ones with the most things; the
   rest, a kind whose read fails and things without a kind fall under one kind «Weitere Einträge».

Never ``graph_export``, ``graph_edges``, ``graph_nodes`` or ``graph_node_types``.

**A thing's relation** comes from the edges between the starting thing and it (``relation``); several are joined
(«Partei, Vermieterin»). An answer without edges leaves the rows without a relation.

**A kind's name in the plural** («Mandate 7»): the Brain has no plural (a node type has a name and a description), so
the plurals come from one table (``PLURALS``): the kinds of the legal pack (builder/packs/legal/pack.json, by key and
by German label) and of the pilot's tenant, Knovas GmbH (KnowledgeBase docs/ModernDocs/client-zero/ontology/
generated/node-types.json, English names, German plurals from their ``display_de``). A kind outside the table keeps the
name the Brain gives it.

**The lists** (B-03) sort and filter what was read, on the Platform (the Core has no paged neighbours read): by name
A to Z (the default) or Z to A, or by relation; one filter text over the names, case and spacing ignored, at most
``FILTER_MAX`` characters; ``brain_views.limit.paged`` cuts the pages.

Nothing here logs: a name, a relation and a filter text are the firm's text (GI-DATA-04).
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import quote

from knovas_client import KnowledgeGraphDisabled

from brain_views.dossier import UNNAMED_NODE, valid_node_id, valid_type_id

MAX_NAMED_KINDS = 20
FILTER_MAX = 100
OTHER_KIND = "ohne"
OTHER_KIND_NAME = "Weitere Einträge"

SORT_NAME = "name"
SORT_NAME_DESC = "name-ab"
SORT_RELATION = "beziehung"


class Unavailable(Exception):
    """The neighbours read failed; the message is the error's type name only."""


@dataclass(frozen=True)
class Thing:
    """A related thing: its id (its dossier's address), its name, and how it relates to the starting thing."""

    id: str
    name: str
    relation: Optional[str]


@dataclass(frozen=True)
class Kind:
    """One kind of related things: ``key`` is the type's uuid, or ``OTHER_KIND`` for «Weitere Einträge»."""

    key: str
    name: str
    things: Tuple[Thing, ...]

    @property
    def count(self) -> int:
        return len(self.things)


@dataclass(frozen=True)
class Related:
    kinds: Tuple[Kind, ...]
    total: int

    def kind(self, key: Any) -> Optional[Kind]:
        """The kind with this key, or None (an unknown key, or one this thing does not have)."""
        for kind in self.kinds:
            if kind.key == key:
                return kind
        return None


NOTHING = Related(kinds=(), total=0)


# -- the plurals ---------------------------------------------------------------------------------------------------

# By the kind's name as the Brain gives it (compared without regard to case and spacing).
PLURALS: Dict[str, str] = {
    # the legal pack (builder/packs/legal/pack.json): by key, and by the German label
    "Matter": "Mandate", "Mandat": "Mandate",
    "Proceeding": "Verfahren", "Verfahren": "Verfahren",
    "Court": "Gerichte", "Gericht": "Gerichte",
    "ProceduralDeadline": "Prozessuale Fristen", "Prozessuale Frist": "Prozessuale Fristen",
    "Submission": "Rechtsschriften", "Rechtsschrift": "Rechtsschriften",
    "CourtDecision": "Urteile", "Urteil": "Urteile",
    "PowerOfAttorney": "Vollmachten", "Vollmacht": "Vollmachten",
    "Forderung": "Forderungen",
    # the reference screens' kinds (02-dossier: «Mandate», «Verträge», «Personen»)
    "Mandant": "Mandanten", "Vertrag": "Verträge", "Partei": "Parteien", "Dokument": "Dokumente",
    "Organisation": "Organisationen",
    # the pilot's tenant, Knovas GmbH (client-zero ontology, generated/node-types.json)
    "Product": "Produkte",
    "PlatformTier": "Plattform-Stufen",
    "ApiServiceGroup": "API-Dienstgruppen",
    "Capability": "Fähigkeiten",
    "BoundedContext": "Software-Bereiche",
    "System": "Systeme",
    "Interface": "Schnittstellen",
    "Interaction": "Komponentenaufrufe",
    "Component": "Software-Komponenten",
    "ComponentContract": "Komponentenverträge",
    "Invariant": "Goldene Invarianten",
    "FeaturePlan": "Feature-Pläne",
    "Phase": "Phasen",
    "Gate": "Gates",
    "Milestone": "Meilensteine",
    "Goal": "Ziele",
    "KeyResult": "Schlüsselergebnisse",
    "Metric": "Kennzahlen",
    "MetricSample": "Messwerte",
    "Decision": "Entscheidungen",
    "Risk": "Risiken",
    "Hypothesis": "Hypothesen",
    "Experiment": "Experimente",
    "Result": "Ergebnisse",
    "PreMortem": "Pre-Mortems",
    "Tripwire": "Auslöseschwellen",
    "Vision": "Visionen",
    "Bet": "Wetten",
    "Forecast": "Prognosen",
    # Knovas GmbH's Claim is «Aussage»; the legal pack's key Claim is «Forderung». The pilot's reading wins here.
    "Claim": "Aussagen",
    "Question": "Prüffragen",
    "SpecItem": "Anforderungen",
    "WorkItem": "Aufgaben",
    "Idea": "Ideen",
    "Issue": "Tickets",
    "IssueProject": "Jira-Projekte",
    "Process": "Prozesse",
    "ProcessStep": "Prozessschritte",
    "ProcessInstance": "Prozessinstanzen",
    "Event": "Ereignisse",
    "RunEvent": "Laufereignisse",
    "Function": "Unternehmensfunktionen",
    "Role": "Rollen",
    "Person": "Personen",
    "Agent": "KI-Agenten",
    "Market": "Märkte",
    "Segment": "Kundensegmente",
    "Persona": "Personas",
    "EvidenceRecord": "Evidenzeinträge",
    "Channel": "Vertriebskanäle",
    "Campaign": "Kampagnen",
    "MarketingAsset": "Marketing-Materialien",
    "Lead": "Leads",
    "Account": "Kundenorganisationen",
    "Opportunity": "Verkaufschancen",
    "Quote": "Angebote",
    "Contract": "Verträge",
    "Customer": "Kundenbeziehungen",
    "Record": "Dokumentbeschreibungen",
    "Repository": "Code-Repositorys",
    "SourceFile": "Quelldateien",
    "Symbol": "Code-Symbole",
    "Commit": "Commits",
    "TestRun": "Testläufe",
    "AlloyCheck": "Alloy-Prüfungen",
    "GlossaryTerm": "Glossarbegriffe",
    "Release": "Releases",
    "Spine": "Strukturachsen",
    "SpineLevel": "Strukturebenen",
    "Company": "Unternehmen",
    "ValueStream": "Wertströme",
    "Deployment": "Deployments",
    "Service": "Dienste",
    "Job": "Geplante Jobs",
    "Environment": "Umgebungen",
    "DataStore": "Datenspeicher",
    "DataSet": "Datenbestände",
    "Setting": "Einstellungen",
    "Workflow": "CI-Workflows",
    "WorkflowJob": "CI-Jobs",
    "Library": "Fremdbibliotheken",
}


def _folded(text: str) -> str:
    return " ".join(text.split()).casefold()


_PLURALS_FOLDED = {_folded(name): plural for name, plural in PLURALS.items()}


def plural(name: str) -> str:
    """The kind's name in the plural from ``PLURALS``; a name outside the table as the Brain gives it."""
    return _PLURALS_FOLDED.get(_folded(name), name)


# -- the read ------------------------------------------------------------------------------------------------------


def read_related(client: Any, node_id: str) -> Related:
    """The things one hop from this node, by kind, as the person the client signs for may see them.

    A graph switched off in the Core and an unknown or hidden node (the client gives an empty answer for its 404)
    are "nothing related". Any other failure of the neighbours read raises Unavailable; a failed kind read only
    leaves that kind unnamed.
    """
    if not valid_node_id(node_id):
        return NOTHING
    try:
        answer = client.graph_neighbors(node_id, depth=1, include_edges=True)
    except KnowledgeGraphDisabled:
        return NOTHING
    except Exception as exc:  # noqa: BLE001 - every other failure is one plain line on the page
        raise Unavailable(type(exc).__name__) from None
    if not isinstance(answer, Mapping):
        return NOTHING
    rows = _rows(answer.get("neighbors"), node_id)
    if not rows:
        return NOTHING
    relations = _relations(answer.get("edges"), node_id)
    names = _kind_names(client, [type_id for _, type_id in rows])
    grouped: Dict[str, List[Thing]] = {}
    for thing_row, type_id in rows:
        key = type_id if type_id in names else OTHER_KIND
        thing = Thing(id=thing_row["id"], name=thing_row["name"], relation=relations.get(thing_row["id"].lower()))
        grouped.setdefault(key, []).append(thing)
    kinds = [Kind(key=key, name=plural(names[key]) if key != OTHER_KIND else OTHER_KIND_NAME,
                  things=tuple(sorted(things, key=lambda t: (sort_key(t.name), t.id))))
             for key, things in grouped.items()]
    # By count, highest first, then by name; «Weitere Einträge», which is no kind of its own, comes last.
    kinds.sort(key=lambda k: (k.key == OTHER_KIND, -k.count, sort_key(k.name), k.key))
    return Related(kinds=tuple(kinds), total=sum(k.count for k in kinds))


def _rows(neighbours: Any, node_id: str) -> List[Tuple[Dict[str, str], Optional[str]]]:
    """(the thing's id and name, its type id) for every row one hop away, each id once, the start left out."""
    found: List[Tuple[Dict[str, str], Optional[str]]] = []
    seen = {node_id.lower()}
    for row in neighbours if isinstance(neighbours, list) else []:
        if not isinstance(row, Mapping):
            continue
        hop = row.get("hop")
        if isinstance(hop, bool) or hop != 1:
            continue
        thing_id = row.get("id")
        if not valid_node_id(thing_id) or thing_id.lower() in seen:
            continue
        seen.add(thing_id.lower())
        type_id = row.get("node_type_id")
        found.append(({"id": thing_id, "name": _clean(row.get("name")) or UNNAMED_NODE},
                      type_id.lower() if valid_type_id(type_id) else None))
    return found


def _relations(edges: Any, node_id: str) -> Dict[str, str]:
    """For each thing joined to the start by an edge (by its id in lower case), its relations joined
    («Partei, Vermieterin»)."""
    start = node_id.lower()
    found: Dict[str, set] = {}
    for edge in edges if isinstance(edges, list) else []:
        if not isinstance(edge, Mapping):
            continue
        ends = [edge.get("node_lo"), edge.get("node_hi")]
        if not all(isinstance(end, str) for end in ends):
            continue
        lowered = [end.lower() for end in ends]
        if start not in lowered or lowered[0] == lowered[1]:
            continue
        other = ends[1] if lowered[0] == start else ends[0]
        relation = _clean(edge.get("relation"))
        if relation:
            found.setdefault(other.lower(), set()).add(relation)
    return {other: ", ".join(sorted(names, key=sort_key)) for other, names in found.items()}


def _kind_names(client: Any, type_ids: Sequence[Optional[str]]) -> Dict[str, str]:
    """{type id: its name} for the kinds with the most things, at most MAX_NAMED_KINDS, in one round of reads."""
    counts: Dict[str, int] = {}
    for type_id in type_ids:
        if type_id:
            counts[type_id] = counts.get(type_id, 0) + 1
    chosen = sorted(counts, key=lambda t: (-counts[t], t))[:MAX_NAMED_KINDS]
    if not chosen:
        return {}
    try:
        answers = client.graph_get_many([(f"/node-types/{quote(t, safe='')}", None) for t in chosen],
                                        workers=min(4, len(chosen)))
    except Exception:  # noqa: BLE001 - the kinds go unnamed; the things are still listed
        return {}
    if not isinstance(answers, Sequence) or len(answers) != len(chosen):
        return {}
    names: Dict[str, str] = {}
    for type_id, answer in zip(chosen, answers):
        if not isinstance(answer, Sequence) or len(answer) != 2:
            continue
        status, payload = answer
        node_type = payload.get("node_type") if status == 200 and isinstance(payload, Mapping) else None
        name = _clean(node_type.get("name")) if isinstance(node_type, Mapping) else ""
        if name:
            names[type_id] = name
    return names


# -- the lists -----------------------------------------------------------------------------------------------------


def sort_key(text: str) -> str:
    """A name as the lists sort it: case, accents and spacing ignored («Ärzte» beside «Arzt», not after «Zürich»)."""
    decomposed = unicodedata.normalize("NFKD", text.replace("ß", "ss"))
    return " ".join("".join(c for c in decomposed if not unicodedata.combining(c)).split()).casefold()


def filter_text(raw: Any) -> str:
    """The filter as the page keeps it: spacing collapsed, at most FILTER_MAX characters, no control characters."""
    if not isinstance(raw, str):
        return ""
    text = "".join(c for c in raw if unicodedata.category(c)[0] != "C")
    return " ".join(text.split())[:FILTER_MAX].strip()


def matches(name: str, needle: str) -> bool:
    """Whether a name holds the filter text, case and spacing ignored."""
    return not needle or _folded(needle) in _folded(name)


def sort_choice(raw: Any, *, relations: bool) -> str:
    """The list's sort from the address: one of the known keys, else by name A to Z."""
    allowed = (SORT_NAME, SORT_NAME_DESC, SORT_RELATION) if relations else (SORT_NAME, SORT_NAME_DESC)
    return raw if raw in allowed else SORT_NAME


def arrange(items: Sequence[Any], *, name: Callable[[Any], str], needle: str, sort: str,
            relation: Optional[Callable[[Any], Optional[str]]] = None) -> List[Any]:
    """The items the filter keeps, in the chosen order (by relation: things without one last, then by name)."""
    kept = [item for item in items if matches(name(item), needle)]
    by_name = sorted(kept, key=lambda item: sort_key(name(item)))
    if sort == SORT_NAME_DESC:
        return list(reversed(by_name))
    if sort == SORT_RELATION and relation is not None:
        return sorted(by_name, key=lambda item: (relation(item) is None, sort_key(relation(item) or "")))
    return by_name


def _clean(value: Any) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""

