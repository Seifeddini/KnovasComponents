"""The assistant's two callables, built from ``KnovasAPIClient`` (PMVP-20).

PMVP-22a only wires them, per request or once per app::

    from assistant.core import make_generate, make_search
    from assistant.service import AssistantService

    service = AssistantService(
        search=make_search(
            api_client,
            split=app.extensions["experiments"]["search"].split,
            user_key=lambda: doc_fields_routes.user_key_for(identity_gate),
        ),
        generate=make_generate(api_client),
    )

**search** is ``/api/search``'s own path and no other (POST-02):
``doc_fields_routes.search_plan`` and ``run_search`` into
``KnovasAPIClient.search_documents`` -> ``POST /secured/query``, the person's
principal assertion attached by the client; the request Knovas receives is
the page's. Two things differ, both on the Platform side of the answer: the
rows keep the Core's passage text (``client.keeping_snippets()``), and of
what app.py does after Knovas answers only the experiments split runs here
-- the page's enrichment, its client-side score thresholds (off unless
configured) and its filename supplement stay in app.py. Experiment documents are taken
out exactly as the search page takes them out: only people allowed to see
them may learn they exist, and their rows here would carry Knovas text.

The assistant needs the secured API (mTLS): the legacy GET search logs the
question and the Core's generation route exists only there. Outside it,
``search`` fails closed with ``CoreFailed``.

**generate** is ``KnovasAPIClient.llm_generate`` (``POST
/secured/llm/generate``, never retried, no body logged); it returns the
answer text only, so the model's name never reaches the service.

HTTP 429 and 503 become ``CoreBusy``, any other HTTP error ``CoreFailed``;
whatever else is raised reaches the service, which answers ``failed``.

**One deadline per answer** (PMVP-24, FU-08). The search with its retries and
the generation end within ``ANSWER_DEADLINE_SECONDS`` (150) of the route's
start, under nginx's and gunicorn's 180: the route starts a ``Deadline`` and
``make_service`` hands it to ``make_search`` and the generate. The search runs
inside ``client.within_time(deadline.left)``, so each attempt reads at most the
time left and a retry happens only if time is left for it; the generation is
not started with less than ``MIN_GENERATE_SECONDS`` (30) left, and its timeout
is at most the time left (the Core's own 120 s; Claude's 60 s,
``providers.make_anthropic_generate``). **A timeout is busy** (decision D1):
the search's or the generation's timeout raises ``CoreBusy``, so the service
answers "ausgelastet"; any other failure stays ``failed``. The one log line of
a deadline says which step ran out of time and the durations, nothing else.

**make_service** (PMVP-25) is the switch and the provider the customer chose:
``OFF`` while ``assistant.enabled`` is off, else the service with ``make_search``
and the generate of ``assistant.provider`` (``knovas``: ``make_generate``;
``anthropic``: ``assistant.providers.make_anthropic_generate``), all read
through the settings' Resolver::

    service = make_service(app.extensions["platform_settings"], api_client,
                           split=app.extensions["experiments"]["search"].split,
                           user_key=lambda: doc_fields_routes.user_key_for(identity_gate))
    if service is OFF:
        ...  # the route refuses
"""

from __future__ import annotations

import contextlib
import logging
import time
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple, Union

import requests

from assistant.errors import CoreBusy, CoreFailed

logger = logging.getLogger(__name__)

#: The whole answer -- the search with its retries and the generation -- ends within this many seconds of the
#: route's start (PMVP-24, decision D2): under nginx's ``proxy_read_timeout`` and gunicorn's ``--timeout`` (180 s),
#: so the person gets the answer or the plain message, never the web server's 504.
ANSWER_DEADLINE_SECONDS = 150.0
#: The generation is not started with less than this many seconds left: it would only run out of time.
MIN_GENERATE_SECONDS = 30.0
#: The reasons of a ``CoreFailed`` that are a timeout: the Core's generation (``knovas_client.llm_generate``) and
#: Claude's (``providers``). They are read as busy (D1).
TIMEOUT_REASONS = frozenset({"timeout", "anthropic_timeout"})


class Deadline:
    """The time one answer has: ``seconds`` from ``started`` (a ``clock()`` reading; by default now).

    The route starts it when the question arrives; ``left()`` is what remains. ``clock`` is ``time.monotonic``;
    a test passes its own.
    """

    def __init__(self, seconds: float = ANSWER_DEADLINE_SECONDS, *, clock: Callable[[], float] = time.monotonic,
                 started: Optional[float] = None) -> None:
        self.seconds = float(seconds)
        self.clock = clock
        self.started = clock() if started is None else float(started)

    def elapsed(self) -> float:
        return self.clock() - self.started

    def left(self) -> float:
        return self.seconds - self.elapsed()


