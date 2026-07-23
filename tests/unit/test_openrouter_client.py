"""Spec 020 US1 (FR-002/006): OpenRouterClient over a MOCKED HTTP endpoint.

No real key, no network — `urllib.request.urlopen` is monkeypatched to a fake
OpenRouter response, capturing the outgoing request.
"""
from __future__ import annotations

import os

os.environ.setdefault("SR_SECRET_KEY", "00" * 32)

import json

import pytest

import sr_agent.llm_core.openrouter_client as orc
from sr_agent.llm_core.openrouter_client import OpenRouterClient, OpenRouterUnavailable
from sr_agent.llm_core.schemas import AgentAction
from sr_agent.models.chat import ChatTurn
from sr_agent.models.memory import SourceType


@pytest.fixture
def captured(monkeypatch):
    """Fake urlopen returning a canned OpenRouter completion; records the request."""
    box: dict = {}

    class _Resp:
        def __init__(self, payload):
            self._b = json.dumps(payload).encode()
        def read(self):
            return self._b
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        box["url"] = req.full_url
        box["headers"] = dict(req.header_items())
        box["body"] = json.loads(req.data.decode())
        return _Resp({"choices": [{"message": {"content": "OUT"}}]})

    monkeypatch.setattr(orc.urllib.request, "urlopen", fake_urlopen)
    return box


def _client():
    return OpenRouterClient(api_key="test-key", model="z-ai/glm-5.2")


def test_generate_returns_message_content_and_json_mode(captured):
    out = _client().generate("hello", fmt="json")
    assert out == "OUT"
    assert captured["body"]["model"] == "z-ai/glm-5.2"
    assert captured["body"]["messages"] == [{"role": "user", "content": "hello"}]
    assert captured["body"]["response_format"] == {"type": "json_object"}
    # Header keys are title-cased by urllib.
    assert captured["headers"].get("Authorization") == "Bearer test-key"


def test_generate_without_fmt_omits_response_format(captured):
    _client().generate("hello")
    assert "response_format" not in captured["body"]


def test_ready_reflects_key_presence():
    assert _client().ready() is True
    assert OpenRouterClient(api_key="").ready() is False


def test_empty_key_raises():
    with pytest.raises(OpenRouterUnavailable):
        OpenRouterClient(api_key="").generate("hi")


def test_http_error_normalized(monkeypatch):
    def boom(req, timeout=None):
        raise orc.urllib.error.URLError("down")
    monkeypatch.setattr(orc.urllib.request, "urlopen", boom)
    with pytest.raises(OpenRouterUnavailable):
        _client().generate("hi")


# ── C1 (FR-006): an OpenRouter-produced turn is external_llm_output ───────────

def test_openrouter_turn_is_external_llm_output():
    aa = AgentAction(next_action="respond", reasoning_summary="from glm")
    turn = ChatTurn(session_id="s1", user_message="hi", agent_action=aa)
    assert turn.source_type == SourceType.external_llm_output
    with pytest.raises(ValueError):
        ChatTurn(session_id="s1", user_message="hi", agent_action=aa,
                 source_type=SourceType.human_input)


# ── max_tokens budget is honored (was silently dropped) ───────────────────────

def test_generate_passes_max_tokens_from_num_predict(captured):
    _client().generate("hi", options={"num_predict": 6000})
    assert captured["body"]["max_tokens"] == 6000


def test_generate_omits_max_tokens_without_num_predict(captured):
    _client().generate("hi")
    assert "max_tokens" not in captured["body"]
