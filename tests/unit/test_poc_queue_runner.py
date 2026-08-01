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
import scripts.solidity_fixers as sf
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


def test_location_names_skips_pascal_suffix_inside_camel_method():
    """calculateFooMode must not yield ExitMode as a deployable type name."""
    assert pqr._location_names(LOCATION) == ["FooCDO", "Foo"]


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
    functions - under a tight per-file budget it would be the first one truncated
    out. Since `location` names it explicitly, it must be rendered first and
    survive, along with its onlyUser(user) CALLER REQUIREMENT annotation."""
    monkeypatch.setattr(pqr, "CALLABLE_API_BUDGET", 250)
    symbol_index = SymbolIndex.build(two_contract_project) if use_ast else None
    capi = pqr.build_callable_api(two_contract_project, LOCATION, symbol_index)
    assert "function cancel(" in capi
    assert "onlyUser(user)" in capi


def test_file_manifest_uses_real_contract_names(two_contract_project):
    """Feature 007 T020: file map names come from the parsed AST, not the
    filename - verified here on a project where they happen to match, and against
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
    result is rendered - both protocols call the SAME _render_lookup_response(),
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
    """Edge case (spec.md): a malformed tool call must not crash - treated as
    an unresolved lookup, logged, counted against budget."""
    idx = SymbolIndex.build(lookup_fixture_project)
    logged = []
    tool_client = _FakeToolClient([
        {"role": "assistant", "content": "",
         "tool_calls": [{"function": {"name": "lookup_symbol", "arguments": {}}}]},
        {"role": "assistant", "content": "pragma solidity ^0.8.28;\ncontract X {}", "tool_calls": []},
    ])
    result = pqr._generate_with_tool_calls(
        tool_client, "BASE", {}, idx, budget=3,
        on_lookup=lambda name, resolved, n: logged.append((name, resolved, n)),
    )
    assert logged == [("", False, 0)]
    assert "pragma solidity" in result and "contract X" in result


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
        {"role": "assistant", "content": "pragma solidity ^0.8.28;\ncontract X {}", "tool_calls": []},
    ])
    result = pqr._generate_with_tool_calls(
        tool_client, "BASE", {}, idx, budget=3,
        on_lookup=lambda name, resolved, n: logged.append((name, resolved, n)),
    )
    assert logged == [("TBalanceState", True, 1)]
    assert "pragma solidity" in result
    assert "<function=" not in result


def test_raw_function_tag_stripped_even_if_never_resolved(lookup_fixture_project):
    """If a raw <function=...> fragment appears on the FINAL turn (budget
    already exhausted, or unparseable), it must still never reach the returned
    source - FR-007."""
    idx = SymbolIndex.build(lookup_fixture_project)
    tool_client = _FakeToolClient([
        {"role": "assistant",
         "content": '<function=lookup_symbol>garbage</function>\npragma solidity ^0.8.28;\ncontract X {}',
         "tool_calls": []},
    ])
    result = pqr._generate_with_tool_calls(tool_client, "BASE", {}, idx, budget=0, on_lookup=None)
    assert "<function=" not in result
    assert "pragma solidity" in result and "contract X" in result


def test_tool_call_wrapper_leaked_as_text_is_parsed(lookup_fixture_project):
    """Live H-01 run (2026-07-06): the SAME model, a DIFFERENT raw-text leak
    format - the generic Hermes/Qwen <tool_call>{...}</tool_call> wrapper,
    distinct from <function=name> (2026-07-05's finding). Both are real,
    recurring shapes this build falls back to; both must be parsed as real
    lookup requests, not written to the PoC file."""
    idx = SymbolIndex.build(lookup_fixture_project)
    logged = []
    tool_client = _FakeToolClient([
        {"role": "assistant",
         "content": '<tool_call>{"name": "lookup_symbol", "arguments": {"name": "TBalanceState"}}</tool_call>',
         "tool_calls": []},
        {"role": "assistant", "content": "pragma solidity ^0.8.28;\ncontract X {}", "tool_calls": []},
    ])
    result = pqr._generate_with_tool_calls(
        tool_client, "BASE", {}, idx, budget=3,
        on_lookup=lambda name, resolved, n: logged.append((name, resolved, n)),
    )
    assert logged == [("TBalanceState", True, 1)]
    assert "pragma solidity" in result


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
    """Live H-01 run (2026-07-06): extraction is non-deterministic - location
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
    extracted precisely - not diluted by ordinary English words in the same
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
    ERC20Foo but declared no Foo at all - no attempt could
    ever succeed regardless of grounding quality, and this cost several live
    attempts to notice by hand. A scaffold mentioning the name elsewhere
    (import, comment) must not count as providing it - only a real state
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
    project, if present on this machine - skipped elsewhere."""
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
# had zero direct tests - the exact gates where a bug becomes a false milestone
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
    import - the three evasions seen 2026-07-05."""
    # (a) no active assertion - empty/commented test
    empty = "contract PoC is Base { function test() public { /* nothing */ } }"
    assert any("no active assertion" in d for d in pqr._poc_defects(empty, ["Target"], scaffold_used=True))
    # (b) re-declares the real target as an inline mock
    mock = ("import {Test} from 'forge-std/Test.sol';\n"
            "contract Target { }\n"
            "contract PoC is Base { function test() public { assertTrue(true); } }")
    assert any("re-declares" in d for d in pqr._poc_defects(mock, ["Target"], scaffold_used=True))
    # (c) missing target import (non-scaffold path - must import the target itself)
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
    callable_api = "// Foo - real callable signatures:\nfunction cancel(address vault, uint256 i) external;"
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
    fixed, changed = sf._fix_setup_override(code)
    assert changed is True
    assert "function setUp" not in fixed
    assert "deal(USDE" in fixed  # statement re-injected into the test body
    assert "super.setUp" not in fixed  # the base-call line is dropped
    # a PoC with no own setUp is left untouched
    clean = "contract PoC is Base { function test_x() public { assertTrue(true); } }"
    _, changed2 = sf._fix_setup_override(clean)
    assert changed2 is False


def test_fix_import_paths_repairs_bare_spdx(tmp_path):
    """FR-005: `_fix_import_paths` restores a bare SPDX line's `//` (a 2314 syntax
    error) line-by-line without touching other lines."""
    code = "SPDX-License-Identifier: MIT\npragma solidity ^0.8.28;\ncontract PoC {}"
    fixed, _matched, changed = sf._fix_import_paths(code, tmp_path)
    assert changed is True
    assert fixed.startswith("// SPDX-License-Identifier: MIT")
    assert "pragma solidity ^0.8.28;" in fixed  # untouched


def test_fix_import_paths_base_dir_corrects_synth_depth(tmp_path):
    """The scaffold-synthesis base lives a level deeper (audit/poc/_synth/) than a drafted PoC
    (audit/poc/), so a model-written import is off by one `../`. Passing `base_dir=synth_dir`
    rewrites it to the right depth (GLM-5.2 live: the sole synth failure was this off-by-one).
    Invented names only - no target material."""
    base = tmp_path / "test" / "poc" / "base"
    base.mkdir(parents=True)
    (base / "DemoBase.sol").write_text("pragma solidity ^0.8.28;\ncontract DemoBase {}", encoding="utf-8")
    # depth correct for audit/poc/ (2 levels), but the synth file sits at audit/poc/_synth/ (3 levels)
    code = ('pragma solidity ^0.8.28;\n'
            'import { DemoBase } from "../../test/poc/base/DemoBase.sol";\n'
            'contract SynthBase is DemoBase {}')
    synth_dir = tmp_path / "audit" / "poc" / "_synth"
    fixed, _matched, changed = sf._fix_import_paths(code, tmp_path, base_dir=synth_dir)
    assert changed is True
    assert 'from "../../../test/poc/base/DemoBase.sol"' in fixed   # up 3, resolves from _synth/
    assert '"../../test/poc/base/DemoBase.sol"' not in fixed        # the off-by-one is gone
    # default base (audit/poc/) leaves the already-correct depth-2 path untouched
    same, _m2, ch2 = sf._fix_import_paths(code, tmp_path)
    assert 'from "../../test/poc/base/DemoBase.sol"' in same and ch2 is False


def test_fix_import_paths_prefixes_dot_slash_for_synth_base(tmp_path):
    """A PoC under audit/poc/ importing bare `_synth/Foo.sol` must become `./_synth/Foo.sol`
    - solc resolves bare paths from the project root, so the bare form 404s (live H-01)."""
    synth = tmp_path / "audit" / "poc" / "_synth"
    synth.mkdir(parents=True)
    (synth / "SynthBase_H_01.sol").write_text(
        "pragma solidity ^0.8.28;\nabstract contract SynthBase_H_01 {}", encoding="utf-8")
    code = (
        "pragma solidity ^0.8.28;\n"
        'import { SynthBase_H_01 } from "_synth/SynthBase_H_01.sol";\n'
        "contract PoC_H_01 is SynthBase_H_01 {}\n"
    )
    fixed, matched, changed = sf._fix_import_paths(code, tmp_path)
    assert matched and changed
    assert 'from "./_synth/SynthBase_H_01.sol"' in fixed
    assert 'from "_synth/SynthBase_H_01.sol"' not in fixed
    # already ./ -relative - idempotent
    same, _m, ch2 = sf._fix_import_paths(fixed, tmp_path)
    assert ch2 is False and 'from "./_synth/SynthBase_H_01.sol"' in same


def test_revert_hints_quotes_fail_and_finding():
    """FR-005: `revert_hints` (compiled-but-reverted feedback) quotes forge's real
    [FAIL...] line plus the finding text, and returns '' when there is no FAIL."""
    task = {"title": "Same-block silo padding", "description": "shift coverage() then cancel()"}
    out = pqr.revert_hints("Ran 1 test\n[FAIL: EvmError: Revert] testX() (gas: 1)", "", task)
    assert "EXPLOIT-LOGIC" in out and "EvmError: Revert" in out and "silo padding" in out
    assert pqr.revert_hints("Ran 1 test\n[PASS] testX()", "", task) == ""


# ── Feature 029: trace-grounded exploit-logic feedback ─────────────────────
# A SYNTHETIC forge -vvv fixture in the REAL forge format (captured from a live -vvv run, then
# renamed to invented placeholders - no target material). -vvv traces only FAILING tests: the
# passing test below has NO Traces block, exactly as forge emits.
_VVV_FIXTURE = """\
Ran 2 tests for test/Exploit.t.sol:ExploitTest
[FAIL: gate blocks the caller] testExploit() (gas: 8772)
Traces:
  [8772] ExploitTest::testExploit()
    ├─ [2453] DemoVault::probe() [staticcall]
    │   └─ ← [Return] 1
    ├─ [549] DemoVault::gate() [staticcall]
    │   └─ ← [Revert] gate blocks the caller
    └─ ← [Revert] gate blocks the caller

Backtrace:
  at DemoVault.gate
  at ExploitTest.testExploit

