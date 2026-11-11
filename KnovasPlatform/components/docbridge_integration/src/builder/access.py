"""The access window: the tenant's access-group tree, its limits, impact and explanations (build plan step B14).

The Brain holds the groups and the folder rules (/secured/access_groups, the folder-rule service) and decides
who may read what; the Platform holds who is in which group (user_access_groups) and asserts it to the Brain
with every request. A group sees what is assigned to it and to every group below it: a partner sees the
documents of the mandate teams under the partner group, a team member only their own team's.

This module reasons over the tree the Brain returns, for the screen only:

- the engine's limits before a change is sent: one parent, no cycles, depth below 32, unique names, the
  administrator group never moved or deleted (KnowledgeBase services/rbac/group_tree.py);
- the impact of a structural change before it is confirmed: who gains or loses sight of how many items
  (KnowledgeBase's delete pre-flight, generalised to create, move and delete);
- a person's effective access (their groups and everything below them), as a highlighted subtree;
- the path that explains an answer of the Brain ("sees it through group X, which is above Y, to which the
  folder rule /Mandate/Meier assigns it"). The answer itself always comes from the Brain (document_readable
  for that person); the path never overrides it.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional

#: KnowledgeBase services/rbac/models.py.
MAX_DEPTH = 32
NAME_MAX_LEN = 128


class AccessError(ValueError):
    """A change the engine would refuse, or one the Brain refused; the message is for the page."""

    def __init__(self, message: str, status: int = 400, code: str = ""):
        super().__init__(message)
        self.message, self.status, self.code = message, status, code


@dataclass
class Tree:
    """The tenant's groups as GET /secured/access_groups returns them, flattened (knovas_client)."""

    groups: dict[str, dict]

    @classmethod
    def of(cls, flat: Iterable[Mapping[str, Any]]) -> "Tree":
        return cls({str(g["group_id"]): dict(g) for g in flat if g.get("group_id")})

    def parent(self, gid: str) -> Optional[str]:
        p = self.groups.get(gid, {}).get("parent_id")
        return str(p) if p else None

    def children(self, gid: Optional[str]) -> list[str]:
        out = [k for k in self.groups if self.parent(k) == gid]
        return sorted(out, key=lambda k: str(self.groups[k].get("name", "")).lower())

    def roots(self) -> list[str]:
        # The administrator group first: the tree hangs from it.
        found = [k for k in self.groups if not self.parent(k) or self.parent(k) not in self.groups]
        return sorted(found, key=lambda k: (not self.groups[k].get("is_admin"), str(self.groups[k].get("name", "")).lower()))

    def ancestors(self, gid: str) -> list[str]:
        """Above the group, nearest first."""
        out, seen = [], {gid}
        current = self.parent(gid)
        while current and current in self.groups and current not in seen:
            out.append(current)
            seen.add(current)
            current = self.parent(current)
        return out

    def descendants(self, gid: str) -> list[str]:
        out, stack = [], list(self.children(gid))
        while stack:
            g = stack.pop(0)
            if g in out:
                continue
            out.append(g)
            stack.extend(self.children(g))
        return out

    def depth(self, gid: str) -> int:
        return len(self.ancestors(gid))

    def height(self, gid: str) -> int:
        """How many levels the subtree under the group adds."""
        kids = self.children(gid)
        return 1 + max((self.height(k) for k in kids), default=0) if kids else 0

    def name(self, gid: Optional[str]) -> str:
        return str(self.groups.get(gid or "", {}).get("name") or gid or "–")

    def is_admin(self, gid: str) -> bool:
        return bool(self.groups.get(gid, {}).get("is_admin"))

    def by_name(self, name: str) -> Optional[str]:
        wanted = str(name).strip().lower()
        return next((k for k, g in self.groups.items() if str(g.get("name", "")).strip().lower() == wanted), None)

    def nested(self, counts: Mapping[str, Mapping[str, int]], highlight: Iterable[str] = ()) -> list[dict]:
        lit = set(highlight)

        def node(gid: str) -> dict:
            g = self.groups[gid]
            return {"id": gid, "name": g.get("name"), "is_admin": bool(g.get("is_admin")), "depth": self.depth(gid),
                    "counts": dict(counts.get(gid) or {}), "lit": gid in lit,
                    "children": [node(k) for k in self.children(gid)]}

        return [node(r) for r in self.roots()]


