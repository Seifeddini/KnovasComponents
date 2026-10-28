"""Write one link or field change into the KnowledgeBase file that owns it (Step 7).

The edit map (graph_views.edit_map, KnowledgeBase DEC-0022) says where each relation and fact a
definition uses is edited: a file and a key path in it, as prose. Each writer here implements one
edit-map id, and records the ``source`` and ``key_path`` it was written for. When the pinned edit
map says something else, the writer refuses ("routing changed") rather than writing to the place
the edit map no longer names; tests/test_views_writers.py fails on re-vendoring.

Edits are line edits on the file's text, never a re-serialisation. After every edit the writer
reads the result back the way KnowledgeBase's loader reads it (``declared`` below is a port of
scripts/client_zero/providers/plan_declarations.py at the pinned commit) and refuses an edit
whose result the loader would not see.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import yaml

from graph_views import edit_map, plan_file
from graph_views.keys import key_type

MODERNDOCS = "docs/ModernDocs"
PLAN_LINKS = f"{MODERNDOCS}/client-zero/ontology/plan-links.yaml"
SAMPLE = f"{MODERNDOCS}/client-zero/ontology/sample.yaml"


class WriteError(Exception):
    """The change cannot be written where the edit map says; the message says why."""


# -- KnowledgeBase's reading of a plan (port of providers/plan_declarations.py) --------------

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
GI_RE = re.compile(r"\bGI-[A-Z0-9]+(?:-[A-Z0-9]+)*-\d+\b")
CONTRACT_ID_RE = re.compile(r"^\s*(?:\|\s*|[-*]\s+(?:\*\*)?)((?:PRE|POST|RESP)-[A-Z0-9][A-Z0-9-]*)\b")
L2_RE = re.compile(r"\bL2-[A-Z0-9][A-Z0-9-]*[A-Z0-9]\b")
ALS_RE = re.compile(r"[A-Za-z0-9_./-]+\.als\b")
AFFECTED_RE = re.compile(r"^\|\s*\*\*Affected L2\*\*\s*\|\s*([^|]+)\|", re.M)
RISK_ROW_RE = re.compile(r"^\|\s*(RISK-\d+)\s*\|(.*)$", re.M)


def sections(text: str) -> List[Tuple[int, int, int, str]]:
    """(heading line, end line, level, title) per heading; a section runs to the next of its level or higher."""
    lines = text.splitlines()
    heads = [(i, len(m.group(1)), m.group(2)) for i, ln in enumerate(lines) if (m := HEADING_RE.match(ln))]
    out = []
    for n, (i, lvl, title) in enumerate(heads):
        end = next((j for j, l2, _ in heads[n + 1:] if l2 <= lvl), len(lines))
        out.append((i, end, lvl, title))
    return out


def titled(text: str, pattern: str) -> List[Tuple[int, int, int, str]]:
    rx = re.compile(pattern, re.I)
    return [s for s in sections(text) if rx.search(re.sub(r"^[\d.\s·§-]+", "", s[3]))]


GI_SECTION = r"^golden invariants"
CONTRACT_SECTION = r"contract"
ALLOY_SECTION = r"^alloy\b"


def alloy_norm(model_path: str) -> str:
    """providers/alloy.py ``_norm``: a plan's model path as the lockfile names it."""
    p = model_path.replace("\\", "/")
    for prefix in ("KnowledgeBase/knovas-software/models/alloy/", "knovas-software/models/alloy/", "models/alloy/"):
        if p.startswith(prefix):
            return p[len(prefix):]
    return p


@dataclass
class Declared:
    invariants: List[str] = field(default_factory=list)
    contracts: List[str] = field(default_factory=list)       # <L2>/<id>
    alloy_models: List[str] = field(default_factory=list)    # as written


def _first_cells(lines: List[str]) -> List[str]:
    cells = []
    for ln in lines:
        s = ln.strip()
        if s.startswith("|") and not re.match(r"^\|\s*-{2,}", s):
            cells.append(s.strip("|").split("|")[0].strip())
    return cells