[PASS] testSetup() (gas: 7746)
Suite result: FAILED. 1 passed; 1 failed; 0 skipped; finished in 14.61ms
"""


def test_trace_excerpt_keeps_failing_region_drops_passing():
    """FR-004 / US1 scenario 2: `_trace_excerpt` returns the failing test's [FAIL] header + its
    Traces/Backtrace, and NOT the passing test or the run summary."""
    out = pqr._trace_excerpt(_VVV_FIXTURE)
    assert "[FAIL: gate blocks the caller]" in out
    assert "Traces:" in out and "DemoVault::gate()" in out and "← [Revert]" in out
    assert "Backtrace:" in out and "at DemoVault.gate" in out
    assert "[PASS] testSetup()" not in out          # passing test excluded
    assert "Suite result:" not in out               # summary excluded


def test_trace_excerpt_empty_without_trace():
    """FR-007 seed: a [FAIL] line WITHOUT a Traces block (default verbosity) → "", and the
    bottom-of-output 'Failing tests:' summary (also traceless) never leaks in."""
    default_verbosity = ("Ran 1 test\n[FAIL: gate blocks the caller] testExploit() (gas: 8772)\n"
                         "Suite result: FAILED. 0 passed; 1 failed; 0 skipped\n"
                         "Failing tests:\n[FAIL: gate blocks the caller] testExploit() (gas: 8772)")
    assert pqr._trace_excerpt(default_verbosity) == ""
    assert pqr._trace_excerpt("Ran 1 test\n[PASS] testSetup() (gas: 1)") == ""


def test_trace_excerpt_bounds_to_budget_keeps_revert():
    """SC-002 / FR-004: an over-budget trace is trimmed to <= budget and still shows the revert-side
    tail (the Backtrace / ← [Revert] where the exploit diverged)."""
    big = _VVV_FIXTURE.replace(
        "    ├─ [2453] DemoVault::probe() [staticcall]\n",
        "".join(f"    ├─ [{i}] DemoVault::step{i}() [staticcall]\n"
                f"    │   └─ ← [Return] {i}\n" for i in range(400)))
    out = pqr._trace_excerpt(big, budget=400)
    assert len(out) <= 400
    assert "[FAIL: gate blocks the caller]" in out   # header retained
    assert "← [Revert]" in out or "Backtrace" in out  # revert region retained


def test_revert_hints_folds_trace_and_keeps_prior_shape():
    """FR-002/FR-003/FR-007/SC-005: revert_hints includes the trace excerpt + finding text when a
    trace is present; is byte-identical to the pre-029 output when absent; and renders an
    authoritative setup-revert fix (missing approve) BEFORE the trace."""
    task = {"title": "Padding self-selects tier", "description": "probe() then gate()"}
    with_trace = pqr.revert_hints(_VVV_FIXTURE, "", task)
    assert "EXECUTION TRACE" in with_trace and "DemoVault::gate()" in with_trace
    assert "Padding self-selects tier" in with_trace and "EXPLOIT-LOGIC" in with_trace

    # No trace → byte-identical to the legacy path (compute the legacy string directly).
    fail_only = "Ran 1 test\n[FAIL: gate blocks the caller] testExploit() (gas: 1)"
    legacy = (
        "The test compiled and ran, but did NOT pass - this is an EXPLOIT-LOGIC problem, "
        "not a compile error:\n" + "[FAIL: gate blocks the caller] testExploit() (gas: 1)"[:800] +
        f"\n\nRe-read the finding and fix the SEQUENCE/PRECONDITIONS, not just syntax:\n"
        f"Title: {task['title']}\nDescription: {task['description']}\n"
        "Common causes: wrong order of calls, a precondition never actually set up "
        "(e.g. a required state/role/balance not established before the exploit step), "
        "asserting the wrong condition, or expecting a revert that the real code doesn't "
        "produce at that call (check which call in the sequence should actually revert).")
    out_no_trace = pqr.revert_hints(fail_only, "", task)
    assert "EXECUTION TRACE" not in out_no_trace
    assert out_no_trace == legacy                   # SC-005 byte-identical

    # Setup-revert fix (missing approve) FIRST, ahead of the trace (FR-003).
    with_approve = _VVV_FIXTURE.replace(
        "← [Revert] gate blocks the caller",
        "← [Revert] ERC20InsufficientAllowance(0xABCD, 0, 100)", 1)
    both = pqr.revert_hints(with_approve, "", task)
    assert both.index("approve") < both.index("EXECUTION TRACE")


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


def test_scaffold_missing_types_subtype_var_satisfies_base_need(tmp_path):
    """Subtype-aware: a base that declares a var of a SUBTYPE of the needed type
    is NOT missing it (Liskov). Live strata L-07: FooProtocolDeploymentBase
    declares `ERC20Foo erc20Cooldown` / `FooCooldown unstakeCooldown`,
    both `is CooldownBase`; a finding needing `CooldownBase` was falsely flagged
    missing → sent to synth → synth-compile-failed. The instances already exist."""
    (tmp_path / "Cooldown.sol").write_text(
        "pragma solidity ^0.8.28;\n"
        "contract CooldownBase {}\n"
        "contract ERC20Foo is CooldownBase {}\n",
        encoding="utf-8")
    (tmp_path / "Deploy.sol").write_text(
        "pragma solidity ^0.8.28;\n"
        "contract Deploy { ERC20Foo internal erc20Cooldown; }",
        encoding="utf-8")
    idx = SymbolIndex.build(tmp_path)
    scaffold = "pragma solidity ^0.8.28;\ncontract Base is Deploy { address alice; }"
    # ERC20Foo is-a CooldownBase → the need is provided → NOT missing
    assert pqr.scaffold_missing_types(scaffold, ["CooldownBase"], idx) == []
    # a SIBLING type (Foo) is NOT provided by an ERC20Foo var
    assert pqr.scaffold_missing_types(scaffold, ["Foo"], idx) == ["Foo"]
    # a LEAF need is not satisfied by a PARENT var (direction matters): a
    # CooldownBase var would not provide ERC20Foo-specific behavior
    scaffold_parent = "pragma solidity ^0.8.28;\ncontract Base2 { CooldownBase internal cb; }"
    assert pqr.scaffold_missing_types(scaffold_parent, ["ERC20Foo"], idx) == ["ERC20Foo"]


def test_scaffold_missing_types_subtype_declared_directly_in_scaffold(tmp_path):
    """The subtype match also fires when the satisfying var is declared in the
    scaffold's OWN contract (not only an inherited parent)."""
    (tmp_path / "Cooldown.sol").write_text(
        "pragma solidity ^0.8.28;\n"
        "contract CooldownBase {}\n"
        "contract FooCooldown is CooldownBase {}\n",
        encoding="utf-8")
    idx = SymbolIndex.build(tmp_path)
    scaffold = ("pragma solidity ^0.8.28;\n"
                "contract Base { FooCooldown internal unstakeCooldown; }")
    assert pqr.scaffold_missing_types(scaffold, ["CooldownBase"], idx) == []


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


# ── Hardening: a finding must NOT borrow another finding's diff via generic overlap ──
# (a wrong fix yields a FALSE `verified`, worse than `no_fix`). Real report: only the
# detailed high-severity findings carry a diff; a low-severity finding sharing generic
# audit words (`wrong`, `owner`, `slot`) with such a section used to grab its diff.
_SPURIOUS_REPORT = '''
## Findings
[1] **1. `finalizeWithFee` checks the wrong owner cap**

**Fix**
```diff
--- a/src/F.sol
+++ b/src/F.sol
@@ -1 +1 @@
-owner
+expected_owner
```
---
[2] **2. `metaKey` nonce uses the wrong reserved slot**

(no fix block - a low-severity finding)
---
[3] **3. `FooPairProvider` discards the wrong oracle observations**

(no fix block - a low-severity finding)
---
'''


def test_extract_fix_refuses_generic_only_overlap():
    """A finding whose title shares only SHORT generic words (`checks`, `wrong`, `owner`)
    with a diff-carrying section - but no finding-specific anchor - gets None, not that
    section's diff. `wrong` is in every heading (generic); `owner`/`checks` are short."""
    spurious = {"id": "L-99", "title": "guard checks the wrong owner on every call"}
    assert pqr.extract_fix_for_finding(_SPURIOUS_REPORT, spurious) is None


def test_extract_fix_matches_on_distinctive_identifier():
    """The finding that actually owns the section (shares its long, rare identifier
    `finalizeWithFee`) still gets its diff - hardening rejects noise, not real matches."""
    real = {"id": "H-01", "title": "`finalizeWithFee` checks the wrong owner cap"}
    fix = pqr.extract_fix_for_finding(_SPURIOUS_REPORT, real)
    assert fix is not None and "expected_owner" in fix


def test_extract_fix_no_diff_section_is_none_not_borrowed():
    """A low-severity finding that matches its OWN (diff-less) section returns None -
    it must not fall through to a different finding's diff."""
    meta = {"id": "L-03", "title": "`metaKey` nonce uses the wrong reserved slot"}
    assert pqr.extract_fix_for_finding(_SPURIOUS_REPORT, meta) is None


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


def test_attach_fixes_pins_both_fixes(tmp_path):
    """Feature 028 FR-003/FR-004: `_attach_fixes` gives a pinned task the SAME two fixes an extracted
    one gets - `fix` from the report (deterministic) and `fix_patch` from the operator map - so the
    pinned path can't drift from the extracted one. Shared by extract_tasks and load_pinned_tasks."""
    report = "[88] **1. Reentrancy in withdraw**\n```diff\n--- a/V.sol\n+++ b/V.sol\n@@ -1 +1 @@\n-x\n+y\n```\n"
    raw = [{"id": "1", "title": "Reentrancy in withdraw", "location": "V.withdraw", "description": "d"}]
    out = pqr._attach_fixes(raw, report, {"1": "OPERATOR-PATCH"})
    assert len(out) == 1 and out[0]["id"] == "1"
    assert out[0]["fix_patch"] == "OPERATOR-PATCH"           # operator patch attached by id
    assert out[0]["fix"] is not None                          # report diff pulled deterministically
    # a task with no title is dropped, exactly like extract_tasks
    assert pqr._attach_fixes([{"id": "x"}], report, {}) == []


def test_load_pinned_tasks_reads_file_and_attaches(tmp_path):
    """Feature 028 FR-001/FR-002: load_pinned_tasks reads the `_extracted_tasks.json`-shaped file
    and returns attached findings - no model call."""
    report = tmp_path / "r.md"; report.write_text("[88] **1. T**\n", encoding="utf-8")
    tasks = tmp_path / "tasks.json"
    tasks.write_text('[{"id":"1","title":"T","location":"L","description":"d"}]', encoding="utf-8")
    out = pqr.load_pinned_tasks(tasks, report, {"1": "P"})
    assert out[0]["id"] == "1" and out[0]["title"] == "T" and out[0]["fix_patch"] == "P"


def test_attach_fixes_passes_through_optional_class(tmp_path):
    """Feature 037 G1: a pinned task MAY carry `class` (or `finding_class`); it is preserved as
    `finding_class` so a per-model measurement is class-stratifiable (035 FR-018). Absent ⇒ "" - a
    class-unlabelled battery still loads byte-identically to before (back-compatible)."""
    report = "[88] **1. T**\n"
    # accepts `class`
    out = pqr._attach_fixes([{"id": "1", "title": "T", "class": "oracle-staleness"}], report, {})
    assert out[0]["finding_class"] == "oracle-staleness"
    # accepts `finding_class` alias
    out2 = pqr._attach_fixes([{"id": "2", "title": "T", "finding_class": "rounding"}], report, {})
    assert out2[0]["finding_class"] == "rounding"
    # absent ⇒ empty, never missing (keeps the key stable for downstream consumers)
    out3 = pqr._attach_fixes([{"id": "3", "title": "T"}], report, {})
    assert out3[0]["finding_class"] == ""


