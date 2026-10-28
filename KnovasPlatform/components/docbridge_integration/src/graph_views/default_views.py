"""Default shared views for the Knovas tenant (Client Zero), Step 6 of the plan.

Each is presentation only: a definition key, a render kind and settings. None
names a node; the person opening a trace picks the plan or component. Seeding
is an admin action (or ``seed`` from a shell), idempotent by name, and every
view it creates is audit-logged like any shared view.
"""
from __future__ import annotations

from typing import Any, Dict, List

KNOVAS: List[Dict[str, Any]] = [
    {"name": "Entwicklungszyklus",
     "description": "Ablauf eines Feature-Plans von der Idee bis zum Release: was jeder Schritt hervorgebracht hat, was fehlt.",
     "source": {"process": "PROC-feature-lifecycle"}, "render_kind": "trace", "settings": {}},
    {"name": "Entwicklungs-Board",
     "description": "Alle Feature-Pläne nach Schritt, die mit den meisten fehlenden Pflicht-Artefakten zuerst.",
     "source": {"process": "PROC-feature-lifecycle"}, "render_kind": "board", "settings": {"sorting": "missing"}},
    {"name": "Management-Sicht",
     "description": "Produktachse mit Status: Produkt, Stufe, Fähigkeit, Software-Bereich, Komponente.",
     "source": {"spine": "SPINE-product"}, "render_kind": "tree", "settings": {}},
    {"name": "Entscheidungen & Risiken",
     "description": "Nur der Kontext: Entscheidungen, Risiken und Ereignisse zu einem Plan oder einer Komponente.",
     "source": {"process": "PROC-feature-lifecycle"}, "render_kind": "trace", "settings": {"sections": ["context"]}},
    {"name": "Markt",
     "description": "Markt-, Vertriebs- und Marketingachse: Segment, Persona, Hypothese, Kampagne, Ergebnis.",
     "source": {"spine": "SPINE-market"}, "render_kind": "tree", "settings": {}},
    {"name": "Software-Dokumentation",
     "description": "Die Software als Dokumentation: System, Software-Bereiche, Komponenten, aus den Seitendefinitionen; "
                    "öffnet die System-Seite.",
     "source": {"spine": "SPINE-product"}, "render_kind": "doc", "settings": {}},
]

SETS = {"knovas": KNOVAS}


def seed(store: Any, admin: Any, which: str = "knovas") -> Dict[str, List[str]]:
    """Create the missing views of a set as shared views. Returns created and kept names."""
    existing = {v["name"].lower() for v in store.list_for(admin) if v["audience"] == "shared"}
    created, kept = [], []
    for view in SETS[which]:
        if view["name"].lower() in existing:
            kept.append(view["name"])
            continue
        store.create(admin, view, audience="shared")
        created.append(view["name"])
    return {"created": created, "kept": kept}
