"""Read and change the fields a feature plan file holds (Step 7).

The field list and layout follow KnowledgeBase's templates
(docs/ModernDocs/_templates/feature-plan.template.md and feature-plan.ai.yaml)
and what its client-zero loader reads (scripts/client_zero/providers/
moderndocs.py): the H1 title, the front-matter ``status``, the header-table
rows **Author**, **Status** and **Affected L2**, the ``## Problem`` and
``## References`` sections, the ``## Component contract deltas`` section and
``| RISK-nn |`` rows. Capability and goal are not in the plan file at all:
they are ``serves`` / ``motivated_by`` / ``justified_by`` edges in
ontology/plan-links.yaml, and are changed there.

Edits are line edits on the original text, never a re-serialisation: the
file keeps its comments, order and wording outside the changed field. A field
whose anchor the file does not have is reported as absent; writing it inserts
it where the template puts it, and the change says so.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

PLAN_LINKS_PATH = "docs/ModernDocs/client-zero/ontology/plan-links.yaml"

H1_RE = re.compile(r"^# (?:Feature(?: Plan)?:\s*)?(.+?)\s*$", re.M)
H1_PREFIX_RE = re.compile(r"^(# (?:Feature(?: Plan)?:\s*)?)(.+?)\s*$", re.M)
FM_RE = re.compile(r"\A---\n(.*?)\n---\n", re.S)
FM_STATUS_RE = re.compile(r"^status:\s*([A-Za-z_-]+)\s*$", re.M)
ROW_RE = {name: re.compile(rf"^(\|\s*\*\*{re.escape(name)}\*\*\s*\|\s*)([^|\n]*?)(\s*\|)\s*$", re.M)
          for name in ("Author", "Status", "Affected L2", "Date")}
L2_RE = re.compile(r"\bL2-[A-Z0-9-]+\b")
RISK_ROW_RE = re.compile(r"^\|\s*\**(RISK-\d+)\**\s*\|(.*)\|\s*$", re.M)
FEAT_RE = re.compile(r"\bFEAT-\d{4}-\d{2}-\d{2}-[a-z0-9-]+\b")

SECTIONS = {"description": "Problem", "sources": "References", "requirements": "Component contract deltas"}


def section_span(text: str, heading: str) -> Optional[Tuple[int, int]]:
    """(start, end) of the body under ``## heading`` up to the next ``## `` or EOF."""
    m = re.search(rf"^## {re.escape(heading)}\s*$", text, re.M)
    if not m:
        return None
    start = m.end() + 1 if m.end() < len(text) and text[m.end()] == "\n" else m.end()
    nxt = re.search(r"^## ", text[start:], re.M)
    end = start + nxt.start() if nxt else len(text)
    return start, end


def _section(text: str, heading: str) -> Optional[str]:
    span = section_span(text, heading)
    return None if span is None else text[span[0]:span[1]].strip("\n")


def parse_risks(text: str) -> List[Dict[str, str]]:
    out, seen = [], set()
    for rid, rest in RISK_ROW_RE.findall(text):
        if rid in seen:
            continue
        seen.add(rid)
        cells = [re.sub(r"\*+", "", c).strip() for c in rest.strip().split("|")]
        out.append({"id": rid, "risk": cells[0] if cells else "",
                    "mitigation": cells[-1] if len(cells) >= 2 else ""})
    return out


@dataclass
class PlanFields:
    """What the plan file holds, field by field; None = the file has no such field."""
    key: str
    title: Optional[str]
    status: Optional[str]
    owner: Optional[str]
    affected_components: Optional[List[str]]
    description: Optional[str]
    sources: Optional[str]
    requirements: Optional[str]
    risks: List[Dict[str, str]] = field(default_factory=list)
    capability: List[str] = field(default_factory=list)
    goal: List[str] = field(default_factory=list)
    hypothesis: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


def read(md: str, plan_key: str, plan_links: str = "") -> PlanFields:
    status = None
    fm = FM_RE.match(md)
    if fm:
        m = FM_STATUS_RE.search(fm.group(1))
        status = m.group(1) if m else None
    if status is None:
        m = ROW_RE["Status"].search(md)
        status = m.group(2).strip().split()[0].strip("*,;(").lower() if m and m.group(2).strip() else None
    h1 = H1_RE.search(md)
    owner = ROW_RE["Author"].search(md)
    affected = ROW_RE["Affected L2"].search(md)
    links = links_of(plan_links, plan_key)
    return PlanFields(
        key=plan_key,
        title=h1.group(1).strip() if h1 else None,
        status=status,
        owner=owner.group(2).strip() if owner else None,
        affected_components=L2_RE.findall(affected.group(2)) if affected else None,
        description=_section(md, SECTIONS["description"]),
        sources=_section(md, SECTIONS["sources"]),
        requirements=_section(md, SECTIONS["requirements"]),
        risks=parse_risks(md),
        capability=links.get("serves", []), goal=links.get("motivated_by", []),
        hypothesis=links.get("justified_by", []))