def test_dep_mounts_grafts_node_modules_readonly_into_the_container(tmp_path):
    """Feature 027 follow-up: the mutation-verify copy skips node_modules (650MB), so the patched
    build resolves `@openzeppelin/...` imports only if the ORIGINAL deps are MOUNTED read-only into
    the container at `/work/node_modules`. A copy-side host-path symlink dangles inside the container
    (it sees only the mount, not the host path), which is why strata-4 stayed `patched_no_build`."""
    proj = tmp_path / "proj"; (proj / "node_modules" / "@x").mkdir(parents=True)
    mounts = pqr._dep_mounts(proj)
    assert len(mounts) == 1
    m = mounts[0]
    assert m.host_path == proj / "node_modules"      # the ORIGINAL, not a copy
    assert m.container_path == "/work/node_modules"   # where foundry.toml `libs` expects it
    assert m.read_only is True                        # deps are never mutated by the fix
    # safe when the dep dir is absent → no mount
    assert pqr._dep_mounts(tmp_path / "nope") == []


def test_mutverify_copy_keeps_the_forge_cache():
    """Feature 027 US2 (FR-005): the falsification copy must INCLUDE the forge cache (`out`,
    `cache_forge`) so the patched rebuild is incremental, not a cold full via_ir build; it still
    skips the huge, irrelevant `.git`/`node_modules`. `_MUTVERIFY_COPY_SKIP` is the
    `shutil.ignore_patterns` callable `fn(dir, names) -> set-to-ignore`."""
    names = ["out", "cache_forge", ".git", "node_modules", "Foo.sol"]
    ignored = pqr._MUTVERIFY_COPY_SKIP("/proj", names)
    assert ".git" in ignored and "node_modules" in ignored      # still skipped (huge, irrelevant)
    assert "cache_forge" not in ignored and "out" not in ignored  # the cache is now COPIED (incremental)
    assert "Foo.sol" not in ignored                              # source files are always copied


def test_mutation_verify_verdicts(tmp_path, monkeypatch):
    """SC-001/SC-002/FR-004: patched-run FAILS → verified; patched-run PASSES →
    unverified_pass; the real project tree is unchanged after either."""
    proj = _mut_project(tmp_path)
    before = (proj / "src" / "A.sol").read_text()
    task = {"id": "H-01", "title": "t", "fix": _FIX_DIFF}
    events = []

    # patched-run FAILS → the exploit genuinely depends on the bug → verified
    # (feature 025: mutation_verify now returns a (status, reason) tuple)
    monkeypatch.setattr(pqr, "run_tests", lambda *a, **k: _MutResult(passed=False))
    assert pqr.mutation_verify(proj, task, "audit/poc/H_01.t.sol", object(), events.append) == ("verified", "")
    assert events[-1]["event"] == "mutation_verified"

    # patched-run still PASSES → it wasn't testing the exploit → unverified_pass
    events.clear()
    monkeypatch.setattr(pqr, "run_tests", lambda *a, **k: _MutResult(passed=True))
    assert pqr.mutation_verify(proj, task, "audit/poc/H_01.t.sol", object(), events.append) == ("unverified_pass", "")
    assert events[-1]["event"] == "mutation_unverified"

    # FR-004: the real source tree is byte-for-byte unchanged (all work on a copy)
    assert (proj / "src" / "A.sol").read_text() == before


def test_mutation_verify_isolates_compile_scope(tmp_path, monkeypatch):
    """A sibling PoC in POC_SUBDIR that breaks against the fix's changed ABI must NOT fail the
    finding under test: mutation_verify prunes the throwaway copy's POC_SUBDIR to the target PoC
    before compiling, so `forge test`'s dir-wide COMPILE never drags in unrelated siblings (the
    `patched_no_build` isolation bug). `_synth/` bases stay; the real tree is untouched."""
    proj = _mut_project(tmp_path)
    poc_dir = proj / pqr.POC_SUBDIR
    (poc_dir / "_synth").mkdir(parents=True)
    (poc_dir / "H_01.t.sol").write_text("// target under test\n", encoding="utf-8")
    (poc_dir / "H_03.t.sol").write_text("// sibling that breaks vs the patched ABI\n", encoding="utf-8")
    (poc_dir / "_synth" / "Base.sol").write_text("// a base the PoC imports\n", encoding="utf-8")

    seen = {}
    def _capture(copy, *a, **k):
        cp = copy / pqr.POC_SUBDIR
        seen["tsol"] = sorted(p.name for p in cp.rglob("*.t.sol"))
        seen["synth_kept"] = (cp / "_synth" / "Base.sol").is_file()
        return _MutResult(passed=False)
    monkeypatch.setattr(pqr, "run_tests", _capture)

    task = {"id": "H-01", "title": "t", "fix": _FIX_DIFF}
    status, _ = pqr.mutation_verify(proj, task, "audit/poc/H_01.t.sol", object(), [].append)
    assert status == "verified"
    assert seen["tsol"] == ["H_01.t.sol"]        # sibling H_03.t.sol pruned from the compile scope
    assert seen["synth_kept"] is True            # _synth bases kept (the PoC imports them)
    assert (poc_dir / "H_03.t.sol").is_file()    # only the COPY was pruned; real tree intact


def test_mutation_verify_unavailable(tmp_path, monkeypatch):
    """FR-005/FR-006: no fix / diff won't apply / patched won't build / infra error
    all return 'unavailable' - never a downgrade."""
    proj = _mut_project(tmp_path)
    events = []

    # no fix → ("unavailable", "no_fix")
    assert pqr.mutation_verify(proj, {"id": "H", "title": "t"}, "p.t.sol", object(), events.append) == ("unavailable", "no_fix")
    assert events[-1]["reason"] == "no_fix"

    # diff won't apply (real hunk header, but the file doesn't exist) → patch_failed
    bad_task = {"id": "H", "title": "t", "fix": "--- a/src/Nope.sol\n+++ b/src/Nope.sol\n@@ -1 +1,2 @@\n x\n+y\n"}
    events.clear()
    assert pqr.mutation_verify(proj, bad_task, "p.t.sol", object(), events.append) == ("unavailable", "patch_failed")
    assert events[-1]["reason"] == "patch_failed"

    # patched source builds-fails (not "Ran N tests") → patched_no_build, not a downgrade
    good_task = {"id": "H", "title": "t", "fix": _FIX_DIFF}
    events.clear()
    monkeypatch.setattr(pqr, "run_tests", lambda *a, **k: type("R", (), {
        "passed": False, "exit_code": 1, "stdout": "Compiler run failed: Error (1): x", "stderr": ""})())
    assert pqr.mutation_verify(proj, good_task, "p.t.sol", object(), events.append) == ("unavailable", "patched_no_build")
    assert events[-1]["reason"] == "patched_no_build"

    # infra error on the re-run → unavailable(infra), never a downgrade
    events.clear()
    def _boom(*a, **k): raise RuntimeError("sandbox down")
    monkeypatch.setattr(pqr, "run_tests", _boom)
    assert pqr.mutation_verify(proj, good_task, "p.t.sol", object(), events.append) == ("unavailable", "infra")
    assert events[-1]["reason"] == "infra"


def test_mutation_verify_operator_patch_precedence(tmp_path, monkeypatch):
    """Feature 025 US2 (FR-004/FR-005): an operator `fix_patch` is used AS-IS and wins over the
    report's `fix`. Here the operator patch applies and the report `fix` would not - proving the
    operator's was the one taken."""
    proj = _mut_project(tmp_path)
    events = []
    monkeypatch.setattr(pqr, "run_tests", lambda *a, **k: _MutResult(passed=False))
    task = {"id": "H", "title": "t",
            "fix": "--- a/src/Nope.sol\n+++ b/src/Nope.sol\n@@ -1 +1,2 @@\n x\n+y\n",  # would fail
            "fix_patch": _FIX_DIFF}                                                   # real, applies
    assert pqr.mutation_verify(proj, task, "audit/poc/H_01.t.sol", object(), events.append) == ("verified", "")


def test_mutation_verify_operator_patch_failed(tmp_path, monkeypatch):
    """US2 scenario 3 (FR-006): an operator patch that won't apply → ('unavailable','patch_failed'),
    never verified, never a failure downgrade."""
    proj = _mut_project(tmp_path)
    events = []
    task = {"id": "H", "title": "t",
            "fix_patch": "--- a/src/Nope.sol\n+++ b/src/Nope.sol\n@@ -1 +1,2 @@\n x\n+y\n"}
    assert pqr.mutation_verify(proj, task, "p.t.sol", object(), events.append) == ("unavailable", "patch_failed")


def test_fix_patch_inside_repo_rejected(tmp_path):
    """Feature 025 FR-015: an operator patch path INSIDE the agent repo is rejected - patches are
    target-specific material and must live outside. External paths parse fine."""
    import pytest
    inside = pqr._AGENT_ROOT / "some_fix.patch"
    with pytest.raises(SystemExit):
        pqr._parse_fix_patches([f"H-01={inside}"])
    # an external, existing file parses to {id: text}
    ext = tmp_path / "ext.patch"
    ext.write_text("--- a/x\n+++ b/x\n", encoding="utf-8")
    assert pqr._parse_fix_patches([f"H-01={ext}"]) == {"H-01": "--- a/x\n+++ b/x\n"}


def test_mutation_verify_reconstruction_refused(tmp_path, monkeypatch):
    """Feature 025 US4: a report `fix` that is an ILLUSTRATION whose anchor cannot be resolved →
    ('unavailable','reconstruction_refused'), and the refusal is logged - never a wrong 'verified'."""
    proj = _mut_project(tmp_path)
    events = []
    # illustrative block (no line numbers) whose anchor `struct Ghost {` exists nowhere in src/A.sol
    task = {"id": "H", "title": "t",
            "fix": "--- a/src/A.sol\n+++ b/src/A.sol\n@@ struct Ghost {\n     uint a;\n+    uint b;\n }\n"}
    assert pqr.mutation_verify(proj, task, "p.t.sol", object(), events.append) == ("unavailable", "reconstruction_refused")
    assert any(e["event"] == "reconstruction_refused" for e in events)


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
    """SC-001/FR-004: a synthesized base that COMPILES is accepted - returned as a
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


def test_synthesize_smoke_uses_relative_import(tmp_path, monkeypatch):
    """Regression: the compile-validation smoke test must import the synth base with a
    `./`-relative path. A bare `_synth/…` import resolves against the project base-path
    (`/work`), not the smoke file's dir, so solc 404'd it and synthesis always failed
    `no_build` (seen live on strata finding-1). run_tests is stubbed, so capture the smoke
    file's text at call time (it is unlinked in the finally)."""
    proj = _synth_project(tmp_path)
    captured = {}
    def _capture_run_tests(project, *a, **k):
        captured["smoke"] = (project / "audit" / "poc" / "_synth_smoke.t.sol").read_text()
        return _COMPILE_OK
    monkeypatch.setattr(pqr, "run_tests", _capture_run_tests)
    pqr.synthesize_scaffold(
        proj, {"id": "H-01", "title": "t", "location": "Foo", "description": "d"},
        ["Foo"], "abstract contract ExistingBase {}", None,
        _FakeGenClient(_SYNTH_BASE_CODE), object(), [].append)
    assert 'from "./_runs/' in captured["smoke"] and "SynthBase_H_01.sol" in captured["smoke"]
    assert captured["smoke"].count('from "./') >= 1
    assert 'from "_synth/' not in captured["smoke"]                    # never the bare form
    assert 'from "_runs/' not in captured["smoke"]                     # must be ./ relative


