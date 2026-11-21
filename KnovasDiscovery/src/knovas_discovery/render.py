"""report.md and connection-plan.md from report.json (and findings.json for the numbers). Plain English, tables."""

from __future__ import annotations

CLASS_MEANING = {
    "A": "standard: a released component connects it with configuration only",
    "B": "standard with mapping: a released component connects it; something is mapped for this client",
    "C": "custom: no component reads it; a connector or an export has to be built",
    "D": "not now: we cannot or should not connect it",
}


def _cell(v) -> str:
    s = "" if v is None else str(v)
    return s.replace("|", "\\|").replace("\n", " ")


def table(header: list[str], rows: list[list]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    out += ["| " + " | ".join(_cell(c) for c in r) + " |" for r in rows]
    return out


def _n(v) -> str:
    if v is None:
        return "?"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, int):
        return f"{v:,}"
    if isinstance(v, float):
        return f"{v:,.4g}" if v < 1 else f"{v:,.1f}"
    return str(v)


def _bytes(v) -> str:
    if v is None:
        return "?"
    for unit, size in (("GB", 1e9), ("MB", 1e6), ("kB", 1e3)):
        if v >= size:
            return f"{v / size:,.1f} {unit}"
    return f"{v} bytes"


def _pct(v) -> str:
    return "?" if v is None else f"{v:.1%}"


def _knovas(k: dict) -> str:
    """released (Knovas Connector); none"""
    comp = k.get("component")
    return f"{k.get('status')} ({comp})" if comp and comp != "none" else str(k.get("status"))


def _evidence(ev: list[dict]) -> str:
    return "; ".join(f"`{e['fact']}` {e['op']} {e['value']} (measured: {_n(e['measured']) if not isinstance(e['measured'], (list, dict)) else 'see the numbers below'})"
                     for e in ev)


def _header(report: dict, title: str) -> list[str]:
    c = report.get("client") or {}
    return [
        f"# {title}: {c.get('key')}",
        "",
        f"Made by knovas-discovery {report.get('tool_version')} from the scan of {report.get('scanned_at')}. "
        f"Sector: {c.get('sector') or '?'}; {_n(c.get('people'))} people, {_n(c.get('lawyers'))} lawyers; "
        f"IT provider: {c.get('it_provider') or '?'}.",
        "",
    ]


def effort_lines(s: dict) -> list[str]:
    e = s.get("effort")
    if not e:
        return []
    if e["points"] is None:
        return ["Effort: set by the consultant in review.yaml.", ""]
    rows = [[d["label"], d["measured"], d["rule"], d["points"]] for d in e["drivers"]]
    rows += [["Added in review", r["reason"] or "(no reason given)", "", r["points"]] for r in e["review_points"]]
    rows.append(["**Total**", "", "", f"**{e['points']}** (effort class **{e['effort_class']}**)"])
    head = "Effort, counted from the scan" if e["mode"] == "computed" else "Effort, set in review.yaml"
    return [f"{head} (effort classes: S up to 8 points, M 9 to 16, L 17 to 26, XL above):", ""] + \
        table(["Driver", "Measured", "Points by rule", "Points"], rows) + [""]


# --------------------------------------------------------------------------- report.md
def report_md(report: dict, findings: dict) -> str:
    L = _header(report, "Discovery report")
    L += ["This report holds numbers, patterns, and the names of database tables and columns. It holds no name of a "
          "person, client, file, folder or group: those are in `detail.json`, which stays at the client.", ""]
    L += ["## Summary", ""]
    L += table(["Class", "Meaning", "Sources"], [[c, CLASS_MEANING[c], report["counts"][c]] for c in "ABCD"])
    L += [""]
    sources = report["sources"]
    L += ["## Sources", ""]
    L += table(["#", "Source", "Kind", "Class", "Knovas today"],
               [[i, f"`{s['result_key']}`", s["name"], s["class"], _knovas(s["knovas"])]
                for i, s in enumerate(sources, 1)])
    L += [""]
    for i, s in enumerate(sources, 1):
        L += [f"### {i}. {s['name']}: `{s['result_key']}`", ""]
        origin = "found by the scan" if s["origin"] == "scan" else "named by the client, not scanned"
        L += [f"Class **{s['class']}** ({CLASS_MEANING[s['class']]}), catalogue entry `{s['entry']}`, {origin}.", ""]
        L += [f"Knovas today: {_knovas(s['knovas'])}. {s['knovas']['how'] or ''}".rstrip(), ""]
        if s["summary"]:
            L += [f"What the scan saw: {s['summary']}.", ""]
        if s["evidence"]:
            L += [f"Why this kind: {_evidence(s['evidence'])}.", ""]
        elif s["entry"] == "no-access":
            L += ["Why this kind: no catalogue entry matches, and the account sees nothing here.", ""]
        elif s["entry"] == "unknown":
            L += ["Why this kind: no catalogue entry matches what was found or named.", ""]
        risks = s["risks"] + [c["text"] for c in s["checks"]]
        for title, items in (("Risks", risks), ("What we need from the client", s["needs_from_client"]),
                             ("Questions", s["questions"]), ("Notes", s["notes"])):
            if items:
                L += [f"{title}:", ""] + [f"- {x}" for x in items] + [""]
        L += effort_lines(s)
    L += _numbers(findings)
    named = report.get("named_by_client") or []
    L += ["## Named by the client", ""]
    if named:
        L += table(["Category", "Product", "Result", "Catalogue entry"],
                   [[n["category"], n["product"], n["result"], f"`{n['entry']}`"] for n in named])
    else:
        L += ["The intake names no software."]
    L += [""]
    L += _environment(report)
    L += _not_ok(report)
    if report["review"]["used"]:
        L += ["## Review", ""]
        L += [f"- {n}" for n in report["review"]["notes"]] or ["The review adds no general note."]
        if report["review"]["unknown_ids"]:
            L += [f"- review.yaml names a source this report does not have: `{k}`" for k in report["review"]["unknown_ids"]]
        L += [""]
    return "\n".join(L).rstrip() + "\n"


