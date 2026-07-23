"""Feature 033 — TEMPORARY differential gate for the commit-1 EXTRACTION step (FR-014).

The extraction (inline transform sequences → named `_seq_*` functions) is the ONE step no
characterization test covers: in commit 1 the loops STILL run their inline sequence, so the
named functions are not yet exercised by the loop. This gate proves each `_seq_*` is
BYTE-IDENTICAL to the real inline behavior.

CRITICAL (FR-014): the inline side is CAPTURED by RUNNING THE REAL loop and reading the
artifact it WRITES (`synth_path.write_text` / the drafted `.t.sol`) — it is NOT re-transcribed.
Transcribing would compare the extraction to a second transcription (vacuous) and would AGREE
on a mis-copied per-call arg — precisely the `base_dir=synth_dir` (synthesis) vs
`import_paths(project)` (drafting) divergence this gate exists to catch. Precedent for the
capture technique: `test_synthesize_smoke_uses_relative_import`.

This file is DELETED in commit 2, once the loops call the `_seq_*` functions (from then the
characterization tests + the 031/032 loop-event tests pin them).
"""
from __future__ import annotations

import types
from pathlib import Path

import scripts.poc_queue_runner as pqr
from sr_agent.eval.tracer import NOOP_TRACER
from sr_agent.packs.audit.tools.write_execute import TestResult as _ForgeResult

# A synth base with (a) an import at the WRONG depth to a real project file — so base_dir
# genuinely changes the rewrite (site-1, the divergence FR-014 names) — and (b) a 9553-
# flaggable line at line 5, so the repair round's address_interface makes a REAL change
# (site-2). No target material.
_BASE = (
    "// SPDX-License-Identifier: MIT\n"                            # 1
    "pragma solidity ^0.8.28;\n"                                  # 2
    'import { DemoBase } from "./DemoBase.sol";\n'                # 3
    "abstract contract SynthBase_H_01 is DemoBase {\n"           # 4
    "    function s() internal { reg.configure(address(thing)); }\n"  # 5 <- 9553-flagged
    "}\n"                                                         # 6
)
_COMPILE_OK = _ForgeResult(passed=True, exit_code=0,
                           stdout="Ran 1 test for audit/poc/_synth_smoke.t.sol", stderr="")
# A 9553 pointing at line 5 of the synth base (real forge format) → address_interface fires.
_COMPILE_FAIL = _ForgeResult(
    passed=False, exit_code=1,
    stdout=("Compiler run failed:\nError (9553): Invalid type for argument in function call. "
            "Invalid implicit conversion from address to contract IDemo requested.\n"
            "  --> audit/poc/_synth/SynthBase_H_01.sol:5:9:\n"),
    stderr="")


class _FakeGenClient:
    def __init__(self, code): self._code = code
    def generate(self, *a, **k): return self._code


def _synth_project(tmp_path: Path) -> Path:
    (tmp_path / "contracts").mkdir()
    (tmp_path / "contracts" / "Foo.sol").write_text(
        "// SPDX-License-Identifier: MIT\npragma solidity ^0.8.28;\n"
        "contract Foo { constructor() {} }\n", encoding="utf-8")
    (tmp_path / "contracts" / "DemoBase.sol").write_text(
        "// SPDX-License-Identifier: MIT\npragma solidity ^0.8.28;\n"
        "abstract contract DemoBase {}\n", encoding="utf-8")
    return tmp_path