def _ms(seconds: float) -> int:
    return int(round(seconds * 1000))


def _within(client: Any, deadline: Optional[Deadline]):
    """``client.within_time(deadline.left)``, or nothing without a deadline or on a client that has no such bound."""
    bound = getattr(client, "within_time", None)
    if deadline is None or not callable(bound):
        return contextlib.nullcontext()
    return bound(deadline.left)


def _timed_out(exc: BaseException) -> bool:
    """A search's timeout: ``requests``' own, or tenacity giving up after attempts that each timed out."""
    if isinstance(exc, requests.exceptions.Timeout):
        return True
    last = getattr(exc, "last_attempt", None)
    try:
        failure = last.exception() if last is not None else None
    except Exception:  # noqa: BLE001 - an attempt without an exception is no timeout
        failure = None
    return isinstance(failure, requests.exceptions.Timeout)

#: HTTP answers that mean "busy, ask again later".
BUSY_STATUSES = frozenset({429, 503})
#: Sampling temperature for answers grounded in passages: low, so the model
#: stays close to the text.
DEFAULT_TEMPERATURE = 0.2

SplitFn = Callable[[Any], Tuple[Any, List[Dict[str, Any]]]]
UserKey = Union[None, str, Callable[[], Optional[str]]]


def make_search(client: Any, *, split: SplitFn, user_key: UserKey = None, enabled: bool = True,
                deadline: Optional[Deadline] = None) -> Callable[[str, int], List[Dict[str, Any]]]:
    """``search(question, limit) -> rows`` for ``AssistantService``.

    ``split`` is ``SearchIntegration.split`` (``app.extensions["experiments"]
    ["search"].split``), required so no wiring can forget it. ``user_key`` is
    the document-fields cache key the search page uses
    (``doc_fields_routes.user_key_for(identity_gate)``), or a callable giving
    it per call; ``enabled`` is ``search_plan``'s switch (the search page
    turns it off only for its local test fixtures). ``deadline`` (PMVP-24)
    bounds the whole search, its retries included, through
    ``client.within_time``; its timeout raises ``CoreBusy``.
    """
    if not callable(split):
        raise TypeError("split must be SearchIntegration.split")
    # Imported here, not at module level: importing web_interface imports the
    # app, and app.py may import this module.
    from web_interface import doc_fields_routes

    def search(question: str, limit: int) -> List[Dict[str, Any]]:
        if not _secured(client):
            logger.warning("Knovas Assistent: search refused, the secured API is not configured")
            raise CoreFailed("secured_api_required")
        key = user_key() if callable(user_key) else user_key
        failure: Optional[Exception] = None
        with _within(client, deadline):
            try:
                plan = doc_fields_routes.search_plan(client, None, key, enabled=enabled)
                if plan.refusal is not None:  # only a filter is refused, and none is sent
                    raise CoreFailed("search_refused")
                with client.keeping_snippets():
                    answer = doc_fields_routes.run_search(
                        client, plan, query=question, limit=limit, filters={})
            except requests.exceptions.HTTPError as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status in BUSY_STATUSES:
                    raise CoreBusy(status=status) from None
                raise CoreFailed("search_http_error", status=status) from None
            except Exception as exc:  # noqa: BLE001 - a timeout is busy; anything else goes on as it was
                if not _timed_out(exc):
                    raise
                failure = CoreBusy("search_timeout")
        if failure is not None:
            # Raised here, outside the handler: the timeout is neither its cause nor its context.
            if deadline is not None:
                logger.warning("Knovas Assistent: deadline step=search ran out of time elapsed_ms=%d left_ms=%d",
                               _ms(deadline.elapsed()), _ms(deadline.left()))
            else:
                logger.warning("Knovas Assistent: deadline step=search timed out")
            raise failure
        answer, _experiment_hits = split(answer)
        rows = answer.get("results") if isinstance(answer, Mapping) else None
        if not isinstance(rows, list):
            return []
        return [row for row in rows if isinstance(row, dict)]

    return search