def test_synthesize_writes_only_audit_area(tmp_path, monkeypatch):
    """FR-006/SC-004: tracked source is unchanged; the smoke test is cleaned up; a
    rejected base never lands in live `_synth/` (evidence stays under `_runs/`)."""
    proj = _synth_project(tmp_path)
    src_before = (proj / "contracts" / "Foo.sol").read_text()
    monkeypatch.setattr(pqr, "run_tests", lambda *a, **k: _COMPILE_FAIL)
    events = []
    path = pqr.synthesize_scaffold(
        proj, {"id": "H-01", "title": "t", "location": "Foo", "description": "d"},
        ["Foo"], "", None, _FakeGenClient(_SYNTH_BASE_CODE), object(), events.append)
    assert path is None  # didn't compile → not promoted
    assert (proj / "contracts" / "Foo.sol").read_text() == src_before  # tracked src untouched
    assert not (proj / "audit" / "poc" / "_synth_smoke.t.sol").exists()  # smoke cleaned up
    assert not (proj / "audit" / "poc" / "_synth" / "SynthBase_H_01.sol").exists()  # live untouched


def test_synthesize_preserves_rejected_base_and_says_why(tmp_path, monkeypatch):
    """Observability: when synthesis gives up, (a) it says WHY the deterministic repair stopped
    (`scaffold_repair_exhausted` naming the fixers consulted), and (b) the rejected base is PRESERVED
    under `_runs/<run_id>/` as an inert `.rejected` file - never overwriting live `_synth/`."""
    proj = _synth_project(tmp_path)
    monkeypatch.setattr(pqr, "run_tests", lambda *a, **k: _COMPILE_FAIL)
    events = []
    path = pqr.synthesize_scaffold(
        proj, {"id": "H-01", "title": "t", "location": "Foo", "description": "d"},
        ["Foo"], "", None, _FakeGenClient(_SYNTH_BASE_CODE), object(), events.append,
        run_id="testrun1")

    assert path is None                                              # still rejected - bar unchanged
    synth_dir = proj / "audit" / "poc" / "_synth"
    assert not (synth_dir / "SynthBase_H_01.sol").exists()           # never leave a live .sol on fail
    rejected = proj / "audit" / "poc" / "_runs" / "testrun1" / "SynthBase_H_01.sol.rejected"
    assert rejected.exists() and "SynthBase_H_01" in rejected.read_text()   # evidence kept, inert
    names = [e["event"] for e in events]
    assert "scaffold_repair_exhausted" in names                      # the give-up is no longer silent
    ex = next(e for e in events if e["event"] == "scaffold_repair_exhausted")
    assert set(r["name"] for r in ex["fixers"]) == {
        "import_paths", "nested_imports", "undeclared_import", "address_interface"}
    assert ex["cause"] in ("repair_exhausted:resolvable", "repair_exhausted:unresolvable")
    failed = next(e for e in events if e["event"] == "scaffold_synthesis_failed")
    assert failed["rejected_base"].endswith(".rejected")             # log points at the evidence
    assert failed["cause"] == ex["cause"]                            # terminal carries the same split
    assert failed["reason"] == "repair_exhausted"


def test_synthesize_live_accepted_survives_later_fail(tmp_path, monkeypatch):
    """Promote-only: a prior live accepted base is not destroyed when a later synth attempt fails."""
    proj = _synth_project(tmp_path)
    live = proj / "audit" / "poc" / "_synth" / "SynthBase_H_01.sol"
    live.parent.mkdir(parents=True, exist_ok=True)
    live.write_text("// ACCEPTED\npragma solidity ^0.8.28;\nabstract contract SynthBase_H_01 {}\n",
                    encoding="utf-8")
    monkeypatch.setattr(pqr, "run_tests", lambda *a, **k: _COMPILE_FAIL)
    path = pqr.synthesize_scaffold(
        proj, {"id": "H-01", "title": "t", "location": "Foo", "description": "d"},
        ["Foo"], "", None, _FakeGenClient(_SYNTH_BASE_CODE), object(), [].append,
        run_id="failrun")
    assert path is None
    assert live.exists() and "ACCEPTED" in live.read_text()
    rejected = proj / "audit" / "poc" / "_runs" / "failrun" / "SynthBase_H_01.sol.rejected"
    assert rejected.exists()


def test_synthesize_reuses_live_base_without_model_call(tmp_path, monkeypatch):
    """A live base that already provides missing_types is returned without a model call
    (and without a synthesis_attempt terminal - reuse must not inflate synth rates)."""
    proj = _synth_project(tmp_path)
    live = proj / "audit" / "poc" / "_synth" / "SynthBase_H_01.sol"
    live.parent.mkdir(parents=True, exist_ok=True)
    live.write_text(
        "// SPDX-License-Identifier: MIT\npragma solidity ^0.8.28;\n"
        "abstract contract SynthBase_H_01 {\n"
        "    Foo internal sharesCooldown;\n"
        "}\n",
        encoding="utf-8",
    )

    class _Boom:
        def generate(self, *a, **k):
            raise AssertionError("model must not be called on reuse")

    events = []
    path = pqr.synthesize_scaffold(
        proj, {"id": "H-01", "title": "t", "location": "Foo", "description": "d"},
        ["Foo"], "", None, _Boom(), object(), events.append, run_id="reuse1")
    assert path == live
    assert any(e["event"] == "scaffold_reused" for e in events)
    assert not any(e.get("terminal") and e.get("level") == "synthesis_attempt" for e in events)


def test_synthesize_no_solc_is_toolchain_not_repair_exhausted(tmp_path, monkeypatch):
    """'no compiler versions available' must be no_build:toolchain (harness-infra), not
    repair_exhausted:unresolvable (synth-model) - otherwise capability smoke launders infra."""
    proj = _synth_project(tmp_path)
    no_solc = type("R", (), {
        "passed": False, "exit_code": 1, "stdout": "",
        "stderr": "Error: Found Solidity sources, but no compiler versions are available for it\n",
    })()
    monkeypatch.setattr(pqr, "run_tests", lambda *a, **k: no_solc)
    events = []
    path = pqr.synthesize_scaffold(
        proj, {"id": "H-01", "title": "t", "location": "Foo", "description": "d"},
        ["Foo"], "", None, _FakeGenClient(_SYNTH_BASE_CODE), object(), events.append,
        run_id="nosolc")
    assert path is None
    terms = [e for e in events if e.get("terminal") and e.get("level") == "synthesis_attempt"]
    assert len(terms) == 1
    assert terms[0]["cause"] == "no_build:toolchain"
    assert terms[0]["nature"] == "harness-infra"
    assert terms[0]["reason"] == "no_build"
    assert not any(e["event"] == "scaffold_repair_exhausted" for e in events)


def test_synthesize_scaffold_failure_paths(tmp_path, monkeypatch):
    """FR-004/FR-005/SC-002: no_build / no_output / infra each → None + the right
    reason, never a used base."""
    proj = _synth_project(tmp_path)
    task = {"id": "H-01", "title": "t", "location": "Foo", "description": "d"}

    # won't compile → repair_exhausted (no fixer matches a bare 7576) / terminal cause is the split
    monkeypatch.setattr(pqr, "run_tests", lambda *a, **k: _COMPILE_FAIL)
    ev = []
    assert pqr.synthesize_scaffold(proj, task, ["Foo"], "", None,
                                   _FakeGenClient(_SYNTH_BASE_CODE), object(), ev.append) is None
    assert ev[-1]["reason"] == "repair_exhausted"
    assert ev[-1]["cause"].startswith("repair_exhausted:")

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


# ── Feature 031: harden scaffold synthesis (deterministic repair pass + 9553) ──
# Invented names only - no target material.

def _forge_9553(typ, path, line):
    """A no-build forge result whose 9553 error names `typ` and points at `line` (real format)."""
    stdout = ("Compiler run failed:\n"
              "Error (9553): Invalid type for argument in function call. "
              f"Invalid implicit conversion from address to contract {typ} requested.\n"
              f"  --> {path}:{line}:9:\n")
    return type("R", (), {"passed": False, "exit_code": 1, "stdout": stdout, "stderr": ""})()


def test_fix_address_interface_wraps_flagged_line():
    """FR-004: `_fix_address_interface` wraps the 9553-flagged argument as `IThing(address(x))` on the
    exact line, edits only that line, and is idempotent."""
    code = ("// SPDX-License-Identifier: MIT\n"          # 1
            "pragma solidity ^0.8.28;\n"                  # 2
            "abstract contract SynthBase_X {\n"           # 3
            "    function s() internal {\n"               # 4
            "        reg.configure(address(thing));\n"    # 5  <- flagged
            "        other.keep(address(y));\n"           # 6  <- NOT flagged
            "    }\n}\n")                                  # 7-8
    forge = _forge_9553("IThing", "audit/poc/_synth/SynthBase_X.sol", 5).stdout
    fixed, _matched, changed = sf._fix_address_interface(code, forge)
    assert changed is True
    assert "reg.configure(IThing(address(thing)));" in fixed          # flagged line wrapped
    assert "other.keep(address(y));" in fixed                          # unflagged line untouched
    fixed2, matched2, changed2 = sf._fix_address_interface(fixed, forge)        # idempotent
    assert matched2 is True and changed2 is False and fixed2 == fixed


def test_fix_address_interface_noop_without_9553():
    """FR-005: no 9553 in the forge output → the code is returned unchanged."""
    code = "contract C { function f() public { g(address(x)); } }"
    fixed, matched, changed = sf._fix_address_interface(code, "Compiler run failed:\nError (7576): Undeclared.")
    assert matched is False and changed is False and fixed == code


def test_targeted_hints_9553_rule():
    """FR-004/FR-005: `_targeted_hints` emits the address→interface hint when the 9553 error is present,
    and stays silent otherwise (shared benefit for the drafting PoC)."""
    with_err = pqr._targeted_hints(
        "Invalid implicit conversion from address to contract IThing requested", "", "")
    assert "IThing(address(" in with_err
    without = pqr._targeted_hints("Error (7576): Undeclared identifier.", "", "")
    assert "address(" not in without


class _CountingSynthClient:
    def __init__(self, text): self._text, self.calls = text, 0
    def generate(self, prompt, options=None): self.calls += 1; return self._text


_SYNTH_TASK = {"id": "X", "title": "t", "location": "Foo", "description": "d"}
# a synth base with an address→interface bug on line 5 (contract name on line 3)
_SYNTH_BAD = ("// SPDX-License-Identifier: MIT\n"          # 1
              "pragma solidity ^0.8.28;\n"                  # 2
              "abstract contract SynthBase_X {\n"           # 3
              "    function s() internal {\n"               # 4
              "        reg.configure(address(thing));\n"    # 5
              "    }\n}\n")                                  # 6-7


