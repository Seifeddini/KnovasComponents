"""The language model the customer chooses for the Knovas Assistent (PMVP-25).

Founder, 2026-11-21: the Platform calls the language model itself, and the customer chooses the provider on the
Platform's settings page. Four installation-wide settings of the registry (``platform_settings.REGISTRY``) decide,
read through its ``Resolver`` per request, so a change applies without a restart:

- ``assistant.enabled``: ``an`` or ``aus`` (``aus`` by default; ``ASSISTANT_ENABLED`` pins it). Off,
  ``core.make_service`` returns ``core.OFF`` and nothing is searched or generated.
- ``assistant.provider``: ``knovas`` (the Core's ``/secured/llm/generate``, ``core.make_generate``, unchanged) or
  ``anthropic`` (Claude, ``make_anthropic_generate`` here).
- ``assistant.model``: with ``anthropic``, one of ``ANTHROPIC_MODELS`` (``claude-opus-5-5`` by default).
- ``assistant.anthropic_api_key``: the secret, sealed by the registry's ``SecretBox``.

``make_anthropic_generate`` gives the same ``generate(*, system_instruction, messages, max_tokens) -> str`` the
service takes from ``core.make_generate``, so the service and the check do not change. One request with the
official SDK (``client.beta.messages.create``): effort ``medium``, the server-side fallback on a policy decline,
no temperature (the current models refuse it) and no thinking setting (adaptive is the default and cannot be
switched off on Opus 5.5). ``max_tokens`` is at least ``ANTHROPIC_MIN_MAX_TOKENS``: thinking counts against it, and
a cut answer loses statements. The client waits ``ANTHROPIC_TIMEOUT_SECONDS`` and never retries (the coordinator,
2026-11-21): a 429 or 529 is "ausgelastet" at once and the person asks again, so no ``retry-after`` wait can outlast
nginx's 180 seconds (FU-08).

What the provider receives is what the service composed and nothing else (POST-02): the system instruction and
the messages, each reduced to its role and its content (the passages the person's search returned, the question
and its history).

The answer: the text is the ``text`` blocks joined. ``stop_reason == "refusal"`` -> ``CoreFailed``;
``max_tokens`` -> the text as it is (the check drops a cut last line). Errors: ``RateLimitError`` and HTTP 529 ->
``CoreBusy`` ("gerade ausgelastet"); the key refused (401, 403), any other HTTP error, no connection and a
timeout -> ``CoreFailed``. The mapped error is raised outside the SDK's ``except`` block, so it carries neither
the SDK's exception (whose request holds the key in a header) as its cause nor as its context.

The key comes from the resolved setting, passed to the client per call. This module never reads it from a file,
never logs it and never puts it into an exception or a page. Without a key no client is built (the SDK would
otherwise take credentials from the environment or a profile on disk) and the call fails with ``CoreFailed``. The
client always talks to ``ANTHROPIC_BASE_URL``, whatever the environment's ``ANTHROPIC_BASE_URL`` says.

This module's log lines carry the model's name, the stop reason, token counts, durations and exception class names
only (POST-04). The SDK's and the HTTP libraries' own loggers would print a request's body (the instruction, the
passages, the question) and a transport error's text at DEBUG; the app holds them at INFO or above whatever
``LOG_LEVEL`` says (``web_interface.app._configure_logging_for_wsgi``, FU-39).

``connection_test`` is the settings page's "Verbindung testen": one call with an invented passage and question.

<<Brain-Werkzeuge>> (PMVP-20 brain-mcp): with ``assistant.mode`` ``brain_mcp`` (only with ``anthropic``),
``make_brain_mcp_ask`` calls Claude with the MCP connector against the Knovas MCP gateway
(``assistant.brain_mcp_url``, ``https://mcp.knovas.ch/mcp`` by default, only under ``knovas.ch``) with the
company's gateway key (``assistant.brain_mcp_key``, a secret) as the server's ``authorization_token``: the same
request as above plus ``mcp_servers``, an ``mcp_toolset`` that allows only ``search`` and ``fetch``, and the beta
``mcp-client-2025-11-20``; within a wall-clock deadline (PMVP-24's numbers). ``assistant.brain_tools`` reads the
tool results and checks the answer. ``settings_problems``, ``settings_warnings`` and ``brain_mcp_refusal`` are the
settings page's lines and its refusal. Then Anthropic receives the gateway key with every question and calls the
gateway with it; the gateway reads with the company's key, not as the person.

<<Wissensgraph>> (PMVP-20 brain-graph): with ``assistant.mode`` ``brain_graph`` (only with ``anthropic``),
``make_brain_graph_turn`` makes one request of a tool loop with client tools (``assistant.brain_graph`` runs the loop,
``assistant.graph_reads`` the reads of the tenant's knowledge graph through the Core): no MCP server, no second key.

The SDK is imported when a call is made, not when this module is imported: the settings page imports this module.
This module is ASCII-only: umlauts are ``\\u`` escapes.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Callable, Dict, List, Optional, Union

from assistant.errors import CoreBusy, CoreError, CoreFailed

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# The settings (platform_settings.REGISTRY) and their values
# ---------------------------------------------------------------------------

SETTING_ENABLED = "assistant.enabled"
SETTING_PROVIDER = "assistant.provider"
SETTING_MODEL = "assistant.model"
SETTING_API_KEY = "assistant.anthropic_api_key"
#: \u00abBrain-Werkzeuge\u00bb (PMVP-20 brain-mcp): the answer mode, the gateway's address and the company's gateway key.
SETTING_MODE = "assistant.mode"
SETTING_BRAIN_MCP_URL = "assistant.brain_mcp_url"
SETTING_BRAIN_MCP_KEY = "assistant.brain_mcp_key"

MODE_PASSAGES = "passages"
MODE_BRAIN_MCP = "brain_mcp"
#: \u00abWissensgraph\u00bb (PMVP-20 brain-graph): Claude reads the tenant's knowledge graph with tools the Platform runs.
MODE_BRAIN_GRAPH = "brain_graph"
MODES = (MODE_PASSAGES, MODE_BRAIN_MCP, MODE_BRAIN_GRAPH)

PROVIDER_KNOVAS = "knovas"
PROVIDER_ANTHROPIC = "anthropic"
PROVIDERS = (PROVIDER_KNOVAS, PROVIDER_ANTHROPIC)

#: The models the customer may choose. ``claude-haiku-4-5`` (previous generation, still served) is offered as an
#: approximate stand-in for a smaller, self-hosted open model (the founder, 2026-11-28): it is asked without the
#: fields it does not take (``model_fields``).
ANTHROPIC_MODELS = ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-haiku-4-5")
#: The offered models of the previous generation: no ``output_config.effort``, no server-side refusal fallback (no
#: ``fallbacks``, no fallback beta) and no adaptive thinking (no thinking setting is sent to any model; extended
#: thinking would need ``budget_tokens`` and stays off). 200K context, 64K output; tool use as the others.
PREVIOUS_GENERATION_MODELS = frozenset({"claude-haiku-4-5"})
DEFAULT_ANTHROPIC_MODEL = "claude-opus-5-5"

#: What switches the assistant on. The page offers "an" and "aus"; a deployment's ASSISTANT_ENABLED may also say
#: true, on, yes, ja or 1. Anything else is off.
ON_VALUES = frozenset({"an", "on", "true", "yes", "ja", "1"})

# ---------------------------------------------------------------------------
# The Anthropic call
# ---------------------------------------------------------------------------

#: The only address the client talks to: an ANTHROPIC_BASE_URL in the environment is not honoured (FU-39).
ANTHROPIC_BASE_URL = "https://api.anthropic.com"
ANTHROPIC_TIMEOUT_SECONDS = 60.0
#: No retry: the SDK would wait whatever ``retry-after`` a 429 or 529 names, past nginx's 180 seconds.
ANTHROPIC_MAX_RETRIES = 0
#: The least ``max_tokens`` on this path: thinking counts against it (the probe used about 200 tokens per answer,
#: but a cut answer loses statements). The service asks for less (``service.DEFAULT_MAX_TOKENS``, sized for the
#: Core's model); here it is raised to this.
ANTHROPIC_MIN_MAX_TOKENS = 8_000
ANTHROPIC_EFFORT = "medium"
ANTHROPIC_BETAS = ("server-side-fallback-2026-07-01",)
ANTHROPIC_FALLBACKS = "default"


def model_fields(name: str, extra_betas: tuple = ()) -> Dict[str, Any]:
    """The request fields that depend on the model, in the order every request sends them: ``output_config`` (effort
    medium), ``betas`` (the server-side fallback's, then ``extra_betas``) and ``fallbacks``. A model of
    ``PREVIOUS_GENERATION_MODELS`` takes none of the three: it gets only ``betas`` with ``extra_betas`` (the MCP
    connector's), and nothing when there are none. The current models' requests are exactly as before."""
    if name in PREVIOUS_GENERATION_MODELS:
        return {"betas": list(extra_betas)} if extra_betas else {}
    return {"output_config": {"effort": ANTHROPIC_EFFORT}, "betas": list(ANTHROPIC_BETAS) + list(extra_betas),
            "fallbacks": ANTHROPIC_FALLBACKS}
#: HTTP answers that mean "busy, ask again later": rate limited, overloaded.
BUSY_STATUSES = frozenset({429, 529})

ClientFactory = Callable[..., Any]
Value = Union[None, str, Callable[[], Any]]
GenerateFn = Callable[..., str]


def is_enabled(value: Any) -> bool:
    """Whether a value of ``assistant.enabled`` (page or deployment) switches the assistant on."""
    return isinstance(value, (str, bool, int)) and str(value).strip().lower() in ON_VALUES


def chosen_provider(value: Any) -> Optional[str]:
    """The provider a value of ``assistant.provider`` names, or None for one that is not offered."""
    text = str(value).strip().lower() if isinstance(value, str) else ""
    return text if text in PROVIDERS else None


def chosen_mode(value: Any) -> Optional[str]:
    """The answer mode a value of ``assistant.mode`` names, or None for one that is not offered."""
    text = str(value).strip().lower() if isinstance(value, str) else ""
    return text if text in MODES else None


def make_anthropic_generate(*, api_key: Value, model: Value = DEFAULT_ANTHROPIC_MODEL,
                            client_factory: Optional[ClientFactory] = None,
                            time_left: Optional[Callable[[], float]] = None) -> GenerateFn:
    """``generate(*, system_instruction, messages, max_tokens) -> str`` through Claude.

    ``api_key`` and ``model`` are values or callables giving them; callables are asked on every call (the
    settings page's values, read through the Resolver). ``client_factory(api_key=..., timeout=...,
    max_retries=...)`` builds the client; by default ``anthropic.Anthropic``. Tests pass a fake. ``time_left``
    (PMVP-24): the seconds the answer's deadline leaves; the call then ends within the smaller of
    ``ANTHROPIC_TIMEOUT_SECONDS`` and that by the wall clock, however slowly Anthropic answers (the client's own
    timeout bounds each read only): it runs in a worker thread (``assistant.core.call_within``), and past its time
    the client is closed and the answer fails as a timeout, which the answer's deadline reads as busy. Without
    ``time_left``, the client waits ``ANTHROPIC_TIMEOUT_SECONDS`` per read, as before.
    """

    def generate(*, system_instruction: str, messages: List[Dict[str, str]], max_tokens: int) -> str:
        key = _text(_read(api_key))
        if not key:
            logger.warning("Knovas Assistent: provider anthropic has no API key")
            raise CoreFailed("anthropic_key_missing")
        name = _text(_read(model)) or DEFAULT_ANTHROPIC_MODEL
        if name not in ANTHROPIC_MODELS:
            logger.warning("Knovas Assistent: the model is not one of %s", ", ".join(ANTHROPIC_MODELS))
            raise CoreFailed("anthropic_model_unknown")
        request = {
            "model": name,
            "max_tokens": max(int(max_tokens), ANTHROPIC_MIN_MAX_TOKENS),
            "system": system_instruction,
            "messages": [{"role": m["role"], "content": m["content"]} for m in messages],
            **model_fields(name),
        }
        import anthropic  # the SDK; imported here so that the settings page does not load it

        from assistant.core import OutOfTime, call_within

        factory = client_factory or _sdk_client
        started = time.monotonic()
        response: Any = None
        failure: Optional[CoreError] = None
        client: Any = None
        try:
            timeout = ANTHROPIC_TIMEOUT_SECONDS if time_left is None else max(
                1.0, min(ANTHROPIC_TIMEOUT_SECONDS, float(time_left())))
            client = factory(api_key=key, timeout=timeout, max_retries=ANTHROPIC_MAX_RETRIES)
            if time_left is None:
                response = client.beta.messages.create(**request)
            else:
                response = call_within(timeout, lambda: client.beta.messages.create(**request),
                                       on_overrun=lambda: _close(client), name="anthropic")
        except anthropic.AnthropicError as exc:
            failure = _mapped(exc, anthropic)
            logger.warning("Knovas Assistent: anthropic call failed (%s%s) model=%s ms=%d",
                           type(exc).__name__, _status_note(failure), name, _ms(started))
        except OutOfTime:
            failure = CoreFailed("anthropic_timeout")
            logger.warning("Knovas Assistent: anthropic call ran out of time model=%s ms=%d", name, _ms(started))
        finally:
            _close(client)
        if failure is not None:
            # Raised here, outside the except block: the SDK's exception is neither cause nor context.
            raise failure

        stop = getattr(response, "stop_reason", None)
        usage = getattr(response, "usage", None)
        logger.info("Knovas Assistent: anthropic model=%s served_by=%s stop=%s input_tokens=%s output_tokens=%s "
                    "ms=%d", name, _safe_name(getattr(response, "model", None)), _safe_name(stop),
                    _count(getattr(usage, "input_tokens", None)), _count(getattr(usage, "output_tokens", None)),
                    _ms(started))
        if stop == "refusal":
            raise CoreFailed("anthropic_refusal")
        return "".join(
            block.text for block in (getattr(response, "content", None) or ())
            if getattr(block, "type", None) == "text" and isinstance(getattr(block, "text", None), str)
        )

    return generate


def unavailable_generate(reason: str) -> GenerateFn:
    """A generate that always fails: the settings name no provider this module offers."""

    def generate(*, system_instruction: str, messages: List[Dict[str, str]], max_tokens: int) -> str:
        logger.warning("Knovas Assistent: no usable provider (%s)", reason)
        raise CoreFailed(reason)

    return generate


# ---------------------------------------------------------------------------
# \u00abBrain-Werkzeuge\u00bb: Claude calls the Knovas MCP gateway itself (PMVP-20 brain-mcp)
# ---------------------------------------------------------------------------

#: The gateway's address by default (KnowledgeBase knovas_mcp/gateway; FEAT-2026-11-16-copilot-mcp-gateway).
DEFAULT_BRAIN_MCP_URL = "https://mcp.knovas.ch/mcp"
#: The only domain the gateway may be at: Anthropic sends the company's gateway key to this address, so an address
#: elsewhere would hand the key to whoever runs it. Checked on the settings page and again before every call.
BRAIN_MCP_DOMAIN = "knovas.ch"
#: The name of the gateway in ``mcp_servers`` and its toolset (``mcp_server_name``).
BRAIN_MCP_SERVER_NAME = "knovas-brain"
#: The MCP connector's beta (claude-api skill, shared/tool-use-concepts.md, "MCP Connector (Beta)").
BRAIN_MCP_BETA = "mcp-client-2025-11-20"
#: The gateway's tools Claude may call (the toolset's allowlist: every other tool of the server stays off).
BRAIN_MCP_TOOLS = ("search", "fetch")
#: How often a paused turn (``stop_reason == "pause_turn"``, the server's tool loop at its limit) is continued.
BRAIN_MCP_MAX_CONTINUATIONS = 2

#: The warning under the mode on the settings page (the founder's words of 2026-11-22).
BRAIN_MCP_WARNING = (
    "Brain-Werkzeuge lesen alles, was der Schl\u00fcssel Ihres Unternehmens sieht, unabh\u00e4ngig von den Rollen der "
    "Person. Nur ohne \u00abEin\u00bb verwenden."
)
#: Why the page refuses ``brain_mcp`` (and the problem lines it shows when a deployment set it all the same).
BRAIN_MCP_NEEDS_ANTHROPIC = (
    "Brain-Werkzeuge gehen nur mit dem Sprachmodell anthropic: Claude ruft das Knovas-Gateway selbst auf."
)
BRAIN_MCP_NEEDS_API_KEY = (
    "Brain-Werkzeuge brauchen den Schl\u00fcssel f\u00fcr die Claude API (Knovas Assistent): bitte zuerst setzen."
)
BRAIN_MCP_NEEDS_GATEWAY_KEY = (
    "Brain-Werkzeuge brauchen den Schl\u00fcssel des Knovas-Gateways: bitte zuerst setzen."
)


def brain_mcp_url_problem(value: Any) -> Optional[str]:
    """Why ``value`` cannot be the gateway's address (a German text for the page), or None when it can.

    Only ``https://`` on ``knovas.ch`` or a name under it, written in ASCII alone, without user, password, query or
    fragment, and without white space or control characters: Anthropic sends the gateway key to this address.
    """
    from urllib.parse import urlsplit

    text = value.strip() if isinstance(value, str) else ""
    if not text:
        return "Die Adresse des Knovas-Gateways fehlt."
    if not text.isascii():
        return "Die Adresse des Knovas-Gateways darf nur ASCII-Zeichen enthalten."
    if (any(char.isspace() or ord(char) < 32 or ord(char) == 127 or char == chr(92) for char in text)
            or len(text) > 500):
        return "Die Adresse des Knovas-Gateways ist keine g\u00fcltige Adresse."
    try:
        parts = urlsplit(text)
        host = (parts.hostname or "").lower()
        parts.port  # noqa: B018 - a bad port raises here
    except ValueError:
        return "Die Adresse des Knovas-Gateways ist keine g\u00fcltige Adresse."
    if parts.scheme.lower() != "https" or not host:
        return "Die Adresse des Knovas-Gateways muss mit https:// beginnen."
    if parts.username is not None or parts.password is not None or parts.query or parts.fragment:
        return "Die Adresse des Knovas-Gateways darf weder Benutzer noch Abfrage noch Anker enthalten."
    if (host != BRAIN_MCP_DOMAIN and not host.endswith("." + BRAIN_MCP_DOMAIN)) or not _HOST_NAME.fullmatch(host):
        return ("Das Knovas-Gateway liegt unter knovas.ch; eine andere Adresse ist nicht erlaubt (Anthropic schickt "
                "den Gateway-Schl\u00fcssel dorthin).")
    return None


#: A host name as the check accepts it: letters, digits, dots and hyphens only (no percent sign, no bracket).
_HOST_NAME = re.compile("[a-z0-9.-]{1,253}")


class BrainCall:
    """What one answer through the Brain's tools brought back: every response of the turn, in order (more than one
    only when a paused turn was continued). The caller reads the tool results and the final text from them."""

    def __init__(self, responses: List[Any], model: str) -> None:
        self.responses = list(responses)
        self.model = model

    @property
    def stop(self) -> Any:
        return getattr(self.responses[-1], "stop_reason", None) if self.responses else None


AskFn = Callable[..., BrainCall]


def make_brain_mcp_ask(*, api_key: Value, model: Value = DEFAULT_ANTHROPIC_MODEL, gateway_url: Value = None,
                       gateway_key: Value, client_factory: Optional[ClientFactory] = None,
                       clock: Callable[[], float] = time.monotonic) -> AskFn:
    """``ask(*, system_instruction, messages, max_tokens, deadline_at, min_seconds) -> BrainCall`` through Claude
    with the MCP connector, against the Knovas MCP gateway (\u00abBrain-Werkzeuge\u00bb).

    The request is today's (``make_anthropic_generate``: the model, effort medium, the server-side fallback, no
    temperature, no thinking setting, at least ``ANTHROPIC_MIN_MAX_TOKENS``) plus the connector: ``mcp_servers``
    with the gateway's address and the company's gateway key as ``authorization_token``, a ``tools`` entry
    ``mcp_toolset`` for that server that allows only ``BRAIN_MCP_TOOLS``, and the beta ``BRAIN_MCP_BETA``. The
    client is today's too: the key of the settings, the pinned address, no retry.

    The deadline (as PMVP-24's order): ``deadline_at`` is a ``clock()`` reading by which the whole turn must end. A
    request is not started with less than ``min_seconds`` left (``CoreBusy``; a continuation is then left out and
    the turn ends as it is). Each request runs on a worker thread with the client's timeout at most the time left
    (``ANTHROPIC_TIMEOUT_SECONDS`` otherwise), and the caller waits for it only until ``deadline_at``: then the
    client is closed and the answer is ``CoreBusy``. A timeout is busy (PMVP-24, decision D1); every other error is
    mapped as ``make_anthropic_generate`` maps it.

    ``api_key``, ``model``, ``gateway_url`` and ``gateway_key`` are values or callables giving them (the settings,
    read on every call). Without either key, or with an address ``brain_mcp_url_problem`` refuses, nothing is sent
    (``CoreFailed``). The gateway key goes into the request body and nowhere else: never a log line, an exception
    or a page. The SDK's DEBUG line of the request's options is held back for these requests whatever level its
    logger has (``_NoBrainRequestOptions``).
    """

    def ask(*, system_instruction: str, messages: List[Dict[str, Any]], max_tokens: int, deadline_at: float,
            min_seconds: float) -> BrainCall:
        key = _text(_read(api_key))
        if not key:
            logger.warning("Knovas Assistent: brain tools need the Claude API key")
            raise CoreFailed("anthropic_key_missing")
        name = _text(_read(model)) or DEFAULT_ANTHROPIC_MODEL
        if name not in ANTHROPIC_MODELS:
            logger.warning("Knovas Assistent: the model is not one of %s", ", ".join(ANTHROPIC_MODELS))
            raise CoreFailed("anthropic_model_unknown")
        url = _text(_read(gateway_url)) or DEFAULT_BRAIN_MCP_URL
        if brain_mcp_url_problem(url) is not None:
            logger.warning("Knovas Assistent: brain tools refused the gateway's address")
            raise CoreFailed("brain_mcp_url_refused")
        token = _text(_read(gateway_key))
        if not token:
            logger.warning("Knovas Assistent: brain tools have no gateway key")
            raise CoreFailed("brain_mcp_key_missing")
        request: Dict[str, Any] = {
            "model": name,
            "max_tokens": max(int(max_tokens), ANTHROPIC_MIN_MAX_TOKENS),
            "system": system_instruction,
            "messages": [{"role": m["role"], "content": m["content"]} for m in messages],
            **model_fields(name, (BRAIN_MCP_BETA,)),
            "mcp_servers": [{"type": "url", "url": url, "name": BRAIN_MCP_SERVER_NAME,
                             "authorization_token": token}],
            "tools": [{"type": "mcp_toolset", "mcp_server_name": BRAIN_MCP_SERVER_NAME,
                       "default_config": {"enabled": False},
                       "configs": {tool: {"enabled": True} for tool in BRAIN_MCP_TOOLS}}],
        }
        import anthropic  # the SDK; imported here so that the settings page does not load it

        _hold_back_brain_request_options()
        factory = client_factory or _sdk_client
        responses: List[Any] = []
        asked = list(request["messages"])
        paused: List[Any] = []
        for turn in range(1 + BRAIN_MCP_MAX_CONTINUATIONS):
            left = deadline_at - clock()
            if left < min_seconds:
                logger.warning("Knovas Assistent: deadline step=%s not started left_ms=%d",
                               "brain_tools" if turn == 0 else "brain_tools_continuation", int(left * 1000))
                if turn == 0:
                    raise CoreBusy("no_time_to_generate")
                break
            response = _request_by_deadline(factory, key, request, deadline_at, clock, anthropic, name)
            responses.append(response)
            if getattr(response, "stop_reason", None) != "pause_turn":
                break
            # A paused turn goes back as it came, after the question, and the server resumes it: no extra user
            # message (claude-api skill, shared/tool-use-concepts.md, "Stop reasons for server-side tools"). After a
            # second pause the assistant's content so far stays one message, so the roles still alternate.
            paused.extend(getattr(response, "content", None) or [])
            request["messages"] = asked + [{"role": "assistant", "content": list(paused)}]
        call = BrainCall(responses, name)
        if call.stop == "refusal":
            raise CoreFailed("anthropic_refusal")
        return call

    return ask


def unavailable_ask(reason: str) -> AskFn:
    """An ask that always fails: \u00abBrain-Werkzeuge\u00bb are chosen without what they need (e.g. another provider)."""

    def ask(**_kwargs: Any) -> BrainCall:
        logger.warning("Knovas Assistent: brain tools not usable (%s)", reason)
        raise CoreFailed(reason)

    return ask


def _request_by_deadline(factory: ClientFactory, key: str, request: Dict[str, Any], deadline_at: float,
                         clock: Callable[[], float], anthropic: Any, name: str) -> Any:
    """One request, waited for until ``deadline_at`` by the wall clock whatever the socket does: PMVP-24's
    ``core.call_within`` (a worker thread; past the time the client is closed and the answer is busy)."""
    from assistant.core import OutOfTime, call_within

    left = deadline_at - clock()
    timeout = max(0.1, min(ANTHROPIC_TIMEOUT_SECONDS, left))
    started = time.monotonic()
    client: Any = None
    response: Any = None
    failure: Optional[CoreError] = None
    try:
        client = factory(api_key=key, timeout=timeout, max_retries=ANTHROPIC_MAX_RETRIES)
        response = call_within(max(0.0, left), lambda: client.beta.messages.create(**request),
                               on_overrun=lambda: _close(client), name="brain-tools")
    except OutOfTime:
        failure = CoreBusy("brain_tools_deadline")
        logger.warning("Knovas Assistent: deadline step=brain_tools ran out of time model=%s ms=%d", name,
                       _ms(started))
    except Exception as exc:  # noqa: BLE001 - mapped; an error outside the SDK fails with nothing of its text
        failure = _mapped_brain(exc, anthropic)
        logger.warning("Knovas Assistent: anthropic call failed (%s%s) model=%s ms=%d", type(exc).__name__,
                       _status_note(failure), name, _ms(started))
    finally:
        _close(client)
    if failure is not None:
        # Raised here, outside any handler: the SDK's exception (its request holds both keys) is neither cause nor
        # context.
        raise failure
    usage = getattr(response, "usage", None)
    logger.info("Knovas Assistent: anthropic model=%s served_by=%s stop=%s input_tokens=%s output_tokens=%s ms=%d",
                name, _safe_name(getattr(response, "model", None)),
                _safe_name(getattr(response, "stop_reason", None)),
                _count(getattr(usage, "input_tokens", None)), _count(getattr(usage, "output_tokens", None)),
                _ms(started))
    return response


def _mapped_brain(exc: BaseException, anthropic: Any) -> CoreError:
    """As ``_mapped``, but a timeout is busy (PMVP-24, D1), and an error outside the SDK fails with no text of it."""
    if isinstance(exc, anthropic.APITimeoutError):
        return CoreBusy("anthropic_timeout")
    if isinstance(exc, anthropic.AnthropicError):
        return _mapped(exc, anthropic)
    return CoreFailed("anthropic_error")


class _NoBrainRequestOptions(logging.Filter):
    """Drops the SDK's DEBUG "Request options" record of a request that carries ``mcp_servers``.

    Its body holds the company's gateway key (``authorization_token``), the question and the chat. The app holds the
    SDK's loggers at INFO (FU-39); this filter keeps that true for these requests when a test or an operator sets
    the logger to DEBUG all the same (FU-46). Other requests' records are left alone.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno > logging.DEBUG:
            return True
        args = record.args
        options = args[0] if isinstance(args, tuple) and args else args
        body = options.get("json_data") if isinstance(options, dict) else None
        return not (isinstance(body, dict) and "mcp_servers" in body)


_SDK_REQUEST_LOGGER = "anthropic._base_client"
_REQUEST_OPTIONS_GUARD = _NoBrainRequestOptions()


def _hold_back_brain_request_options() -> None:
    target = logging.getLogger(_SDK_REQUEST_LOGGER)
    if _REQUEST_OPTIONS_GUARD not in target.filters:
        target.addFilter(_REQUEST_OPTIONS_GUARD)


def _mapped(exc: BaseException, anthropic: Any) -> CoreError:
    """The SDK's error as the service reads it: busy or failed, with a status and a reason code only."""
    status = getattr(exc, "status_code", None)
    status = status if isinstance(status, int) and not isinstance(status, bool) else None
    if isinstance(exc, anthropic.RateLimitError) or status in BUSY_STATUSES:
        return CoreBusy("anthropic_busy", status=status)
    if isinstance(exc, anthropic.AuthenticationError):
        return CoreFailed("anthropic_key_refused", status=status)
    if isinstance(exc, anthropic.PermissionDeniedError):
        return CoreFailed("anthropic_permission_denied", status=status)
    if isinstance(exc, anthropic.APIStatusError):
        return CoreFailed("anthropic_http_error", status=status)
    if isinstance(exc, anthropic.APITimeoutError):
        return CoreFailed("anthropic_timeout")
    if isinstance(exc, anthropic.APIConnectionError):
        return CoreFailed("anthropic_no_connection")
    return CoreFailed("anthropic_error")


def _sdk_client(**kwargs: Any) -> Any:
    import anthropic

    return anthropic.Anthropic(**{**kwargs, "base_url": ANTHROPIC_BASE_URL})


def _close(client: Any) -> None:
    close = getattr(client, "close", None)
    if callable(close):
        try:
            close()
        except Exception as exc:  # noqa: BLE001 - closing never fails an answer
            logger.debug("Knovas Assistent: anthropic client not closed (%s)", type(exc).__name__)


# ---------------------------------------------------------------------------
# \u00abWissensgraph\u00bb: Claude reads the knowledge graph with tools the Platform runs (PMVP-20 brain-graph)
# ---------------------------------------------------------------------------

#: The warning under the mode for \u00abWissensgraph\u00bb, in the words of \u00abBrain-Werkzeuge\u00bb's warning.
BRAIN_GRAPH_WARNING = (
    "Wissensgraph liest den Wissensgraphen mit dem Zertifikat Ihres Unternehmens, unabh\u00e4ngig von den Rollen der "
    "Person. Nur ohne \u00abEin\u00bb verwenden."
)
BRAIN_GRAPH_NEEDS_ANTHROPIC = (
    "Wissensgraph geht nur mit dem Sprachmodell anthropic: Claude ruft die Werkzeuge der Plattform selbst auf."
)
BRAIN_GRAPH_NEEDS_API_KEY = (
    "Wissensgraph braucht den Schl\u00fcssel f\u00fcr die Claude API (Knovas Assistent): bitte zuerst setzen."
)

TurnFn = Callable[..., Any]


def make_brain_graph_turn(*, api_key: Value, model: Value = DEFAULT_ANTHROPIC_MODEL,
                          client_factory: Optional[ClientFactory] = None,
                          clock: Callable[[], float] = time.monotonic) -> TurnFn:
    """``turn(*, system_instruction, messages, tools, tool_choice, max_tokens, deadline_at, min_seconds) -> response``:
    one request of \u00abWissensgraph\u00bb's tool loop (``assistant.brain_graph``), with client tools the Platform runs.

    The request is today's (``make_anthropic_generate``: the model, effort medium, the server-side fallback, no
    temperature, no thinking setting, at least ``ANTHROPIC_MIN_MAX_TOKENS``) plus ``tools`` (client tools, no MCP
    server) and ``tool_choice`` (``auto``, or ``none`` for the last request; a forced tool is refused by the current
    models). The messages go as they are: the assistant's content of earlier rounds unchanged (its thinking blocks
    included) and the tool results. Not started with less than ``min_seconds`` before ``deadline_at``
    (``CoreBusy``); waited for until ``deadline_at`` by the wall clock (``_request_by_deadline``: a timeout is busy,
    every other error mapped as ``make_anthropic_generate`` maps it). ``stop_reason`` ``refusal`` -> ``CoreFailed``.
    Without a key nothing is sent (``CoreFailed``). The key is never logged; the one log line per request carries
    the model, the stop reason, token counts and milliseconds.
    """

    def turn(*, system_instruction: str, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]],
             tool_choice: Dict[str, Any], max_tokens: int, deadline_at: float, min_seconds: float) -> Any:
        key = _text(_read(api_key))
        if not key:
            logger.warning("Knovas Assistent: the knowledge graph mode needs the Claude API key")
            raise CoreFailed("anthropic_key_missing")
        name = _text(_read(model)) or DEFAULT_ANTHROPIC_MODEL
        if name not in ANTHROPIC_MODELS:
            logger.warning("Knovas Assistent: the model is not one of %s", ", ".join(ANTHROPIC_MODELS))
            raise CoreFailed("anthropic_model_unknown")
        left = deadline_at - clock()
        if left < min_seconds:
            logger.warning("Knovas Assistent: deadline step=brain_graph not started left_ms=%d", int(left * 1000))
            raise CoreBusy("no_time_to_generate")
        request: Dict[str, Any] = {
            "model": name,
            "max_tokens": max(int(max_tokens), ANTHROPIC_MIN_MAX_TOKENS),
            "system": system_instruction,
            "messages": [{"role": m["role"], "content": m["content"]} for m in messages],
            "tools": list(tools),
            "tool_choice": dict(tool_choice),
            **model_fields(name),
        }
        import anthropic  # the SDK; imported here so that the settings page does not load it

        response = _request_by_deadline(client_factory or _sdk_client, key, request, deadline_at, clock, anthropic,
                                        name)
        if getattr(response, "stop_reason", None) == "refusal":
            raise CoreFailed("anthropic_refusal")
        return response

    return turn