def make_generate(client: Any, *, temperature: float = DEFAULT_TEMPERATURE,
                  timeout: Optional[float] = None, deadline: Optional[Deadline] = None) -> Callable[..., str]:
    """``generate(*, system_instruction, messages, max_tokens) -> str`` for
    ``AssistantService``: ``client.llm_generate``, the answer text only.

    With a ``deadline`` (PMVP-24) the call runs inside ``client.within_time``,
    so its timeout (the Core's 120 s) is at most the time left, and it is not
    started with less than ``MIN_GENERATE_SECONDS`` left (``in_time``). A
    timeout is ``CoreBusy`` either way.
    """

    def generate(*, system_instruction: str, messages: List[Dict[str, str]],
                 max_tokens: int) -> str:
        with _within(client, deadline):
            result = client.llm_generate(
                messages,
                system_instruction=system_instruction,
                max_tokens=max_tokens,
                temperature=temperature,
                timeout=timeout,
            )
        return result["content"]

    return in_time(generate, deadline)


def in_time(generate: Callable[..., str], deadline: Optional[Deadline]) -> Callable[..., str]:
    """``generate`` within the answer's deadline (PMVP-24): not started with less than ``MIN_GENERATE_SECONDS``
    left (``CoreBusy``, nothing sent), and its timeout (``TIMEOUT_REASONS``) read as busy (D1). Never retried."""

    def bounded(*, system_instruction: str, messages: List[Dict[str, str]], max_tokens: int) -> str:
        if deadline is not None and deadline.left() < MIN_GENERATE_SECONDS:
            logger.warning("Knovas Assistent: deadline step=generation not started elapsed_ms=%d left_ms=%d",
                           _ms(deadline.elapsed()), _ms(deadline.left()))
            raise CoreBusy("no_time_to_generate")
        started = deadline.clock() if deadline is not None else time.monotonic()
        failure: Optional[CoreBusy] = None
        try:
            return generate(system_instruction=system_instruction, messages=messages, max_tokens=max_tokens)
        except CoreFailed as exc:
            if exc.reason not in TIMEOUT_REASONS:
                raise
            failure = CoreBusy("generation_timeout")
        took = (deadline.clock() if deadline is not None else time.monotonic()) - started
        logger.warning("Knovas Assistent: deadline step=generation ran out of time generate_ms=%d elapsed_ms=%d",
                       _ms(took), _ms(deadline.elapsed()) if deadline is not None else _ms(took))
        # Raised here, outside the handler: the timeout is neither its cause nor its context.
        raise failure

    return bounded


#: What ``make_service`` returns while the assistant is switched off (``assistant.enabled``): its routes refuse.
OFF = "off"


def make_service(settings: Any, client: Any, *, split: SplitFn, user_key: UserKey = None,
                 anthropic_client: Optional[Callable[..., Any]] = None,
                 deadline: Optional[Deadline] = None) -> Any:
    """The assistant as the settings say now (PMVP-25): ``OFF``, or an ``AssistantService``.

    ``settings`` is the installation's ``platform_settings.Resolver`` (``app.extensions["platform_settings"]``);
    build the service per request, so a change on the settings page applies without a restart. Off
    (``assistant.enabled``), nothing is built and nothing is searched. On, the search is ``make_search`` whatever
    the provider (POST-02), and ``assistant.provider`` chooses the generate: ``knovas`` is ``make_generate``, the
    Core's call exactly as before; ``anthropic`` is ``providers.make_anthropic_generate`` on the key and the model
    of the settings, read on every call. A provider that is not offered fails every answer (``failed``).
    ``anthropic_client`` replaces the SDK's client factory (tests). ``deadline`` (PMVP-24) is the answer's, started
    by the route; without one the search and the generation wait as long as before, and a timeout is busy all the
    same.
    """
    from assistant import providers
    from assistant.service import AssistantService

    if not providers.is_enabled(settings.value(providers.SETTING_ENABLED)):
        return OFF
    provider = providers.chosen_provider(settings.value(providers.SETTING_PROVIDER))
    if provider == providers.PROVIDER_KNOVAS:
        generate = make_generate(client, deadline=deadline)
    elif provider == providers.PROVIDER_ANTHROPIC:
        generate = in_time(providers.make_anthropic_generate(
            api_key=lambda: settings.secret(providers.SETTING_API_KEY),
            model=lambda: settings.value(providers.SETTING_MODEL),
            client_factory=anthropic_client,
            time_left=deadline.left if deadline is not None else None,
        ), deadline)
    else:
        generate = providers.unavailable_generate("provider_unknown")
    return AssistantService(search=make_search(client, split=split, user_key=user_key, deadline=deadline),
                            generate=generate)


def _secured(client: Any) -> bool:
    probe = getattr(client, "secured_mode", None)
    if not callable(probe):
        return False
    try:
        return bool(probe())
    except Exception:  # noqa: BLE001 - unknown means not secured
        return False