# ------------------------------------------------------------------ the engine's limits

def _name(name: Any) -> str:
    text = str(name or "").strip()
    if not 1 <= len(text) <= NAME_MAX_LEN:
        raise AccessError(f"Ein Gruppenname hat 1 bis {NAME_MAX_LEN} Zeichen.")
    return text


def check_create(tree: Tree, name: Any, parent: Optional[str]) -> str:
    text = _name(name)
    if tree.by_name(text):
        raise AccessError(f"Eine Gruppe «{text}» gibt es schon.", 409, "duplicate_name")
    if parent is not None:
        if parent not in tree.groups:
            raise AccessError("Die Elterngruppe gibt es nicht.", 404, "unknown_group")
        if tree.depth(parent) + 1 >= MAX_DEPTH:
            raise AccessError(f"Tiefer als {MAX_DEPTH} Ebenen geht der Baum nicht.", 400, "depth")
    return text


def check_rename(tree: Tree, gid: str, name: Any) -> str:
    if gid not in tree.groups:
        raise AccessError("Diese Gruppe gibt es nicht.", 404, "unknown_group")
    text = _name(name)
    other = tree.by_name(text)
    if other and other != gid:
        raise AccessError(f"Eine Gruppe «{text}» gibt es schon.", 409, "duplicate_name")
    return text


def check_move(tree: Tree, gid: str, new_parent: Optional[str]) -> None:
    if gid not in tree.groups:
        raise AccessError("Diese Gruppe gibt es nicht.", 404, "unknown_group")
    if tree.is_admin(gid):
        raise AccessError("Die Administratorgruppe bleibt, wo sie ist.", 409, "admin_group_protected")
    if new_parent is None:
        raise AccessError("Jede Gruppe hängt unter einer anderen; nur die Administratorgruppe ist oben.", 400, "root")
    if new_parent not in tree.groups:
        raise AccessError("Die neue Elterngruppe gibt es nicht.", 404, "unknown_group")
    if new_parent == gid or new_parent in tree.descendants(gid):
        raise AccessError("Eine Gruppe kann nicht unter sich selbst oder eine ihrer Untergruppen.", 400, "cycle")
    if new_parent == tree.parent(gid):
        raise AccessError("Die Gruppe hängt schon dort.", 400, "same_parent")
    if tree.depth(new_parent) + 1 + tree.height(gid) >= MAX_DEPTH:
        raise AccessError(f"Tiefer als {MAX_DEPTH} Ebenen geht der Baum nicht.", 400, "depth")


def check_delete(tree: Tree, gid: str, assigned: Mapping[str, int]) -> None:
    """KnowledgeBase's delete pre-flight: admin group, children, anything still assigned (GroupInUse)."""
    if gid not in tree.groups:
        raise AccessError("Diese Gruppe gibt es nicht.", 404, "unknown_group")
    if tree.is_admin(gid):
        raise AccessError("Die Administratorgruppe lässt sich nie löschen.", 409, "admin_group_protected")
    if tree.children(gid):
        raise AccessError("Die Gruppe hat Untergruppen; erst diese verschieben oder löschen.", 409, "group_not_empty")
    used = {k: v for k, v in assigned.items() if v}
    if used:
        what = ", ".join(f"{v} {LABELS.get(k, k)}" for k, v in used.items())
        raise AccessError(f"Der Gruppe ist noch etwas zugewiesen ({what}); erst die Zuweisungen lösen.", 409, "group_in_use")


LABELS = {"documents": "Dokumente", "folder_rules": "Ordnerregeln", "node_types": "Knotentypen", "members": "Mitglieder"}


# ------------------------------------------------------------------ impact

def readers(tree: Tree, gid: str, members: Mapping[str, Iterable[str]], *, parent_of: Optional[Mapping[str, Optional[str]]] = None) -> set[str]:
    """Who sees what is assigned to ``gid``: its members and the members of every group above it."""
    chain, seen = [gid], {gid}
    current = (parent_of or {}).get(gid, tree.parent(gid)) if parent_of is not None else tree.parent(gid)
    while current and current not in seen:
        chain.append(current)
        seen.add(current)
        current = (parent_of or {}).get(current, tree.parent(current)) if parent_of is not None else tree.parent(current)
    return {u for g in chain for u in members.get(g, ())}