def _numbers(findings: dict) -> list[str]:
    probes = findings.get("probes") or {}
    L: list[str] = []
    shares = [r for r in probes.get("fileshares") or [] if r["status"] in ("ok", "partial")]
    if shares:
        L += ["## File shares in numbers", ""]
        keys = [f"`{r['key']}`" for r in shares]
        rows_def = [
            ("Files", lambda f: _n(f["files"])), ("Size", lambda f: _bytes(f["bytes"])),
            ("Folders", lambda f: _n(f["folders"])), ("Deepest level", lambda f: _n(f["max_depth"])),
            ("Files the Connector reads", lambda f: f"{_n(f['connector_readable_files'])} ({_pct(f['connector_readable_share'])})"),
            ("Folders that refuse the account", lambda f: _n(f["refused_folders"])),
            ("Newest file, days", lambda f: _n(f["age"]["newest_days"])),
            ("Oldest file, days", lambda f: _n(f["age"]["oldest_days"])),
            ("Files < 1 year / 1-5 / 5-10 / > 10 years", lambda f: " / ".join(_n(f["age"]["bands"][b]) for b in ("<1y", "1-5y", "5-10y", ">10y"))),
            ("AutoDoc names (share of files)", lambda f: _pct(f["patterns"]["autodoc_names_share"])),
            ("Number-range folders", lambda f: _n(f["patterns"]["range_folders"])),
            ("Office lock files", lambda f: _n(f["patterns"]["office_lock_files"])),
            ("Outlook archives", lambda f: _n(f["patterns"]["outlook_archives"])),
            ("Dictation files", lambda f: _n(f["patterns"]["dictation_files"])),
            ("Scanner output", lambda f: _n(f["patterns"]["scanner_files"])),
            ("Files without extension", lambda f: _n(f["patterns"]["no_extension_files"])),
            ("Paths over 240 characters", lambda f: _n(f["patterns"]["long_paths"])),
            ("Empty files", lambda f: _n(f["patterns"]["empty_files"])),
            ("Largest file", lambda f: _bytes(f["largest_file_bytes"])),
            ("Listing: entries per second", lambda f: _n(f["listing"]["entries_per_second"])),
            ("Listing: seconds", lambda f: _n(f["listing"]["seconds"])),
            ("First read: files to upload", lambda f: _n(f["first_read_forecast"]["files_to_upload"])),
            ("First read: hours at 4 per minute", lambda f: _n(f["first_read_forecast"]["hours_at_4_per_minute"])),
        ]
        L += table(["Fact"] + keys, [[label] + [fn(r["facts"]) for r in shares] for label, fn in rows_def])
        L += [""]
        L += ["Files per extension (the ten most frequent per root):", ""]
        for r in shares:
            ext = sorted(r["facts"]["extensions"].items(), key=lambda kv: (-kv[1], kv[0]))[:10]
            L += [f"- `{r['key']}`: " + (", ".join(f"{k or '(none)'} {_n(v)}" for k, v in ext) or "none")]
        L += [""]
    dirs = [r for r in probes.get("directories") or [] if r["status"] in ("ok", "partial")]
    if dirs:
        L += ["## Directories in numbers", ""]
        names = [("is_active_directory", "Active Directory"), ("people_enabled", "People, enabled"),
                 ("people_disabled", "People, disabled"), ("service_accounts", "Service accounts"),
                 ("groups", "Groups"), ("groups_empty", "Groups without members"),
                 ("max_nesting", "Deepest group nesting"), ("computers", "Computers")]
        L += table(["Fact"] + [f"`{r['key']}`" for r in dirs], [[label] + [_n(r["facts"].get(k)) for r in dirs] for k, label in names])
        L += [""]
    for r in [r for r in probes.get("databases") or [] if r["status"] in ("ok", "partial") and r["facts"].get("table_list")]:
        f = r["facts"]
        L += [f"## Database `{r['key']}` in numbers", ""]
        L += [f"{_n(f['tables'])} tables, {_n(f['rows_total'])} rows, {_n(f['foreign_keys'])} foreign keys, "
              f"{_n(f['views'])} views and {_n(f['procedures'])} procedures visible to the login. Product: "
              f"{f.get('product') or 'not recognised'}. Words of a legal practice in table names: "
              f"{_n(f['vocabulary'].get('legal_practice'))} of 10.", ""]
        L += ["Roles are guesses from names, keys, columns and row counts; the consultant confirms them.", ""]
        rows = [[f"`{t['name']}`", _n(t["rows"]), t["columns"], ", ".join(t["key"]) or "(none)", t["foreign_keys"],
                 "yes" if t["rowversion"] else "", ", ".join(t["changed_at"]), ", ".join(t["delete_flag"]), t["role"]]
                for t in sorted(f["table_list"], key=lambda t: (t["role"], t["name"].upper()))]
        L += table(["Table", "Rows", "Columns", "Key", "Foreign keys", "Rowversion", "Changed at", "Delete flag", "Role"], rows)
        L += [""]
    return L


