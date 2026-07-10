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


def test_tool_call_wrapper_leaked_as_text_is_parsed(lookup_fixture_project):
    """Live H-01 run (2026-07-06): the SAME model, a DIFFERENT raw-text leak
    format — the generic Hermes/Qwen <tool_call>{...}</tool_call> wrapper,
    distinct from <function=name> (2026-07-05's finding). Both are real,
    recurring shapes this build falls back to; both must be parsed as real
    lookup requests, not written to the PoC file."""
    idx = SymbolIndex.build(lookup_fixture_project)
    logged = []
    tool_client = _FakeToolClient([
        {"role": "assistant",
         "content": '<tool_call>{"name": "lookup_symbol", "arguments": {"name": "TBalanceState"}}</tool_call>',
         "tool_calls": []},
        {"role": "assistant", "content": "final clean source", "tool_calls": []},
    ])
    result = pqr._generate_with_tool_calls(
        tool_client, "BASE", {}, idx, budget=3,
        on_lookup=lambda name, resolved, n: logged.append((name, resolved, n)),
    )
    assert logged == [("TBalanceState", True, 1)]
    assert result == "final clean source"


def test_orphan_tool_call_marker_stripped(lookup_fixture_project):
    """Live H-01 run (2026-07-06): a bare `</tool_call>` leaked as line 1 of
    the final answer with NO matching opening tag anywhere in that turn's
    content (the model's earlier turns had already made real structured tool
    calls; only this stray closing marker leaked into the code-writing turn).
    Must be stripped even though there's nothing to parse as a call."""
    idx = SymbolIndex.build(lookup_fixture_project)
    tool_client = _FakeToolClient([
        {"role": "assistant", "content": "</tool_call>\npragma solidity ^0.8.28;\ncontract X {}",
         "tool_calls": []},
    ])
    result = pqr._generate_with_tool_calls(tool_client, "BASE", {}, idx, budget=3, on_lookup=None)
    assert "tool_call" not in result
    assert "pragma solidity" in result


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


# ── mechanism_signal: description as a candidate source, not just location ──

def test_mechanism_signal_falls_back_to_description_when_location_is_bare():
    """Live H-01 run (2026-07-06): extraction is non-deterministic — location
    degraded to a bare filename ("Foo.sol", no method names) on one
    run even though the SAME finding's description names the real mechanism
    (`coverage()`, `cancel()`) in markdown code spans. A PoC that reached a
    real fork PASS with zero structural defects while testing something
    entirely unrelated (a generic "revert on zero shares" sanity check) went
    undetected because mechanism_signal only checked location, which was
    blind this run. checked/called must reflect the description's real
    mechanism, not silently return empty."""
    vacuous_but_structurally_clean_code = """
    contract PoC_H_01 is Base {
        function testRevertWhenRequestRedeemWithZeroShares() public {
            vm.expectRevert();
            IFoo(x).requestRedeem(a, b, c, d, 0, 0, 0);
        }
    }
    """
    description = (
        "A redeemer can lock fee-free padding shares into the silo to shift "
        "`coverage()` into the least-restrictive tier before their real "
        "redemption in the same block, then reclaim the padding via "
        "`cancel()`, which has no minimum-dwell or forfeit check."
    )
    result = pqr.mechanism_signal(vacuous_but_structurally_clean_code, "Foo.sol", description)
    assert result["checked"] == ["coverage", "cancel"]
    assert result["called"] == []


def test_mechanism_signal_description_extraction_is_precise_not_noisy():
    """Backtick-quoted method references in prose (`coverage()`) must be
    extracted precisely — not diluted by ordinary English words in the same
    sentence (before/which/meant/enforce/...), which would drown the
    diagnostic signal in noise even though it's already diagnostic-only."""
    description = (
        "This bypasses the cooldown lock that tier is meant to enforce via "
        "`cancel()`."
    )
    result = pqr.mechanism_signal("contract X {}", "", description)
    assert result["checked"] == ["cancel"]


# ── scaffold_missing_types: the scaffold structurally can't deploy a target ──