def declared(text: str, sidecar: Optional[str]) -> Declared:
    """Every id the plan declares, resolved or not (``declared`` without the key sets)."""
    d = Declared()
    lines = text.splitlines()
    gis: List[str] = []
    for i, end, _, _ in titled(text, GI_SECTION):
        body = lines[i + 1:end]
        for cell in _first_cells(body):
            gis += GI_RE.findall(cell)
        for ln in body:
            if re.match(r"^\s*[-*]\s+", ln):
                m = GI_RE.search(ln.split(":")[0])
                if m:
                    gis.append(m.group(0))
    if sidecar:
        try:
            block = (yaml.safe_load(sidecar) or {}).get("golden_invariants") or {}
        except yaml.YAMLError:
            block = {}
        for kind in ("preserved", "extended", "proposed"):
            for item in (block.get(kind) or []) if isinstance(block, dict) else []:
                gid = item.get("id") if isinstance(item, dict) else item
                if gid and GI_RE.fullmatch(str(gid)):
                    gis.append(str(gid))
    d.invariants = list(dict.fromkeys(gis))
    for i, end, _, _ in titled(text, CONTRACT_SECTION):
        comp = None
        for ln in lines[i + 1:end]:
            h = HEADING_RE.match(ln)
            lead = h.group(2) if h else (ln.strip() if ln.lstrip().startswith("**") else None)
            if lead is not None:
                m = L2_RE.search(lead)
                comp = m.group(0) if m else comp
                if h:
                    continue
            m = CONTRACT_ID_RE.match(ln)
            if m and comp and f"{comp}/{m.group(1)}" not in d.contracts:
                d.contracts.append(f"{comp}/{m.group(1)}")
    for i, end, _, _ in titled(text, ALLOY_SECTION):
        for ln in lines[i + 1:end]:
            for p in ALS_RE.findall(ln):
                if p not in d.alloy_models:
                    d.alloy_models.append(p)
    return d


# -- writers ------------------------------------------------------------------------------

@dataclass
class Plan:
    key: str
    path: str                       # docs/ModernDocs/plans/<file>.md

    @property
    def sidecar(self) -> str:
        return self.path[:-3] + ".ai.yaml"


Texts = Dict[str, Optional[str]]    # path -> text at the base (None: the file does not exist)


class Writer:
    """One edit-map id. Subclasses set the routing they implement and the four operations."""

    edit_id: str = ""
    source: str = ""                #: the edit map's ``source`` this writer was written for
    key_path: str = ""              #: … and its ``key_path``
    can_add = True                  #: False: adding needs more than a key (a new risk row)
    label = ""

    def files(self, plan: Plan) -> List[str]:
        raise NotImplementedError

    def present(self, texts: Texts, plan: Plan) -> List[str]:
        raise NotImplementedError

    def normalise(self, target: str) -> str:
        return target.strip()

    def check_target(self, target: str) -> None:
        """Refuse a target that is not a key of the relation's other end."""

    def add(self, texts: Texts, plan: Plan, target: str, note: str) -> Dict[str, str]:
        raise NotImplementedError

    def remove(self, texts: Texts, plan: Plan, target: str) -> Dict[str, str]:
        raise NotImplementedError

    def routing_problem(self) -> Optional[str]:
        e = edit_map.entry(self.edit_id)
        if e is None:
            return f"„{self.edit_id}“ steht nicht in der Edit-Map."
        if not e.get("editable"):
            return f"Laut Edit-Map nicht editierbar ({e.get('reason')}): {e.get('fix_at')}"
        if e.get("source") != self.source or e.get("key_path") != self.key_path:
            return (f"Die Edit-Map nennt für „{self.edit_id}“ jetzt {e.get('source')} · {e.get('key_path')}; "
                    f"die Plattform schreibt {self.source} · {self.key_path}.")
        return None


def _insert_after(text: str, line_no: int, new_lines: List[str]) -> str:
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    lines[line_no + 1:line_no + 1] = [ln + "\n" for ln in new_lines]
    return "".join(lines)


def _drop_lines(text: str, drop: List[int]) -> str:
    lines = text.splitlines(keepends=True)
    return "".join(ln for i, ln in enumerate(lines) if i not in set(drop))


# -- YAML edge lists (plan-links.yaml, sample.yaml) -------------------------------------------

EDGE_RE = re.compile(r"^(\s*)-\s*\{\s*from:\s*([^,}]+?)\s*,\s*relation:\s*([a-z_]+)\s*,\s*to:\s*([^,}]+?)\s*\}\s*(#.*)?$")


