"""External agents' signed claims, handed to the Core's MCP gateway ahead of use (RB-7, Core request CR-R5).

The Platform has no MCP endpoint (founder, 2026-11-21: "Core gateway only"). An external agent calls the Core's
gateway (``mcp.knovas.ch``) with a key Knovas issued for the agent's id (CR-R6); per call the gateway sends one claim
of the agent's pool upstream, and the Secure API verifies it like a person's claim and burns its ``jti``. This module
fills the pools (design ``docs/platform-mvp/design/role-builder.md`` § 5):

- **for every live external agent** (not revoked, its person active, at most 32 groups): ``PUT /secured/agents/<agent
  id>/claims`` with exactly ``{"claims": [...]}``: ``POOL_SIZE`` (20, the Core's maximum) claims, each from
  ``AgentStore.claim_for`` at that moment, signed by the Platform's broker key through ``PrincipalBroker``, each
  with its own ``jti`` and a lifetime of ``CLAIM_LIFETIME_SECONDS`` (300). So an agent makes at most 20 upstream
  calls a minute;
- **for every other external agent**: ``DELETE /secured/agents/<agent id>/claims``, never a ``PUT``;
- **never for the Knovas Assistent**: it goes through the Platform with its own claim (RB-6).

Neither call carries a person's ``principal_assertion``, ``access_groups`` or an ``actor``: the push is the
Platform's own call (``KnovasAPIClient.put_agent_claims`` / ``delete_agent_claims``).

**The switch** ``agents.core_gateway`` (``platform_settings.REGISTRY``; ``aus`` by default; ``AGENTS_CORE_GATEWAY``
pins it), read through the settings' Resolver on every call. Off, nothing here opens a connection or reaches the
Core. It stays off until the Core has CR-R5 and, on a client tenant, until «Ein» (founder: "only after «Ein»").

**When:**

- ``sync()`` sends for every external agent now: ``app.extensions["agents"]["sync"]()``;
- ``deliver(agent_id)`` sends for one agent: the agent pages call it after their change is committed (creating,
  revoking, adding or removing an agent on a role), so those changes reach the Core at once;
- ``AgentSyncJob`` calls ``sync`` every ``SYNC_INTERVAL_SECONDS`` (60) on a connection of its own, in one gunicorn
  worker at a time: the worker that holds the session advisory lock ``LEADER_LOCK`` on its job connection pushes,
  the others wait for it to go away. The first push is one interval after the start: nothing is written to a
  tenant at start. Role and person changes made elsewhere (the role page, the people page) reach the Core at the
  next minute; a pool's claims end within 300 seconds in any case.

**One agent at a time:** every push for an agent holds the advisory lock ``agent_lock(agent id)`` while it reads the
claim and sends it, so a minute's push that read an agent before it was revoked cannot land after the revoke's
``DELETE``: the later reader waits and sends what is true then.

**Failures** (404 while the Core lacks CR-R5, 409 ``not_brokered``, 400, 5xx, no connection) are logged at WARNING
with the agent's id, the HTTP status and the exception's class, and never raised into a page or the job. No claim,
agent name, person name or group id is logged, DEBUG included; no exception's text either.
"""
from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from typing import Any, Callable, Dict, Iterator, Optional

from identity.agents import EXTERNAL, AgentError, AgentNotFound, AgentStore, _uuid
from identity.roles import RoleError

logger = logging.getLogger(__name__)

#: The setting and the environment variable that pins it.
SETTING = "agents.core_gateway"
SWITCH_ENV = "AGENTS_CORE_GATEWAY"
ON_VALUES = frozenset({"an", "on", "true", "1", "ja", "yes"})

#: Claims per push: the Core's maximum (CR-R5). Each upstream call of the gateway burns one.
POOL_SIZE = 20
#: The lifetime of each claim: the Core's verifier refuses more (identity.assertion.MAX_LIFETIME_SECONDS).
CLAIM_LIFETIME_SECONDS = 300
#: The job's cadence.
SYNC_INTERVAL_SECONDS = 60.0
#: One push may take this long (each read of the answer; requests' timeout).
PUSH_TIMEOUT_SECONDS = 10.0
#: How long a push waits for the agent's lock (another push of that agent still running) before it gives up.
LOCK_WAIT_SECONDS = 30.0

