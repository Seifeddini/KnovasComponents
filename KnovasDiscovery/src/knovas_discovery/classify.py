"""Classification: every probe result and every piece of software the client named, against the catalogue.

One probe result can be several sources (a document store that also holds dictations and scanner output). Of the
entries of one `group` that match the same result only the highest `priority` is kept. A result that matches no
entry is class C "unknown kind", except a result in which the account sees nothing: that is class D, no access.
"""

from __future__ import annotations

from . import effort as eff
from .conditions import all_hold, get_fact, holds, render

PROBE_SECTIONS = (("fileshare", "fileshares"), ("directory", "directories"), ("mssql", "databases"), ("m365", "m365"))
CLASS_ORDER = {"A": 0, "B": 1, "C": 2, "D": 3}
# The client's categories in intake.yaml -> catalogue categories, to see whether the scan found what they named.
INTAKE_CATEGORY = {"practice": "records", "dms": "documents", "mail": "mail", "accounting": "finance",
                   "time": "records", "collaboration": "collaboration", "web-api": "records",
                   "documents": "documents", "records": "records", "finance": "finance", "identity": "identity"}

NO_ACCESS = {
    "key": "no-access", "name": "Nothing the account can read", "category": "other", "class": "D",
    "knovas": {"status": "none", "component": "none", "how": "The scan saw nothing here with the account it was given."},
    "needs_from_client": [], "risks": [],
    "questions": ["Is it right that the account sees nothing here, or was the wrong account or folder given?"],
}
UNKNOWN = {
    "key": "unknown", "name": "Unknown kind: add it to the catalogue", "category": "other", "class": "C",
    "knovas": {"status": "none", "component": "none", "how": "No catalogue entry matches what the scan found."},
    "needs_from_client": [], "risks": [], "questions": ["What is this source, and which software writes it?"],
    "effort_drivers": ["manual"],
}


def is_empty(probe: str, facts: dict) -> bool:
    """The account sees nothing in this result."""
    if probe == "fileshare":
        return not facts.get("files")
    if probe == "mssql":
        return not facts.get("tables")
    if probe == "directory":
        return not any(facts.get(k) for k in ("people_enabled", "people_disabled", "service_accounts", "groups"))
    if probe == "m365":
        return all(not facts.get(k) for k in ("people", "sharepoint_sites", "teams", "licences"))
    return False


def evidence(block: dict, facts: dict) -> list[dict]:
    return [{"fact": c["fact"], "op": c["op"], "value": c.get("value"), "measured": get_fact(facts, c["fact"])}
            for c in block["when"]]


def match(on: str, facts: dict, entries: list[dict]) -> list[tuple[dict, list[dict]]]:
    """Entries with a detect block for `on` whose conditions all hold, with the evidence; then group/priority."""
    found = []
    for e in entries:
        for block in e.get("detect") or []:
            if block.get("on") == on and all_hold(block["when"], facts):
                found.append((e, evidence(block, facts)))
                break
    best: dict[str, int] = {}
    for e, _ in found:
        if "group" in e:
            best[e["group"]] = max(best.get(e["group"], e["priority"]), e["priority"])
    return [(e, ev) for e, ev in found if "group" not in e or e["priority"] == best[e["group"]]]