def unavailable_turn(reason: str) -> TurnFn:
    """A turn that always fails: \u00abWissensgraph\u00bb is chosen without what it needs (another provider, no
    secured API)."""

    def turn(**_kwargs: Any) -> Any:
        logger.warning("Knovas Assistent: the knowledge graph mode is not usable (%s)", reason)
        raise CoreFailed(reason)

    return turn


# ---------------------------------------------------------------------------
# The settings page: "Verbindung testen" and what is missing
# ---------------------------------------------------------------------------

#: The connection test's passage and question: invented, never a customer's text.
TEST_PASSAGE = "Die Beispiel AG mit Sitz in Musterstadt wurde am 3. Mai 2021 gegr\u00fcndet."
TEST_QUESTION = "Wann wurde die Beispiel AG gegr\u00fcndet?"

#: What the page shows for each outcome of the connection test.
TEST_TEXTS = {
    "ok": "funktioniert",
    "no_key": "kein Schl\u00fcssel gesetzt",
    "key_refused": "Schl\u00fcssel abgelehnt",
    "permission_denied": "Schl\u00fcssel abgelehnt: keine Berechtigung f\u00fcr dieses Modell",
    "busy": "ausgelastet, bitte gleich noch einmal versuchen",
    "no_connection": "keine Verbindung",
    "refused": "die Testfrage wurde abgelehnt",
    "model_unknown": "dieses Modell wird nicht angeboten",
    "error": "Fehler der Claude API",
}
_OUTCOME_BY_REASON = {
    "anthropic_key_missing": "no_key",
    "anthropic_key_refused": "key_refused",
    "anthropic_permission_denied": "permission_denied",
    "anthropic_no_connection": "no_connection",
    "anthropic_timeout": "no_connection",
    "anthropic_refusal": "refused",
    "anthropic_model_unknown": "model_unknown",
}