#: The session advisory lock the pushing worker holds on its job connection (one installation per database).
LEADER_LOCK = "knovas.agents.core_gateway.job"
_AGENT_LOCK_PREFIX = "knovas.agents.claims:"

SENT, DROPPED, FAILED, SKIPPED = "sent", "dropped", "failed", "skipped"


def gateway_on(value: Any) -> bool:
    """Whether a value of ``agents.core_gateway`` (page or deployment) switches the delivery on."""
    return isinstance(value, (str, bool, int)) and str(value).strip().lower() in ON_VALUES


def _status(exc: BaseException) -> Any:
    return getattr(getattr(exc, "response", None), "status_code", None) or "-"


@contextmanager
def agent_lock(conn: Any, agent_id: Any, *, wait: float = LOCK_WAIT_SECONDS) -> Iterator[None]:
    """Hold the agent's push lock (a session advisory lock on ``conn``) for the block; released after it.

    ``agent_id`` is the agent's UUID (one lock per agent, whatever its spelling). Waits at most ``wait`` seconds for
    another push of the agent, then raises ``TimeoutError``: a page never hangs behind a stuck push.
    """
    name = _AGENT_LOCK_PREFIX + str(agent_id)
    deadline = time.monotonic() + max(0.0, float(wait))
    while True:
        row = conn.execute("SELECT pg_try_advisory_lock(hashtextextended(%s, 0))", (name,)).fetchone()
        if row and row[0]:
            break
        if time.monotonic() >= deadline:
            raise TimeoutError("the agent's push lock is held")
        time.sleep(0.1)
    try:
        yield
    finally:
        try:
            conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (name,))
        except Exception as exc:  # noqa: BLE001 - a broken connection has released it already
            logger.warning("Agent claims: lock of agent %s not released (%s); it ends with the connection",
                           agent_id, type(exc).__name__)