def test_scaffold_missing_types_flags_undeclared_contract():
    """Live H-01 finding (2026-07-06): the auto-discovered scaffold deployed
    ERC20Foo but declared no Foo at all — no attempt could
    ever succeed regardless of grounding quality, and this cost several live
    attempts to notice by hand. A scaffold mentioning the name elsewhere
    (import, comment) must not count as providing it — only a real state
    variable declaration of that type does."""
    scaffold = """
    import { Foo } from "./Foo.sol";
    contract Base {
        ERC20Foo internal erc20Cooldown;
        // Foo is mentioned here but never declared as a state var
    }
    """
    assert pqr.scaffold_missing_types(scaffold, ["Foo"]) == ["Foo"]


def test_scaffold_missing_types_empty_when_declared():
    scaffold = "contract Base { Foo internal sharesCooldown; }"
    assert pqr.scaffold_missing_types(scaffold, ["Foo"]) == []


def test_scaffold_missing_types_against_real_scaffolds():
    """Non-synthetic sanity check against this session's actual target
    project, if present on this machine — skipped elsewhere."""
    bad = Path("/Users/ramilmustafin/Projects/Contests/2026-06-foo-bb/contracts/"
               "test/PoC/Guardian/FooProtocolDeploymentBase.sol")
    good = Path("/Users/ramilmustafin/Projects/Contests/2026-06-foo-bb/contracts/"
                "audit/proof-of-code-composer/base/ReviewerFooBase.sol")
    if not bad.is_file() or not good.is_file():
        pytest.skip("external target project not present on this machine")
    assert pqr.scaffold_missing_types(bad.read_text(), ["Foo"]) == ["Foo"]
    assert pqr.scaffold_missing_types(good.read_text(), ["Foo"]) == []


# ── Feature 009 US1: verdict gates + deterministic repair helpers ──────────
# These functions DECIDE pass/fail/compiled/vacuous/stall. Before spec 009 they
# had zero direct tests — the exact gates where a bug becomes a false milestone
# (spec 006 traces to a `_compiled` denylist bug caught only in a live run). Each
# test pins a bug class actually seen this session, offline, synthetic input only.


def test_compiled_positive_signal_only():
    """SC-001/FR-002: `_compiled` must key on the POSITIVE 'Ran N tests' signal,
    never on the absence of a known failure phrase. A real compile failure worded
    differently from any anticipated denylist entry (the spec-006 incident:
    'Encountered invalid solc version') must read as NOT compiled."""
    # genuine run of a suite → compiled, regardless of pass/fail/revert after
    assert pqr._compiled("Ran 2 tests for audit/poc/H_01.t.sol", "") is True
    assert pqr._compiled("Ran 1 test for X\n[FAIL: Revert] testX()", "") is True
    # the exact spec-006 class: a real compile failure with an unanticipated message
    assert pqr._compiled("Error: Encountered invalid solc version in ...", "") is False
    assert pqr._compiled("Compiler run failed:\nError (2904): Declaration not found", "") is False
    # empty / whitespace / truncated output → not compiled, never an exception
    assert pqr._compiled("", "") is False
    assert pqr._compiled("   \n  ", "") is False


def test_poc_defects_flags_empty_mock_and_missing_import():
    """FR-003: the vacuous-PoC gate flags (a) an empty/commented body with no
    assertion, (b) a re-declared/mocked target contract, (c) a missing target
    import — the three evasions seen 2026-07-05."""
    # (a) no active assertion — empty/commented test
    empty = "contract PoC is Base { function test() public { /* nothing */ } }"
    assert any("no active assertion" in d for d in pqr._poc_defects(empty, ["Target"], scaffold_used=True))
    # (b) re-declares the real target as an inline mock
    mock = ("import {Test} from 'forge-std/Test.sol';\n"
            "contract Target { }\n"
            "contract PoC is Base { function test() public { assertTrue(true); } }")
    assert any("re-declares" in d for d in pqr._poc_defects(mock, ["Target"], scaffold_used=True))
    # (c) missing target import (non-scaffold path — must import the target itself)
    noimport = ("import {Test} from 'forge-std/Test.sol';\n"
                "contract PoC { function test() public { assertTrue(true); } }")
    assert any("does not import the real target" in d for d in pqr._poc_defects(noimport, ["Target"], scaffold_used=False))
    # a clean scaffold-inheriting PoC that asserts and imports the target → no defects
    clean = ("import {Target} from '../src/Target.sol';\n"
             "contract PoC is Base { function test() public { assertEq(target.x(), 1); } }")
    assert pqr._poc_defects(clean, ["Target"], scaffold_used=True) == []


