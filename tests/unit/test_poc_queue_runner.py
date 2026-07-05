"""build_file_manifest / build_callable_api: AST-backed grounding (feature 007 T020)
+ the per-name budget fairness / location-priority fix (2026-07-05).

Mirrors the exact motivating incident from the live H-01 run: with several
contract names in one finding's `location`, an earlier name's own budget-hungry
block previously starved a later name out of the prompt entirely, and even after
giving each name its own share, the actual finding-target function could still be
truncated out if declared after other functions in the same file.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import scripts.poc_queue_runner as pqr
from scripts.solidity_index import SymbolIndex

FIRST_SRC = """
pragma solidity ^0.8.28;

contract FooCDO {
    function totalAssets(address tranche) external view returns (uint256) {}
    function totalStrategyAssets() external view returns (uint256) {}
    function pricePerShare(address tranche) external view returns (uint256) {}
    function maxDeposit(address tranche) external view returns (uint256) {}
    function coverage() external view returns (uint32) {}
}
"""

SECOND_SRC = """
pragma solidity ^0.8.28;

contract Foo {
    function requestRedeem(address vault, address token, address from, address to,
                            uint256 shares, uint256 fee, uint32 cooldownSeconds)
        external onlyRole(0) {}
    function finalize(address vault, address user) external returns (uint256) {}
    function cancel(address vault, address user, uint256 i) external onlyUser(user) {}
}
"""


@pytest.fixture
def two_contract_project(tmp_path: Path) -> Path:
    (tmp_path / "contracts").mkdir()
    (tmp_path / "contracts" / "FooCDO.sol").write_text(FIRST_SRC, encoding="utf-8")
    (tmp_path / "contracts" / "Foo.sol").write_text(SECOND_SRC, encoding="utf-8")
    return tmp_path


LOCATION = "FooCDO.coverage / calculateFooMode + Foo.cancel"


@pytest.mark.parametrize("use_ast", [True, False])
def test_each_name_gets_its_own_budget_share(two_contract_project, monkeypatch, use_ast):
    """The original bug: FooCDO (first name) exhausting a SHARED budget meant
    Foo (second name) never got a block at all. Each name must get its
    own share so both survive a tight budget."""
    monkeypatch.setattr(pqr, "CALLABLE_API_BUDGET", 250)
    symbol_index = SymbolIndex.build(two_contract_project) if use_ast else None
    capi = pqr.build_callable_api(two_contract_project, LOCATION, symbol_index)
    assert "// FooCDO" in capi
    assert "// Foo" in capi


@pytest.mark.parametrize("use_ast", [True, False])
def test_location_named_function_survives_truncation(two_contract_project, monkeypatch, use_ast):
    """`cancel` is declared LAST in Foo.sol, after two other external
    functions — under a tight per-file budget it would be the first one truncated
    out. Since `location` names it explicitly, it must be rendered first and
    survive, along with its onlyUser(user) CALLER REQUIREMENT annotation."""
    monkeypatch.setattr(pqr, "CALLABLE_API_BUDGET", 250)
    symbol_index = SymbolIndex.build(two_contract_project) if use_ast else None
    capi = pqr.build_callable_api(two_contract_project, LOCATION, symbol_index)
    assert "function cancel(" in capi
    assert "onlyUser(user)" in capi


def test_file_manifest_uses_real_contract_names(two_contract_project):
    """Feature 007 T020: file map names come from the parsed AST, not the
    filename — verified here on a project where they happen to match, and against
    the real target (see test_solidity_index.py) where they don't."""
    idx = SymbolIndex.build(two_contract_project)
    fm = pqr.build_file_manifest(two_contract_project, idx)
    assert "FooCDO:" in fm
    assert "Foo:" in fm


# ── Feature 008: native tool-calling round-trip ─────────────────────────────

LOOKUP_FIXTURE_SRC = """
pragma solidity ^0.8.28;

interface ICooldown {
    struct TBalanceState {
        uint256 pending;
        uint256 claimable;
    }
}
"""


@pytest.fixture
def lookup_fixture_project(tmp_path: Path) -> Path:
    (tmp_path / "Cooldown.sol").write_text(LOOKUP_FIXTURE_SRC, encoding="utf-8")
    return tmp_path


class _FakeMarkerClient:
    """Scripted client.generate() for the spec 007 text-marker round-trip."""
    def __init__(self, responses):
        self._responses = list(responses)
        self.prompts: list[str] = []

    def generate(self, prompt, options=None):
        self.prompts.append(prompt)
        return self._responses.pop(0)


class _FakeToolClient:
    """Scripted client.chat() for the native tool-calling round-trip."""
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[list[dict]] = []

    def chat(self, messages, tools=None, options=None):
        self.calls.append(messages)
        return self._responses.pop(0)