class AgentDelivery:
    """Fills the Core's pools of external agents' claims (CR-R5), behind the switch ``agents.core_gateway``.

    Args:
        client: the Platform's ``KnovasAPIClient`` (``put_agent_claims``, ``delete_agent_claims``).
        signer: the broker's ``AssertionSigner`` (the key Knovas registered for this tenant).
        tenant_id: the tenant the claims are for (``tid``).
        setting: gives the switch's value now (the Resolver's ``value(SETTING)``).
        connect: opens a connection of the delivery's own (autocommit); used when a call is given none.
    """

    def __init__(self, *, client: Any, signer: Any, tenant_id: str, setting: Callable[[], Any],
                 connect: Callable[[], Any]) -> None:
        self._client = client
        self._signer = signer
        self._tenant_id = str(tenant_id)
        self._setting = setting
        self._connect = connect

    def on(self) -> bool:
        """The switch, read now. Whatever cannot be read counts as off."""
        try:
            return gateway_on(self._setting())
        except Exception as exc:  # noqa: BLE001 - a switch that cannot be read is off
            logger.warning("Agent claims: the switch is not readable (%s); nothing is sent", type(exc).__name__)
            return False

    @contextmanager
    def _connection(self, conn: Any) -> Iterator[Any]:
        if conn is not None:
            yield conn
            return
        own = self._connect()
        try:
            yield own
        finally:
            try:
                own.close()
            except Exception:  # noqa: BLE001
                pass

    def sync(self, conn: Any = None) -> Optional[Dict[str, int]]:
        """Send for every external agent now. None when the switch is off or the agents cannot be read.

        Never raises. Returns the counts (``sent``, ``dropped``, ``failed``).
        """
        if not self.on():
            return None
        counts = {SENT: 0, DROPPED: 0, FAILED: 0}
        try:
            with self._connection(conn) as c:
                externals = [a.id for a in AgentStore(c).list_agents() if a.kind == EXTERNAL]
                for agent_id in externals:
                    try:
                        outcome = self._deliver_one(c, agent_id)
                    except Exception as exc:  # noqa: BLE001 - one agent never stops the others
                        logger.warning("Agent claims: delivery for agent %s stopped (%s)", agent_id,
                                       type(exc).__name__)
                        outcome = FAILED
                    if outcome in counts:
                        counts[outcome] += 1
        except Exception as exc:  # noqa: BLE001 - never into the job or a page
            logger.warning("Agent claims: the sync stopped (%s)", type(exc).__name__)
            return None
        logger.info("Agent claims: pools sent for %d agents, dropped for %d, %d failed",
                    counts[SENT], counts[DROPPED], counts[FAILED])
        return counts

    def deliver(self, agent_id: Any, conn: Any = None) -> Optional[str]:
        """Send for one agent now (after a change on the agent pages is committed). None when off.

        Never raises. Returns ``sent``, ``dropped``, ``failed`` or ``skipped`` (the assistant, an unknown id).
        """
        if not self.on():
            return None
        try:
            with self._connection(conn) as c:
                outcome = self._deliver_one(c, agent_id)
        except Exception as exc:  # noqa: BLE001 - never into a page
            logger.warning("Agent claims: delivery for agent %s stopped (%s)", agent_id, type(exc).__name__)
            return FAILED
        if outcome in (SENT, DROPPED):
            logger.info("Agent claims: agent %s %s at once", agent_id, outcome)
        return outcome

    def _signed_pool(self, conn: Any, agent_id: Any) -> Optional[list]:
        """The agent's pool, signed now; None when it has no claim (revoked, person inactive, over 32 groups)."""
        from identity.principal import PrincipalBroker
        from identity.users import UserRepository

        broker = PrincipalBroker(user_repo=UserRepository(conn), signer=self._signer, tenant_id=self._tenant_id,
                                 agents=AgentStore(conn))
        try:
            return broker.agent_assertions(agent_id, count=POOL_SIZE, lifetime_seconds=CLAIM_LIFETIME_SECONDS)
        except (AgentError, RoleError):
            return None
        except Exception as exc:  # noqa: BLE001 - a claim that cannot be made now is no claim: the pool is dropped
            logger.warning("Agent claims: no claim could be made for agent %s (%s); its pool is dropped",
                           agent_id, type(exc).__name__)
            return None

    def _deliver_one(self, conn: Any, agent_id: Any) -> str:
        ident = _uuid(agent_id)
        if ident is None:
            return SKIPPED
        with agent_lock(conn, ident):
            try:
                agent = AgentStore(conn).get_agent(ident)
            except AgentNotFound:
                return SKIPPED
            if agent.kind != EXTERNAL:  # the assistant goes through the Platform
                return SKIPPED
            pool = self._signed_pool(conn, agent.id)
            try:
                if pool:
                    self._client.put_agent_claims(str(agent.id), pool, timeout=PUSH_TIMEOUT_SECONDS)
                    return SENT
                self._client.delete_agent_claims(str(agent.id), timeout=PUSH_TIMEOUT_SECONDS)
                return DROPPED
            except Exception as exc:  # noqa: BLE001 - the Core may lack CR-R5, refuse, or be away
                logger.warning("Agent claims: the Core did not take the %s for agent %s (HTTP %s, %s)",
                               "pool" if pool else "drop", agent.id, _status(exc), type(exc).__name__)
                return FAILED