def _environment(report: dict) -> list[str]:
    e = report.get("environment")
    L = ["## Environment of the scan", ""]
    if not e:
        return L + ["The environment probe did not run.", ""]
    d = e.get("docker") or {}
    rows = [["Operating system", e.get("os")], ["Python", e.get("python")],
            ["Inside a container", _n(e.get("in_container"))],
            ["Docker", f"{d.get('version')}" if d.get("present") else "not found"],
            ["CPUs", _n(e.get("cpus"))], ["Memory", _bytes(e.get("memory_bytes"))],
            ["Free disk", _bytes(e.get("disk_free_bytes"))],
            ["Knovas API host answers", "yes" if e.get("api_reachable") else
             (f"no ({e.get('api_error')})" if e.get("api_host_set") else "not configured")],
            ["Proxy variables set", ", ".join(e.get("proxy_vars") or []) or "none"]]
    return L + table(["Fact", "Value"], rows) + [""]


PROBE_NAMES = {"fileshare": "file shares", "directory": "directories", "mssql": "databases", "m365": "Microsoft 365"}


def _not_ok(report: dict) -> list[str]:
    rows = [[n["probe"], f"`{n['key']}`", n["status"], n["error"] or ""] for n in report["probes_not_ok"]]
    L = ["## Probes that failed, were partial or were skipped", ""]
    L += table(["Probe", "Key", "Status", "Error"], rows) if rows else ["None: every probe ran to the end."]
    L += [""]
    if report.get("not_targeted"):
        L += ["Not looked at, because intake.yaml names no target: "
              + ", ".join(PROBE_NAMES[p] for p in report["not_targeted"]) + ".", ""]
    return L


# --------------------------------------------------------------------------- connection-plan.md
def plan_md(report: dict) -> str:
    L = _header(report, "Connection plan")
    L += ["Phases follow the class of each source. A custom source carries its effort in points and an effort "
          "class; this plan says nothing about cost.", ""]
    by = {c: [s for s in report["sources"] if s["class"] == c] for c in "ABCD"}
    L += ["## Phase 1: standard (class A)", ""]
    L += table(["Source", "Kind", "Component", "How"],
               [[f"`{s['result_key']}`", s["name"], s["knovas"]["component"], s["knovas"]["how"]] for s in by["A"]]) \
        if by["A"] else ["No source of class A."]
    L += ["", "## Phase 2: standard with mapping (class B)", ""]
    L += table(["Source", "Kind", "Component", "What is set up or mapped"],
               [[f"`{s['result_key']}`", s["name"], s["knovas"]["component"], s["knovas"]["how"]] for s in by["B"]]) \
        if by["B"] else ["No source of class B."]
    L += ["", "## Phase 3: custom (class C)", ""]
    if by["C"]:
        rows = []
        for s in by["C"]:
            e = s["effort"] or {}
            pts = e.get("points")
            rows.append([f"`{s['result_key']}`", s["name"], "set in review.yaml" if pts is None else pts,
                         e.get("effort_class") or "not set"])
        L += table(["Source", "Kind", "Effort points", "Effort class"], rows) + [""]
        for s in by["C"]:
            L += [f"### Effort of `{s['result_key']}` ({s['name']})", ""] + effort_lines(s)
    else:
        L += ["No source of class C.", ""]
    L += ["## Not now (class D)", ""]
    L += table(["Source", "Kind", "Why"],
               [[f"`{s['result_key']}`", s["name"], s["knovas"]["how"]] for s in by["D"]]) if by["D"] else ["No source of class D."]
    L += ["", "## What we need from the client", ""]
    L += [f"{i}. {n}" for i, n in enumerate(report["needs_from_client"], 1)] or ["Nothing yet."]
    L += ["", "## Open questions", ""]
    L += [f"{i}. {q}" for i, q in enumerate(report["open_questions"], 1)] or ["None."]
    L += [""]
    L += _not_ok(report)
    return "\n".join(L).rstrip() + "\n"