def test_diff_synth_prewrite_and_repair_match_real_loop(tmp_path, monkeypatch):
    """Sites 1 & 2: capture the synth file the REAL synthesize_scaffold loop writes each
    round, and assert the extracted `_seq_synth_prewrite`/`_seq_synth_repair` reproduce it
    byte-for-byte on the same inputs (with base_dir=synth_dir — a wrong base would differ)."""
    proj = _synth_project(tmp_path)
    synth_dir = proj / pqr._SYNTH_SUBDIR
    synth_path = synth_dir / "SynthBase_H_01.sol"

    states: list[str] = []
    results = [_COMPILE_FAIL, _COMPILE_OK]

    def _capture(*a, **k):
        states.append(synth_path.read_text())   # what the loop just WROTE this round
        return results[len(states) - 1]

    monkeypatch.setattr(pqr, "run_tests", _capture)
    path = pqr.synthesize_scaffold(
        proj, {"id": "H-01", "title": "t", "location": "Foo", "description": "d"},
        ["Foo"], "abstract contract ExistingBase {}", None,
        _FakeGenClient(_BASE), object(), [].append)
    assert path is not None                       # accepted on the 2nd (OK) round
    assert len(states) == 2

    # Site 1 (pre-write): inline == extracted
    pre_out, _ = pqr._seq_synth_prewrite(_BASE, proj, synth_dir)
    assert states[0] == pre_out
    assert "../../../contracts/DemoBase.sol" in states[0]   # base_dir=synth_dir depth (not poc's)

    # Site 2 (repair round 1): inline == extracted, driven by the SAME fail blob
    blob = _COMPILE_FAIL.stdout + "\n" + _COMPILE_FAIL.stderr
    rep_out, applied = pqr._seq_synth_repair(states[0], blob, proj, synth_dir, None)
    assert states[1] == rep_out
    assert applied == ["address_interface"]                       # a REAL site-2 change occurred
    assert "IDemo(address(thing))" in states[1]


def _args(project: Path) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        project=project, test_scaffold="", no_scaffold=True, no_example=True,
        example_poc="", no_file_map=True, lookup_budget=0, attempts=1, image=None,
        no_scaffold_synthesis=False,
    )


def test_diff_postmodel_matches_real_loop(tmp_path, monkeypatch):
    """Sites 3/5 (post-model): capture the `.t.sol` the REAL _process_finding writes after
    its post-model fixers, and assert `_seq_postmodel` reproduces it byte-for-byte with the
    SAME derived args (guard=False, scaffold="", import_paths(project) — the OTHER side of the
    base_dir divergence)."""
    (tmp_path / "audit" / "poc").mkdir(parents=True)
    draft = ("SPDX-License-Identifier: MIT\npragma solidity ^0.8.28;\n"   # bare SPDX → import_paths fires
             "contract PoC is Base { function test_x() public { assertEq(uint256(1), 1); } }")
    _PASS = _ForgeResult(passed=True, exit_code=0, stdout="Ran 1 test\n[PASS] test_x()", stderr="")

    captured: dict[str, str] = {}
    real_write = pqr.write_poc
    def _cap_write(fid, poc_dir, *, generator):
        res = real_write(fid, poc_dir, generator=generator)
        captured["code"] = res.path.read_text()
        return res
    monkeypatch.setattr(pqr, "write_poc", _cap_write)
    monkeypatch.setattr(pqr, "draft", lambda *a, **k: draft)
    monkeypatch.setattr(pqr, "run_tests", lambda *a, **k: _PASS)

    pqr._process_finding(
        {"id": "X-01", "title": "t", "location": "", "description": "d"},
        args=_args(tmp_path), client=object(), sandbox=object(), log=[].append,
        symbol_index=None, file_map="", protocol_mode="marker", fork_rpc=None,
        require_pass_effective=False, poc_dir=tmp_path / "audit" / "poc", tracer=NOOP_TRACER,
    )
    expected, _ = pqr._seq_postmodel(draft, tmp_path, None, "", "", guard=False)
    assert captured["code"] == expected
    assert captured["code"].startswith("// SPDX-License-Identifier: MIT")   # import_paths(project) ran

# Site 4 (drafting in-place, undeclared → address): its fixers are the same _fix_* the above
# sites exercise; it is pinned by test_solidity_fixers.py + the 032 `deterministic_fix`
# integration test (test_loop_deterministic_9553_fix_no_model_no_attempt). No separate capture.
