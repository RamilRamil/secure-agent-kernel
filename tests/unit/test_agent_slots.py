"""Spec 019 US3 (FR-006/011): two independent agent slots; keys write-only."""
from __future__ import annotations

import os

os.environ.setdefault("SR_SECRET_KEY", "00" * 32)

from types import SimpleNamespace

import pytest

from frontend.backend import model_config
from frontend.backend.model_config import ADDITIONAL, ModelConfig, set_additional
from sr_agent.llm_core.gemini_client import GeminiClient
from sr_agent.llm_core.local_client import LocalClient


@pytest.fixture(autouse=True)
def restore():
    saved = (ADDITIONAL.endpoint, ADDITIONAL.model, ADDITIONAL.backend, ADDITIONAL._paid_key)
    yield
    (ADDITIONAL.endpoint, ADDITIONAL.model, ADDITIONAL.backend, ADDITIONAL._paid_key) = saved


def test_additional_off_by_default_returns_no_client():
    assert ADDITIONAL.backend == "off"
    assert ModelConfig(backend="off").additional_client() is None


def test_additional_paid_without_key_is_unconfigured(monkeypatch):
    monkeypatch.setattr(model_config, "config", SimpleNamespace(gemini_api_key=""))
    mc = ModelConfig(backend="paid")
    assert mc.additional_client() is None            # no silent keyless call


def test_additional_paid_with_key_builds_gemini(monkeypatch):
    monkeypatch.setattr(model_config, "config", SimpleNamespace(gemini_api_key="ENVK"))
    mc = ModelConfig(backend="paid", model="gemini-2.0-flash")
    c = mc.additional_client()
    assert isinstance(c, GeminiClient) and c.api_key == "ENVK"


def test_additional_local_builds_local_client():
    assert isinstance(ModelConfig(backend="local").additional_client(), LocalClient)


def test_set_additional_accepts_off_and_rejects_unknown():
    assert set_additional(backend="off")["backend"] == "off"
    assert set_additional(backend="local")["backend"] == "local"
    with pytest.raises(ValueError):
        set_additional(backend="bogus")


def test_additional_public_never_exposes_key():
    ADDITIONAL._paid_key = "sk-secret"
    pub = ADDITIONAL.public()
    assert pub["has_paid_key"] is True
    assert "sk-secret" not in repr(pub)
    assert "_paid_key" not in pub and "paid_key" not in pub


def test_main_backend_still_local_or_paid_only():
    # MAIN slot must NOT accept "off" — keeps spec-005/018 contract.
    from frontend.backend.model_config import set_config
    with pytest.raises(ValueError):
        set_config(backend="off")