def test_stall_signature_keys_on_message_not_line():
    """FR-004: a repeated identical error must produce the SAME stall signature even
    when its reported line number shifts between attempts (the model rewrites the
    whole file, so lines move). Root-caused 2026-07-05: a line-keyed signature
    missed 4 of 5 real H-01 stalls."""
    a = "Error (7576): Undeclared identifier.\n  --> audit/poc/H_01.t.sol:53:9:"
    b = "Error (7576): Undeclared identifier.\n  --> audit/poc/H_01.t.sol:48:9:"
    assert pqr._error_signature(a) == pqr._error_signature(b)
    assert pqr._error_signature(a) == ("Undeclared identifier.",)
    # a different error message → different signature
    c = "Error (2904): Declaration not found."
    assert pqr._error_signature(c) != pqr._error_signature(a)
    # runtime FAIL reason signature, independent of gas/line noise
    assert pqr._fail_signature("[FAIL: EvmError: Revert] testA() (gas: 44300562)") == ("EvmError: Revert",)


def test_targeted_hints_resolve_member_and_path_errors():
    """FR-005: `_targeted_hints`/`_sig_by_method` turn a compiler error into an
    authoritative fix against the real signatures/paths, not a hope."""
    callable_api = "// Foo — real callable signatures:\nfunction cancel(address vault, uint256 i) external;"
    file_map = "Foo: ../../contracts/tranches/base/cooldown/Foo.sol"
    # 9582 member-not-found → list the contract's real functions
    member_err = 'Error (9582): Member "setFoo" not found or not visible after argument-dependent lookup in contract Foo.'
    hints = pqr._targeted_hints(member_err, callable_api, file_map)
    assert "setFoo" in hints and "cancel" in hints
    # 6275 source-not-found → the real import path
    src_err = 'Error (6275): Source "IFooCooldown.sol" not found'
    hints2 = pqr._targeted_hints(src_err, callable_api, file_map)
    assert "IFooCooldown" in hints2
    # _sig_by_method finds a signature by method name across blocks
    assert "cancel" in pqr._sig_by_method(callable_api, "cancel")
    assert pqr._sig_by_method(callable_api, "nonexistent") == ""


def test_fix_setup_override_strips_and_reinjects():
    """FR-005: `_fix_setup_override` removes a PoC's own setUp() (which 4334s against
    a non-virtual base) and re-injects its statements into the first test."""
    # a realistic multi-line setUp body (how a model actually drafts it): the
    # fixer drops the `super.setUp()` line and re-injects the remaining statements.
    code = ("contract PoC is Base {\n"
            "    function setUp() public override {\n"
            "        super.setUp();\n"
            "        deal(USDE, address(this), 10e18);\n"
            "    }\n"
            "    function test_x() public { assertTrue(true); }\n"
            "}")
    fixed, changed = pqr._fix_setup_override(code)
    assert changed is True
    assert "function setUp" not in fixed
    assert "deal(USDE" in fixed  # statement re-injected into the test body
    assert "super.setUp" not in fixed  # the base-call line is dropped
    # a PoC with no own setUp is left untouched
    clean = "contract PoC is Base { function test_x() public { assertTrue(true); } }"
    _, changed2 = pqr._fix_setup_override(clean)
    assert changed2 is False


def test_fix_import_paths_repairs_bare_spdx(tmp_path):
    """FR-005: `_fix_import_paths` restores a bare SPDX line's `//` (a 2314 syntax
    error) line-by-line without touching other lines."""
    code = "SPDX-License-Identifier: MIT\npragma solidity ^0.8.28;\ncontract PoC {}"
    fixed, changed = pqr._fix_import_paths(code, tmp_path)
    assert changed is True
    assert fixed.startswith("// SPDX-License-Identifier: MIT")
    assert "pragma solidity ^0.8.28;" in fixed  # untouched


