"""docs/facts.md: every fact each probe produces, one line each. `python -m knovas_discovery.facts > docs/facts.md`.

tests/test_facts_doc.py checks that the file equals this output and that the probes produce exactly these facts.
"""

from __future__ import annotations

import sys

from .probes import all_facts

INTRO = """\
# Facts the probes produce

Generated from the probes' `FACTS` (`python -m knovas_discovery.facts > docs/facts.md`); a test keeps the two equal
and checks that each probe produces exactly these facts. A catalogue condition (`detect`, `checks`) may name only a
fact listed here, by its dotted path; `knovas-discovery catalogue --check` enforces it. Facts are numbers, patterns
and database schema names: never a name of a person, client, file, folder or group (those go to `detail.json`).

Rules behind the guesses (`heuristics.py`, `probes/fileshare.py`, `probes/directory.py`):

- Table roles, in this order: **leftover** if the table has no key (primary key, else its first unique index) or its
  name speaks of a backup, copy, import, test or temporary table or ends in a date (6 to 8 digits); **software** if
  the name starts with SYS, CFG, LOG, AUDIT, SETTING, PARAM or LOCK, or speaks of locks (sperr), numbering
  (nummern), logins (ends in benutzer), sessions, or is only a version table; **records** if the key is made only of
  other tables' one-column keys (a link table); **lookup** if the key is one column and the table has at most 4
  columns, or at most 5 columns and at most 50 rows; else **records**.
- A key of several columns is never a lookup: such a row belongs to something else.
- A rights table is a records table whose name speaks of rights, access or locks (recht, berechtig, zugriff,
  permission, acl, sperr), and is not a SYS table: a lookup only lists kinds (areas of law, kinds of right).
- Names: "custom" does not count inside "customer", "eav" only as a word of its own, "temp" not inside "template",
  "test" not inside "testament".
- Directory: Administrator, Guest and krbtgt (relative ids 500 to 502) are not people; groups in CN=Builtin and the
  default groups in CN=Users (relative id below 1000, DnsAdmins, DnsUpdateProxy) are not the firm's groups.
- File share: the long-path limit (240) counts the path below the share, root included.
"""


def render() -> str:
    lines = [INTRO]
    for probe, facts in all_facts().items():
        lines.append(f"## {probe}\n")
        for name, meaning in facts.items():
            lines.append(f"- `{name}`: {meaning}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    sys.stdout.write(render())
