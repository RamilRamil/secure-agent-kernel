"""Spec 018 US2 (FR-002/003/004): key precedence, write-only key, backend wiring.

Offline — no SDK, no network. `reasoning_client()` only CONSTRUCTS a client here
(no call is made), so google-genai need not be installed.
"""
from __future__ import annotations

import os

os.environ.setdefault("SR_SECRET_KEY", "00" * 32)

from types import SimpleNamespace

import pytest

from frontend.backend import model_config
from frontend.backend.model_config import CONFIG, ModelConfig, set_config
from sr_agent.llm_core.gemini_client import SIMPLE_MODELS, GeminiClient
from sr_agent.llm_core.local_client import LocalClient


@pytest.fixture(autouse=True)
def restore_config():
    """set_config mutates the process-wide CONFIG — snapshot and restore it."""
    saved = (CONFIG.endpoint, CONFIG.model, CONFIG.backend, CONFIG._paid_key)
    yield
    CONFIG.endpoint, CONFIG.model, CONFIG.backend, CONFIG._paid_key = saved


def _with_env_key(monkeypatch, value: str):
    monkeypatch.setattr(model_config, "config", SimpleNamespace(gemini_api_key=value))


def test_backend_paid_accepted_and_unknown_rejected():
    assert set_config(backend="paid")["backend"] == "paid"
    assert set_config(backend="local")["backend"] == "local"
    with pytest.raises(ValueError):
        set_config(backend="gemini")   # not a valid value — set stays {local, paid}
    with pytest.raises(ValueError):
        set_config(backend="bogus")


def test_key_precedence_ui_over_env(monkeypatch):
    _with_env_key(monkeypatch, "ENV_KEY")
    mc = ModelConfig(backend="paid")
    assert mc.effective_gemini_key() == "ENV_KEY"      # env fallback
    mc._paid_key = "UI_KEY"
    assert mc.effective_gemini_key() == "UI_KEY"        # UI wins


def test_reasoning_client_builds_gemini_for_paid(monkeypatch):
    _with_env_key(monkeypatch, "ENV_KEY")
    mc = ModelConfig(backend="paid", model="gemini-2.0-flash")
    client = mc.reasoning_client()
    assert isinstance(client, GeminiClient)
    assert client.api_key == "ENV_KEY"
    assert client.model == "gemini-2.0-flash"


def test_reasoning_client_defaults_gemini_model(monkeypatch):
    _with_env_key(monkeypatch, "ENV_KEY")
    mc = ModelConfig(backend="paid")   # model=None → default
    assert mc.reasoning_client().model == SIMPLE_MODELS[0]


def test_reasoning_client_local_for_local():
    mc = ModelConfig(backend="local")
    assert isinstance(mc.reasoning_client(), LocalClient)


def test_no_key_means_not_ready(monkeypatch):
    _with_env_key(monkeypatch, "")       # neither env nor UI
    mc = ModelConfig(backend="paid")
    assert mc.reasoning_client().ready() is False   # clear disabled state, no call


def test_public_never_contains_the_key():
    mc = ModelConfig(backend="paid")
    mc._paid_key = "sk-secret"
    pub = mc.public()
    assert set(pub) == {"endpoint", "model", "backend", "has_paid_key"}
    assert pub["has_paid_key"] is True
    assert "sk-secret" not in repr(pub)
    assert "_paid_key" not in pub and "paid_key" not in pub