KEY_MISSING_TEXT = (
    "Anbieter anthropic ist gew\u00e4hlt, aber es ist kein lesbarer Schl\u00fcssel gesetzt: "
    "So kann der Knovas Assistent nicht antworten."
)


def connection_test(api_key: Optional[str], model: Any, *,
                    client_factory: Optional[ClientFactory] = None) -> Dict[str, Any]:
    """One call to Claude with an invented passage and question, as the assistant would make it.

    ``{"ok": True, "message": "funktioniert", "model": ...}`` or ``{"ok": False, "error": <plain text>,
    "outcome": <code>}``. Never echoes the key; logs the outcome code only.
    """
    from assistant.service import DEFAULT_MAX_TOKENS, Passage, compose

    name = _text(model) or DEFAULT_ANTHROPIC_MODEL
    outcome = "ok"
    status: Optional[int] = None
    if not _text(api_key):
        outcome = "no_key"
    else:
        passage = Passage(number=1, pointer="verbindungstest", title="Verbindungstest", page=None,
                          text=TEST_PASSAGE)
        system_instruction, messages = compose(TEST_QUESTION, [passage])
        generate = make_anthropic_generate(api_key=api_key, model=name, client_factory=client_factory)
        try:
            generate(system_instruction=system_instruction, messages=messages, max_tokens=DEFAULT_MAX_TOKENS)
        except CoreBusy as exc:
            outcome, status = "busy", exc.status
        except CoreFailed as exc:
            outcome, status = _OUTCOME_BY_REASON.get(exc.reason, "error"), exc.status
        except Exception as exc:  # noqa: BLE001 - e.g. a key the HTTP library refuses as a header; its text may
            # hold the key, so only the class is logged and nothing of it is returned
            logger.warning("Knovas Assistent: connection test raised %s", type(exc).__name__)
            outcome = "error"
    logger.info("Knovas Assistent: connection test %s", outcome)
    if outcome == "ok":
        return {"ok": True, "message": TEST_TEXTS["ok"], "model": name}
    text = TEST_TEXTS[outcome]
    if outcome == "error" and status is not None:
        text += f" (HTTP {status})"
    return {"ok": False, "error": text, "outcome": outcome}


