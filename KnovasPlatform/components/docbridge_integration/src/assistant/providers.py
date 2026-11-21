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

The SDK is imported when a call is made, not when this module is imported: the settings page imports this module.
This module is ASCII-only: umlauts are ``\\u`` escapes.
"""

from __future__ import annotations

import logging
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

PROVIDER_KNOVAS = "knovas"
PROVIDER_ANTHROPIC = "anthropic"
PROVIDERS = (PROVIDER_KNOVAS, PROVIDER_ANTHROPIC)

ANTHROPIC_MODELS = ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5")
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


def make_anthropic_generate(*, api_key: Value, model: Value = DEFAULT_ANTHROPIC_MODEL,
                            client_factory: Optional[ClientFactory] = None) -> GenerateFn:
    """``generate(*, system_instruction, messages, max_tokens) -> str`` through Claude.

    ``api_key`` and ``model`` are values or callables giving them; callables are asked on every call (the
    settings page's values, read through the Resolver). ``client_factory(api_key=..., timeout=...,
    max_retries=...)`` builds the client; by default ``anthropic.Anthropic``. Tests pass a fake.
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
            "output_config": {"effort": ANTHROPIC_EFFORT},
            "betas": list(ANTHROPIC_BETAS),
            "fallbacks": ANTHROPIC_FALLBACKS,
        }
        import anthropic  # the SDK; imported here so that the settings page does not load it

        factory = client_factory or _sdk_client
        started = time.monotonic()
        response: Any = None
        failure: Optional[CoreError] = None
        client: Any = None
        try:
            client = factory(api_key=key, timeout=ANTHROPIC_TIMEOUT_SECONDS,
                             max_retries=ANTHROPIC_MAX_RETRIES)
            response = client.beta.messages.create(**request)
        except anthropic.AnthropicError as exc:
            failure = _mapped(exc, anthropic)
            logger.warning("Knovas Assistent: anthropic call failed (%s%s) model=%s ms=%d",
                           type(exc).__name__, _status_note(failure), name, _ms(started))
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


def settings_problems(settings: Any) -> Dict[str, str]:
    """What the settings page says next to a setting: the key is missing while anthropic is chosen."""
    try:
        if chosen_provider(settings.value(SETTING_PROVIDER)) != PROVIDER_ANTHROPIC:
            return {}
        if _text(settings.secret(SETTING_API_KEY)):
            return {}
    except Exception as exc:  # noqa: BLE001 - the page still loads; it just says nothing here
        logger.warning("Knovas Assistent: settings not readable (%s)", type(exc).__name__)
        return {}
    return {SETTING_API_KEY: KEY_MISSING_TEXT}


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