def test_revert_hints_quotes_fail_and_finding():
    """FR-005: `revert_hints` (compiled-but-reverted feedback) quotes forge's real
    [FAIL...] line plus the finding text, and returns '' when there is no FAIL."""
    task = {"title": "Same-block silo padding", "description": "shift coverage() then cancel()"}
    out = pqr.revert_hints("Ran 1 test\n[FAIL: EvmError: Revert] testX() (gas: 1)", "", task)
    assert "EXPLOIT-LOGIC" in out and "EvmError: Revert" in out and "silo padding" in out
    assert pqr.revert_hints("Ran 1 test\n[PASS] testX()", "", task) == ""


# ── Feature 009 US3: scaffold sufficiency understands inheritance ──────────

def test_scaffold_missing_types_sees_inherited_var(tmp_path):
    """FR-008/SC-004: a scaffold whose needed type's state variable is declared in a
    PARENT base it inherits must NOT be reported missing. The pre-009 single-file
    regex was blind to this (it only saw the one file's text), the exact
    regex-fragility class specs 007/008 moved away from."""
    (tmp_path / "Parent.sol").write_text(
        "pragma solidity ^0.8.28;\ncontract FooDeploy { Foo internal sharesCooldown; }",
        encoding="utf-8")
    idx = SymbolIndex.build(tmp_path)
    scaffold = "pragma solidity ^0.8.28;\ncontract ReviewerBase is FooDeploy { address alice; }"
    # AST + inheritance-aware: not missing (provided via the inherited parent)
    assert pqr.scaffold_missing_types(scaffold, ["Foo"], idx) == []
    # the old regex path (no index) is blind to inheritance → false-flags it,
    # which is exactly the bug US3 fixes.
    assert pqr.scaffold_missing_types(scaffold, ["Foo"], None) == ["Foo"]


def test_scaffold_missing_types_still_flags_truly_absent(tmp_path):
    """A scaffold that declares nothing of the needed type anywhere in its chain IS
    reported missing (the real H-01 case: FooProtocolDeploymentBase provides
    ERC20Foo, never Foo)."""
    (tmp_path / "Parent.sol").write_text(
        "pragma solidity ^0.8.28;\ncontract Deploy { ERC20Foo internal erc20Cooldown; }",
        encoding="utf-8")
    idx = SymbolIndex.build(tmp_path)
    scaffold = "pragma solidity ^0.8.28;\ncontract Base is Deploy { address alice; }"
    assert pqr.scaffold_missing_types(scaffold, ["Foo"], idx) == ["Foo"]


def test_scaffold_missing_types_direct_declaration_via_ast(tmp_path):
    """A directly-declared state var is seen by the AST path too (and, unlike the
    regex, a bare mention in an import/comment does NOT count as provided)."""
    idx = SymbolIndex.build(tmp_path)  # empty project index
    direct = "pragma solidity ^0.8.28;\ncontract Base { Foo internal sharesCooldown; }"
    assert pqr.scaffold_missing_types(direct, ["Foo"], idx) == []
    # name only in an import + comment, never a real state var → still missing
    mention_only = ("pragma solidity ^0.8.28;\n"
                    "import {Foo} from './x.sol';\n"
                    "// Foo is referenced here but not declared\n"
                    "contract Base { address alice; }")
    assert pqr.scaffold_missing_types(mention_only, ["Foo"], idx) == ["Foo"]


# ── Feature 010: mutation-based PASS verification ──────────────────────────

import subprocess as _subprocess

_SYNTH_REPORT = '''
## Findings
[88] **1. Same-block silo padding lets a redeemer self-select the exit-tier**

**Fix**
```diff
--- a/src/A.sol
+++ b/src/A.sol
@@ -1,2 +1,3 @@
 contract A {
+    uint256 public added;
 }
```
---
[75] **2. finalizeWithFee checks the wrong owner cap**

(no fix block for this finding)
---
'''


def test_extract_fix_verbatim():
    """FR-001/R1: the finding's fenced diff is pulled byte-for-byte; a finding with
    no `**Fix**` block yields None (e.g. the report's finding #2/#4)."""
    fix1 = pqr.extract_fix_for_finding(
        _SYNTH_REPORT, {"id": "H-01", "title": "Same-block silo padding lets a redeemer self-select the exit-tier"})
    assert fix1 is not None
    assert "+    uint256 public added;" in fix1  # verbatim, indentation intact
    assert "--- a/src/A.sol" in fix1
    fix2 = pqr.extract_fix_for_finding(
        _SYNTH_REPORT, {"id": "H-02", "title": "finalizeWithFee checks the wrong owner cap"})
    assert fix2 is None
    # a title that matches nothing → no confident section → None, never a wrong diff
    assert pqr.extract_fix_for_finding(_SYNTH_REPORT, {"id": "X", "title": "totally unrelated topic here"}) is None


