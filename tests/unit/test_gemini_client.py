"""Spec 018 US1 (FR-005/006): GeminiClient behavior with a MOCKED SDK.

No real key, no network — a fake `google.genai` is injected into sys.modules so
generate()/ready() exercise the real client code against a stub.
"""
from __future__ import annotations

import sys
import types as pytypes

import pytest

from sr_agent.llm_core.gemini_client import GeminiUnavailable


@pytest.fixture
def fake_sdk(monkeypatch):
    """Inject a fake google-genai. Returns a `captured` dict recording the call."""
    captured: dict = {}

    class FakeGCC:  # types.GenerateContentConfig
        def __init__(self, **kw):
            self.kw = kw

    class FakeModels:
        def generate_content(self, model, contents, config=None):
            captured["model"] = model
            captured["contents"] = contents
            captured["config"] = config
            return pytypes.SimpleNamespace(text="MOCK_OUTPUT")

    class FakeClient:
        def __init__(self, api_key):
            captured["api_key"] = api_key
            self.models = FakeModels()

    google_mod = pytypes.ModuleType("google")
    genai_mod = pytypes.ModuleType("google.genai")
    types_mod = pytypes.ModuleType("google.genai.types")
    types_mod.GenerateContentConfig = FakeGCC
    genai_mod.Client = FakeClient
    genai_mod.types = types_mod
    google_mod.genai = genai_mod

    monkeypatch.setitem(sys.modules, "google", google_mod)
    monkeypatch.setitem(sys.modules, "google.genai", genai_mod)
    monkeypatch.setitem(sys.modules, "google.genai.types", types_mod)
    return captured


def _client(model="gemini-2.5-flash"):
    from sr_agent.llm_core.gemini_client import GeminiClient
    return GeminiClient(api_key="test-key", model=model)


def test_ready_true_with_sdk_and_key(fake_sdk):
    assert _client().ready() is True


def test_generate_returns_text_and_passes_json_mode(fake_sdk):
    out = _client().generate("hello", fmt="json")
    assert out == "MOCK_OUTPUT"
    assert fake_sdk["api_key"] == "test-key"
    assert fake_sdk["model"] == "gemini-2.5-flash"
    assert fake_sdk["config"] is not None
    assert fake_sdk["config"].kw.get("response_mime_type") == "application/json"


def test_generate_without_fmt_omits_json_config(fake_sdk):
    _client().generate("hello")
    assert fake_sdk["config"] is None


def test_ready_false_and_generate_raises_without_sdk():
    # No fake_sdk fixture → google-genai genuinely absent in this env.
    c = _client()
    assert c.ready() is False
    with pytest.raises(GeminiUnavailable):
        c.generate("hello")


def test_generate_raises_without_key(fake_sdk):
    from sr_agent.llm_core.gemini_client import GeminiClient
    with pytest.raises(GeminiUnavailable):
        GeminiClient(api_key="", model="gemini-2.5-flash").generate("hello")