BRAIN_MCP_KEY_MISSING_TEXT = (
    "Brain-Werkzeuge sind gew\u00e4hlt, aber es ist kein lesbarer Schl\u00fcssel des Knovas-Gateways gesetzt: "
    "So kann der Knovas Assistent nicht antworten."
)


def settings_problems(settings: Any) -> Dict[str, str]:
    """What the settings page says next to a setting: the key is missing while anthropic is chosen; and, with
    \u00abBrain-Werkzeuge\u00bb chosen (a deployment may pin them past the page's refusal), another provider, no gateway
    key or an address that is refused."""
    problems: Dict[str, str] = {}
    try:
        provider = chosen_provider(settings.value(SETTING_PROVIDER))
        if provider == PROVIDER_ANTHROPIC and not _text(settings.secret(SETTING_API_KEY)):
            problems[SETTING_API_KEY] = KEY_MISSING_TEXT
        mode = chosen_mode(settings.value(SETTING_MODE))
        if mode == MODE_BRAIN_GRAPH and provider != PROVIDER_ANTHROPIC:
            problems[SETTING_MODE] = BRAIN_GRAPH_NEEDS_ANTHROPIC
        if mode == MODE_BRAIN_MCP:
            if provider != PROVIDER_ANTHROPIC:
                problems[SETTING_MODE] = BRAIN_MCP_NEEDS_ANTHROPIC
            if not _text(settings.secret(SETTING_BRAIN_MCP_KEY)):
                problems[SETTING_BRAIN_MCP_KEY] = BRAIN_MCP_KEY_MISSING_TEXT
            url_problem = brain_mcp_url_problem(settings.value(SETTING_BRAIN_MCP_URL) or DEFAULT_BRAIN_MCP_URL)
            if url_problem:
                problems[SETTING_BRAIN_MCP_URL] = url_problem
    except Exception as exc:  # noqa: BLE001 - the page still loads; it just says nothing here
        logger.warning("Knovas Assistent: settings not readable (%s)", type(exc).__name__)
        return {}
    return problems