def _init_git_project(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "A.sol").write_text("contract A {\n}\n", encoding="utf-8")
    _subprocess.run(["git", "init", "-q"], cwd=tmp_path)
    return tmp_path


def test_git_apply_real_diff(tmp_path):
    """FR-009: a real unified diff applies via standard tooling; a non-applying diff
    reports failure (no fuzzy patching)."""
    proj = _init_git_project(tmp_path)
    good = ("--- a/src/A.sol\n+++ b/src/A.sol\n@@ -1,2 +1,3 @@\n"
            " contract A {\n+    uint256 public added;\n }\n")
    assert pqr._git_apply(proj, good) is True
    assert "added" in (proj / "src" / "A.sol").read_text()
    # a diff that references a nonexistent file / wrong context → clean failure
    bad = ("--- a/src/Nope.sol\n+++ b/src/Nope.sol\n@@ -1,1 +1,2 @@\n"
           " contract Nope {}\n+// x\n")
    assert pqr._git_apply(proj, bad) is False


class _MutResult:
    def __init__(self, passed, stdout="Ran 1 test\n[PASS] t()", stderr=""):
        self.passed = passed
        self.exit_code = 0 if passed else 1
        self.stdout = stdout if passed else "Compiler run failed:\nRan 1 test\n[FAIL: Revert] t()"
        self.stderr = stderr


