"""PMVP-25, FU-39: at LOG_LEVEL=DEBUG neither the Anthropic SDK nor the HTTP libraries log customer text.

The SDK logs every request's options at DEBUG (the instruction, the passages, the question, the history) and a
transport error's text with its traceback. The app holds those loggers at INFO or above whatever LOG_LEVEL says
(``web_interface.app._configure_logging_for_wsgi``); this covers the Brain Builder's views assistant too. The calls
go through the real SDK over an in-process transport: nothing reaches Anthropic.
"""
from __future__ import annotations

import logging

import httpx2
import pytest

from assistant import providers
from assistant.service import AssistantService
from test_assistant_providers import (ANSWER, KEY, PASSAGE_1, PASSAGE_2, QUESTION, REAL_SDK_CLIENT, _search,
                                      _wire_message)
from web_interface.app import QUIET_LOGGERS, _configure_logging_for_wsgi

TRANSPORT_TEXT = "the socket said: " + PASSAGE_2
CUSTOMER_TEXT = (QUESTION, PASSAGE_1, PASSAGE_2, KEY, TRANSPORT_TEXT)


class _Config:
    def get(self, key, default=None):
        return default


@pytest.fixture(autouse=True)
def levels():
    """The loggers' levels as they were: these tests change them as the app does."""
    names = ("",) + QUIET_LOGGERS + ("web_interface", "web_interface.app", "knovas_client")
    saved = {name: logging.getLogger(name).level for name in names}
    yield
    for name, level in saved.items():
        logging.getLogger(name).setLevel(level)


def _factory(handler):
    def factory(**kwargs):
        return REAL_SDK_CLIENT(**kwargs, http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))

    return factory


def _answered(request):
    return httpx2.Response(200, json=_wire_message(ANSWER))


def _no_connection(request):
    raise httpx2.ConnectError(TRANSPORT_TEXT, request=request)


def _answer(handler):
    generate = providers.make_anthropic_generate(api_key=KEY, client_factory=_factory(handler))
    return AssistantService(search=_search(), generate=generate).answer(QUESTION)


def test_without_the_hold_the_sdk_logs_the_request_and_the_transport_error(caplog):
    """The control, what FU-39 found: the root at DEBUG and the SDK's loggers not held."""
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.NOTSET)
    caplog.set_level(logging.DEBUG)
    _answer(_answered)
    _answer(_no_connection)
    assert "Request options" in caplog.text and PASSAGE_1 in caplog.text and QUESTION in caplog.text
    assert TRANSPORT_TEXT in caplog.text


def test_at_debug_the_sdk_logs_no_request_and_no_transport_error(caplog, monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    _configure_logging_for_wsgi(_Config())
    caplog.set_level(logging.DEBUG)
    assert logging.getLogger().getEffectiveLevel() == logging.DEBUG
    assert {logging.getLogger(name).getEffectiveLevel() for name in QUIET_LOGGERS} == {logging.INFO}
    assert _answer(_answered).status == "answered"
    assert _answer(_no_connection).status == "failed"
    assert "Knovas Assistent: anthropic model=claude-opus-5-5" in caplog.text      # the assistant's own lines stay
    assert "Request options" not in caplog.text and "Encountered Exception" not in caplog.text
    for text in CUSTOMER_TEXT:
        assert text not in caplog.text
    assert not [r for r in caplog.records if r.name.split(".")[0] in QUIET_LOGGERS and r.levelno < logging.INFO]


def test_a_quieter_log_level_stays_quieter(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    _configure_logging_for_wsgi(_Config())
    assert {logging.getLogger(name).level for name in QUIET_LOGGERS} == {logging.WARNING}
