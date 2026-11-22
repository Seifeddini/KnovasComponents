"""The process line's page (PMVP-32a, plan B-06): where one thing stands in its process. Attached to the Brain views'
blueprint (brain_views_routes.py), behind its switch and gate.

- ``GET /ablauf/<node_id>``: the thing's kind as the eyebrow, its name as the title, «Ablauf «<process>» · Schritt n von
  N»; the main path as a line (done, current, open), about twelve steps a row; the card «Jetzt: <step>» with «seit
  <date>» and who; «Danach: <next step>»; «Andere Wege» on request. What is read is ``brain_views.process_line``'s
  (as the signed-in person, per thing). A thing in no process: one sentence (200). A process not completely
  described: one sentence (200). A hidden or missing thing: the dossier's 404. A failed read: one plain message (502).
- **What is drawn** goes through ``brain_views.limit.bounded``: the thing (its head) and the steps of the main path,
  at most 25. With more steps, the drawn ones are a window that holds the current step, and the rest is a count that
  opens the whole main path as a list (``?ansicht=schritte``).

Every address is built here with url_for. The route logs no name or fact; only the type of a failed read.
"""
from __future__ import annotations

import logging
import math
from datetime import date
from typing import Any, Callable, Dict, List, Mapping, Optional

from flask import Blueprint, abort, render_template, request, url_for

from brain_views.limit import bounded, more_text
from brain_views.process_line import REWORKS_TO, Line, NotFound, Unavailable, read_line

logger = logging.getLogger(__name__)

PER_ROW = 12
VIEW_STEPS = "schritte"
_MONTHS = ("Jan.", "Feb.", "März", "Apr.", "Mai", "Juni", "Juli", "Aug.", "Sept.", "Okt.", "Nov.", "Dez.")


def german_date(day: date) -> str:
    """«16. Nov. 2026», as the reference screens write a date."""
    return f"{day.day}. {_MONTHS[day.month - 1]} {day.year}"


def rows_of(items: List[Any], per_row: int = PER_ROW) -> List[List[Any]]:
    """The drawn steps in rows of about ``per_row``, as even as they go (13 steps: 7 and 6)."""
    if not items:
        return []
    count = math.ceil(len(items) / per_row)
    size = math.ceil(len(items) / count)
    return [items[i:i + size] for i in range(0, len(items), size)]


def attach_process_line_routes(bp: Blueprint, *, refuse_unless_open: Callable[[], None], client: Callable[[], Any],
                               page_context: Callable[[], Mapping[str, Any]]) -> None:
    """The process line's route on the Brain views' blueprint, behind ``refuse_unless_open`` (the switch and the
    gate)."""

    @bp.route("/ablauf/<node_id>")
    def ablauf(node_id: str):
        refuse_unless_open()
        try:
            line = read_line(client(), node_id)
        except NotFound:
            abort(404)
        except Unavailable as exc:
            logger.warning("Process line: a read failed (%s)", exc)
            return render_template("ablauf.html", **dict(page_context()), line=None), 502
        return render_template("ablauf.html", **dict(page_context()), **_page(line, request.args))


def _page(line: Line, args: Mapping[str, Any]) -> Dict[str, Any]:
    here = {"node_id": line.node_id}
    page: Dict[str, Any] = {"line": line, "dossier_href": url_for("brain_views.dossier", **here)}
    if not (line.in_process and line.complete):
        return page
    main = list(line.main)
    current = (line.position - 1) if line.position else None
    states = ["done" if current is not None and i < current else "now" if i == current else "open"
              for i in range(len(main))]
    probe = bounded(main, reserved=1)                      # the thing's head is drawn as well
    room = len(probe.shown)
    start = 0
    if current is not None and probe.rest:
        start = max(0, min(current - room // 2, len(main) - room))
    drawn = bounded(main[start:], reserved=1)
    shown = [{"number": start + i + 1, "name": step.name, "state": states[start + i]}
             for i, step in enumerate(drawn.shown)]
    hidden = start + drawn.rest
    page.update({
        "rows": rows_of(shown),
        "hidden": hidden,
        "hidden_text": more_text(hidden),
        "steps_href": url_for("brain_views.ablauf", ansicht=VIEW_STEPS, **here),
        "all_steps": [{"number": i + 1, "name": s.name, "state": states[i]} for i, s in enumerate(main)]
        if args.get("ansicht") == VIEW_STEPS else None,
        "since": german_date(line.since) if line.since else None,
        "ways": [{"name": way.step.name, "back": way.relation == REWORKS_TO} for way in line.ways],
    })
    return page
