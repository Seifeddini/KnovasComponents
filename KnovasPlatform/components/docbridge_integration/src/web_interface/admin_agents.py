"""The console's agent pages (RB-7) and the agents of a role (RB-6, RB-7): plan FEAT-2026-11-19-platform-mvp.

An agent is a member of roles that is not a person (``identity.agents``): the Knovas Assistent, or an external agent
(Claude Code, Copilot ...) an administrator registers here. The pages, all in the console's blueprint behind its
``require_admin``; every POST checks the form field ``csrf_token`` first and answers 400 with the page again:

    GET  /admin/agents                      (admin.agents) every agent: its name, the assistant or an external
                                            agent, whom it acts for, its id (the «Kennung» Knovas binds the gateway
                                            key to, CR-R6), its roles, revoked or not; the switch's state
    POST /admin/agents/create               name, on_behalf_of (an active person's id or empty): an external agent;
                                            the answer names its id and how to connect (KnovasPlatform/docs/agents.md)
    POST /admin/agents/<id>/revoke          revokes an external agent (not offered for the assistant)
    POST /admin/roles/<id>/agents/add       agent_id: on the role page, «+ Agent hinzufügen»; Administration 400
    POST /admin/roles/<id>/agents/remove    agent_id: takes an agent out, the assistant included

A change is made by the store (one transaction, its audit row by id) and then, once committed, delivered for that
agent to the Core's gateway at once (``app.extensions["agents"]["deliver"]``, behind the switch
``agents.core_gateway``; off, nothing reaches the Core). A Core that refuses or lacks the route never breaks a page.

The two role routes belong on RB-3/RB-4's role page: ``role_page(role_id, error=..., notice=..., status=...)`` draws
it when given; without it they answer with this page.

Logs: ids and counts only, never an agent's or a person's name. Plan items RB-6, RB-7; work order
docs/platform-mvp/work-orders/RB-6-7.md.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

from flask import abort, current_app, render_template, request

from identity.agent_delivery import SETTING, gateway_on
from identity.agents import (
    ASSISTANT,
    AgentError,
    AgentNotFound,
    AgentStore,
    ProtectedAgentError,
)
from identity.roles import RoleError, RoleNotFound, RoleStore

logger = logging.getLogger(__name__)

EXPIRED_TEXT = "Formular ist abgelaufen. Bitte erneut versuchen."
KIND_WORDS = {"assistant": "Knovas Assistent", "external": "Externer Agent"}
#: Whom an agent acts for, when no person is fixed.
ACTS_SIGNED_IN = "die angemeldete Person"
ACTS_ON_ITS_OWN = "selbständig"
#: Where an administrator reads how to connect an agent (KnovasPlatform/docs/agents.md).
HOW_TO_CONNECT = "KnovasPlatform/docs/agents.md"


def _deliver(agent_id: Any) -> Optional[str]:
    """Send this agent's claims to the Core now (or drop them), if the switch is on. Never raises into the page.

    Returns the delivery's outcome (``sent``, ``dropped``, ``failed``, ``skipped``), None when the switch is off.
    """
    agents = current_app.extensions.get("agents") or {}
    deliver = agents.get("deliver")
    if deliver is None:
        return None
    try:
        return deliver(agent_id)
    except Exception as exc:  # noqa: BLE001 - the delivery logs its own failures; a page never breaks on it
        logger.warning("Agent claims: delivery for agent %s failed (%s)", agent_id, type(exc).__name__)
        return "failed"


#: What a revoke says about the Core, by the delivery's outcome: the Platform's part acts at once in every case.
REVOKED_AT_KNOVAS = {
    "dropped": "Knovas hat seine Rechte verworfen.",
    "failed": "Knovas war nicht erreichbar: Rechte, die Knovas noch hält, enden nach höchstens 5 Minuten.",
    None: ("Die Übergabe an Knovas ist aus: Rechte, die Knovas von früher noch hält, enden nach höchstens "
           "5 Minuten."),
}


def _switch_on() -> Optional[bool]:
    """The switch ``agents.core_gateway`` now, or None when it cannot be read."""
    settings = current_app.extensions.get("platform_settings")
    if settings is None:
        return None
    try:
        return gateway_on(settings.value(SETTING))
    except Exception as exc:  # noqa: BLE001 - the page says it does not know
        logger.warning("Agenten: the switch is not readable (%s)", type(exc).__name__)
        return None


def agent_rows(conn: Any, people: Dict[Any, Any], roles: Dict[Any, str]) -> List[Dict[str, Any]]:
    """Every agent as the pages show it: plain values. ``people``: id -> display name; ``roles``: id -> name."""
    rows = []
    for agent in AgentStore(conn).list_agents():
        if agent.kind == ASSISTANT:
            acts_for = ACTS_SIGNED_IN
        elif agent.on_behalf_of is not None:
            acts_for = people.get(agent.on_behalf_of) or "eine Person"
        else:
            acts_for = ACTS_ON_ITS_OWN
        rows.append({
            "id": str(agent.id),
            "name": agent.name,
            "kind": agent.kind,
            "kind_words": KIND_WORDS.get(agent.kind, agent.kind),
            "acts_for": acts_for,
            "roles": [roles[r] for r in roles if r in agent.roles],
            "role_ids": agent.roles,
            "revoked": agent.revoked,
        })
    return rows


def role_agents(conn: Any, role: Any, people: Dict[Any, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """The agent rows of a role's «Mitglieder» (RB-3/RB-4's role page), and the agents «+ Agent hinzufügen» offers.

    ``members``: the role's agents, the assistant first («verwendet diese Daten in Antworten an Mitglieder»), each
    with whom it acts for and whether it is revoked; ``addable``: the agents not in the role and not revoked, none for
    Administration (no agent joins it). ``people``: id -> display name.
    """
    members, addable = [], []
    for agent in AgentStore(conn).list_agents():
        if agent.kind == ASSISTANT:
            note = "verwendet diese Daten in Antworten an Mitglieder"
        elif agent.on_behalf_of is not None:
            note = f"externer Agent, handelt für {people.get(agent.on_behalf_of) or 'eine Person'}"
        else:
            note = "externer Agent, selbständig"
        row = {"id": str(agent.id), "name": agent.name, "kind": agent.kind, "note": note, "revoked": agent.revoked}
        if role.id in agent.roles:
            members.append(row)
        elif not agent.revoked and role.kind != "administration":
            addable.append(row)
    return {"members": members, "addable": addable}


def attach_agent_routes(bp, gate, *, csrf_valid: Callable[[str], bool], csrf_token: Callable[[], str],
                        page_context: Callable[[], Dict[str, Any]], require_admin: Callable,
                        role_page: Optional[Callable[..., Any]] = None) -> None:
    """Mount the agent routes onto the console's blueprint (one call in ``create_admin_blueprint``)."""

    def _form_csrf_ok() -> bool:
        return csrf_valid(str(request.form.get("csrf_token", "") or ""))

    def _me() -> Any:
        return gate.current_user()

    def _page(error: Optional[str] = None, notice: Optional[str] = None, created: Optional[str] = None,
              status: int = 200):
        conn = gate.connection()
        everyone = gate.users().list_all()
        people = {p.id: p.display_name or p.email for p in everyone}
        roles = {r.id: r.name for r in RoleStore(conn).list_roles()}
        active = sorted((p for p in everyone if p.is_active), key=lambda p: (p.display_name or p.email).casefold())
        return render_template(
            "admin_agents.html",
            active_nav="admin",
            **page_context(),
            agents=agent_rows(conn, people, roles),
            people=[{"id": str(p.id), "name": p.display_name or p.email} for p in active],
            switch_on=_switch_on(),
            created=created,
            how_to_connect=HOW_TO_CONNECT,
            error=error,
            notice=notice,
            csrf_token=csrf_token(),
        ), status

    def _role_answer(role_id: Any, *, error: Optional[str] = None, notice: Optional[str] = None,
                     status: int = 200):
        """The role page again (RB-3/RB-4's), else this page."""
        if role_page is not None:
            return role_page(role_id, error=error, notice=notice, status=status)
        return _page(error=error, notice=notice, status=status)

    @bp.route("/agents")
    @require_admin
    def agents():
        return _page()

    @bp.route("/agents/create", methods=["POST"])
    @require_admin
    def create_agent():
        if not _form_csrf_ok():
            return _page(error=EXPIRED_TEXT, status=400)
        name = str(request.form.get("name", "") or "")
        on_behalf_of = str(request.form.get("on_behalf_of", "") or "").strip() or None
        try:
            agent = AgentStore(gate.connection()).create_agent(name=name, on_behalf_of=on_behalf_of, by=_me().id)
        except AgentError as exc:
            return _page(error=str(exc), status=400)
        _deliver(agent.id)
        return _page(notice=f"«{agent.name}» ist angelegt.", created=str(agent.id))

    @bp.route("/agents/<agent_id>/revoke", methods=["POST"])
    @require_admin
    def revoke_agent(agent_id: str):
        if not _form_csrf_ok():
            return _page(error=EXPIRED_TEXT, status=400)
        try:
            agent = AgentStore(gate.connection()).revoke_agent(agent_id, by=_me().id)
        except AgentNotFound:
            abort(404)
        except ProtectedAgentError as exc:
            return _page(error=str(exc), status=400)
        outcome = _deliver(agent.id)
        at_knovas = REVOKED_AT_KNOVAS.get(outcome, REVOKED_AT_KNOVAS["failed"])
        return _page(notice=f"«{agent.name}» ist widerrufen und hat auf der Plattform keine Rechte mehr. {at_knovas}")

    def _role_change(role_id: str, change: str):
        if not _form_csrf_ok():
            return _role_answer(role_id, error=EXPIRED_TEXT, status=400)
        conn = gate.connection()
        try:
            role = RoleStore(conn).get_role(role_id)
        except RoleNotFound:
            abort(404)
        store = AgentStore(conn)
        try:
            agent = store.get_agent(str(request.form.get("agent_id", "") or ""))
            if change == "add":
                store.add_to_role(role.id, agent.id, by=_me().id)
            else:
                store.remove_from_role(role.id, agent.id, by=_me().id)
        except (AgentError, RoleError) as exc:
            # An unknown agent, Administration, a revoked agent, a 33rd group: nothing was written.
            return _role_answer(role.id, error=str(exc), status=400)
        _deliver(agent.id)
        if change == "add":
            notice = f"«{agent.name}» ist jetzt Mitglied der Rolle «{role.name}»."
        else:
            notice = f"«{agent.name}» gehört nicht mehr zur Rolle «{role.name}»."
        return _role_answer(role.id, notice=notice)

    @bp.route("/roles/<role_id>/agents/add", methods=["POST"])
    @require_admin
    def add_agent_to_role(role_id: str):
        return _role_change(role_id, "add")

    @bp.route("/roles/<role_id>/agents/remove", methods=["POST"])
    @require_admin
    def remove_agent_from_role(role_id: str):
        return _role_change(role_id, "remove")


__all__ = ("ACTS_ON_ITS_OWN", "ACTS_SIGNED_IN", "KIND_WORDS", "agent_rows", "attach_agent_routes", "role_agents")