class YamlEdges(Writer):
    """``edges: - {from: A, relation: r, to: B}``; the instance is the ``from`` or the ``to`` end."""

    def __init__(self, edit_id: str, path: str, relation: str, target_types: Tuple[str, ...], *,
                 instance_is: str, source: str, key_path: str, label: str):
        self.edit_id, self.path, self.relation = edit_id, path, relation
        self.target_types, self.instance_is = target_types, instance_is
        self.source, self.key_path, self.label = source, key_path, label

    def files(self, plan: Plan) -> List[str]:
        return [self.path]

    def _ends(self, plan: Plan, target: str) -> Tuple[str, str]:
        return (plan.key, target) if self.instance_is == "from" else (target, plan.key)

    def _edges(self, text: str) -> List[Tuple[int, str, str, str]]:
        out = []
        for i, ln in enumerate((text or "").splitlines()):
            m = EDGE_RE.match(ln)
            if m:
                out.append((i, m.group(2), m.group(3), m.group(4)))
        return out

    def present(self, texts: Texts, plan: Plan) -> List[str]:
        mine, other = (1, 3) if self.instance_is == "from" else (3, 1)     # (line, from, relation, to)
        return [str(e[other]) for e in self._edges(texts.get(self.path) or "")
                if e[2] == self.relation and e[mine] == plan.key]

    def check_target(self, target: str) -> None:
        if key_type(target, *self.target_types) is None:
            raise WriteError(f"{target} ist kein Schlüssel vom Typ {' oder '.join(self.target_types)} (terms.yaml).")

    def add(self, texts: Texts, plan: Plan, target: str, note: str) -> Dict[str, str]:
        text = texts.get(self.path)
        if text is None:
            raise WriteError(f"{self.path} gibt es auf dem Basisstand nicht.")
        frm, to = self._ends(plan, target)
        edges = self._edges(text)
        if edges:
            last = EDGE_RE.match(text.splitlines()[edges[-1][0]])
            indent = last.group(1) if last else "  "
            new = _insert_after(text, edges[-1][0], [f"{indent}# {note}", f"{indent}- {{from: {frm}, relation: {self.relation}, to: {to}}}"])
        elif re.search(r"^edges:\s*\[\]\s*$", text, re.M):
            new = re.sub(r"^edges:\s*\[\]\s*$", f"edges:\n  # {note}\n  - {{from: {frm}, relation: {self.relation}, to: {to}}}",
                         text, count=1, flags=re.M)
        else:
            raise WriteError(f"{self.path} hat keine Liste edges, in die sich die Kante einfügen lässt.")
        return {self.path: new}

    def remove(self, texts: Texts, plan: Plan, target: str) -> Dict[str, str]:
        text = texts.get(self.path) or ""
        frm, to = self._ends(plan, target)
        drop = [i for i, f, r, t in self._edges(text) if f == frm and r == self.relation and t == to]
        if not drop:
            raise WriteError(f"Die Kante {frm} –{self.relation}→ {to} steht nicht in {self.path} "
                             "(dort liest die Edit-Map sie); sie kommt aus einer anderen Quelle.")
        return {self.path: _drop_lines(text, drop)}


# -- the plan file --------------------------------------------------------------------------

def _plan_text(texts: Texts, plan: Plan) -> str:
    text = texts.get(plan.path)
    if text is None:
        raise WriteError(f"Plan-Datei {plan.path} gibt es auf dem Basisstand nicht.")
    return text


def _section_anchor(text: str, before: Tuple[str, ...]) -> Optional[int]:
    """Line of the first level-2 heading whose title matches one of ``before`` (template order)."""
    for i, _, lvl, title in sections(text):
        clean = re.sub(r"^[\d.\s·§-]+", "", title)
        if lvl == 2 and any(re.search(p, clean, re.I) for p in before):
            return i
    return None


def _new_section(text: str, block: List[str], before: Tuple[str, ...]) -> str:
    at = _section_anchor(text, before)
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    new = [ln + "\n" for ln in block] + ["\n"]
    if at is None:
        return "".join(lines).rstrip("\n") + "\n\n" + "".join(new).rstrip("\n") + "\n"
    lines[at:at] = new
    return "".join(lines)


def _table_rows(lines: List[str], start: int, end: int) -> List[Tuple[int, int]]:
    """(first line, last line) of each table between ``start`` and ``end``."""
    out, i = [], start
    while i < end:
        if lines[i].lstrip().startswith("|"):
            j = i
            while j + 1 < end and lines[j + 1].lstrip().startswith("|"):
                j += 1
            out.append((i, j))
            i = j + 1
        else:
            i += 1
    return out