class AgentSyncJob:
    """Calls ``sync(conn)`` every ``interval`` seconds, in one worker at a time, on a connection of its own.

    Each tick: while ``enabled()`` is false (the switch is off) it holds no connection and no lock. Otherwise it
    keeps one connection, tries the session advisory lock ``lock_name`` on it until it holds it, and while it holds
    it, pushes. A lost connection gives the lock up; the next tick reconnects and tries again, so when the pushing
    worker ends another takes over within one interval. ``keep_running()`` false ends the thread at its next tick
    (a test app: its tests call ``sync`` themselves). The first tick is one interval after ``start``.
    """

    def __init__(self, *, sync: Callable[[Any], Any], connect: Callable[[], Any], enabled: Callable[[], bool],
                 interval: float = SYNC_INTERVAL_SECONDS, lock_name: str = LEADER_LOCK,
                 keep_running: Callable[[], bool] = lambda: True,
                 wait: Optional[Callable[[float], bool]] = None) -> None:
        self._sync = sync
        self._connect = connect
        self._enabled = enabled
        self.interval = float(interval)
        self.lock_name = str(lock_name)
        self._keep_running = keep_running
        self._halt = threading.Event()
        self._wait = wait or self._halt.wait
        self._conn: Any = None
        self.leader = False
        self._thread: Optional[threading.Thread] = None

    # -- one tick ------------------------------------------------------------------------------------------------

    def _connection(self) -> Any:
        conn = self._conn
        if conn is not None and not getattr(conn, "closed", False) and not getattr(conn, "broken", False):
            return conn
        self._drop()
        conn = self._connect()
        if not getattr(conn, "autocommit", True):
            conn.autocommit = True
        self._conn = conn
        return conn

    def _drop(self) -> None:
        """Close the job's connection: its session lock ends with it."""
        conn, self._conn, self.leader = self._conn, None, False
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    def run_once(self) -> bool:
        """One tick. True when this job holds the lock and pushed. Never raises."""
        try:
            if not self._enabled():
                self._drop()
                return False
            conn = self._connection()
            if not self.leader:
                row = conn.execute("SELECT pg_try_advisory_lock(hashtextextended(%s, 0))",
                                   (self.lock_name,)).fetchone()
                self.leader = bool(row and row[0])
                if not self.leader:
                    return False
            self._sync(conn)
            return True
        except Exception as exc:  # noqa: BLE001 - the job must not die; the next tick starts afresh
            logger.warning("Agent claims: the job's tick failed (%s); it reconnects at the next one",
                           type(exc).__name__)
            self._drop()
            return False

    # -- the thread ----------------------------------------------------------------------------------------------

    def run(self) -> None:
        try:
            while not self._wait(self.interval):
                if self._halt.is_set() or not self._keep_running():
                    break
                self.run_once()
        finally:
            self._drop()

    def start(self) -> "AgentSyncJob":
        self._thread = threading.Thread(target=self.run, name="agent-claims", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._halt.set()


def install_agent_delivery(app: Any, *, gate: Any, client: Any, signer: Any, tenant_id: str, settings: Any,
                           start_job: bool = True) -> AgentDelivery:
    """``app.extensions["agents"]`` and the job (one call in app.py, with accounts on).

    ``app.extensions["agents"]``: ``sync()`` sends for every external agent now (the frozen seam), ``deliver(agent
    id)`` for one, ``on()`` reads the switch, ``job`` is the running ``AgentSyncJob`` (None when not started).
    Nothing is sent here: the job's first push is one interval after the start.
    """
    delivery = AgentDelivery(client=client, signer=signer, tenant_id=tenant_id,
                             setting=lambda: settings.value(SETTING), connect=gate.open_connection)
    job = None
    if start_job:
        job = AgentSyncJob(sync=lambda conn: delivery.sync(conn), connect=gate.open_connection,
                           enabled=delivery.on, keep_running=lambda: not getattr(app, "testing", False)).start()
    app.extensions["agents"] = {
        "sync": lambda: delivery.sync(),
        "deliver": lambda agent_id: delivery.deliver(agent_id),
        "on": delivery.on,
        "delivery": delivery,
        "job": job,
    }
    return delivery


__all__ = (
    "CLAIM_LIFETIME_SECONDS", "LEADER_LOCK", "LOCK_WAIT_SECONDS", "POOL_SIZE", "PUSH_TIMEOUT_SECONDS", "SETTING", "SWITCH_ENV",
    "SYNC_INTERVAL_SECONDS", "AgentDelivery", "AgentSyncJob", "agent_lock", "gateway_on", "install_agent_delivery",
)
