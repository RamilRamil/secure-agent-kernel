"""Spec 020 US2 (FR-003/004/005): OpenRouter slot wiring + env/UI key precedence."""
from __future__ import annotations

import os

os.environ.setdefault("SR_SECRET_KEY", "00" * 32)

from types import SimpleNamespace

import pytest

from frontend.backend import model_config
from frontend.backend.model_config import ADDITIONAL, ModelConfig, set_additional, set_config
from sr_agent.llm_core.local_client import LocalClient
from sr_agent.llm_core.openrouter_client import OPENROUTER_MODELS, OpenRouterClient


def _snap(mc):
    return (mc.model, mc.backend, mc._paid_key)


def _restore_snap(mc, snap):
    (mc.model, mc.backend, mc._paid_key) = snap


@pytest.fixture(autouse=True)
def restore():
    main, add = _snap(model_config.CONFIG), _snap(ADDITIONAL)
    yield
    _restore_snap(model_config.CONFIG, main)
    _restore_snap(ADDITIONAL, add)


def _env_key(monkeypatch, value: str):
    ns = SimpleNamespace(gemini_api_key="", openrouter_api_key=value)
    monkeypatch.setattr(model_config, "config", ns)


def test_openrouter_backend_accepted_both_slots():
    assert set_config(backend="openrouter")["backend"] == "openrouter"
    assert set_additional(backend="openrouter")["backend"] == "openrouter"
    with pytest.raises(ValueError):
        set_config(backend="nonsense")


def test_key_precedence_ui_over_env(monkeypatch):
    _env_key(monkeypatch, "ENVK")
    mc = ModelConfig(backend="openrouter")
    assert mc.effective_openrouter_key() == "ENVK"
    mc._paid_key = "UIK"
    assert mc.effective_openrouter_key() == "UIK"


def test_reasoning_client_builds_openrouter(monkeypatch):
    _env_key(monkeypatch, "ENVK")
    mc = ModelConfig(backend="openrouter", model="z-ai/glm-5.2")
    c = mc.reasoning_client()
    assert isinstance(c, OpenRouterClient)
    assert c.api_key == "ENVK" and c.model == "z-ai/glm-5.2"


def test_reasoning_client_defaults_openrouter_model(monkeypatch):
    _env_key(monkeypatch, "ENVK")
    assert ModelConfig(backend="openrouter").reasoning_client().model == OPENROUTER_MODELS[0]


def test_additional_openrouter_none_without_key(monkeypatch):
    _env_key(monkeypatch, "")
    assert ModelConfig(backend="openrouter").additional_client() is None


def test_additional_openrouter_client_with_key(monkeypatch):
    _env_key(monkeypatch, "ENVK")
    assert isinstance(ModelConfig(backend="openrouter").additional_client(), OpenRouterClient)


def test_local_unaffected():
    assert isinstance(ModelConfig(backend="local").reasoning_client(), LocalClient)


def test_public_never_contains_key():
    mc = ModelConfig(backend="openrouter")
    mc._paid_key = "sk-or-secret"
    pub = mc.public()
    assert set(pub) == {"endpoint", "model", "backend", "has_paid_key"}
    assert pub["has_paid_key"] is True
    assert "sk-or-secret" not in repr(pub)