def _cells(line: str) -> List[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


class AffectedRow(Writer):
    edit_id = "FeaturePlan affects Component"
    source = "docs/ModernDocs/plans/*.md"
    key_path = "table row | **Affected L2** | (L2-… keys of the manifest)"
    label = "Betroffene Komponenten (Affected L2)"

    def files(self, plan: Plan) -> List[str]:
        return [plan.path, plan.sidecar]

    def present(self, texts: Texts, plan: Plan) -> List[str]:
        m = AFFECTED_RE.search(texts.get(plan.path) or "")
        return list(dict.fromkeys(L2_RE.findall(m.group(1)))) if m else []

    def check_target(self, target: str) -> None:
        if not L2_RE.fullmatch(target):
            raise WriteError(f"{target} ist kein L2-Schlüssel; die Zeile Affected L2 nimmt nur L2-… auf.")

    def _write(self, texts: Texts, plan: Plan, keys: List[str]) -> Dict[str, str]:
        out = {plan.path: plan_file.apply(_plan_text(texts, plan), {"affected_components": keys}).text}
        side = texts.get(plan.sidecar)
        if side:
            new = plan_file.apply_ai_yaml(side, {"affected_components": keys}).text
            if new != side:
                out[plan.sidecar] = new
        return out

    def add(self, texts: Texts, plan: Plan, target: str, note: str) -> Dict[str, str]:
        return self._write(texts, plan, self.present(texts, plan) + [target])

    def remove(self, texts: Texts, plan: Plan, target: str) -> Dict[str, str]:
        return self._write(texts, plan, [k for k in self.present(texts, plan) if k != target])


class GoldenInvariants(Writer):
    edit_id = "FeaturePlan keeps Invariant"
    source = "docs/ModernDocs/plans/*.md (+ the sidecar .ai.yaml)"
    key_path = ("## Golden Invariants table, first column (| GI-… |); .ai.yaml golden_invariants."
                "preserved/extended/proposed (DEC-0023)")
    label = "Goldene Invarianten"

    def files(self, plan: Plan) -> List[str]:
        return [plan.path, plan.sidecar]

    def present(self, texts: Texts, plan: Plan) -> List[str]:
        return declared(texts.get(plan.path) or "", texts.get(plan.sidecar)).invariants

    def check_target(self, target: str) -> None:
        if not GI_RE.fullmatch(target):
            raise WriteError(f"{target} ist keine Invarianten-Id (GI-…).")

    def add(self, texts: Texts, plan: Plan, target: str, note: str) -> Dict[str, str]:
        text = _plan_text(texts, plan)
        lines = text.splitlines()
        secs = titled(text, GI_SECTION)
        if secs:
            i, end, _, _ = secs[0]
            tables = _table_rows(lines, i + 1, end)
            if tables:
                first, last = tables[0]
                head = _cells(lines[first])
                cells = [target] + [""] * (len(head) - 1)
                if len(head) > 1 and head[1].lower().startswith("impact"):
                    cells[1] = "preserved"
                return {plan.path: _insert_after(text, last, ["| " + " | ".join(cells) + " |"])}
            return {plan.path: _insert_after(text, i, ["", "| GI ID | Impact |", "|-------|--------|",
                                                       f"| {target} | preserved |"])}
        return {plan.path: _new_section(text, ["## Golden Invariants", "", "| GI ID | Impact |", "|-------|--------|",
                                               f"| {target} | preserved |"], (r"^alloy\b", r"^estimate", r"^references"))}

    def remove(self, texts: Texts, plan: Plan, target: str) -> Dict[str, str]:
        out: Dict[str, str] = {}
        text = _plan_text(texts, plan)
        lines = text.splitlines(keepends=True)
        drop, edits = [], {}
        for i, end, _, _ in titled(text, GI_SECTION):
            for n in range(i + 1, end):
                ln = lines[n]
                s = ln.strip()
                if s.startswith("|") and not re.match(r"^\|\s*-{2,}", s):
                    first = s.strip("|").split("|")[0]
                    ids = GI_RE.findall(first)
                    if ids == [target]:
                        drop.append(n)
                    elif target in ids:   # several ids in one cell: take this one out
                        edits[n] = ln.replace(target, "", 1)
                elif re.match(r"^\s*[-*]\s+", ln):
                    m = GI_RE.search(ln.split(":")[0])
                    if m and m.group(0) == target:
                        drop.append(n)
        if drop or edits:
            lines = [edits.get(n, ln) for n, ln in enumerate(lines)]
            out[plan.path] = "".join(ln for n, ln in enumerate(lines) if n not in set(drop))
        side = texts.get(plan.sidecar)
        if side:
            new = _drop_sidecar_invariant(side, target)
            if new != side:
                out[plan.sidecar] = new
        if not out:
            raise WriteError(f"{target} steht weder in der Golden-Invariants-Tabelle noch in der .ai.yaml.")
        return out


def _drop_sidecar_invariant(text: str, target: str) -> str:
    """Remove the id inside ``golden_invariants:``: a ``- GI-X`` or ``- id: GI-X`` item (with its
    indented lines), or one entry of an inline list ``preserved: [GI-X, GI-Y]``."""
    lines = text.splitlines(keepends=True)
    out, inside, skip_indent = [], False, None
    for ln in lines:
        if re.match(r"^golden_invariants:", ln):
            inside = True
            out.append(ln)
            continue
        if inside and ln.strip() and not ln[0].isspace():
            inside = False
        if skip_indent is not None:
            if ln.strip() and len(ln) - len(ln.lstrip()) > skip_indent:
                continue
            skip_indent = None
        if inside:
            m = re.match(r"^(\s*)-\s*(?:id:\s*)?([A-Z0-9-]+)\s*$", ln.rstrip("\n"))
            if m and m.group(2) == target:
                skip_indent = len(m.group(1))
                continue
            if re.match(rf"^\s*-\s*\{{\s*id:\s*[\"']?{re.escape(target)}[\"']?\s*[,}}]", ln):   # - {id: GI-X, …}
                continue
            flow = re.match(r"^(\s*[a-z_]+:\s*\[)(.*)(\]\s*)$", ln.rstrip("\n"))
            if flow and target in [x.strip() for x in flow.group(2).split(",")]:
                kept = [x.strip() for x in flow.group(2).split(",") if x.strip() and x.strip() != target]
                ln = flow.group(1) + ", ".join(kept) + flow.group(3) + "\n"
        out.append(ln)
    return "".join(out)


class ContractDeltas(Writer):
    edit_id = "FeaturePlan keeps ComponentContract"
    source = "docs/ModernDocs/plans/*.md"
    key_path = ("## Component contract deltas: ### L2-… block, | PRE-… | / | POST-… | rows or RESP-…: bullets; "
                "linked when the component spec defines the id")
    label = "Komponentenverträge"

    def files(self, plan: Plan) -> List[str]:
        return [plan.path]

    def present(self, texts: Texts, plan: Plan) -> List[str]:
        return declared(texts.get(plan.path) or "", None).contracts

    def check_target(self, target: str) -> None:
        comp, _, cid = target.partition("/")
        if not (L2_RE.fullmatch(comp) and re.fullmatch(r"(?:PRE|POST|RESP)-[A-Z0-9][A-Z0-9-]*", cid)):
            raise WriteError(f"{target} ist kein Vertragsschlüssel der Form L2-…/PRE-…, POST-… oder RESP-….")

    def add(self, texts: Texts, plan: Plan, target: str, note: str) -> Dict[str, str]:
        text = _plan_text(texts, plan)
        comp, _, cid = target.partition("/")
        bullet = f"- {cid}: (Vertrag aus der Komponenten-Spezifikation; {note})"
        secs = [s for s in titled(text, CONTRACT_SECTION) if s[2] == 2] or titled(text, CONTRACT_SECTION)
        if not secs:
            return {plan.path: _new_section(text, ["## Component contract deltas", "", f"### {comp}", "", bullet],
                                            (r"^glossary", r"^golden invariants", r"^alloy\b", r"^references"))}
        i, end, lvl, _ = secs[0]
        lines = text.splitlines()
        for n in range(i + 1, end):
            h = HEADING_RE.match(lines[n])
            hit = L2_RE.search(h.group(2)) if h and len(h.group(1)) > lvl else None
            if hit and hit.group(0) == comp:
                return {plan.path: _insert_after(text, n, ["", bullet])}
        last = end - 1
        while last > i and not lines[last].strip():
            last -= 1
        return {plan.path: _insert_after(text, last, ["", f"{'#' * (lvl + 1)} {comp}", "", bullet])}

    def remove(self, texts: Texts, plan: Plan, target: str) -> Dict[str, str]:
        text = _plan_text(texts, plan)
        comp, _, cid = target.partition("/")
        lines = text.splitlines()
        drop = []
        for i, end, _, _ in titled(text, CONTRACT_SECTION):
            cur = None
            for n in range(i + 1, end):
                ln = lines[n]
                h = HEADING_RE.match(ln)
                lead = h.group(2) if h else (ln.strip() if ln.lstrip().startswith("**") else None)
                if lead is not None:
                    m = L2_RE.search(lead)
                    cur = m.group(0) if m else cur
                    if h:
                        continue
                m = CONTRACT_ID_RE.match(ln)
                if m and cur == comp and m.group(1) == cid:
                    drop.append(n)
        if not drop:
            raise WriteError(f"{cid} steht nicht unter {comp} in den Component contract deltas.")
        return {plan.path: _drop_lines(text, drop)}


class AlloySection(Writer):
    edit_id = "AlloyCheck verifies FeaturePlan"
    source = "docs/ModernDocs/plans/*.md"
    key_path = "## Alloy table, model paths (*.als); every check of the model verifies the plan"
    label = "Alloy-Modelle"

    def files(self, plan: Plan) -> List[str]:
        return [plan.path]

    def normalise(self, target: str) -> str:
        """A check key ``ALLOY-<model>#<command>`` stands for its model: the plan names models."""
        t = target.strip()
        if t.startswith("ALLOY-"):
            t = t[len("ALLOY-"):].split("#", 1)[0]
        return alloy_norm(t)

    def present(self, texts: Texts, plan: Plan) -> List[str]:
        return list(dict.fromkeys(alloy_norm(p) for p in declared(texts.get(plan.path) or "", None).alloy_models))

    def check_target(self, target: str) -> None:
        if not ALS_RE.fullmatch(target) or ".." in target.split("/"):
            raise WriteError(f"{target} ist kein Alloy-Modellpfad (…/name.als).")

    def add(self, texts: Texts, plan: Plan, target: str, note: str) -> Dict[str, str]:
        text = _plan_text(texts, plan)
        row = f"| `{target}` | extend | |"
        secs = titled(text, ALLOY_SECTION)
        if secs:
            i, end, _, _ = secs[0]
            lines = text.splitlines()
            for first, last in _table_rows(lines, i + 1, end):
                if "model" in lines[first].lower():
                    head = _cells(lines[first])
                    cells = [f"`{target}`"] + [""] * (len(head) - 1)
                    if len(head) > 1 and head[1].lower().startswith("action"):
                        cells[1] = "extend"
                    return {plan.path: _insert_after(text, last, ["| " + " | ".join(cells) + " |"])}
            last = end - 1
            while last > i and not lines[last].strip():
                last -= 1
            return {plan.path: _insert_after(text, last, ["", "| Model path | Action | Checks |",
                                                          "|------------|--------|--------|", row])}
        return {plan.path: _new_section(text, ["## Alloy", "", "| Model path | Action | Checks |",
                                               "|------------|--------|--------|", row],
                                        (r"^estimate", r"^test plan", r"^size estimate", r"^references"))}

    def remove(self, texts: Texts, plan: Plan, target: str) -> Dict[str, str]:
        """Drop a line that names only this model; on a line naming others too, take out just this one."""
        text = _plan_text(texts, plan)
        lines = text.splitlines(keepends=True)
        hit = False
        for i, end, _, _ in titled(text, ALLOY_SECTION):
            for n in range(i + 1, end):
                found = ALS_RE.findall(lines[n])
                if not any(alloy_norm(p) == target for p in found):
                    continue
                hit = True
                if all(alloy_norm(p) == target for p in found):
                    lines[n] = ""
                    continue
                ln = lines[n]
                for p in {p for p in found if alloy_norm(p) == target}:
                    esc = re.escape(p)
                    ln = re.sub(rf"\[`?[^\]]*`?\]\(\s*{esc}\s*\)", "", ln)          # [label](path)
                    ln = re.sub(rf"\[`?{esc}`?\]\([^)]*\)", "", ln)                 # [path](anything)
                    ln = re.sub(rf"`{esc}`", "", ln)
                    ln = re.sub(rf"(?<![A-Za-z0-9_./-]){esc}(?![A-Za-z0-9_])", "", ln)
                ln = re.sub(r"(,\s*){2,}", ", ", ln)
                ln = re.sub(r"\|\s*,\s*", "| ", ln)
                ln = re.sub(r",\s*\|", " |", ln)
                lines[n] = ln
        if not hit:
            raise WriteError(f"Das Modell {target} steht in keinem Alloy-Abschnitt des Plans.")
        return {plan.path: "".join(lines)}


class RiskRows(Writer):
    edit_id = "Risk affects FeaturePlan"
    source = "docs/ModernDocs/plans/*.md"
    key_path = "the plan's risk table"
    can_add = False                 # a new risk is a row with text: "Bearbeiten" → Risiken
    label = "Risiken"

    def files(self, plan: Plan) -> List[str]:
        return [plan.path]

    def present(self, texts: Texts, plan: Plan) -> List[str]:
        return [f"{plan.key}/{rid}" for rid, _ in RISK_ROW_RE.findall(texts.get(plan.path) or "")]

    def check_target(self, target: str) -> None:
        if not re.fullmatch(r"FEAT-[A-Za-z0-9-]+/RISK-\d+", target):
            raise WriteError(f"{target} ist kein Risiko eines Plans (FEAT-…/RISK-nn).")

    def add(self, texts: Texts, plan: Plan, target: str, note: str) -> Dict[str, str]:
        raise WriteError("Ein neues Risiko ist eine Zeile mit Text: über „Bearbeiten“ → Risiken.")

    def remove(self, texts: Texts, plan: Plan, target: str) -> Dict[str, str]:
        owner, _, rid = target.partition("/")
        if owner != plan.key:
            raise WriteError(f"{target} gehört zu {owner}, nicht zu {plan.key}.")
        text = _plan_text(texts, plan)
        drop = [n for n, ln in enumerate(text.splitlines()) if (m := RISK_ROW_RE.match(ln)) and m.group(1) == rid]
        if not drop:
            raise WriteError(f"{rid} steht in keiner Risikotabelle des Plans.")
        return {plan.path: _drop_lines(text, drop)}


WRITERS: Dict[str, Writer] = {w.edit_id: w for w in [
    YamlEdges("FeaturePlan serves Capability", PLAN_LINKS, "serves", ("Capability",), instance_is="from",
              source="client-zero/ontology/plan-links.yaml", key_path="edges[relation=serves]", label="Fähigkeit"),
    YamlEdges("FeaturePlan motivated_by Goal", PLAN_LINKS, "motivated_by", ("Goal",), instance_is="from",
              source="client-zero/ontology/plan-links.yaml", key_path="edges[relation=motivated_by]", label="Ziel"),
    YamlEdges("FeaturePlan justified_by Hypothesis", PLAN_LINKS, "justified_by", ("Hypothesis",), instance_is="from",
              source="client-zero/ontology/plan-links.yaml", key_path="edges[relation=justified_by]", label="Hypothese"),
    YamlEdges("PreMortem examines FeaturePlan", SAMPLE, "examines", ("PreMortem",), instance_is="to",
              source="client-zero/ontology/sample.yaml", key_path="edges[relation=examines]", label="Pre-Mortem"),
    YamlEdges("Risk mitigated_by FeaturePlan", SAMPLE, "mitigated_by", ("Risk",), instance_is="to",
              source="client-zero/ontology/sample.yaml", key_path="edges[relation=mitigated_by]",
              label="Risiko (gemindert durch)"),
    AffectedRow(), GoldenInvariants(), ContractDeltas(), AlloySection(), RiskRows(),
]}


def writer_for(edit_id: str) -> Tuple[Optional[Writer], Optional[str]]:
    """(writer, None) when the Platform can write this id where the edit map says, else (None, why)."""
    w = WRITERS.get(edit_id)
    e = edit_map.entry(edit_id)
    if e is not None and not e.get("editable"):
        return None, f"Nicht in der Plattform änderbar ({e.get('reason')}). Quelle: {e.get('fix_at')}"
    if w is None:
        if e is None:
            return None, f"„{edit_id}“ steht nicht in der Edit-Map; KnowledgeBase sagt nicht, wo es geändert wird."
        return None, f"Die Plattform schreibt {e.get('source')} · {e.get('key_path')} noch nicht."
    problem = w.routing_problem()
    return (None, problem) if problem else (w, None)


def apply(writer: Writer, texts: Texts, plan: Plan, kind: str, target: str, note: str) -> Dict[str, str]:
    """Apply one add or remove; refuse a result the loader would not read as intended."""
    target = writer.normalise(target)
    writer.check_target(target)
    before = writer.present(texts, plan)
    if kind == "add":
        if target in before:
            raise WriteError(f"{target} ist bereits eingetragen.")
        changed = writer.add(texts, plan, target, note)
    elif kind == "remove":
        if target not in before:
            raise WriteError(f"{target} ist nicht eingetragen (in {', '.join(writer.files(plan))}).")
        changed = writer.remove(texts, plan, target)
    else:
        raise WriteError(f"Unbekannte Art {kind!r}.")
    after = writer.present({**texts, **changed}, plan)
    expected = set(before) | {target} if kind == "add" else set(before) - {target}
    if set(after) != expected:
        lost, extra = sorted(expected - set(after)), sorted(set(after) - expected)
        raise WriteError(f"Die Änderung an {', '.join(changed)} würde der Loader nicht so lesen ({kind} {target}"
                         + (f"; verloren: {', '.join(lost)}" if lost else "") + (f"; zusätzlich: {', '.join(extra)}" if extra else "")
                         + "). Bitte in der Datei von Hand ändern.")
    return changed


# -- plan fields (Bearbeiten) ---------------------------------------------------------------

#: form field -> (edit-map id that classifies it, how the plan file holds it)
FIELD_IDS: Dict[str, Optional[str]] = {
    "title": "name",
    "description": "description",
    "owner": "FeaturePlan.owner",
    "risks": "Risk affects FeaturePlan",
    "requirements": "FeaturePlan keeps ComponentContract",
    "sources": None,                # ## References has no edit-map entry
    "target_date": None,            # the template has no target date
}
#: key fields become link operations, so a change set stays a list of single changes
LINK_FIELDS: Dict[str, Tuple[str, ...]] = {
    "capability": ("FeaturePlan serves Capability",),
    "goal": ("FeaturePlan motivated_by Goal", "FeaturePlan justified_by Hypothesis"),
    "affected_components": ("FeaturePlan affects Component",),
}
FIELD_UNROUTED = {
    "sources": "„## References“ hat keinen Eintrag in der Edit-Map und keine Änderungsklasse (KnowledgeBase).",
    "target_date": "Die Plan-Vorlage hat kein Zieldatum; **Date** ist das Erstellungsdatum.",
}


def apply_field(texts: Texts, plan: Plan, fld: str, value: Any) -> Dict[str, str]:
    """A plan field written into the plan file and, where it mirrors it, the .ai.yaml."""
    if fld in FIELD_UNROUTED:
        raise WriteError(FIELD_UNROUTED[fld])
    if fld not in FIELD_IDS:
        raise WriteError(f"Unbekanntes Feld {fld!r}.")
    text = _plan_text(texts, plan)
    out = {plan.path: plan_file.apply(text, {fld: value}).text}
    side = texts.get(plan.sidecar)
    if side:
        new = plan_file.apply_ai_yaml(side, {fld: value}).text
        if new != side:
            out[plan.sidecar] = new
    return out


def set_status(texts: Texts, plan: Plan, status: str) -> Dict[str, str]:
    """The rollback: front matter, the Status row and the .ai.yaml feature.status."""
    out = {plan.path: plan_file.apply(_plan_text(texts, plan), {"status": status}).text}
    side = texts.get(plan.sidecar)
    if side:
        new = plan_file.apply_ai_yaml(side, {"status": status}).text
        if new != side:
            out[plan.sidecar] = new
    return out


#: facts the plan file holds and the field writers above write (edit-map source and key path)
FIELD_ROUTES: Dict[str, Tuple[str, str]] = {
    "FeaturePlan.owner": ("docs/ModernDocs/plans/*.md",
                          "| **Author** | row (else the git author who added the file); mapped by ontology/owners.yaml"),
    "FeaturePlan.status": ("docs/ModernDocs/plans/*.md", "front matter `status` (else the | **Status** | row)"),
}


def field_routing_problem(edit_id: str) -> Optional[str]:
    e = edit_map.entry(edit_id)
    want = FIELD_ROUTES.get(edit_id)
    if e is None or want is None:
        return f"„{edit_id}“ steht nicht in der Edit-Map." if e is None else None
    if (e.get("source"), e.get("key_path")) != want:
        return f"Die Edit-Map nennt für „{edit_id}“ jetzt {e.get('source')} · {e.get('key_path')}."
    return None


def unroutable_report() -> List[Dict[str, str]]:
    """Every editable edit-map entry the Platform cannot write, and every writer the edit map no longer matches."""
    out = []
    for eid, e in sorted(edit_map.items().items()):
        if not e.get("editable") or e["kind"] == "type":
            continue
        if eid in FIELD_ROUTES:
            problem = field_routing_problem(eid)
            if problem:
                out.append({"id": eid, "why": problem})
            continue
        w, why = writer_for(eid)
        if w is None:
            out.append({"id": eid, "why": why or ""})
    return out