def summary(probe: str, facts: dict) -> str:
    f = facts
    if probe == "fileshare":
        age = f.get("age") or {}
        if not f.get("files"):
            return (f"no file visible to the account; {f.get('folders', 0):,} folders seen, "
                    f"{f.get('refused_folders', 0)} refuse the account")
        return (f"{f.get('files', 0):,} files ({_bytes(f.get('bytes'))}) in {f.get('folders', 0):,} folders; "
                f"{f.get('connector_readable_files', 0):,} ({f.get('connector_readable_share') or 0:.1%}) in formats the "
                f"Connector reads; newest file {_days(age.get('newest_days'))}, oldest {_days(age.get('oldest_days'))}; "
                f"{f.get('refused_folders', 0)} folders refuse the account")
    if probe == "mssql":
        return (f"{f.get('tables', 0)} tables, {f.get('rows_total', 0):,} rows: {f.get('record_tables', 0)} records, "
                f"{f.get('lookup_tables', 0)} lookup, {f.get('software_tables', 0)} software, "
                f"{f.get('leftover_tables', 0)} leftover; {f.get('foreign_keys', 0)} foreign keys; "
                f"{f.get('version_name') or f.get('version') or 'version unknown'}, {f.get('edition') or 'edition unknown'}, "
                f"{f.get('authentication') or 'unknown'} authentication")
    if probe == "directory":
        return (f"{'Active Directory' if f.get('is_active_directory') else 'LDAP directory'}: "
                f"{f.get('people_enabled', 0)} people enabled, {f.get('people_disabled', 0)} disabled, "
                f"{f.get('service_accounts', 0)} service accounts; {f.get('groups', 0)} groups "
                f"({f.get('groups_empty', 0)} empty), nested {f.get('max_nesting', 0)} deep; "
                f"{f.get('computers', 0)} computers")
    if probe == "m365":
        lic = f.get("licences") or {}
        return (f"{_n(f.get('people'))} people with a licence; {sum(lic.values()):,} licences in use; "
                f"{_n(f.get('sharepoint_sites'))} SharePoint sites ({_bytes(f.get('drives_bytes'))} in drives); "
                f"Exchange Online {'in use' if f.get('exchange_online') else 'not seen'}; {_n(f.get('teams'))} teams")
    return ""


def _n(v) -> str:
    return "?" if v is None else f"{v:,}"


def _days(v) -> str:
    if v is None:
        return "?"
    if v >= 730:
        return f"{v // 365} years old"
    return f"{v} days old"


def _bytes(v) -> str:
    if v is None:
        return "? bytes"
    for unit, size in (("GB", 1e9), ("MB", 1e6), ("kB", 1e3)):
        if v >= size:
            return f"{v / size:.1f} {unit}"
    return f"{v} bytes"


def _source(entry: dict, origin: str, probe: str, result_key: str, facts: dict | None, ev: list[dict],
            text: str) -> dict:
    k = entry.get("knovas") or {}
    return {
        "id": f"{entry['key']}@{result_key}",
        "origin": origin,
        "probe": probe,
        "result_key": result_key,
        "entry": entry["key"],
        "name": entry["name"],
        "category": entry.get("category"),
        "class": entry["class"],
        "class_from": "catalogue" if entry["key"] not in ("unknown", "no-access") else "rule",
        "knovas": {"status": k.get("status"), "component": k.get("component"), "how": k.get("how")},
        "summary": text,
        "evidence": ev,
        "checks": [],
        "needs_from_client": list(entry.get("needs_from_client") or []),
        "risks": list(entry.get("risks") or []),
        "questions": list(entry.get("questions") or []),
        "effort_drivers": list(entry.get("effort_drivers") or []),
        "_facts": facts,
        "notes": [],
        "effort": None,
        "price": None,
    }


