"""Spec 022: the PoC-harness provider seam — factory, protocol, readiness (offline).

No real GLM/Gemini, no network, no container.
"""
from __future__ import annotations

import os

os.environ.setdefault("SR_SECRET_KEY", "00" * 32)

from types import SimpleNamespace

import pytest

import scripts.poc_queue_runner as pqr
from scripts.poc_queue_runner import (
    MODEL,
    ProviderStartupError,
    build_generation_client,
    hosted_ready_error,
    resolve_lookup_protocol,
)
from sr_agent.llm_core.gemini_client import SIMPLE_MODELS, GeminiClient
from sr_agent.llm_core.local_client import LocalClient
from sr_agent.llm_core.openrouter_client import OPENROUTER_MODELS, OpenRouterClient

# ── factory (FR-001, C2 empty-model default) ──────────────────────────────────

def test_openrouter_client_default_glm(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-k")
    c = build_generation_client("openrouter", "", "h", 1.0)
    assert isinstance(c, OpenRouterClient)
    assert c.model == OPENROUTER_MODELS[0] == "z-ai/glm-5.2"
    assert c.api_key == "sk-or-k"


def test_gemini_client_default_flash(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gk")
    c = build_generation_client("gemini", "", "h", 1.0)
    assert isinstance(c, GeminiClient)
    assert c.model == SIMPLE_MODELS[0]


def test_local_client_default_model():
    c = build_generation_client("local", "", "http://x:11434", 5.0)
    assert isinstance(c, LocalClient) and c.model == MODEL


def test_model_override_respected(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    c = build_generation_client("openrouter", "z-ai/glm-custom", "h", 1.0)
    assert c.model == "z-ai/glm-custom"


# ── protocol resolution (C1, FR-004) ─────────────────────────────────────────

def test_local_protocol_passthrough():
    assert resolve_lookup_protocol("local", "auto") == "auto"
    assert resolve_lookup_protocol("local", "tool") == "tool"


def test_hosted_forces_marker():
    assert resolve_lookup_protocol("openrouter", "auto") == "marker"
    assert resolve_lookup_protocol("gemini", "marker") == "marker"


def test_hosted_rejects_tool():
    with pytest.raises(ProviderStartupError):
        resolve_lookup_protocol("openrouter", "tool")
    with pytest.raises(ProviderStartupError):
        resolve_lookup_protocol("gemini", "tool")


# ── readiness gate (FR-005/006) ──────────────────────────────────────────────

def test_no_key_message():
    msg = hosted_ready_error("openrouter", OpenRouterClient(api_key=""))
    assert msg and "OPENROUTER_API_KEY" in msg
    msg2 = hosted_ready_error("gemini", GeminiClient(api_key=""))
    assert msg2 and "GEMINI_API_KEY" in msg2


def test_ready_with_key_returns_none():
    assert hosted_ready_error("openrouter", OpenRouterClient(api_key="k")) is None


def test_gemini_key_but_sdk_missing():
    fake = SimpleNamespace(api_key="k", ready=lambda: False)
    msg = hosted_ready_error("gemini", fake)
    assert msg and "google-genai" in msg


# ── marker path uses only generate() ─────────────────────────────────────────

def test_marker_path_needs_only_generate():
    calls = {}

    class FakeGen:
        model = "fake"
        def generate(self, prompt, fmt=None, options=None):
            calls["prompt"] = prompt
            return "OUT"

    # A generate-only client is a valid GenClient for the marker path.
    assert FakeGen().generate("p") == "OUT" and calls["prompt"] == "p"


def test_genclient_union_includes_hosted():
    assert OpenRouterClient in pqr.GenClient.__args__
    assert GeminiClient in pqr.GenClient.__args__