def settings_warnings() -> Dict[str, str]:
    """The lines the settings page always shows under a setting: the warning under the answer mode (both modes
    that read with the company's own credentials, \u00abBrain-Werkzeuge\u00bb and \u00abWissensgraph\u00bb)."""
    return {SETTING_MODE: BRAIN_MCP_WARNING + " " + BRAIN_GRAPH_WARNING}


#: What a removed page value falls back to (config.yaml's defaults of the two choices).
_FALLBACKS = {SETTING_MODE: MODE_PASSAGES, SETTING_PROVIDER: PROVIDER_KNOVAS}


def brain_mcp_refusal(settings: Any, values: Any) -> Optional[str]:
    """Why a save of the settings page is refused, or None: it would leave \u00abBrain-Werkzeuge\u00bb chosen without
    the provider anthropic, the Claude API key or the gateway key, or \u00abWissensgraph\u00bb without the provider
    anthropic or the Claude API key.

    ``values`` is what the page sends (a key's new value, or None or an empty text to remove the page's value);
    what it leaves out is read from ``settings``. Only a save that touches the mode, the provider or one of the two
    keys is looked at, so an unrelated save is never refused for a deployment's choice.
    """
    watched = (SETTING_MODE, SETTING_PROVIDER, SETTING_API_KEY, SETTING_BRAIN_MCP_KEY)
    if not isinstance(values, dict) or not any(key in values for key in watched):
        return None

    def removed(raw: Any) -> bool:
        return raw is None or (isinstance(raw, str) and not raw.strip())

    def choice(key: str) -> Any:
        if key in values:
            return _FALLBACKS[key] if removed(values[key]) else values[key]
        return settings.value(key)

    def has_secret(key: str) -> bool:
        if key in values:
            return not removed(values[key])
        return bool(_text(settings.secret(key)))

    mode = chosen_mode(choice(SETTING_MODE))
    if mode == MODE_BRAIN_GRAPH:
        if chosen_provider(choice(SETTING_PROVIDER)) != PROVIDER_ANTHROPIC:
            return BRAIN_GRAPH_NEEDS_ANTHROPIC
        if not has_secret(SETTING_API_KEY):
            return BRAIN_GRAPH_NEEDS_API_KEY
        return None
    if mode != MODE_BRAIN_MCP:
        return None
    if chosen_provider(choice(SETTING_PROVIDER)) != PROVIDER_ANTHROPIC:
        return BRAIN_MCP_NEEDS_ANTHROPIC
    if not has_secret(SETTING_API_KEY):
        return BRAIN_MCP_NEEDS_API_KEY
    if not has_secret(SETTING_BRAIN_MCP_KEY):
        return BRAIN_MCP_NEEDS_GATEWAY_KEY
    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read(value: Value) -> Any:
    return value() if callable(value) else value


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _ms(since: float) -> int:
    return int((time.monotonic() - since) * 1000)


def _status_note(exc: CoreError) -> str:
    return f", HTTP {exc.status}" if exc.status is not None else ""


def _safe_name(value: Any) -> str:
    """A model name or stop reason for a log line: short, no spaces, else a placeholder."""
    if isinstance(value, str) and 0 < len(value) <= 64 and value.replace("-", "").replace("_", "").replace(
            ".", "").isalnum():
        return value
    return "-"


def _count(value: Any) -> str:
    return str(value) if isinstance(value, int) and not isinstance(value, bool) else "-"