# -- plan-links.yaml (capability and goal) ---------------------------------------

LINK_RE = re.compile(r"^\s*-\s*\{\s*from:\s*([^,}]+?)\s*,\s*relation:\s*([a-z_]+)\s*,\s*to:\s*([^,}]+?)\s*\}\s*$", re.M)


def links_of(plan_links: str, plan_key: str) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for frm, rel, to in LINK_RE.findall(plan_links or ""):
        if frm == plan_key:
            out.setdefault(rel, []).append(to)
    return out


def set_links(plan_links: str, plan_key: str, relation: str, targets: List[str], note: str) -> str:
    """Make this plan's edges of one relation exactly ``targets``.

    Lines that stay keep their place; dropped targets lose their line; new ones
    are appended under a comment. Other plans' lines stay byte for byte.
    """
    lines = plan_links.splitlines(keepends=True)
    present: List[str] = []
    kept = []
    for ln in lines:
        m = LINK_RE.match(ln.rstrip("\n"))
        if m and m.group(1) == plan_key and m.group(2) == relation:
            if m.group(3) in targets and m.group(3) not in present:
                present.append(m.group(3))
                kept.append(ln)
            continue
        kept.append(ln)
    text = "".join(kept)
    if text and not text.endswith("\n"):
        text += "\n"
    new = [t for t in targets if t not in present]
    if new:
        text += f"  # {note}\n" + "".join(f"  - {{from: {plan_key}, relation: {relation}, to: {t}}}\n" for t in new)
    return text


# -- writing the plan file ------------------------------------------------------------

class Edit:
    """The changed text plus a note per field on how it was changed."""

    def __init__(self, text: str):
        self.text = text
        self.notes: Dict[str, str] = {}


def _header_table_end(text: str) -> Optional[int]:
    """End offset of the first metadata table after the H1, if there is one."""
    h1 = H1_RE.search(text)
    if not h1:
        return None
    m = re.search(r"(?:^\|.*\|\s*\n)+", text[h1.end():], re.M)
    if not m or text[h1.end():h1.end() + m.start()].strip():
        return None
    return h1.end() + m.end()


def _set_row(e: Edit, name: str, value: str, fld: str) -> None:
    rx = ROW_RE[name]
    if rx.search(e.text):
        e.text = rx.sub(lambda m: f"{m.group(1)}{value}{m.group(3)}", e.text, count=1)
        e.notes[fld] = f"Zeile **{name}** geändert"
        return
    end = _header_table_end(e.text)
    row = f"| **{name}** | {value} |\n"
    if end is not None:
        e.text = e.text[:end] + row + e.text[end:]
        e.notes[fld] = f"Zeile **{name}** in die Kopf-Tabelle eingefügt (Vorlage)"
    else:
        h1 = H1_RE.search(e.text)
        at = h1.end() + 1 if h1 else 0
        e.text = e.text[:at] + f"\n| Field | Value |\n|-------|-------|\n{row}" + e.text[at:]
        e.notes[fld] = f"Kopf-Tabelle mit **{name}** angelegt (Vorlage)"


def _set_section(e: Edit, heading: str, body: str, fld: str, *, after: Optional[str] = None) -> None:
    body = body.strip("\n") + "\n"
    span = section_span(e.text, heading)
    if span:
        start, end = span
        trailing = "\n" if end < len(e.text) else ""
        e.text = e.text[:start] + body + trailing + e.text[end:]
        e.notes[fld] = f"Abschnitt „{heading}“ ersetzt"
        return
    block = f"\n## {heading}\n\n{body}"
    anchor = _header_table_end(e.text) if after == "header" else None
    if anchor is not None:
        e.text = e.text[:anchor] + block + e.text[anchor:]
    else:
        e.text = e.text.rstrip("\n") + "\n" + block
    e.notes[fld] = f"Abschnitt „{heading}“ neu angelegt (Vorlage)"


def set_status(e: Edit, status: str) -> None:
    fm = FM_RE.match(e.text)
    if fm and FM_STATUS_RE.search(fm.group(1)):
        new_fm = FM_STATUS_RE.sub(f"status: {status}", fm.group(1), count=1)
        e.text = f"---\n{new_fm}\n---\n" + e.text[fm.end():]
        e.notes["status"] = "status im Front Matter gesetzt"
    elif fm:
        e.text = f"---\n{fm.group(1)}\nstatus: {status}\n---\n" + e.text[fm.end():]
        e.notes["status"] = "status ins Front Matter eingefügt"
    else:
        e.text = f"---\nstatus: {status}\n---\n\n" + e.text
        e.notes["status"] = "Front Matter mit status angelegt"
    if ROW_RE["Status"].search(e.text):
        e.text = ROW_RE["Status"].sub(lambda m: f"{m.group(1)}{status}{m.group(3)}", e.text, count=1)