def classify(findings: dict, catalogue: dict, pricing: dict, review: dict | None = None,
             software: list[dict] | None = None) -> dict:
    entries = catalogue.get("entries") or []
    checks = catalogue.get("checks") or []
    review = review or {}
    sources: list[dict] = []
    not_ok: list[dict] = []
    probes = findings.get("probes") or {}

    env = probes.get("environment")
    if env and env.get("status") != "ok":
        not_ok.append({"probe": "environment", "key": env["key"], "status": env["status"], "error": env.get("error")})

    for probe, section in PROBE_SECTIONS:
        for r in probes.get(section) or []:
            if r["status"] not in ("ok", "partial"):
                not_ok.append({"probe": probe, "key": r["key"], "status": r["status"], "error": r.get("error")})
                continue
            if r["status"] == "partial":
                not_ok.append({"probe": probe, "key": r["key"], "status": r["status"], "error": r.get("error")})
            facts = r.get("facts") or {}
            found = match(probe, facts, entries)
            if not found:
                found = [(NO_ACCESS if is_empty(probe, facts) else UNKNOWN, [])]
            text = summary(probe, facts)
            made = [_source(e, "scan", probe, r["key"], facts, ev, text) for e, ev in found]
            for s in made:
                if s["entry"] in ("no-access", "unknown"):  # say where, so the question stands on its own in a list
                    s["questions"] = [f"`{r['key']}`: {q[0].lower()}{q[1:]}" for q in s["questions"]]
            # Checks are risks of the main source of the result: the one of a group (the kind of share, of
            # database), else the first in catalogue order.
            main = next((s for s, (e, _) in zip(made, found) if "group" in e), made[0])
            for c in checks:
                if c.get("on") == probe and all_hold(c["when"], facts):
                    main["checks"].append({"key": c["key"], "text": render(c["text"], facts)})
            sources.extend(made)

    # What the client named: confirmed by the scan, or a source of its own ("named by the client, not scanned").
    named = []
    scanned_keys = {s["entry"] for s in sources}
    scanned_categories = {s["category"] for s in sources if s["entry"] not in ("unknown", "no-access")}
    for i, item in enumerate(software if software is not None else findings.get("software") or []):
        facts = {"category": item.get("category"), "product": item.get("product")}
        found = match("intake", facts, entries)
        hit = next((e for e, _ in found if e["key"] in scanned_keys), None)
        if hit is not None:
            named.append({**facts, "result": "confirmed by the scan", "entry": hit["key"]})
            continue
        if not found and INTAKE_CATEGORY.get(facts["category"]) in scanned_categories:
            match_entry = next(s["entry"] for s in sources if s["category"] == INTAKE_CATEGORY[facts["category"]])
            named.append({**facts, "result": "confirmed by the scan", "entry": match_entry})
            continue
        key = f"intake:{i + 1}:{facts['category']}:{facts['product']}"
        if not found:
            found = [(UNKNOWN, [])]
        for e, ev in found:
            s = _source(e, "intake", "intake", key, facts, ev,
                        f"named by the client, not scanned (category {facts['category']}, product {facts['product']})")
            sources.append(s)
            named.append({**facts, "result": "named by the client, not scanned", "entry": e["key"]})

    # The consultant's review: class overrides, added effort points, notes.
    rsources = review.get("sources") or {}
    ids = {s["id"] for s in sources}
    unknown_ids = sorted(k for k in rsources if k not in ids)
    for s in sources:
        r = rsources.get(s["id"]) or {}
        if r.get("class") in CLASS_ORDER and r["class"] != s["class"]:
            s["notes"].append(f"Class set to {r['class']} in review (catalogue: {s['class']})"
                              + (f": {r['reason']}" if r.get("reason") else "."))
            s["class"], s["class_from"] = r["class"], "review"
        s["notes"].extend(str(n) for n in r.get("notes") or [])
        if s["class"] == "C":
            computable = s["probe"] == "mssql" and s["entry"] not in ("unknown",)
            s["effort"] = eff.compute(s["effort_drivers"], s["_facts"], r.get("effort") or [], pricing, computable)
        s["price"] = eff.price(s["class"], (s["effort"] or {}).get("effort_class"), pricing)
        del s["_facts"]
    sources.sort(key=lambda s: (CLASS_ORDER[s["class"]], s["entry"], s["result_key"]))

    def dedup(items):
        out = []
        for it in items:
            if it not in out:
                out.append(it)
        return out

    needs = dedup(n for s in sources if s["class"] != "D" for n in s["needs_from_client"])
    questions = dedup(q for s in sources for q in s["questions"])
    for n in not_ok:
        if n["status"] == "failed":
            questions.append(f"The {n['probe']} probe of {n['key']} failed ({n['error']}): fix the cause and scan "
                             f"again, or answer what it would have measured by hand.")
    for s in sources:
        if s["effort"] and s["effort"]["points"] is None:
            questions.append(f"Effort of {s['id']}: set by the consultant in review.yaml.")
    counts = {c: sum(1 for s in sources if s["class"] == c) for c in CLASS_ORDER}
    not_targeted = [probe for probe, section in PROBE_SECTIONS if not probes.get(section)]
    names = {"fileshare": "file shares", "directory": "a directory", "mssql": "databases", "m365": "Microsoft 365"}
    for probe in not_targeted:
        questions.append(f"The intake names no target for {names[probe]}: does the client have one?")
    return {
        "tool_version": findings.get("tool_version"),
        "scanned_at": findings.get("scanned_at"),
        "client": findings.get("client"),
        "counts": counts,
        "sources": sources,
        "named_by_client": named,
        "environment": (env or {}).get("facts") if env and env.get("status") in ("ok", "partial") else None,
        "probes_not_ok": not_ok,
        "not_targeted": not_targeted,
        "needs_from_client": needs,
        "open_questions": dedup(questions),
        "review": {"used": bool(review), "notes": [str(n) for n in review.get("notes") or []],
                   "unknown_ids": unknown_ids},
    }


__all__ = ["classify", "match", "is_empty", "holds"]