def impact(tree: Tree, change: Mapping[str, Any], members: Mapping[str, Iterable[str]],
           items: Mapping[str, int]) -> dict:
    """Who gains or loses sight of how many items through a structural change, before it is made.

    ``change``: {"op": "move", "group", "parent"} or {"op": "delete", "group"} or {"op": "create", "parent"}.
    ``items``: per group, how many things are assigned to it (documents, folder rules, node types).
    """
    op = change.get("op")
    gains: dict[str, int] = defaultdict(int)
    losses: dict[str, int] = defaultdict(int)
    if op == "move":
        gid, new_parent = str(change["group"]), str(change["parent"])
        moved = [gid] + tree.descendants(gid)
        after = {k: tree.parent(k) for k in tree.groups}
        after[gid] = new_parent
        for g in moved:
            n = int(items.get(g) or 0)
            if not n:
                continue
            before_r = readers(tree, g, members)
            after_r = readers(tree, g, members, parent_of=after)
            for u in after_r - before_r:
                gains[u] += n
            for u in before_r - after_r:
                losses[u] += n
    elif op == "delete":
        gid = str(change["group"])
        # Only an empty group can go: its members lose nothing they see through it, yet they lose the group.
        for u in members.get(gid, ()):
            losses[u] += 0
    return {"gains": dict(gains), "losses": dict(losses),
            "items": sum(int(items.get(g) or 0) for g in ([str(change["group"])] + tree.descendants(str(change["group"]))
                                                         if change.get("group") else [])),
            "people_gaining": len(gains), "people_losing": len([u for u, n in losses.items() if n])}


# ------------------------------------------------------------------ effective access and explanations

def effective(tree: Tree, user_groups: Iterable[str]) -> list[str]:
    """The groups whose items a person sees: their own and everything below them."""
    out: list[str] = []
    for g in user_groups:
        if g in tree.groups:
            for k in [g] + tree.descendants(g):
                if k not in out:
                    out.append(k)
    return out


def explain(tree: Tree, user_name: str, user_groups: Iterable[str], acl_groups: Iterable[str], *,
            governor: Optional[str], readable: Optional[bool], item: str) -> dict:
    """Why a person sees an item, or why not -- the path through the tree. ``readable`` is the Brain's answer."""
    mine = [g for g in user_groups if g in tree.groups]
    acl = [g for g in acl_groups if g in tree.groups]
    via = governor or "eine eigene Zuweisung"
    if not acl:
        text = (f"{user_name} sieht {item}: es ist keiner Gruppe zugewiesen (uneingeschränkt)."
                if readable is not False else f"{user_name} sieht {item} nicht, obwohl es keiner Gruppe zugewiesen ist.")
        return {"readable": readable, "path": [], "text": text}
    for target in acl:
        for g in mine:
            if g == target or g in tree.ancestors(target):
                chain = [g] + [a for a in reversed(tree.ancestors(target)) if a in tree.descendants(g)] + ([target] if g != target else [])
                names = [tree.name(x) for x in chain]
                if len(names) == 1:
                    path = f"über die Gruppe {names[0]}"
                else:
                    path = f"über die Gruppe {names[0]}, die über " + " und ".join(names[1:]) + " steht"
                text = f"{user_name} sieht {item} {path}, der {via} es zuweist."
                if readable is False:
                    text += " Das Brain verneint es trotzdem: der Mandant setzt den Zugriff anders durch (siehe Durchsetzung)."
                return {"readable": readable, "path": chain, "text": text}
    where = ", ".join(tree.name(g) for g in acl)
    own = ", ".join(tree.name(g) for g in mine) or "keiner Gruppe"
    text = (f"{user_name} sieht {item} nicht: {via} weist es {where} zu, und {user_name} gehört zu {own}, "
            f"die weder diese Gruppe sind noch über ihr stehen.")
    if readable:
        text = f"{user_name} sieht {item}, obwohl kein Weg durch den Baum führt (das Brain erlaubt es; Durchsetzung aus?)."
    return {"readable": readable, "path": [], "text": text}