def _set_risks(e: Edit, risks: List[Dict[str, str]]) -> None:
    """Rewrite existing RISK rows in place; append new ones as a template risk table."""
    existing = {r["id"] for r in parse_risks(e.text)}
    wanted = {r["id"]: r for r in risks if r.get("id")}
    for rid in existing:
        rx = re.compile(rf"^\|\s*\**{re.escape(rid)}\**\s*\|.*\|\s*$\n?", re.M)
        if rid in wanted:
            r = wanted[rid]
            e.text = rx.sub(f"| {rid} | {r.get('risk', '').strip()} | {r.get('mitigation', '').strip()} |\n", e.text, count=1)
        else:
            e.text = rx.sub("", e.text, count=1)
    new = [r for rid, r in wanted.items() if rid not in existing]
    if new:
        rows = "".join(f"| {r['id']} | {r.get('risk', '').strip()} | {r.get('mitigation', '').strip()} |\n" for r in new)
        span = section_span(e.text, "Risks")
        if span:
            e.text = e.text[:span[1]].rstrip("\n") + "\n" + rows + ("\n" if span[1] < len(e.text) else "") + e.text[span[1]:]
        else:
            e.text = e.text.rstrip("\n") + "\n\n## Risks\n\n| ID | Risk | Mitigation |\n|----|------|------------|\n" + rows
    e.notes["risks"] = "RISK-Zeilen geändert"


def apply(md: str, changes: Dict[str, Any]) -> Edit:
    """Apply field changes to the plan markdown. Capability/goal are not here (plan-links)."""
    e = Edit(md)
    if "title" in changes:
        if H1_PREFIX_RE.search(e.text):
            e.text = H1_PREFIX_RE.sub(lambda m: m.group(1) + str(changes["title"]).strip(), e.text, count=1)
            e.notes["title"] = "Überschrift geändert"
    if "owner" in changes:
        _set_row(e, "Author", str(changes["owner"]).strip(), "owner")
    if "affected_components" in changes:
        _set_row(e, "Affected L2", ", ".join(changes["affected_components"]), "affected_components")
    if "description" in changes:
        _set_section(e, "Problem", str(changes["description"]), "description", after="header")
    if "sources" in changes:
        _set_section(e, "References", str(changes["sources"]), "sources")
    if "requirements" in changes:
        _set_section(e, "Component contract deltas", str(changes["requirements"]), "requirements")
    if "risks" in changes:
        _set_risks(e, list(changes["risks"]))
    if "status" in changes:
        set_status(e, str(changes["status"]))
    return e


def apply_ai_yaml(text: str, changes: Dict[str, Any]) -> Edit:
    """The fields the .ai.yaml mirrors: feature.title/status/author, problem.statement, hierarchy.affected_L2."""
    import json

    e = Edit(text)

    def scalar(key_path: str, value: str, fld: str) -> None:
        parent, key = key_path.split(".")
        block = re.search(rf"^{parent}:\s*\n((?:[ \t]+.*\n|\s*\n)*)", e.text, re.M)
        if not block:
            return
        rx = re.compile(rf"^([ \t]+{key}:)[ \t]*(.*)$", re.M)
        inner = block.group(1)
        if not rx.search(inner):
            return
        rendered = value if re.fullmatch(r"[A-Za-z0-9_.-]+", value or "") else json.dumps(value, ensure_ascii=False)
        new_inner = rx.sub(lambda m: f"{m.group(1)} {rendered}", inner, count=1)
        e.text = e.text[:block.start(1)] + new_inner + e.text[block.end(1):]
        e.notes[fld] = f".ai.yaml {key_path}"

    if "title" in changes:
        scalar("feature.title", str(changes["title"]), "title")
    if "status" in changes:
        scalar("feature.status", str(changes["status"]), "status")
    if "owner" in changes:
        scalar("feature.author", str(changes["owner"]), "owner")
    if "description" in changes:
        scalar("problem.statement", " ".join(str(changes["description"]).split()), "description")
    if "affected_components" in changes:
        m = re.search(r"^([ \t]+)affected_L2:\s*\n((?:\1[ \t]+-.*\n)*)", e.text, re.M)
        if m:
            indent = m.group(1) + "  "
            items = "".join(f"{indent}- {c}\n" for c in changes["affected_components"])
            e.text = e.text[:m.start(2)] + items + e.text[m.end(2):]
            e.notes["affected_components"] = ".ai.yaml hierarchy.affected_L2"
    return e