def test_synth_repair_accepts_after_deterministic_fix(tmp_path, monkeypatch):
    """SC-001/SC-005: a base that fails 9553 then compiles after the deterministic fix is ACCEPTED,
    and the repair makes NO extra model call (client.generate called exactly once - the generation)."""
    (tmp_path / "audit" / "poc").mkdir(parents=True)
    results = [_forge_9553("IThing", "audit/poc/_synth/SynthBase_X.sol", 5), _COMPILE_OK]
    monkeypatch.setattr(pqr, "run_tests", lambda *a, **k: results.pop(0))
    client = _CountingSynthClient(_SYNTH_BAD)
    events = []
    path = pqr.synthesize_scaffold(tmp_path, _SYNTH_TASK, ["Foo"], "", None, client, object(), events.append)
    assert path is not None and path.exists()
    assert client.calls == 1                                       # no extra model call in the repair
    assert any(e["event"] == "scaffold_repair" for e in events)
    assert events[-1]["event"] == "scaffold_synthesized" and events[-1]["repair_rounds"] == 1
    assert "IThing(address(thing))" in path.read_text()            # the fix persisted to the base


def test_synth_repair_early_stops_on_no_fix(tmp_path, monkeypatch):
    """FR-007 / A2 case (c): a non-deterministically-fixable error (no 9553/import fix applies) → the
    pass STOPS after ONE build (no redundant recompile) and rejects."""
    (tmp_path / "audit" / "poc").mkdir(parents=True)
    calls = {"n": 0}
    def _rt(*a, **k):
        calls["n"] += 1
        return _COMPILE_FAIL                                       # 7576, nothing deterministic to fix
    monkeypatch.setattr(pqr, "run_tests", _rt)
    events = []
    path = pqr.synthesize_scaffold(tmp_path, _SYNTH_TASK, ["Foo"], "", None,
                                   _CountingSynthClient(_SYNTH_BAD), object(), events.append)
    assert path is None
    assert calls["n"] == 1                                         # early stop - not SYNTH_REPAIR_ROUNDS
    assert events[-1]["event"] == "scaffold_synthesis_failed"


def test_synth_repair_bounded_by_rounds(tmp_path, monkeypatch):
    """SC-002 / A2 case (a): a base fixable each round but never compiling runs AT MOST
    SYNTH_REPAIR_ROUNDS builds, then rejects - the bound holds."""
    (tmp_path / "audit" / "poc").mkdir(parents=True)
    # a base with a distinct wrappable line per round, so each round changes the code and continues
    lines = ["// SPDX-License-Identifier: MIT", "pragma solidity ^0.8.28;", "abstract contract SynthBase_X {",
             "    function s() internal {"]
    for i in range(pqr.SYNTH_REPAIR_ROUNDS):
        lines.append(f"        r{i}.cfg(address(p{i}));")          # lines 5, 6, 7, …
    lines += ["    }", "}"]
    base = "\n".join(lines) + "\n"
    p = "audit/poc/_synth/SynthBase_X.sol"
    results = [_forge_9553("IThing", p, 5 + i) for i in range(pqr.SYNTH_REPAIR_ROUNDS)]
    calls = {"n": 0}
    def _rt(*a, **k):
        calls["n"] += 1
        return results.pop(0)
    monkeypatch.setattr(pqr, "run_tests", _rt)
    events = []
    path = pqr.synthesize_scaffold(tmp_path, _SYNTH_TASK, ["Foo"], "", None,
                                   _CountingSynthClient(base), object(), events.append)
    assert path is None
    assert calls["n"] == pqr.SYNTH_REPAIR_ROUNDS                   # ran the full bound, no more
    assert events[-1]["event"] == "scaffold_synthesis_failed"


def test_synth_accepts_first_build_zero_repairs(tmp_path, monkeypatch):
    """SC-003: a base that compiles on the FIRST smoke build is accepted with zero repair rounds."""
    (tmp_path / "audit" / "poc").mkdir(parents=True)
    monkeypatch.setattr(pqr, "run_tests", lambda *a, **k: _COMPILE_OK)
    events = []
    path = pqr.synthesize_scaffold(tmp_path, _SYNTH_TASK, ["Foo"], "", None,
                                   _CountingSynthClient(_SYNTH_BAD), object(), events.append)
    assert path is not None
    assert events[-1]["event"] == "scaffold_synthesized" and events[-1]["repair_rounds"] == 0
    assert not any(e["event"] == "scaffold_repair" for e in events)


# ── Observability + retry (timestamps, model retry) ───────────────────────

def test_stamp_adds_ts():
    """Every log event is prefixed with a wall-clock `ts` so per-stage durations are recoverable."""
    e = pqr._stamp({"event": "tested", "attempt": 1})
    assert e["event"] == "tested" and e["attempt"] == 1 and isinstance(e["ts"], float)


def test_call_with_retry_retries_and_logs():
    """A transient model failure is retried and a `model_retry` event is logged; the successful
    value is returned."""
    calls = {"n": 0}
    def fn():
        calls["n"] += 1
        if calls["n"] < 2:
            raise pqr.OpenRouterUnavailable("read timed out")
        return "ok"
    events = []
    assert pqr._call_with_retry(fn, log=events.append, stage="draft", fid="X") == "ok"
    assert calls["n"] == 2
    assert [e["event"] for e in events] == ["model_retry"] and events[0]["stage"] == "draft"


def test_call_with_retry_reraises_after_exhausting():
    """After `attempts` transient failures the last error is re-raised (an honest give-up)."""
    import pytest
    def fn():
        raise pqr.OpenRouterUnavailable("boom")
    with pytest.raises(pqr.OpenRouterUnavailable):
        pqr._call_with_retry(fn, log=[].append, stage="fix", fid="X", attempts=2)


# ── Feature 032: deterministic compile-fixers (auto-import undeclared) ──────
# Invented names only - no target material.

def _undeclared_block(name, code="7576"):
    """A SYNTHETIC forge 7576/7920 block with `name` under the caret (real forge shape)."""
    prefix = "        uint256 z = "
    src = prefix + name + ";"
    col = len(prefix)
    msg = "Undeclared identifier." if code == "7576" else "Identifier not found or not unique."
    return (f"Error ({code}): {msg}\n  --> audit/poc/p.t.sol:9:{col+1}:\n   |\n"
            f"9 | {src}\n  | {' ' * col}{'^' * len(name)}\n")


_UND_CODE = ("// SPDX-License-Identifier: MIT\npragma solidity ^0.8.28;\n"
             "contract PoC { function t() public { uint256 z = Widget; } }")


def test_fix_undeclared_import_adds_known_symbol():
    """FR-001: an undeclared name the file-map resolves is auto-imported with its real path."""
    out, matched, applied = sf._fix_undeclared_import(
        _UND_CODE, _undeclared_block("Widget"), "Widget: contracts/Widget.sol")
    assert matched and applied and 'import { Widget } from "contracts/Widget.sol";' in out


def test_fix_undeclared_import_handles_7920_wording():
    """FR-001: the 7920 'Identifier not found' wording also triggers the import."""
    out, matched, applied = sf._fix_undeclared_import(
        _UND_CODE, _undeclared_block("Widget", "7920"), "Widget: contracts/Widget.sol")
    assert matched and applied and "import { Widget }" in out


def test_fix_undeclared_import_skips_unknown_anti_invention():
    """FR-003: a name the file-map does NOT resolve is NEVER imported (anti-invention)."""
    out, matched, applied = sf._fix_undeclared_import(
        _UND_CODE, _undeclared_block("Widget"), "Other: contracts/Other.sol")
    assert not matched and not applied and out == _UND_CODE


def test_fix_undeclared_import_mix_known_and_unknown():
    """FR-001/FR-003: only the known name is imported; the unknown is left for the model."""
    forge = _undeclared_block("Widget") + _undeclared_block("Bogus")
    out, matched, applied = sf._fix_undeclared_import(
        _UND_CODE, forge, "Widget: contracts/Widget.sol")
    assert matched and applied and "import { Widget }" in out and "import { Bogus }" not in out


def test_fix_undeclared_import_idempotent():
    """FR-002: a name already imported is not re-added."""
    fm = "Widget: contracts/Widget.sol"
    out, _, _ = sf._fix_undeclared_import(_UND_CODE, _undeclared_block("Widget"), fm)
    out2, matched2, applied2 = sf._fix_undeclared_import(out, _undeclared_block("Widget"), fm)
    assert not matched2 and not applied2 and out2 == out


def test_fix_undeclared_import_noop_without_file_map():
    """FR-007: no file-map (no index) → the transform is a no-op (never an error)."""
    out, matched, applied = sf._fix_undeclared_import(
        _UND_CODE, _undeclared_block("Widget"), "")
    assert not matched and not applied and out == _UND_CODE


def test_fix_undeclared_import_copies_from_parent_scaffold():
    """Synth secondary authority: name absent from file_map but present as a named import
    in the parent scaffold is copied (no lib/remapping search)."""
    existing = ('import { Widget } from "@deps/Widget.sol";\n'
                "abstract contract Parent {}\n")
    out, matched, applied = sf._fix_undeclared_import(
        _UND_CODE, _undeclared_block("Widget", "7920"), "", existing=existing)
    assert matched and applied
    assert 'import { Widget } from "@deps/Widget.sol";' in out


def test_fix_undeclared_import_file_map_wins_over_scaffold():
    existing = 'import { Widget } from "@deps/Wrong.sol";\n'
    out, matched, applied = sf._fix_undeclared_import(
        _UND_CODE, _undeclared_block("Widget"),
        "Widget: contracts/Widget.sol", existing=existing)
    assert matched and applied
    assert 'from "contracts/Widget.sol"' in out
    assert "@deps/Wrong" not in out


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


# ── Robust task extraction for hosted/reasoning models (empty/fenced replies) ──
class _FakeExtractClient:
    """A generate() that returns scripted replies (for extract_tasks)."""
    def __init__(self, replies):
        self._r = list(replies)
    def generate(self, prompt, fmt=None, options=None):
        return self._r.pop(0)


_ONE_TASK = '{"tasks":[{"id":"1","title":"t","location":"L","description":"d"}]}'


def test_extract_tasks_strips_markdown_fences(tmp_path):
    """A reply wrapped in ```json fences parses (not an opaque JSONDecodeError)."""
    rep = tmp_path / "r.md"; rep.write_text("# report\n", encoding="utf-8")
    client = _FakeExtractClient(["```json\n" + _ONE_TASK + "\n```"])
    tasks = pqr.extract_tasks(client, rep, log=[].append)
    assert [t["id"] for t in tasks] == ["1"]


def test_extract_tasks_retries_empty_then_succeeds(tmp_path):
    """An empty reply (reasoning model returned no content) is retried, not fatal."""
    rep = tmp_path / "r.md"; rep.write_text("# report\n", encoding="utf-8")
    ev = []
    client = _FakeExtractClient(["", _ONE_TASK])
    tasks = pqr.extract_tasks(client, rep, log=ev.append)
    assert [t["id"] for t in tasks] == ["1"]
    assert any(e.get("event") == "model_retry" for e in ev)


