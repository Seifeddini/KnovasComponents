#!/usr/bin/env python3
"""Compare Knovas Discovery's findings for the demo law firm with the generator's truth (synthetic data).

    python verify_against_truth.py --out out --work ~/demo-kanzlei/work

Reads out/findings.json, out/detail.json (refused folder paths), out/report.json, and from the work folder
truth/inventory.json, truth/sources.json, manifest.jsonl, site/directory.json, site/db/00_schema.sql. Prints one
PASS/FAIL or a number per line; mismatches are listed, never hidden. Standard library only. Exit code 1 if any FAIL.
The output names tables and share roots; folder paths only when they do not match.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

lines: list[str] = []
tally = Counter()


def check(ok: bool, text: str) -> bool:
    tally["PASS" if ok else "FAIL"] += 1
    lines.append(f"{'PASS' if ok else 'FAIL'}  {text}")
    return ok


def note(text: str) -> None:
    lines.append(f"      {text}")


def head(text: str) -> None:
    lines.append("")
    lines.append(f"## {text}")


def ext(path: str) -> str:
    return os.path.splitext(path.rsplit("/", 1)[-1])[1].lower()


def chain(groups: dict[str, list[str]]) -> int:
    def depth(g: str, seen: frozenset) -> int:
        return max((1 + depth(c, seen | {c}) for c in groups.get(g, []) if c not in seen), default=0)
    return max((depth(g, frozenset({g})) for g in groups), default=0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--work", required=True)
    a = ap.parse_args()
    out, work = Path(a.out), Path(a.work)
    findings = json.loads((out / "findings.json").read_text("utf-8"))
    detail = json.loads((out / "detail.json").read_text("utf-8"))
    report = json.loads((out / "report.json").read_text("utf-8"))
    inventory = json.loads((work / "truth" / "inventory.json").read_text("utf-8"))
    truth = json.loads((work / "truth" / "sources.json").read_text("utf-8"))
    directory_json = json.loads((work / "site" / "directory.json").read_text("utf-8"))
    schema = (work / "site" / "db" / "00_schema.sql").read_text("utf-8")
    manifest = [json.loads(line) for line in (work / "manifest.jsonl").read_text("utf-8").splitlines() if line]

    lines.append("Knovas Discovery against the demo law firm's truth (all data synthetic)")
    lines.append(f"scan of {findings['scanned_at']}, knovas-discovery {findings['tool_version']}; "
                 f"truth as of {inventory['as_of']}")
    probes = findings["probes"]

    # ------------------------------------------------------------------ file share
    head("File share: per root, against manifest.jsonl (files the account may read)")
    shares = [r for r in probes["fileshares"]]
    found_refused: list[str] = []
    for r in shares:
        root = r["key"].split(":", 1)[1]
        f = r["facts"]
        below = [m for m in manifest if m["path"].startswith(root + "/")]
        reach = [m for m in below if m["reachable"]]
        if r["status"] not in ("ok", "partial"):
            check(False, f"{root}: probe {r['status']} ({r['error']})")
            continue
        check(f["files"] == len(reach), f"files {root}: scan {f['files']:,}, manifest {len(reach):,} readable "
                                        f"({len(below):,} on the share)")
        check(f["bytes"] == sum(m["size"] for m in reach),
              f"bytes {root}: scan {f['bytes']:,}, manifest {sum(m['size'] for m in reach):,}")
        want_ext = dict(Counter(ext(m["path"]) for m in reach))
        check(f["extensions"] == want_ext, f"files per extension {root}: {len(f['extensions'])} extensions"
              + ("" if f["extensions"] == want_ext else f"; differ: scan {f['extensions']} truth {want_ext}"))
        readable = sum(1 for m in reach if m["syncable"])
        check(f["connector_readable_files"] == readable,
              f"connector-readable files {root}: scan {f['connector_readable_files']:,}, manifest syncable and "
              f"reachable {readable:,}")
        empty = sum(1 for m in reach if m["size"] == 0)
        check(f["patterns"]["empty_files"] == empty, f"empty files {root}: scan {f['patterns']['empty_files']}, "
                                                     f"manifest {empty}")
        found_refused += detail["probes"].get(f"fileshare/{r['key']}", {}).get("refused_folders", [])
        speed = f["listing"]["entries_per_second"]
        note(f"{root}: {f['folders']:,} folders, {f['refused_folders']} refused, listed in {f['listing']['seconds']} s"
             + (f" ({speed:,.0f} entries/s)" if speed is not None else " (nothing listable)"))

    roots = [r["key"].split(":", 1)[1] for r in shares]

    def at_or_below(p: str, folder: str) -> bool:
        return p == folder or p.startswith(folder + "/")

    # The folders that refuse the account, from the site's own permissions (site/acl.json, applied parents first:
    # protect=true replaces the grants, protect=false adds to them; svc-knovas reads through itself and its groups).
    acl = sorted(json.loads((work / "site" / "acl.json").read_text("utf-8")),
                 key=lambda e: -1 if not e["path"] else e["path"].count("/"))
    groups = {g["name"]: g for g in directory_json["groups"]}
    me = {"svc-knovas"}
    grew = True
    while grew:
        more = {n for n, g in groups.items() if me & (set(g["members"]) | set(g["member_groups"]))} - me
        me |= more
        grew = bool(more)

    def may_list(folder: str) -> bool:
        grants: set[str] = set()
        for e in acl:
            if not e["path"] or at_or_below(folder, e["path"]):
                names = {g["principal"] for g in e["grants"] if g["access"] in ("read", "modify", "full")}
                grants = names if e["protect"] else grants | names
        return bool(grants & me)

    all_dirs = set()
    for m in manifest:
        parts = m["path"].split("/")[:-1]
        all_dirs |= {"/".join(parts[:i]) for i in range(1, len(parts) + 1)}
    all_dirs |= {e["path"] for e in acl if e["path"]}
    expected_refused = sorted(d for d in all_dirs if any(at_or_below(d, rt) for rt in roots) and not may_list(d)
                              and (d in roots or may_list(d.rsplit("/", 1)[0])))
    per_root = ", ".join(f"{rt}: {sum(1 for p in found_refused if at_or_below(p, rt))}" for rt in roots)
    check(sorted(found_refused) == expected_refused,
          f"refused folders: {len(found_refused)} found ({per_root}), {len(expected_refused)} expected from "
          f"site/acl.json (the topmost folders below the roots that the scan account may not list)")
    for p in sorted(set(expected_refused) - set(found_refused)):
        note(f"expected but not found: {p}")
    for p in sorted(set(found_refused) - set(expected_refused)):
        note(f"found but not expected: {p}")
    truth_refused = sorted(p for p in inventory["not_reachable_by_knovas_account"]
                           if any(p.startswith(rt + "/") for rt in roots))
    uncovered = [p for p in truth_refused if not any(at_or_below(p, fr) for fr in found_refused)]
    leaky = [fr for fr in found_refused if any(m["reachable"] and at_or_below(m["path"], fr) for m in manifest)]
    check(not uncovered and not leaky,
          f"inventory's unreachable trees: {len(truth_refused) - len(uncovered)} of {len(truth_refused)} lie at or "
          f"below a refused folder found (the inventory names each file's own permission folder, e.g. one per "
          f"person below a root that already refuses); {len(leaky)} refused folders hold a file the truth calls readable")
    for p in uncovered:
        note(f"not found: {p}")
    for p in leaky:
        note(f"refused but readable in the truth: {p}")
    unreach = [m["path"] for m in manifest if not m["reachable"] and any(m["path"].startswith(rt + "/") for rt in roots)]
    covered = sum(1 for p in unreach if any(at_or_below(p, fr) for fr in found_refused))
    check(covered == len(unreach), f"every unreachable file of the manifest lies below a refused folder: "
                                   f"{covered:,} of {len(unreach):,}")

    def visible_folders(root: str) -> int:
        """Every folder below the root, less those inside a folder that refuses the account (from acl.json).
        The inventory's own `folders` counts folders that hold files directly, the root included: another measure."""
        return sum(1 for d in all_dirs if d.startswith(root + "/")
                   and not any(d.startswith(t + "/") for t in expected_refused))

    for r in shares:
        root = r["key"].split(":", 1)[1]
        if r["status"] in ("ok", "partial"):
            want = visible_folders(root)
            check(r["facts"]["folders"] == want, f"folders {root}: scan {r['facts']['folders']}, manifest {want} "
                                                 f"(below the root, not inside a refused folder)")

    autodoc = next((r for r in shares if r["key"].endswith(inventory["sources"]["autodoc"]["root"])), None)
    gemeinsam = next((r for r in shares if r["key"].endswith(inventory["sources"]["gemeinsam"]["root"])), None)
    if autodoc:
        rf = autodoc["facts"]["patterns"]["range_folders"]
        check(rf == 63 == inventory["sources"]["autodoc"]["folders"], f"range folders in the store: {rf} (truth 63)")
        share_ad = autodoc["facts"]["patterns"]["autodoc_names_share"]
        want = sum(1 for m in manifest if m["path"].startswith(inventory["sources"]["autodoc"]["root"] + "/")
                   and re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}_[^_]+_",
                                m["path"].rsplit("/", 1)[-1], re.I))
        note(f"AutoDoc names in the store: {share_ad:.2%} of files (manifest: {want:,} of "
             f"{inventory['sources']['autodoc']['files']:,})")
    if gemeinsam:
        scans = sum(1 for m in manifest if m["kind"] == "scan" and m["reachable"])
        check(gemeinsam["facts"]["patterns"]["scanner_files"] == scans,
              f"scanner output in the common drive: scan {gemeinsam['facts']['patterns']['scanner_files']}, "
              f"manifest kind=scan {scans}")
    pst_all = sum(1 for m in manifest if ext(m["path"]) in (".pst", ".ost"))
    pst_seen = sum(r["facts"]["patterns"]["outlook_archives"] for r in shares if r["status"] in ("ok", "partial"))
    pst_reach = sum(1 for m in manifest if ext(m["path"]) in (".pst", ".ost") and m["reachable"])
    check(pst_seen == pst_reach, f"Outlook archives: scan saw {pst_seen}, manifest readable {pst_reach} "
                                 f"(of {pst_all} on the share)")
    dss = sum(1 for m in manifest if ext(m["path"]) in (".dss", ".ds2", ".dvf") and m["reachable"])
    dss_seen = sum(r["facts"]["patterns"]["dictation_files"] for r in shares if r["status"] in ("ok", "partial"))
    check(dss_seen == dss, f"dictation files: scan {dss_seen:,}, manifest readable {dss:,}")

    # ------------------------------------------------------------------ directory
    head("Directory, against truth/sources.json and site/directory.json")
    d = next((r for r in probes["directories"]), None)
    td = truth["directory"]
    if not d or d["status"] != "ok":
        check(False, f"directory probe: {d['status'] if d else 'missing'} ({d['error'] if d else ''})")
    else:
        f = d["facts"]
        check(f["is_active_directory"] is True, "is Active Directory")
        check(f["people_enabled"] == td["people_enabled"], f"people enabled: {f['people_enabled']} (truth {td['people_enabled']})")
        check(f["people_disabled"] == td["people_disabled"], f"people disabled: {f['people_disabled']} (truth {td['people_disabled']})")
        check(f["service_accounts"] == td["service_accounts"],
              f"service accounts: {f['service_accounts']} (truth {td['service_accounts']})")
        check(f["groups"] == len(td["groups"]), f"groups: {f['groups']} (truth {len(td['groups'])})")
        nesting = chain({g["name"]: g["member_groups"] for g in directory_json["groups"]})
        check(f["max_nesting"] == nesting, f"deepest group nesting: {f['max_nesting']} (truth {nesting})")
        empty = sum(1 for g in directory_json["groups"] if not g["members"] and not g["member_groups"])
        check(f["groups_empty"] == empty, f"groups without members: {f['groups_empty']} (truth {empty})")
        note(f"computers: {f['computers']}")

    # ------------------------------------------------------------------ database
    head("Database, against truth/sources.json (tables: rows, key, changed_by, deleted_by, role)")
    db = next((r for r in probes["databases"] if r["status"] == "ok" and r["facts"].get("table_list")), None)
    ttables = {t["table"].upper(): t for t in truth["database"]["tables"]}
    if not db:
        check(False, "database probe: no database with tables")
    else:
        f = db["facts"]
        scan = {t["name"].upper(): t for t in f["table_list"]}
        check(f["tables"] == len(ttables) == 23, f"tables: {f['tables']} (truth {len(ttables)})")
        check(set(scan) == set(ttables), "table names equal" + ("" if set(scan) == set(ttables) else
              f": only scan {sorted(set(scan) - set(ttables))}, only truth {sorted(set(ttables) - set(scan))}"))
        common = sorted(set(scan) & set(ttables))
        bad_rows = [n for n in common if scan[n]["rows"] != ttables[n]["rows"]]
        check(not bad_rows, f"rows per table: {len(common) - len(bad_rows)} of {len(common)} equal"
              + "".join(f"; {n}: scan {scan[n]['rows']}, truth {ttables[n]['rows']}" for n in bad_rows))
        bad_keys = [n for n in common if scan[n]["key"] != ttables[n]["key"]]
        check(not bad_keys, f"keys: {len(common) - len(bad_keys)} of {len(common)} equal"
              + "".join(f"; {n}: scan {scan[n]['key']}, truth {ttables[n]['key']}" for n in bad_keys))
        check(f["foreign_keys"] == 0, f"foreign keys: {f['foreign_keys']} (truth: none declared)")

        rowversion_cols: dict[str, set] = {}
        for m in re.finditer(r"CREATE TABLE dbo\.(\w+) \((.*?)\n\);", schema, re.S):
            for line in m.group(2).splitlines():
                parts = line.strip().rstrip(",").split()
                if len(parts) >= 2 and parts[1].lower() in ("rowversion", "timestamp"):
                    rowversion_cols.setdefault(m.group(1).upper(), set()).add(parts[0])
        bad_marker = []
        for n in common:
            tset = set(ttables[n]["changed_by"])
            rv = rowversion_cols.get(n, set()) & tset
            want_changed = sorted(tset - rv)
            if scan[n]["rowversion"] != bool(rv) or sorted(scan[n]["changed_at"]) != want_changed:
                bad_marker.append(f"{ttables[n]['table']}: truth changed_by {ttables[n]['changed_by']}, scan "
                                  f"rowversion={scan[n]['rowversion']} changed_at={scan[n]['changed_at']}")
        check(not bad_marker, f"rowversion and changed-at vs changed_by: {len(common) - len(bad_marker)} of "
                              f"{len(common)} tables as in the truth")
        for b in bad_marker:
            note(f"mismatch: {b}")
        bad_flag = []
        for n in common:
            db_ = ttables[n]["deleted_by"]
            want = [db_.split(":", 1)[1]] if db_.startswith("flag:") else []
            if scan[n]["delete_flag"] != want:
                bad_flag.append(f"{ttables[n]['table']}: truth deleted_by {db_}, scan delete_flag {scan[n]['delete_flag']}")
        check(not bad_flag, f"delete flags vs deleted_by: {len(common) - len(bad_flag)} of {len(common)} tables as in "
                            f"the truth")
        for b in bad_flag:
            note(f"mismatch: {b}")
        bad_role = [f"{ttables[n]['table']}: guessed {scan[n]['role']}, truth {ttables[n]['role']}"
                    for n in common if scan[n]["role"] != ttables[n]["role"]]
        right = len(common) - len(bad_role)
        check(not bad_role, f"role accuracy: {right}/{len(common)} = {right / len(common):.3f}")
        for b in bad_role:
            note(f"mismatch: {b}")
        by_role = Counter(t["role"] for t in ttables.values())
        note("truth roles: " + ", ".join(f"{k} {v}" for k, v in sorted(by_role.items()))
             + "; scan: " + ", ".join(f"{k} {f[fact]}" for k, fact in (("leftover", "leftover_tables"),
                                                                         ("lookup", "lookup_tables"),
                                                                         ("records", "record_tables"),
                                                                         ("software", "software_tables"))))

    # ------------------------------------------------------------------ classification
    head("Classification (report.json)")
    src = report["sources"]

    def find(entry: str, key_end: str | None = None):
        return [s for s in src if s["entry"] == entry and (key_end is None or s["result_key"].endswith(key_end))]

    ad_root, gm_root = inventory["sources"]["autodoc"]["root"], inventory["sources"]["gemeinsam"]["root"]
    hit = find("autodoc-store", ad_root)
    check(bool(hit) and hit[0]["class"] == "B" and not find("fileshare", ad_root),
          f"store -> autodoc-store {hit[0]['class'] if hit else 'missing'} (want B; no plain fileshare on that root)")
    hit = find("fileshare", gm_root)
    check(bool(hit) and hit[0]["class"] == "A", f"common drive -> fileshare {hit[0]['class'] if hit else 'missing'} (want A)")
    hit = find("active-directory")
    check(bool(hit) and hit[0]["class"] == "B", f"directory -> active-directory {hit[0]['class'] if hit else 'missing'} (want B)")
    hit = find("practice-database")
    ok = bool(hit) and hit[0]["class"] == "C" and hit[0]["effort"] and hit[0]["effort"]["points"] is not None
    check(ok, f"database -> practice-database {hit[0]['class'] if hit else 'missing'} (want C)"
          + (f", effort {hit[0]['effort']['points']} points, class {hit[0]['effort']['effort_class']}" if ok else ""))
    if ok:
        for dr in hit[0]["effort"]["drivers"]:
            note(f"{dr['label']}: {dr['measured']} -> {dr['points']}")
    hit = find("outlook-archives")
    check(bool(hit) and all(s["class"] == "D" for s in hit),
          f".pst -> outlook-archives {hit[0]['class'] if hit else 'not reported'} (want D)"
          + ("" if hit else f": the {pst_all} .pst files are all in folders that refuse the account, so the scan "
                            f"cannot see them"))
    hit = find("dictations")
    check(bool(hit) and all(s["class"] == "D" for s in hit),
          f"dictations -> dictations D on {len(hit)} root(s): " + ", ".join(s["result_key"] for s in hit))
    hit = find("scanner-output", gm_root)
    check(bool(hit) and hit[0]["class"] == "A", f"scanner output (common drive) -> scanner-output "
                                                f"{hit[0]['class'] if hit else 'missing'} (want A)")
    others = find("scanner-output")
    if len(others) > len(hit):
        note("scanner-output also on: " + ", ".join(s["result_key"] for s in others if s not in hit)
             + " (a file named like scanner output in the store)")
    for s in find("no-access"):
        note(f"{s['result_key']} -> no-access {s['class']} (nothing readable with the account)")
    note("all sources: " + ", ".join(f"{s['entry']}@{s['result_key']} {s['class']}" for s in src))

    lines.append("")
    lines.append(f"{tally['PASS']} PASS, {tally['FAIL']} FAIL")
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")  # the same bytes on Windows and in the container
    sys.stdout.write("\n".join(lines) + "\n")
    return 1 if tally["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