def _mut_project(tmp_path):
    """A real tmp 'project' with a git repo + the finding's fix target, so
    mutation_verify's copytree + git_apply run for real."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "A.sol").write_text("contract A {\n}\n", encoding="utf-8")
    _subprocess.run(["git", "init", "-q"], cwd=tmp_path)
    return tmp_path


_FIX_DIFF = ("--- a/src/A.sol\n+++ b/src/A.sol\n@@ -1,2 +1,3 @@\n"
             " contract A {\n+    uint256 public added;\n }\n")


def test_mutation_verify_verdicts(tmp_path, monkeypatch):
    """SC-001/SC-002/FR-004: patched-run FAILS → verified; patched-run PASSES →
    unverified_pass; the real project tree is unchanged after either."""
    proj = _mut_project(tmp_path)
    before = (proj / "src" / "A.sol").read_text()
    task = {"id": "H-01", "title": "t", "fix": _FIX_DIFF}
    events = []

    # patched-run FAILS → the exploit genuinely depends on the bug → verified
    monkeypatch.setattr(pqr, "run_tests", lambda *a, **k: _MutResult(passed=False))
    assert pqr.mutation_verify(proj, task, "audit/poc/H_01.t.sol", object(), events.append) == "verified"
    assert events[-1]["event"] == "mutation_verified"

    # patched-run still PASSES → it wasn't testing the exploit → unverified_pass
    events.clear()
    monkeypatch.setattr(pqr, "run_tests", lambda *a, **k: _MutResult(passed=True))
    assert pqr.mutation_verify(proj, task, "audit/poc/H_01.t.sol", object(), events.append) == "unverified_pass"
    assert events[-1]["event"] == "mutation_unverified"

    # FR-004: the real source tree is byte-for-byte unchanged (all work on a copy)
    assert (proj / "src" / "A.sol").read_text() == before


def test_mutation_verify_unavailable(tmp_path, monkeypatch):
    """FR-005/FR-006: no fix / diff won't apply / patched won't build / infra error
    all return 'unavailable' — never a downgrade."""
    proj = _mut_project(tmp_path)
    events = []

    # no fix
    assert pqr.mutation_verify(proj, {"id": "H", "title": "t"}, "p.t.sol", object(), events.append) == "unavailable"
    assert events[-1]["reason"] == "no_fix"

    # diff won't apply
    bad_task = {"id": "H", "title": "t", "fix": "--- a/src/Nope.sol\n+++ b/src/Nope.sol\n@@ -1 +1,2 @@\n x\n+y\n"}
    events.clear()
    assert pqr.mutation_verify(proj, bad_task, "p.t.sol", object(), events.append) == "unavailable"
    assert events[-1]["reason"] == "patch_failed"

    # patched source builds-fails (not "Ran N tests") → patched_no_build, not a downgrade
    good_task = {"id": "H", "title": "t", "fix": _FIX_DIFF}
    events.clear()
    monkeypatch.setattr(pqr, "run_tests", lambda *a, **k: type("R", (), {
        "passed": False, "exit_code": 1, "stdout": "Compiler run failed: Error (1): x", "stderr": ""})())
    assert pqr.mutation_verify(proj, good_task, "p.t.sol", object(), events.append) == "unavailable"
    assert events[-1]["reason"] == "patched_no_build"

    # infra error on the re-run → unavailable(infra), never a downgrade
    events.clear()
    def _boom(*a, **k): raise RuntimeError("sandbox down")
    monkeypatch.setattr(pqr, "run_tests", _boom)
    assert pqr.mutation_verify(proj, good_task, "p.t.sol", object(), events.append) == "unavailable"
    assert events[-1]["reason"] == "infra"


# ── Feature 011: scaffold synthesis ────────────────────────────────────────

class _FakeGenClient:
    """A client whose .generate returns scripted text (for synthesize_scaffold)."""
    def __init__(self, text):
        self._text = text
    def generate(self, prompt, options=None):
        return self._text


def _synth_project(tmp_path):
    """A tmp project with the missing contract's real source, so
    read_location_source finds it and synthesize_scaffold can ground on it."""
    (tmp_path / "contracts").mkdir()
    (tmp_path / "contracts" / "Foo.sol").write_text(
        "// SPDX-License-Identifier: MIT\npragma solidity ^0.8.28;\n"
        "contract Foo { constructor() {} }\n", encoding="utf-8")
    return tmp_path


_SYNTH_BASE_CODE = ("// SPDX-License-Identifier: MIT\npragma solidity ^0.8.28;\n"
                    "abstract contract SynthBase_H_01 is ExistingBase {\n"
                    "    Foo internal sharesCooldown;\n"
                    "    function setUpSynth() internal { sharesCooldown = new Foo(); }\n"
                    "}\n")
_COMPILE_OK = type("R", (), {"passed": True, "exit_code": 0,
                             "stdout": "Ran 1 test for audit/poc/_synth_smoke.t.sol", "stderr": ""})()
_COMPILE_FAIL = type("R", (), {"passed": False, "exit_code": 1,
                               "stdout": "Compiler run failed:\nError (7576): Undeclared identifier.", "stderr": ""})()


def test_synthesize_scaffold_accepts_compiling(tmp_path, monkeypatch):
    """SC-001/FR-004: a synthesized base that COMPILES is accepted — returned as a
    Path under the untracked audit area, with a `scaffold_synthesized` event."""
    proj = _synth_project(tmp_path)
    monkeypatch.setattr(pqr, "run_tests", lambda *a, **k: _COMPILE_OK)
    events = []
    path = pqr.synthesize_scaffold(
        proj, {"id": "H-01", "title": "t", "location": "Foo", "description": "d"},
        ["Foo"], "abstract contract ExistingBase {}", None,
        _FakeGenClient(_SYNTH_BASE_CODE), object(), events.append)
    assert path is not None
    assert path.exists() and "audit/poc/_synth" in str(path)
    assert events[-1]["event"] == "scaffold_synthesized"


def test_synthesize_writes_only_audit_area(tmp_path, monkeypatch):
    """FR-006/SC-004: tracked source is unchanged; the smoke test is cleaned up; a
    rejected base is removed."""
    proj = _synth_project(tmp_path)
    src_before = (proj / "contracts" / "Foo.sol").read_text()
    monkeypatch.setattr(pqr, "run_tests", lambda *a, **k: _COMPILE_FAIL)
    events = []
    path = pqr.synthesize_scaffold(
        proj, {"id": "H-01", "title": "t", "location": "Foo", "description": "d"},
        ["Foo"], "", None, _FakeGenClient(_SYNTH_BASE_CODE), object(), events.append)
    assert path is None  # didn't compile → discarded
    assert (proj / "contracts" / "Foo.sol").read_text() == src_before  # tracked src untouched
    assert not (proj / "audit" / "poc" / "_synth_smoke.t.sol").exists()  # smoke cleaned up
    assert not (proj / "audit" / "poc" / "_synth" / "SynthBase_H_01.sol").exists()  # rejected base removed


def test_synthesize_scaffold_failure_paths(tmp_path, monkeypatch):
    """FR-004/FR-005/SC-002: no_build / no_output / infra each → None + the right
    reason, never a used base."""
    proj = _synth_project(tmp_path)
    task = {"id": "H-01", "title": "t", "location": "Foo", "description": "d"}

    # won't compile → no_build
    monkeypatch.setattr(pqr, "run_tests", lambda *a, **k: _COMPILE_FAIL)
    ev = []
    assert pqr.synthesize_scaffold(proj, task, ["Foo"], "", None,
                                   _FakeGenClient(_SYNTH_BASE_CODE), object(), ev.append) is None
    assert ev[-1]["reason"] == "no_build"

    # model returns non-Solidity → no_output (run_tests never reached)
    ev = []
    assert pqr.synthesize_scaffold(proj, task, ["Foo"], "", None,
                                   _FakeGenClient("sorry, I cannot help"), object(), ev.append) is None
    assert ev[-1]["reason"] == "no_output"

    # infra error during validation → infra
    def _boom(*a, **k): raise RuntimeError("sandbox down")
    monkeypatch.setattr(pqr, "run_tests", _boom)
    ev = []
    assert pqr.synthesize_scaffold(proj, task, ["Foo"], "", None,
                                   _FakeGenClient(_SYNTH_BASE_CODE), object(), ev.append) is None
    assert ev[-1]["reason"] == "infra"


# ── Feature 012: harness prompt management ─────────────────────────────────

class _FakeVersionedTracer:
    """A tracer whose get_prompt_versioned returns a scripted (text, version)."""
    enabled = True
    def __init__(self, text, version):
        self._text, self._version = text, version
        self._client = None
    def get_prompt_versioned(self, name, fallback):
        return self._text, self._version


def test_resolve_prompt_fallback_when_disabled():
    """FR-002/SC-001: tracing off → the byte-exact constant + version None."""
    from sr_agent.eval.tracer import NOOP_TRACER
    text, prov = pqr._resolve_prompt(NOOP_TRACER, "poc-draft", "HELLO {who}", who="world")
    assert text == "HELLO world"
    assert prov == {"name": "poc-draft", "version": None}


def test_resolve_prompt_uses_versioned():
    """SC-002: a fetched versioned prompt is used and its version recorded."""
    tr = _FakeVersionedTracer("FETCHED {who}", 5)
    text, prov = pqr._resolve_prompt(tr, "poc-draft", "FALLBACK {who}", who="x")
    assert text == "FETCHED x"
    assert prov == {"name": "poc-draft", "version": 5}


def test_resolve_prompt_format_failure_falls_back():
    """FR-007: an edited fetched template referencing a placeholder the harness does
    NOT provide raises KeyError on .format → fall back to the constant (never
    crashes) with version None."""
    tr = _FakeVersionedTracer("EDITED with {unexpected} key", 9)  # harness passes only {who}
    text, prov = pqr._resolve_prompt(tr, "poc-draft", "FALLBACK {who}", who="x")
    assert text == "FALLBACK x"                 # fell back to the constant
    assert prov == {"name": "poc-draft", "version": None}


def test_seed_prompts_noop_when_disabled():
    """SC-005: seeding is a silent no-op with Langfuse disabled."""
    from sr_agent.eval.tracer import NOOP_TRACER
    pqr.seed_prompts(NOOP_TRACER)  # must not raise


def test_seed_prompts_creates_one_per_prompt():
    """SC-005: with a Langfuse client, one create per harness prompt, production."""
    created = []
    class _C:
        def create_prompt(self, name, prompt, labels):
            created.append((name, labels))
    tr = type("T", (), {"enabled": True, "_client": _C()})()
    pqr.seed_prompts(tr)
    assert {n for n, _ in created} == set(pqr._HARNESS_PROMPTS)
    assert all(labels == ["production"] for _, labels in created)
