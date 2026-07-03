"""Local model client + local Stage 2 tests (T057). No Ollama required."""
import pytest
from pathlib import Path

from sr_agent.llm_core.local_client import ModelUnavailableError
from sr_agent.packs.audit.analyze import analyze_target, build_analysis_prompt
from sr_agent.memory.episodic import EpisodicMemory
from sr_agent.packs.audit.session import AuditInput, AuditSession, Principal
from sr_agent.packs.audit.finding import Severity
from sr_agent.models.memory import SourceType
from sr_agent.packs.audit.planner.stage2 import run_stage2_local

SECRET = b"test-secret-key-32-bytes-exactly!"

_GOOD = ('```json\n{"findings": [{"finding_id": "F-1", "location": "Vault.sol:18",'
         ' "function_name": "withdraw", "severity": "high", "bastet_tag": "reentrancy",'
         ' "notes": "external call before state update"}]}\n```')


class _FakeClient:
    model = "fake-model"

    def __init__(self, response="", raises=False):
        self._response = response
        self._raises = raises

    def generate(self, prompt: str, fmt: str | None = None) -> str:
        if self._raises:
            raise ModelUnavailableError("down")
        return self._response


def test_build_prompt_wraps_context_as_data():
    p = build_analysis_prompt("Vault.sol:withdraw", "contract Vault {}")
    assert "[DATA START]" in p and "[DATA END]" in p
    assert "Vault.sol:withdraw" in p


def test_analyze_target_parses_findings():
    r = analyze_target(_FakeClient(_GOOD), "Vault.sol:withdraw", "code")
    assert len(r.findings) == 1
    assert r.findings[0].finding.severity is Severity.high


def test_analyze_target_empty_findings():
    r = analyze_target(_FakeClient('{"findings": []}'), "t", "c")
    assert r.findings == []


def test_analyze_target_garbage_needs_resend():
    r = analyze_target(_FakeClient("sorry, no json"), "t", "c")
    assert r.needs_resend


@pytest.fixture
def session() -> AuditSession:
    pr = Principal(user_id="u", platform="cli", project_id="proj1")
    return AuditSession(principal=pr, audit_input=AuditInput(path=Path("c"), principal=pr))


@pytest.fixture
def memory(tmp_path: Path) -> EpisodicMemory:
    return EpisodicMemory(tmp_path / "mem", SECRET)


def test_run_stage2_local_writes_external_llm_output(session, memory):
    result = run_stage2_local(
        session, ["Vault.sol:withdraw"], memory, _FakeClient(_GOOD), lambda t: "code"
    )
    assert result.status == "done"
    assert len(result.findings) == 1
    records = memory.load_for_principal(session.principal)
    assert records and all(r.source_type is SourceType.external_llm_output for r in records)


def test_run_stage2_local_skips_on_model_error(session, memory):
    result = run_stage2_local(
        session, ["Vault.sol:withdraw"], memory, _FakeClient(raises=True), lambda t: "code"
    )
    assert result.status == "done"
    assert result.findings == []
    assert memory.load_for_principal(session.principal) == []


# ── available() tag-strictness + for_stage2 chain (feature 003 workability fixes) ──

import json as _json
from sr_agent.llm_core import local_client as _lc


class _TagsResp:
    def __init__(self, models):
        self._b = _json.dumps({"models": [{"name": n} for n in models]}).encode()

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_urlopen(models):
    def _open(url, timeout=5):
        return _TagsResp(models)
    return _open


def test_available_exact_tag_is_strict(monkeypatch):
    monkeypatch.setattr(_lc.urllib.request, "urlopen", _fake_urlopen(["qwen2.5-coder:3b"]))
    assert _lc.LocalClient(model="qwen2.5-coder:3b").available() is True
    # a pulled :3b must NOT report a different tag :7b as available
    assert _lc.LocalClient(model="qwen2.5-coder:7b").available() is False


def test_available_untagged_matches_any_tag(monkeypatch):
    monkeypatch.setattr(_lc.urllib.request, "urlopen", _fake_urlopen(["sr-stage2:latest"]))
    assert _lc.LocalClient(model="sr-stage2").available() is True


def test_for_stage2_prefers_7b_over_3b(monkeypatch):
    # only 7b and 3b pulled (no sr-stage2 / qwen3:4b) → chain picks 7b
    monkeypatch.setattr(
        _lc.LocalClient, "available",
        lambda self: self.model in ("qwen2.5-coder:7b", "qwen2.5-coder:3b"),
    )
    assert _lc.LocalClient.for_stage2().model == "qwen2.5-coder:7b"