def test_extract_tasks_all_empty_raises_model_error(tmp_path):
    """Persistent empty replies raise a MODEL_ERROR (→ clean extract_failed in main), not
    an opaque `Expecting value: line 1 column 1 (char 0)`."""
    rep = tmp_path / "r.md"; rep.write_text("# report\n", encoding="utf-8")
    client = _FakeExtractClient(["", "", ""])
    with pytest.raises(pqr.OpenRouterUnavailable):
        pqr.extract_tasks(client, rep, log=[].append)


# ── Feature 040 US1: run-scoped attribution via _stamp (T008) ────────────────
def test_stamp_injects_run_scoped_fields():
    e = pqr._stamp({"event": "grounding"}, run_id="R1", model="m/x", code_version="abc1234")
    assert e["run_id"] == "R1"
    assert e["model"] == "m/x"
    assert e["code_version"] == "abc1234"
    assert isinstance(e["ts"], float)
    assert e["event"] == "grounding"


def test_stamp_is_run_scoped_only_no_terminal_or_finding():
    # _stamp sets ONLY run-scoped fields - never finding_id/terminal/cause/nature (those
    # are per-call-site). A non-terminal event must not gain a `terminal` field here.
    e = pqr._stamp({"event": "provider"}, run_id="R1", model="m/x", code_version="c")
    assert "finding_id" not in e
    assert "terminal" not in e
    assert "cause" not in e


def test_stamp_entry_fields_win_over_injected():
    e = pqr._stamp({"event": "x", "model": "per-case/model"}, run_id="R1", model="run/model")
    assert e["model"] == "per-case/model"   # a call site may override


def test_stamp_empty_context_is_backward_compatible():
    e = pqr._stamp({"event": "x"})
    assert set(e) == {"ts", "event"}        # no attribution keys when none supplied


def test_mint_run_id_shape_and_uniqueness():
    a, b = pqr._mint_run_id(), pqr._mint_run_id()
    assert a.endswith(tuple("0123456789abcdef")) and "Z-" in a
    assert a != b                            # random suffix disambiguates


def test_code_version_is_a_string():
    assert isinstance(pqr._code_version(), str)


# ── Feature 040 US1: exactly one terminal per accounting unit (T009) ─────────
import scripts.scaffold_causes as _sc  # noqa: E402  (target-free shared taxonomy)


def _terminals(events, level):
    return [e for e in events if e.get("terminal") and e.get("level") == level]


def test_synth_emits_exactly_one_synthesis_terminal_on_success(tmp_path, monkeypatch):
    """A synthesized (compiling) base closes its synthesis attempt in exactly ONE terminal:
    level=synthesis_attempt, attempt_seq=1, cause=synthesized, ok:true (success is not a nature)."""
    proj = _synth_project(tmp_path)
    monkeypatch.setattr(pqr, "run_tests", lambda *a, **k: _COMPILE_OK)
    events = []
    pqr.synthesize_scaffold(
        proj, {"id": "H-01", "title": "t", "location": "Foo", "description": "d"},
        ["Foo"], "abstract contract ExistingBase {}", None,
        _FakeGenClient(_SYNTH_BASE_CODE), object(), events.append)
    terms = _terminals(events, "synthesis_attempt")
    assert len(terms) == 1
    t = terms[0]
    assert t["attempt_seq"] == 1 and t["cause"] == "synthesized"
    assert t.get("ok") is True and "nature" not in t


def test_synth_emits_exactly_one_synthesis_terminal_on_failure(tmp_path, monkeypatch):
    """A non-compiling base closes in exactly ONE synthesis terminal. When repair applies
    nothing, the cause is `repair_exhausted:*` (FR-001c split); the diagnostic
    `scaffold_repair(_exhausted)` events on the way carry NO `terminal` field."""
    proj = _synth_project(tmp_path)
    monkeypatch.setattr(pqr, "run_tests", lambda *a, **k: _COMPILE_FAIL)
    events = []
    pqr.synthesize_scaffold(
        proj, {"id": "H-01", "title": "t", "location": "Foo", "description": "d"},
        ["Foo"], "", None, _FakeGenClient(_SYNTH_BASE_CODE), object(), events.append)
    terms = _terminals(events, "synthesis_attempt")
    assert len(terms) == 1
    assert terms[0]["cause"] in _sc.SYNTHESIS_CAUSES
    assert terms[0]["cause"].startswith("repair_exhausted:")
    assert _sc.cause_nature(terms[0]["cause"]) == terms[0]["nature"]
    # non-terminal diagnostics stay non-terminal
    for e in events:
        if e["event"] in ("scaffold_repair", "scaffold_repair_exhausted", "scaffold_insufficient"):
            assert "terminal" not in e


def test_synth_repair_exhausted_resolvable_vs_unresolvable(tmp_path, monkeypatch):
    """T031/US4: matched&&!applied → repair_exhausted:resolvable (harness-infra);
    !matched → repair_exhausted:unresolvable (synth-model). No laundering across the line."""
    proj = _synth_project(tmp_path)

    # Unresolvable: generic 7576, no fixer domain matches → synth-model.
    monkeypatch.setattr(pqr, "run_tests", lambda *a, **k: _COMPILE_FAIL)
    unres_ev = []
    pqr.synthesize_scaffold(
        proj, {"id": "H-01", "title": "t", "location": "Foo", "description": "d"},
        ["Foo"], "", None, _FakeGenClient(_SYNTH_BASE_CODE), object(), unres_ev.append)
    unres = _terminals(unres_ev, "synthesis_attempt")[0]
    assert unres["cause"] == "repair_exhausted:unresolvable"
    assert unres["nature"] == "synth-model"

    # Resolvable: 9553 present but flagged line out of range → address_interface matched&&!applied.
    bad_9553 = type("R", (), {
        "passed": False, "exit_code": 1,
        "stdout": ("Compiler run failed:\n"
                   "Error (9553): Invalid type for argument in function call. "
                   "Invalid implicit conversion from address to contract IThing requested.\n"
                   "  --> audit/poc/_synth/SynthBase_H_01.sol:99:9:\n"),
        "stderr": "",
    })()
    monkeypatch.setattr(pqr, "run_tests", lambda *a, **k: bad_9553)
    res_ev = []
    pqr.synthesize_scaffold(
        proj, {"id": "H-01", "title": "t", "location": "Foo", "description": "d"},
        ["Foo"], "", None, _FakeGenClient(_SYNTH_BASE_CODE), object(), res_ev.append)
    res = _terminals(res_ev, "synthesis_attempt")[0]
    assert res["cause"] == "repair_exhausted:resolvable"
    assert res["nature"] == "harness-infra"
    assert res["cause"] != unres["cause"]


def test_insufficiency_ladder_no_draft_on_known_insufficient(tmp_path, monkeypatch):
    """T032/US4 Option-C: after synth fails, never draft on the insufficient base;
    land base-insufficient (lookup couldn't run) or lookup_failed (lookup ran) - never
    not_triggered."""
    import types
    from sr_agent.eval.tracer import NOOP_TRACER

    proj = _synth_project(tmp_path)
    (proj / "audit" / "poc").mkdir(parents=True, exist_ok=True)
    task = {"id": "H-01", "title": "t", "location": "Foo", "description": "d"}
    drafted = {"n": 0}

    def _fake_synth(*a, **k):
        log = a[7] if len(a) > 7 else k.get("log")
        log({"event": "scaffold_synthesis_failed", "finding_id": task["id"], "reason": "no_build",
             **pqr._terminal_fields("synthesis_attempt", "no_build:code", attempt_seq=1)})
        return None

    def _boom_draft(*a, **k):
        drafted["n"] += 1
        raise AssertionError("draft must not run on a known-insufficient base (FR-011)")

    monkeypatch.setattr(pqr, "scaffold_missing_types", lambda *a, **k: ["Foo"])
    monkeypatch.setattr(pqr, "synthesize_scaffold", _fake_synth)
    monkeypatch.setattr(pqr, "draft", _boom_draft)
    monkeypatch.setattr(pqr, "resolve_scaffold", lambda *a, **k: [])
    monkeypatch.setattr(pqr, "read_scaffold", lambda *a, **k: "")
    monkeypatch.setattr(pqr, "resolve_example", lambda *a, **k: None)
    monkeypatch.setattr(pqr, "read_example", lambda *a, **k: "")
    monkeypatch.setattr(pqr, "build_callable_api", lambda *a, **k: "")

    args = types.SimpleNamespace(
        project=proj, test_scaffold="", no_scaffold=True, no_example=True,
        example_poc="", no_file_map=True, lookup_budget=0, attempts=1, image=None,
        no_scaffold_synthesis=False,
    )
    # Lookup cannot run (no index, budget 0) → base-insufficient.
    events = []
    outcome = pqr._process_finding(
        task, args=args, client=object(), sandbox=object(), log=events.append,
        symbol_index=None, file_map="", protocol_mode="marker",
        fork_rpc=None, require_pass_effective=False, poc_dir=proj / "audit" / "poc",
        tracer=NOOP_TRACER,
    )
    assert drafted["n"] == 0
    assert outcome == "base-insufficient"
    terms = _terminals(events, "finding_attempt")
    assert len(terms) == 1
    assert terms[0]["cause"] == "base-insufficient"
    assert terms[0]["nature"] == "harness-infra"
    assert terms[0]["cause"] != "not_triggered"

    # Lookup can run (index + budget) → lookup_failed; still no draft.
    class _Idx:
        def lookup(self, name):
            return []

    args.lookup_budget = 2
    events2 = []
    outcome2 = pqr._process_finding(
        task, args=args, client=object(), sandbox=object(), log=events2.append,
        symbol_index=_Idx(), file_map="", protocol_mode="marker",
        fork_rpc=None, require_pass_effective=False, poc_dir=proj / "audit" / "poc",
        tracer=NOOP_TRACER,
    )
    assert drafted["n"] == 0
    assert outcome2 == "lookup_failed"
    terms2 = _terminals(events2, "finding_attempt")
    assert len(terms2) == 1
    assert terms2[0]["cause"] == "lookup_failed"
    assert terms2[0]["nature"] == "model"
    assert any(e.get("stage") == "insufficiency_ladder" for e in events2)


def test_synth_no_output_terminal_is_synth_model(tmp_path, monkeypatch):
    """A model that returns non-Solidity closes in one synthesis terminal cause=no_output:model
    (synth-model nature) - the synthesis model, not the harness, failed to emit a base."""
    proj = _synth_project(tmp_path)
    events = []
    pqr.synthesize_scaffold(
        proj, {"id": "H-01", "title": "t", "location": "Foo", "description": "d"},
        ["Foo"], "", None, _FakeGenClient("sorry, no can do"), object(), events.append)
    terms = _terminals(events, "synthesis_attempt")
    assert len(terms) == 1
    assert terms[0]["cause"] == "no_output:model" and terms[0]["nature"] == "synth-model"


class _RaisingGenClient:
    """A client whose .generate raises a transport error (MODEL_ERRORS) - the call never returns."""
    def generate(self, prompt, options=None):
        raise pqr.OpenRouterUnavailable("503 upstream unavailable")


