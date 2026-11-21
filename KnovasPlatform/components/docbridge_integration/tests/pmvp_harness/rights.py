"""The rights table: ``rights.csv`` as the demo law firm plan writes it.

Header ``person,pointer,may_read,rule``, one row per person and document Knovas could show
(``demo_kanzlei/truth.py``: ``rights_rows``). ``may_read`` is ``true`` or ``false``. ``rule`` says why; the harness
reads past it.

Documents are compared by ``pointers.normalise``, so two rows that name one document in two spellings (another
case, a trailing slash) and disagree are a contradiction and refused.
"""
from __future__ import annotations

import csv
import io
from typing import Dict, Iterable, List, Optional, Tuple

from pmvp_harness.files import read_text
from pmvp_harness.pointers import normalise

HEADER = ["person", "pointer", "may_read", "rule"]
_VALUES = {"true": True, "false": False}


class Rights:
    """Who may read which document, as far as the table says."""

    def __init__(self, rows: Iterable[Tuple[str, str, bool]] = ()):
        self._rows: Dict[Tuple[str, str], bool] = {}
        self._spelling: Dict[str, str] = {}
        for position, (person, pointer, allowed) in enumerate(rows, start=1):
            key = (person, normalise(pointer))
            if not key[1]:
                raise ValueError(f"rights table, row {position}: no pointer")
            if self._rows.get(key, bool(allowed)) != bool(allowed):
                raise ValueError(f"rights table, row {position}: contradicts an earlier row")
            self._rows[key] = bool(allowed)
            self._spelling.setdefault(key[1], str(pointer).strip())

    def __len__(self) -> int:
        return len(self._rows)

    def may_read(self, person: str, pointer: str, *, pointer_prefix: str = "") -> Optional[bool]:
        """True or False as the table says; None when it has no row for this person and document."""
        return self._rows.get((person, normalise(pointer, pointer_prefix)))

    def knows(self, pointer: str, *, pointer_prefix: str = "") -> bool:
        """Whether the table has a row for this document, for anybody."""
        return normalise(pointer, pointer_prefix) in self._spelling

    def spelling(self, pointer: str, *, pointer_prefix: str = "") -> Optional[str]:
        """The document as the table spells it; None when the table does not know it."""
        return self._spelling.get(normalise(pointer, pointer_prefix))

    def leaks(self, person: str, pointers: Iterable[str], *, pointer_prefix: str = "") -> List[str]:
        """Those of ``pointers`` the table forbids to ``person``, as given and in order."""
        return [p for p in pointers if self.may_read(person, p, pointer_prefix=pointer_prefix) is False]


def load_rights(path) -> Rights:
    rows: List[Tuple[str, str, bool]] = []
    first_line: Dict[Tuple[str, str], Tuple[bool, int]] = {}
    reader = csv.reader(io.StringIO(read_text(path, "rights file"), newline=""))
    table = []
    try:
        for cells in reader:
            table.append((cells, reader.line_num))
    except csv.Error:
        raise ValueError(f"rights file, line {reader.line_num}: not readable as CSV") from None
    header = table[0][0] if table else None
    if header is None or [cell.strip() for cell in header] != HEADER:
        raise ValueError(f"rights file: the header is not {','.join(HEADER)}")
    for cells, line in table[1:]:
        if not any(cell.strip() for cell in cells):
            continue
        if len(cells) != len(HEADER):
            raise ValueError(f"rights file, line {line}: {len(cells)} columns instead of {len(HEADER)}")
        person, pointer, may_read, _rule = (cell.strip() for cell in cells)
        key = (person, normalise(pointer))
        if not person or not key[1]:
            raise ValueError(f"rights file, line {line}: 'person' or 'pointer' is empty")
        allowed = _VALUES.get(may_read.lower())
        if allowed is None:
            raise ValueError(f"rights file, line {line}: 'may_read' is not true or false")
        if key in first_line and first_line[key][0] != allowed:
            raise ValueError(f"rights file, line {line}: contradicts line {first_line[key][1]}")
        first_line.setdefault(key, (allowed, line))
        rows.append((person, pointer, allowed))
    return Rights(rows)