def test_tool_and_marker_protocols_render_lookup_identically(lookup_fixture_project):
    """SC-002: switching transport must not change WHAT gets resolved or how a
    result is rendered — both protocols call the SAME _render_lookup_response(),
    so the content a model actually sees must be byte-identical."""
    idx = SymbolIndex.build(lookup_fixture_project)
    expected = pqr._render_lookup_response([("TBalanceState", idx.lookup("TBalanceState"))])
    assert "pending" in expected and "claimable" in expected  # sanity: real content

    marker_client = _FakeMarkerClient(["LOOKUP: TBalanceState", "final marker source"])
    pqr._generate_with_lookups(marker_client, "BASE PROMPT", {}, idx, 3, None)
    assert expected in marker_client.prompts[-1]

    tool_client = _FakeToolClient([
        {"role": "assistant", "content": "",
         "tool_calls": [{"function": {"name": "lookup_symbol", "arguments": {"name": "TBalanceState"}}}]},
        {"role": "assistant", "content": "final tool source", "tool_calls": []},
    ])
    pqr._generate_with_tool_calls(tool_client, "BASE PROMPT", {}, idx, 3, None)
    tool_msg_contents = [m["content"] for m in tool_client.calls[-1] if m.get("role") == "tool"]
    assert tool_msg_contents == [expected]


def test_tool_calls_respect_budget_and_log_each(lookup_fixture_project):
    idx = SymbolIndex.build(lookup_fixture_project)
    logged: list[tuple[str, bool, int]] = []
    tool_client = _FakeToolClient([
        {"role": "assistant", "content": "",
         "tool_calls": [{"function": {"name": "lookup_symbol", "arguments": {"name": "TBalanceState"}}}]},
        {"role": "assistant", "content": "",
         "tool_calls": [{"function": {"name": "lookup_symbol", "arguments": {"name": "NotReal"}}}]},
        {"role": "assistant", "content": "final", "tool_calls": []},
    ])
    result = pqr._generate_with_tool_calls(
        tool_client, "BASE", {}, idx, budget=1,
        on_lookup=lambda name, resolved, n: logged.append((name, resolved, n)),
    )
    # budget=1: only the FIRST call resolves; the second turn's tool_calls hits
    # used >= budget and the round-trip stops, returning that turn's content.
    assert logged == [("TBalanceState", True, 1)]
    assert result == ""  # the turn that hit budget exhaustion had empty content


def test_tool_call_missing_name_argument_is_unresolved(lookup_fixture_project):
    """Edge case (spec.md): a malformed tool call must not crash — treated as
    an unresolved lookup, logged, counted against budget."""
    idx = SymbolIndex.build(lookup_fixture_project)
    logged = []
    tool_client = _FakeToolClient([
        {"role": "assistant", "content": "",
         "tool_calls": [{"function": {"name": "lookup_symbol", "arguments": {}}}]},
        {"role": "assistant", "content": "final", "tool_calls": []},
    ])
    result = pqr._generate_with_tool_calls(
        tool_client, "BASE", {}, idx, budget=3,
        on_lookup=lambda name, resolved, n: logged.append((name, resolved, n)),
    )
    assert logged == [("", False, 0)]
    assert result == "final"


def test_raw_function_tag_leaked_as_text_is_parsed_not_written(lookup_fixture_project):
    """Live H-01 run (2026-07-05): qwen3-coder:30b's first attempt wrote
    `<function=lookup_symbol>` as literal content instead of populating Ollama's
    structured tool_calls field, and it leaked into the PoC file as line 1,
    breaking compilation. The round-trip must parse this as a real lookup
    request instead of returning it as final source."""
    idx = SymbolIndex.build(lookup_fixture_project)
    logged = []
    tool_client = _FakeToolClient([
        {"role": "assistant",
         "content": '<function=lookup_symbol>{"name": "TBalanceState"}</function>',
         "tool_calls": []},
        {"role": "assistant", "content": "final clean source", "tool_calls": []},
    ])
    result = pqr._generate_with_tool_calls(
        tool_client, "BASE", {}, idx, budget=3,
        on_lookup=lambda name, resolved, n: logged.append((name, resolved, n)),
    )
    assert logged == [("TBalanceState", True, 1)]
    assert result == "final clean source"
    assert "<function=" not in result


def test_raw_function_tag_stripped_even_if_never_resolved(lookup_fixture_project):
    """If a raw <function=...> fragment appears on the FINAL turn (budget
    already exhausted, or unparseable), it must still never reach the returned
    source — FR-007."""
    idx = SymbolIndex.build(lookup_fixture_project)
    tool_client = _FakeToolClient([
        {"role": "assistant",
         "content": 'some prose <function=lookup_symbol>garbage</function> more text',
         "tool_calls": []},
    ])
    result = pqr._generate_with_tool_calls(tool_client, "BASE", {}, idx, budget=0, on_lookup=None)
    assert "<function=" not in result
    assert "some prose" in result and "more text" in result


# ── Feature 008: protocol selection (contracts/protocol-selection.md) ──────

class _StubClient:
    def __init__(self, supports, model="qwen-test"):
        self._supports = supports
        self.model = model

    def supports_tools(self):
        return self._supports


def test_auto_selects_tool_when_capable():
    assert pqr._select_protocol("auto", _StubClient(True)) == ("tool", "detected")


def test_auto_selects_marker_when_not_tool_capable():
    assert pqr._select_protocol("auto", _StubClient(False)) == ("marker", "detected")


def test_forced_tool_protocol_errors_on_incapable_model():
    with pytest.raises(SystemExit) as exc_info:
        pqr._select_protocol("tool", _StubClient(False))
    assert exc_info.value.code == 2


def test_forced_marker_protocol_on_capable_model():
    assert pqr._select_protocol("marker", _StubClient(True)) == ("marker", "forced")


def test_forced_tool_protocol_on_capable_model():
    assert pqr._select_protocol("tool", _StubClient(True)) == ("tool", "forced")