def test_synth_transport_crash_is_no_output_crash_not_model(tmp_path, monkeypatch):
    """US3 (T026/SC-007): a synthesis whose model CALL failed at the transport layer (503/timeout -
    the call never returned) is `no_output:crash` (harness-infra), DISTINCT from a model that
    responded with junk (`no_output:model`, synth-model). The two must not launder into each other."""
    proj = _synth_project(tmp_path)
    crash_ev, model_ev = [], []
    pqr.synthesize_scaffold(
        proj, {"id": "H-01", "title": "t", "location": "Foo", "description": "d"},
        ["Foo"], "", None, _RaisingGenClient(), object(), crash_ev.append)
    pqr.synthesize_scaffold(
        proj, {"id": "H-01", "title": "t", "location": "Foo", "description": "d"},
        ["Foo"], "", None, _FakeGenClient("sorry, no can do"), object(), model_ev.append)
    crash = _terminals(crash_ev, "synthesis_attempt")[0]
    model = _terminals(model_ev, "synthesis_attempt")[0]
    assert crash["cause"] == "no_output:crash" and crash["nature"] == "harness-infra"
    assert model["cause"] == "no_output:model" and model["nature"] == "synth-model"
    assert crash["cause"] != model["cause"]           # no laundering across the model/infra line


def test_terminal_fields_ok_vs_nature_are_exclusive():
    ok = pqr._terminal_fields("synthesis_attempt", "synthesized", attempt_seq=1)
    assert ok["ok"] is True and "nature" not in ok
    infra = pqr._terminal_fields("finding_attempt", "base-insufficient")
    assert infra["nature"] == "harness-infra" and "ok" not in infra
    budget = pqr._terminal_fields("finding_attempt", "not_attempted:budget")
    assert "nature" not in budget and "ok" not in budget   # excluded from the share entirely


def test_finding_cause_covers_every_runner_outcome():
    """Every `outcome` string _process_finding can return maps into the finding closed set -
    so the `task_done` terminal never carries an out-of-set cause (unknown ⇒ unclassified)."""
    runner_outcomes = {
        "passed_verified", "unverified_pass", "passed_unchecked", "compiled",
        "vacuous_pass", "reverted_exhausted", "compile_only_defective", "exhausted",
        "sandbox_unavailable", "run_error", "draft_failed", "fix_failed",
        "base-insufficient", "lookup_failed",
    }
    for oc in runner_outcomes:
        assert pqr._finding_cause(oc) in _sc.FINDING_CAUSES
    assert pqr._finding_cause("some_future_outcome") == "unclassified"


# ── Feature 040 US1: budget cut closes every remaining finding (T010) ────────
def test_budget_skips_emit_not_attempted_for_every_remaining():
    """A --max-minutes cut must emit a `not_attempted:budget` finding-attempt terminal for EVERY
    remaining queued finding, so `queued == terminal_emitted` and the classifier cannot publish a
    share on a silently-shrunken denominator (Top-risk-1)."""
    remaining = [{"id": "H-05"}, {"id": "H-06"}, {"id": "H-07"}]
    events = []
    pqr._emit_budget_skips(events.append, remaining)
    terms = _terminals(events, "finding_attempt")
    assert len(terms) == len(remaining)                      # one per remaining finding
    assert {t["finding_id"] for t in terms} == {"H-05", "H-06", "H-07"}
    for t in terms:
        assert t["cause"] == "not_attempted:budget"
        assert "nature" not in t and "ok" not in t           # excluded from nature_share
        assert not _sc.in_denominator(t["cause"])            # not counted in `attempted`


# ── Feature 042: scaffold precondition completeness (runner wiring) ──────────

_FIX_042 = Path(__file__).resolve().parents[1] / "fixtures" / "scaffold_reachability"
_GATE_042 = (_FIX_042 / "config_manager_field" / "incomplete" / "Gate.sol").read_text(encoding="utf-8")
_TRACE_042 = (_FIX_042 / "traces" / "ascii_arrow.txt").read_text(encoding="utf-8")

_SYNTH_DEMO_INCOMPLETE = (
    "// SPDX-License-Identifier: MIT\npragma solidity ^0.8.28;\n"
    "abstract contract SynthBase_H_01 is ExistingBase {\n"
    "    DemoVault internal demoVault;\n"
    "    function setUpSynth() internal { demoVault = new DemoVault(); }\n"
    "}\n"
)


def _demo_vault_project(tmp_path: Path) -> Path:
    (tmp_path / "contracts").mkdir(parents=True, exist_ok=True)
    (tmp_path / "contracts" / "DemoVault.sol").write_text(_GATE_042, encoding="utf-8")
    return tmp_path


class _SpyGenClient:
    """Captures prompts passed to generate (feature 042 T022)."""
    def __init__(self, text):
        self._text = text
        self.prompts: list[str] = []

    def generate(self, prompt, options=None):
        self.prompts.append(prompt)
        return self._text


def _forge_repeat_fail(stdout_extra: str = "") -> object:
    """Compiled-but-reverted forge result whose trace yields DemoVault::gate."""
    body = _TRACE_042 + "\nRan 1 test for audit/poc/X.t.sol\n" + stdout_extra
    return type("R", (), {
        "passed": False, "exit_code": 1, "stdout": body, "stderr": "",
    })()


def _poc_with_prank(caller: str, method: str = "gate") -> str:
    return (
        f'import {{DemoVault}} from "../contracts/DemoVault.sol";\n'
        f"contract PoC is Base {{\n"
        f"  function test_x() public {{\n"
        f"    vm.prank({caller});\n"
        f"    vault.{method}();\n"
        f"    assertEq(1, 1);\n"
        f"  }}\n"
        f"}}\n"
    )


def _pf_args(project: Path, attempts: int = 4):
    import types
    return types.SimpleNamespace(
        project=project, test_scaffold="", no_scaffold=True, no_example=True,
        example_poc="", no_file_map=True, lookup_budget=0, attempts=attempts, image=None,
        no_scaffold_synthesis=False,
    )


def test_042_synthesize_scaffold_prompt_and_reachability_out(tmp_path, monkeypatch):
    """T022: matching pattern → synthesis extras + reachability_out; non-match → no finding_location."""
    from scripts import scaffold_reachability as sreach

    # --- matching config_manager_field ---
    proj = _demo_vault_project(tmp_path / "match")
    monkeypatch.setattr(pqr, "run_tests", lambda *a, **k: _COMPILE_OK)
    client = _SpyGenClient(_SYNTH_DEMO_INCOMPLETE)
    events: list[dict] = []
    reach_out: list = []
    task = {
        "id": "H-01", "title": "t",
        "location": "DemoVault.sol:gate",
        "description": "configManager gate blocks caller",
    }
    path = pqr.synthesize_scaffold(
        proj, task, ["DemoVault"], "abstract contract ExistingBase {}", None,
        client, object(), events.append, reachability_out=reach_out)
    assert path is not None
    assert client.prompts, "generate must be called"
    prompt = client.prompts[0]
    assert "[DATA START finding_location]" in prompt
    assert "[DATA START location_source]" in prompt
    assert "setConfigManager" in prompt
    assert reach_out, "reachability_out must be extended on incomplete match"
    assert all(isinstance(c, sreach.ReachabilityCheck) for c in reach_out)
    assert all(c.status == "incomplete" for c in reach_out)
    synth_ev = next(e for e in events if e["event"] == "scaffold_synthesized")
    assert "reachability_checks" in synth_ev
    assert synth_ev["reachability_checks"][0]["status"] == "incomplete"
    assert synth_ev["reachability_checks"][0]["protected_call_site"] == {
        "contract": "DemoVault", "method": "gate",
    }

    # --- non-matching location (plain Foo, no config-manager pattern) ---
    nomatch = tmp_path / "nomatch"
    nomatch.mkdir(parents=True, exist_ok=True)
    proj2 = _synth_project(nomatch)
    client2 = _SpyGenClient(_SYNTH_BASE_CODE)
    events2: list[dict] = []
    reach2: list = []
    path2 = pqr.synthesize_scaffold(
        proj2,
        {"id": "H-01", "title": "t", "location": "Foo", "description": "d"},
        ["Foo"], "abstract contract ExistingBase {}", None,
        client2, object(), events2.append, reachability_out=reach2)
    assert path2 is not None
    prompt2 = client2.prompts[0]
    assert "[DATA START finding_location]" not in prompt2
    assert reach2 == []
    synth_ev2 = next(e for e in events2 if e["event"] == "scaffold_synthesized")
    assert "reachability_checks" not in synth_ev2


def test_042_process_finding_repeat_hint(tmp_path, monkeypatch):
    """T042: streak fires with hypothesis / corroborated forms; no-missing-types has no NameError."""
    from sr_agent.eval.tracer import NOOP_TRACER
    from scripts import scaffold_reachability as sreach

    proj = _demo_vault_project(tmp_path)
    (proj / "audit" / "poc").mkdir(parents=True, exist_ok=True)
    task = {
        "id": "H-01", "title": "repeat",
        "location": "DemoVault.sol:gate",
        "description": "gate blocked",
    }
    fail = _forge_repeat_fail()
    # attempts=4 so attempt 3 (streak==3) still enters the fix/hint path
    pocs = [_poc_with_prank(c) for c in ("alice", "bob", "carol", "dave")]

    def _drive(*, missing, synth_fn, drafts, fixes, results, attempts=4):
        draft_q, fix_q, res_q = list(drafts), list(fixes), list(results)
        monkeypatch.setattr(pqr, "scaffold_missing_types", lambda *a, **k: missing)
        monkeypatch.setattr(pqr, "synthesize_scaffold", synth_fn)
        monkeypatch.setattr(pqr, "draft", lambda *a, **k: draft_q.pop(0))
        monkeypatch.setattr(pqr, "fix", lambda *a, **k: fix_q.pop(0))
        monkeypatch.setattr(pqr, "run_tests", lambda *a, **k: res_q.pop(0))
        monkeypatch.setattr(pqr, "resolve_scaffold", lambda *a, **k: [])
        monkeypatch.setattr(pqr, "read_scaffold", lambda *a, **k: "")
        monkeypatch.setattr(pqr, "resolve_example", lambda *a, **k: None)
        monkeypatch.setattr(pqr, "read_example", lambda *a, **k: "")
        monkeypatch.setattr(pqr, "build_callable_api", lambda *a, **k: "")
        events: list[dict] = []
        pqr._process_finding(
            task, args=_pf_args(proj, attempts), client=object(), sandbox=object(),
            log=events.append, symbol_index=None, file_map="", protocol_mode="marker",
            fork_rpc=None, require_pass_effective=True, poc_dir=proj / "audit" / "poc",
            tracer=NOOP_TRACER,
        )
        return events

    # (1) no missing types → hypothesis_confirmed, no NameError
    def _boom_synth(*a, **k):
        raise AssertionError("synthesize_scaffold must not run when missing_types is empty")

    events = _drive(
        missing=[], synth_fn=_boom_synth,
        drafts=[pocs[0]], fixes=pocs[1:], results=[fail] * 4)
    hints = [e for e in events if e["event"] == "repeat_revert_hint"]
    assert hints, "repeat_revert_hint must fire after streak reaches REPEAT_THRESHOLD"
    assert hints[0]["form"] == "hypothesis_confirmed"
    assert hints[0]["confirmed_caller_change"] is True
    assert "regardless of caller" in hints[0]["hints"]

    # (2) synth seeds incomplete matching CallSite → corroborated
    def _seed_synth(*a, **k):
        out = k.get("reachability_out")
        if out is not None:
            out.append(sreach.ReachabilityCheck(
                pattern="config_manager_field",
                status="incomplete",
                missing=["setConfigManager"],
                protected_call_site=sreach.CallSite("DemoVault", "gate"),
            ))
        live = proj / "audit" / "poc" / "_synth" / "SynthBase_H_01.sol"
        live.parent.mkdir(parents=True, exist_ok=True)
        live.write_text("// SPDX-License-Identifier: MIT\npragma solidity ^0.8.28;\n"
                        "abstract contract SynthBase_H_01 {}\n", encoding="utf-8")
        return live

    events2 = _drive(
        missing=["DemoVault"], synth_fn=_seed_synth,
        drafts=[pocs[0]], fixes=pocs[1:], results=[fail] * 4)
    hints2 = [e for e in events2 if e["event"] == "repeat_revert_hint"]
    assert hints2 and hints2[0]["form"] == "corroborated"
    assert "setConfigManager" in hints2[0]["hints"]


