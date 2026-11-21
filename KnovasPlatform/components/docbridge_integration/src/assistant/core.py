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

import logging
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple, Union

import requests

from assistant.errors import CoreBusy, CoreFailed

logger = logging.getLogger(__name__)

#: HTTP answers that mean "busy, ask again later".
BUSY_STATUSES = frozenset({429, 503})
#: Sampling temperature for answers grounded in passages: low, so the model
#: stays close to the text.
DEFAULT_TEMPERATURE = 0.2

SplitFn = Callable[[Any], Tuple[Any, List[Dict[str, Any]]]]
UserKey = Union[None, str, Callable[[], Optional[str]]]


def make_search(client: Any, *, split: SplitFn, user_key: UserKey = None,
                enabled: bool = True) -> Callable[[str, int], List[Dict[str, Any]]]:
    """``search(question, limit) -> rows`` for ``AssistantService``.

    ``split`` is ``SearchIntegration.split`` (``app.extensions["experiments"]
    ["search"].split``), required so no wiring can forget it. ``user_key`` is
    the document-fields cache key the search page uses
    (``doc_fields_routes.user_key_for(identity_gate)``), or a callable giving
    it per call; ``enabled`` is ``search_plan``'s switch (the search page
    turns it off only for its local test fixtures).
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
        plan = doc_fields_routes.search_plan(client, None, key, enabled=enabled)
        if plan.refusal is not None:  # only a filter is refused, and none is sent
            raise CoreFailed("search_refused")
        try:
            with client.keeping_snippets():
                answer = doc_fields_routes.run_search(
                    client, plan, query=question, limit=limit, filters={})
        except requests.exceptions.HTTPError as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in BUSY_STATUSES:
                raise CoreBusy(status=status) from None
            raise CoreFailed("search_http_error", status=status) from None
        answer, _experiment_hits = split(answer)
        rows = answer.get("results") if isinstance(answer, Mapping) else None
        if not isinstance(rows, list):
            return []
        return [row for row in rows if isinstance(row, dict)]

    return search


def make_generate(client: Any, *, temperature: float = DEFAULT_TEMPERATURE,
                  timeout: Optional[float] = None) -> Callable[..., str]:
    """``generate(*, system_instruction, messages, max_tokens) -> str`` for
    ``AssistantService``: ``client.llm_generate``, the answer text only."""

    def generate(*, system_instruction: str, messages: List[Dict[str, str]],
                 max_tokens: int) -> str:
        result = client.llm_generate(
            messages,
            system_instruction=system_instruction,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout,
        )
        return result["content"]

    return generate


#: What ``make_service`` returns while the assistant is switched off (``assistant.enabled``): its routes refuse.
OFF = "off"


def make_service(settings: Any, client: Any, *, split: SplitFn, user_key: UserKey = None,
                 anthropic_client: Optional[Callable[..., Any]] = None) -> Any:
    """The assistant as the settings say now (PMVP-25): ``OFF``, or an ``AssistantService``.

    ``settings`` is the installation's ``platform_settings.Resolver`` (``app.extensions["platform_settings"]``);
    build the service per request, so a change on the settings page applies without a restart. Off
    (``assistant.enabled``), nothing is built and nothing is searched. On, the search is ``make_search`` whatever
    the provider (POST-02), and ``assistant.provider`` chooses the generate: ``knovas`` is ``make_generate``, the
    Core's call exactly as before; ``anthropic`` is ``providers.make_anthropic_generate`` on the key and the model
    of the settings, read on every call. A provider that is not offered fails every answer (``failed``).
    ``anthropic_client`` replaces the SDK's client factory (tests).
    """
    from assistant import providers
    from assistant.service import AssistantService

    if not providers.is_enabled(settings.value(providers.SETTING_ENABLED)):
        return OFF
    provider = providers.chosen_provider(settings.value(providers.SETTING_PROVIDER))
    if provider == providers.PROVIDER_KNOVAS:
        generate = make_generate(client)
    elif provider == providers.PROVIDER_ANTHROPIC:
        generate = providers.make_anthropic_generate(
            api_key=lambda: settings.secret(providers.SETTING_API_KEY),
            model=lambda: settings.value(providers.SETTING_MODEL),
            client_factory=anthropic_client,
        )
    else:
        generate = providers.unavailable_generate("provider_unknown")
    return AssistantService(search=make_search(client, split=split, user_key=user_key), generate=generate)


def _secured(client: Any) -> bool:
    probe = getattr(client, "secured_mode", None)
    if not callable(probe):
        return False
    try:
        return bool(probe())
    except Exception:  # noqa: BLE001 - unknown means not secured
        return False