def test_042_process_finding_mechanism_regression(tmp_path, monkeypatch):
    """T051: compiled attempt drops a previously-exercised method → mechanism_regression_hint."""
    from sr_agent.eval.tracer import NOOP_TRACER

    proj = tmp_path
    (proj / "contracts").mkdir()
    (proj / "audit" / "poc").mkdir(parents=True, exist_ok=True)
    task = {
        "id": "H-02", "title": "mech",
        "location": "DemoVault.coverage / DemoVault.cancel",
        "description": "coverage and cancel matter",
    }

    def _poc(methods: list[str]) -> str:
        calls = "\n".join(f"    vault.{m}();" for m in methods)
        return (
            'import {DemoVault} from "../contracts/DemoVault.sol";\n'
            "contract PoC is Base {\n"
            "  function test_x() public {\n"
            f"{calls}\n"
            "    assertEq(1, 1);\n"
            "  }\n"
            "}\n"
        )

    fail = type("R", (), {
        "passed": False, "exit_code": 1,
        "stdout": "Ran 1 test for audit/poc/H_02.t.sol\n[FAIL: revert] test_x()",
        "stderr": "",
    })()
    # attempt1+2 call both; attempt3 drops cancel → reminder on the fix path (attempts=4)
    drafts = [_poc(["coverage", "cancel"])]
    fixes = [
        _poc(["coverage", "cancel"]),
        _poc(["coverage"]),           # drops cancel - fires reminder when tested
        _poc(["coverage"]),
    ]
    draft_q, fix_q, res_q = list(drafts), list(fixes), [fail] * 4
    monkeypatch.setattr(pqr, "scaffold_missing_types", lambda *a, **k: [])
    monkeypatch.setattr(pqr, "draft", lambda *a, **k: draft_q.pop(0))
    monkeypatch.setattr(pqr, "fix", lambda *a, **k: fix_q.pop(0))
    monkeypatch.setattr(pqr, "run_tests", lambda *a, **k: res_q.pop(0))
    monkeypatch.setattr(pqr, "resolve_scaffold", lambda *a, **k: [])
    monkeypatch.setattr(pqr, "read_scaffold", lambda *a, **k: "")
    monkeypatch.setattr(pqr, "resolve_example", lambda *a, **k: None)
    monkeypatch.setattr(pqr, "read_example", lambda *a, **k: "")
    monkeypatch.setattr(pqr, "build_callable_api", lambda *a, **k: "")

    events: list[dict] = []
    pqr._process_finding(
        task, args=_pf_args(proj, attempts=4), client=object(), sandbox=object(),
        log=events.append, symbol_index=None, file_map="", protocol_mode="marker",
        fork_rpc=None, require_pass_effective=True, poc_dir=proj / "audit" / "poc",
        tracer=NOOP_TRACER,
    )
    mech_ev = [e for e in events if e["event"] == "mechanism_regression_hint"]
    assert mech_ev, "mechanism_regression_hint must fire when a compiled attempt drops a method"
    assert "cancel" in mech_ev[0]["hints"]
    assert "Previously-exercised" in mech_ev[0]["hints"]


def test_043_process_finding_refuse_restores_baseline(tmp_path, monkeypatch):
    """Feature 043 T024: compile-fail after a compiled checkpoint refuses adopt and
    grounds the next fix on the restore-target body (not the refused source)."""
    from sr_agent.eval.tracer import NOOP_TRACER

    proj = tmp_path
    (proj / "contracts").mkdir()
    (proj / "audit" / "poc").mkdir(parents=True, exist_ok=True)
    task = {
        "id": "H-43", "title": "checkpoint",
        "location": "DemoVault.sol:gate",
        "description": "gate blocked",
    }
    body_a = (
        'import {DemoVault} from "../contracts/DemoVault.sol";\n'
        "contract PoC is Base {\n"
        "  function test_x() public {\n"
        "    DemoVault(address(0x1)).gate();\n"
        "    assertEq(1, 1);\n"
        "  }\n"
        "}\n"
    )
    body_c = (
        "contract PoC is Base {\n"
        "  function test_x() public { inventedHelperThatDoesNotExist(); }\n"
        "}\n"
    )
    compiled_fail = type("R", (), {
        "passed": False, "exit_code": 1,
        "stdout": "Ran 1 test for audit/poc/H_43.t.sol\n[FAIL: DepositCapReached] test_x()",
        "stderr": "",
    })()
    compile_error = type("R", (), {
        "passed": False, "exit_code": 1,
        "stdout": "Compiler run failed:\nError (7576): Undeclared identifier.",
        "stderr": "",
    })()
    results = [compiled_fail, compile_error, compiled_fail]
    fix_inputs: list[str] = []
    draft_q = [body_a]
    fix_q = [body_c, body_a]

    def _fix(client, task, code, feedback, *a, **k):
        fix_inputs.append(code)
        return fix_q.pop(0)

    monkeypatch.setattr(pqr, "scaffold_missing_types", lambda *a, **k: [])
    monkeypatch.setattr(pqr, "draft", lambda *a, **k: draft_q.pop(0))
    monkeypatch.setattr(pqr, "fix", _fix)
    monkeypatch.setattr(pqr, "run_tests", lambda *a, **k: results.pop(0))
    monkeypatch.setattr(pqr, "_seq_draft_inplace", lambda code, blob, file_map: (code, []))
    monkeypatch.setattr(pqr, "resolve_scaffold", lambda *a, **k: [])
    monkeypatch.setattr(pqr, "read_scaffold", lambda *a, **k: "")
    monkeypatch.setattr(pqr, "resolve_example", lambda *a, **k: None)
    monkeypatch.setattr(pqr, "read_example", lambda *a, **k: "")
    monkeypatch.setattr(pqr, "build_callable_api", lambda *a, **k: "")

    events: list[dict] = []
    pqr._process_finding(
        task, args=_pf_args(proj, attempts=3), client=object(), sandbox=object(),
        log=events.append, symbol_index=None, file_map="", protocol_mode="marker",
        fork_rpc=None, require_pass_effective=True, poc_dir=proj / "audit" / "poc",
        tracer=NOOP_TRACER, run_id="run043",
    )
    refused = [e for e in events if e.get("event") == "compile_adopt_rejected"]
    assert refused, "compile_adopt_rejected must fire on post-DET non-compile with checkpoint"
    assert refused[0]["restore_kind"] == "non_vacuous"
    art = proj / refused[0]["artifact_path"]
    assert art.is_file()
    assert "inventedHelperThatDoesNotExist" in art.read_text(encoding="utf-8")
    assert len(fix_inputs) >= 2
    # Checkpoint may differ from raw draft after _seq_postmodel; next fix must match
    # the compiled working body from attempt 1, not the refused body_c.
    assert fix_inputs[1] == fix_inputs[0]
    assert "inventedHelperThatDoesNotExist" not in fix_inputs[1]
    assert "DemoVault" in fix_inputs[1]


def test_015_empty_fix_keep_still_first(tmp_path, monkeypatch):
    """FR-010 / T028: empty fix payload keeps prior code (feature 015), unchanged by 043."""
    from sr_agent.eval.tracer import NOOP_TRACER

    proj = tmp_path
    (proj / "contracts").mkdir()
    (proj / "audit" / "poc").mkdir(parents=True, exist_ok=True)
    task = {
        "id": "H-15", "title": "emptyfix",
        "location": "DemoVault.sol:gate",
        "description": "gate",
    }
    body = (
        'import {DemoVault} from "../contracts/DemoVault.sol";\n'
        "contract PoC is Base {\n"
        "  function test_x() public { assertEq(1, 1); }\n"
        "}\n"
    )
    fail = type("R", (), {
        "passed": False, "exit_code": 1,
        "stdout": "Ran 1 test for audit/poc/H_15.t.sol\n[FAIL: x] test_x()",
        "stderr": "",
    })()
    fix_seen: list[str] = []

    def _fix(client, task, code, feedback, *a, **k):
        fix_seen.append(code)
        return ""  # empty payload -> keep

    monkeypatch.setattr(pqr, "scaffold_missing_types", lambda *a, **k: [])
    monkeypatch.setattr(pqr, "draft", lambda *a, **k: body)
    monkeypatch.setattr(pqr, "fix", _fix)
    monkeypatch.setattr(pqr, "run_tests", lambda *a, **k: fail)
    monkeypatch.setattr(pqr, "resolve_scaffold", lambda *a, **k: [])
    monkeypatch.setattr(pqr, "read_scaffold", lambda *a, **k: "")
    monkeypatch.setattr(pqr, "resolve_example", lambda *a, **k: None)
    monkeypatch.setattr(pqr, "read_example", lambda *a, **k: "")
    monkeypatch.setattr(pqr, "build_callable_api", lambda *a, **k: "")

    events: list[dict] = []
    pqr._process_finding(
        task, args=_pf_args(proj, attempts=2), client=object(), sandbox=object(),
        log=events.append, symbol_index=None, file_map="", protocol_mode="marker",
        fork_rpc=None, require_pass_effective=True, poc_dir=proj / "audit" / "poc",
        tracer=NOOP_TRACER, run_id="run015",
    )
    assert any(e.get("event") == "fix_no_code" for e in events)
    assert not any(e.get("event") == "compile_adopt_rejected" for e in events)
    # Second attempt still runs with kept body (empty fix did not wipe).
    tested = [e for e in events if e.get("event") == "tested"]
    assert len(tested) == 2


# ── Feature 047 US1: synth call sites must thread the missing-type signal ──────
def test_synth_calls_fix_wiring_receivers_with_missing_type_signal():
    """FR-004 plumbing: `synthesize_scaffold` must pass `missing_types` + `symbol_index`
    to `fix_wiring_receivers` at BOTH synth sites (prewrite + repair round). Source-level
    guard so the subtype-aware branch is actually reachable in the synth path."""
    import inspect
    src = inspect.getsource(pqr.synthesize_scaffold)
    calls = [ln for ln in src.splitlines() if "fix_wiring_receivers(" in ln]
    # gather the small window after each call (kwargs may wrap to the next line)
    joined = src
    n_sites = joined.count("fix_wiring_receivers(")
    assert n_sites >= 2, f"expected >=2 synth call sites, found {n_sites}"
    assert joined.count("missing_types=missing_types") >= 2, (
        "both synth call sites must pass missing_types=missing_types")
    assert joined.count("symbol_index=symbol_index") >= 2, (
        "both synth call sites must pass symbol_index=symbol_index")
