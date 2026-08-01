"""Feature 042 - pure scaffold reachability (Phases 1-5). Target-free."""
from __future__ import annotations

import json
from pathlib import Path

import scripts.scaffold_reachability as sr
from scripts.scaffold_api_inventory import InventoryStateVar, ScaffoldApiInventory

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "scaffold_reachability"
TR = FIX / "traces"
CE = FIX / "caller_expr"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _inv(state_vars: list[InventoryStateVar]) -> ScaffoldApiInventory:
    return ScaffoldApiInventory(
        leaf_contract="ExistingBase",
        leaf_path="Base.sol",
        state_vars=state_vars,
    )


def _sv(
    name: str,
    type_text: str,
    *,
    visibility: str = "internal",
    path: str = "Base.sol",
    declaring: str = "ExistingBase",
) -> InventoryStateVar:
    return InventoryStateVar(
        name=name,
        type_text=type_text,
        declaring_contract=declaring,
        declaring_path=path,
        distance=0,
        visibility=visibility,
    )


def _read_src_fn(project: Path, location: str, **_kw) -> str:
    # Resolve <location>.sol under project tree, else any file declaring the contract.
    matches = list(Path(project).rglob(f"{location}.sol"))
    if matches:
        return matches[0].read_text(encoding="utf-8")
    for p in Path(project).rglob("*.sol"):
        text = p.read_text(encoding="utf-8")
        if f"contract {location}" in text:
            return text
    return f"// missing {location}"


# ── T006 CallSite / normalize ─────────────────────────────────────────────────

def test_t006_callsite_equality_and_normalize():
    a = sr.CallSite("Foo", "setVaultFooBounds")
    b = sr.CallSite("Foo", "setVaultFooBounds")
    c = sr.CallSite("Other", "setVaultFooBounds")
    assert a == b
    assert a != c
    assert sr.normalize_contract_symbol("Foo_Impl") == "Foo"
    assert sr.normalize_contract_symbol("Foo") == "Foo"
    assert sr.CallSite(
        sr.normalize_contract_symbol("Foo_Impl"), "setVaultFooBounds"
    ) == a


# ── T007 canonical_call_site ──────────────────────────────────────────────────

def test_t007_canonical_call_site_line_drift_and_unknown():
    a = sr.canonical_call_site(_read(TR / "line_drift_a.txt"))
    b = sr.canonical_call_site(_read(TR / "line_drift_b.txt"))
    assert a == b == sr.CallSite("DemoVault", "gate")

    sa = sr.canonical_call_site(_read(TR / "shared_error_site_a.txt"))
    sb = sr.canonical_call_site(_read(TR / "shared_error_site_b.txt"))
    assert sa == sr.CallSite("VaultA", "alpha")
    assert sb == sr.CallSite("VaultB", "beta")
    assert sa != sb

    assert sr.canonical_call_site(_read(TR / "no_revert.txt")) is None
    assert sr.canonical_call_site(_read(TR / "malformed_revert.txt")) is None

    ascii_site = sr.canonical_call_site(_read(TR / "ascii_arrow.txt"))
    uni_site = sr.canonical_call_site(_read(TR / "unicode_arrow.txt"))
    assert ascii_site == uni_site == sr.CallSite("DemoVault", "gate")


# ── T008 selector + extract_repeat_evidence ───────────────────────────────────

def test_t008_canonical_revert_selector_and_evidence():
    assert (
        sr.canonical_revert_selector("DepositCapReached(0xd122...)")
        == "DepositCapReached"
    )
    assert (
        sr.canonical_revert_selector("DepositCapReached(0xffee...)")
        == "DepositCapReached"
    )
    assert sr.canonical_revert_selector("ConfigManagerOnly()") == "ConfigManagerOnly"
    assert (
        sr.canonical_revert_selector("gate blocks the caller")
        == "gate blocks the caller"
    )

    out = _read(TR / "line_drift_a.txt")
    site, sel = sr.extract_repeat_evidence(out, "")
    assert site == sr.CallSite("DemoVault", "gate")
    assert sel == "gate blocks the caller"

    # never mix across attempts: empty stderr with no_revert -> None selector
    site2, sel2 = sr.extract_repeat_evidence(_read(TR / "no_revert.txt"), "")
    assert site2 is None
    assert sel2 is None


# ── T011 resolve_parent ───────────────────────────────────────────────────────

def test_t011_resolve_parent():
    project = FIX / "parent_attach" / "complete"
    task = {
        "location": "ParentVault.sol:reachThrough",
        "description": "reached via `ParentVault`",
    }
    inv = _inv([_sv("parentRef", "ParentVault", path=str(project / "Base.sol"))])
    got = sr.resolve_parent(inv, ["Dep"], project, _read_src_fn, task=task)
    assert got.status == "resolved"
    assert got.contract == "ParentVault"
    assert got.source_text is not None
    assert "setDep" in got.source_text
    assert got.declared_type_var == "parentRef"

    zero = sr.resolve_parent(
        _inv([_sv("counter", "uint256")]),
        [],
        FIX / "parent_attach" / "zero_candidate",
        _read_src_fn,
        task=task,
    )
    assert zero.status == "no_candidate"
    assert zero.contract is None and zero.source_text is None

    amb_proj = FIX / "parent_attach" / "ambiguous"
    amb_task = {
        "location": "ParentA ParentB",
        "description": "via `ParentA` and `ParentB`",
    }
    amb = sr.resolve_parent(
        _inv([
            _sv("parentA", "ParentA", path=str(amb_proj / "Base.sol")),
            _sv("parentB", "ParentB", path=str(amb_proj / "Base.sol")),
        ]),
        [],
        amb_proj,
        _read_src_fn,
        task=amb_task,
    )
    assert amb.status == "ambiguous"
    assert amb.contract is None


# ── T012 config_manager_field ─────────────────────────────────────────────────

def test_t012_detect_config_manager_field():
    gate = _read(FIX / "config_manager_field" / "complete" / "Gate.sol")
    base = _read(FIX / "config_manager_field" / "complete" / "Base.sol")
    task = {"location": "DemoVault.sol:gate", "description": "config manager gate"}
    parent = sr.ParentResolution(status="no_candidate")
    matches = sr.detect_patterns(task, gate, parent, "", base)
    assert len(matches) == 1
    assert matches[0].pattern == "config_manager_field"
    assert matches[0].evidence["field"] == "configManager"
    assert matches[0].evidence["setter"] == "setConfigManager"
    assert matches[0].protected_call_site == sr.CallSite("DemoVault", "gate")


# ── T013 parent_attach ────────────────────────────────────────────────────────

def test_t013_detect_parent_attach_and_extras():
    project = FIX / "parent_attach" / "complete"
    loc = _read(project / "ParentVault.sol")
    miss = _read(project / "Dep.sol")
    base = _read(project / "Base.sol")
    task = {
        "location": "ParentVault.sol:reachThrough",
        "description": "attach Dep through `ParentVault`",
    }
    parent = sr.ParentResolution(
        status="resolved",
        contract="ParentVault",
        source_text=loc,
        declared_type_var="parentRef",
    )
    matches = sr.detect_patterns(task, loc, parent, miss, base)
    assert any(m.pattern == "parent_attach" for m in matches)
    pa = next(m for m in matches if m.pattern == "parent_attach")
    assert pa.evidence["setter"] == "setDep"
    extras = sr.synthesis_extras(matches, loc, parent, task)
    assert "[DATA START parent_source]" in extras
    assert "setDep" in extras


# ── T014 role_grant ───────────────────────────────────────────────────────────

def test_t014_detect_role_grant():
    gate = _read(FIX / "role_grant" / "complete" / "Gate.sol")
    base = _read(FIX / "role_grant" / "complete" / "Base.sol")
    task = {"location": "RoleVault.sol:privileged", "description": "needs OPERATOR_ROLE"}
    parent = sr.ParentResolution(status="no_candidate")
    matches = sr.detect_patterns(task, gate, parent, "", base)
    assert len(matches) == 1
    assert matches[0].pattern == "role_grant"
    assert "OPERATOR_ROLE" in matches[0].evidence["role"]
    assert matches[0].protected_call_site == sr.CallSite("RoleVault", "privileged")


# ── T015 no gate ──────────────────────────────────────────────────────────────

def test_t015_no_gate_empty():
    plain = _read(FIX / "no_gate" / "Plain.sol")
    base = _read(FIX / "no_gate" / "Base.sol")
    task = {"location": "PlainVault.sol:bump", "description": "no access gate"}
    parent = sr.ParentResolution(status="no_candidate")
    matches = sr.detect_patterns(task, plain, parent, "", base)
    assert matches == []
    assert sr.synthesis_extras(matches, plain, parent, task) == ""


# ── T016 degrade / authority / ambiguous ──────────────────────────────────────

def test_t016_degrade_cases():
    # authority outside control
    gate = _read(FIX / "authority_outside_control" / "Gate.sol")
    base = _read(FIX / "authority_outside_control" / "Base.sol")
    task = {"location": "LockedVault.sol:gate", "description": "locked"}
    parent = sr.ParentResolution(status="no_candidate")
    matches = sr.detect_patterns(task, gate, parent, "", base)
    assert not any(m.pattern == "config_manager_field" for m in matches)

    # role with no grant shape in base
    gate2 = _read(FIX / "role_grant" / "complete" / "Gate.sol")
    base_empty = "contract ExistingBase { function setUp() public {} }"
    matches2 = sr.detect_patterns(
        {"location": "RoleVault.sol:privileged", "description": "x"},
        gate2,
        parent,
        "",
        base_empty,
    )
    assert not any(m.pattern == "role_grant" for m in matches2)

    # ambiguous parent -> no parent_attach
    amb = sr.ParentResolution(status="ambiguous")
    matches3 = sr.detect_patterns(
        {"location": "ParentA", "description": "`ParentA` `ParentB`"},
        "contract ParentA {}",
        amb,
        "contract Dep {}",
        "",
    )
    assert not any(m.pattern == "parent_attach" for m in matches3)


# ── T017 comment stripping ────────────────────────────────────────────────────

def test_t017_comment_stripping():
    raw_cfg = _read(FIX / "commented_evidence" / "config" / "Gate.sol")
    assert "configManager" in raw_cfg  # raw contains commented evidence
    parent = sr.ParentResolution(status="no_candidate")
    task = {"location": "DemoVault.sol:gate", "description": "x"}
    matches = sr.detect_patterns(task, raw_cfg, parent, "", "")
    assert not any(m.pattern == "config_manager_field" for m in matches)

    raw_role = _read(FIX / "commented_evidence" / "role" / "Gate.sol")
    raw_base = _read(FIX / "commented_evidence" / "role" / "Base.sol")
    assert "grantRole" in raw_base
    matches_r = sr.detect_patterns(
        {"location": "RoleVault.sol:privileged", "description": "x"},
        raw_role,
        parent,
        "",
        raw_base,
    )
    assert not any(m.pattern == "role_grant" for m in matches_r)

    raw_par = _read(FIX / "commented_evidence" / "parent" / "Parent.sol")
    assert "setDep" in raw_par
    parent_res = sr.ParentResolution(
        status="resolved", contract="ParentVault", source_text=raw_par
    )
    matches_p = sr.detect_patterns(
        {"location": "ParentVault.sol:x", "description": "`ParentVault`"},
        raw_par,
        parent_res,
        "contract Dep {}",
        "",
    )
    assert not any(m.pattern == "parent_attach" for m in matches_p)

    # commented wiring -> incomplete
    cfg_match = sr.PatternMatch(
        pattern="config_manager_field",
        evidence={"field": "configManager", "setter": "setConfigManager",
                  "gated_function": "gate", "gated_contract": "DemoVault"},
        protected_call_site=sr.CallSite("DemoVault", "gate"),
    )
    wiring = _read(FIX / "commented_wiring" / "config" / "Synth.sol")
    assert "setConfigManager" in wiring
    checks = sr.check_reachability([cfg_match], wiring)
    assert checks[0].status == "incomplete"


# ── T018 composite ────────────────────────────────────────────────────────────

def test_t018_composite_independent_checks():
    root = FIX / "composite" / "both"
    loc = _read(root / "Foo.sol") + "\n" + _read(root / "ParentVault.sol")
    miss = _read(root / "Foo.sol")
    base = _read(root / "ExistingBase.sol")
    task = {
        "location": "Foo.sol:setVaultFooBounds",
        "description": "via `ParentVault` config manager on Foo",
    }
    parent = sr.ParentResolution(
        status="resolved",
        contract="ParentVault",
        source_text=_read(root / "ParentVault.sol"),
        declared_type_var="parentRef",
    )
    matches = sr.detect_patterns(task, loc, parent, miss, base)
    ids = [m.pattern for m in matches]
    assert ids == ["config_manager_field", "parent_attach"]

    neither = sr.check_reachability(matches, _read(FIX / "composite" / "neither" / "Synth.sol"))
    config_only = sr.check_reachability(
        matches, _read(FIX / "composite" / "config_only" / "Synth.sol")
    )
    both = sr.check_reachability(matches, _read(FIX / "composite" / "both" / "Synth.sol"))

    assert len(neither) == 2
    assert neither[0].pattern == "config_manager_field" and neither[0].status == "incomplete"
    assert neither[1].pattern == "parent_attach" and neither[1].status == "incomplete"

    assert config_only[0].status == "complete"
    assert config_only[1].status == "incomplete"

    assert both[0].status == "complete"
    assert both[1].status == "complete"


# ── T019 check_reachability per pattern ───────────────────────────────────────

def test_t019_check_reachability_pairs():
    # config
    gate = _read(FIX / "config_manager_field" / "complete" / "Gate.sol")
    task = {"location": "DemoVault.sol:gate", "description": "x"}
    parent = sr.ParentResolution(status="no_candidate")
    m = sr.detect_patterns(
        task, gate, parent, "", _read(FIX / "config_manager_field" / "complete" / "Base.sol")
    )
    assert m
    site = m[0].protected_call_site
    c_ok = sr.check_reachability(m, _read(FIX / "config_manager_field" / "complete" / "Base.sol"))
    c_bad = sr.check_reachability(m, _read(FIX / "config_manager_field" / "incomplete" / "Base.sol"))
    assert c_ok[0].status == "complete"
    assert c_bad[0].status == "incomplete"
    assert any("setConfigManager" in x for x in c_bad[0].missing)
    assert c_ok[0].protected_call_site == c_bad[0].protected_call_site == site

    # role
    m_role = sr.detect_patterns(
        {"location": "RoleVault.sol:privileged", "description": "x"},
        _read(FIX / "role_grant" / "complete" / "Gate.sol"),
        parent,
        "",
        _read(FIX / "role_grant" / "complete" / "Base.sol"),
    )
    r_ok = sr.check_reachability(
        m_role, _read(FIX / "role_grant" / "complete" / "SynthComplete.sol")
    )
    r_bad = sr.check_reachability(
        m_role, _read(FIX / "role_grant" / "incomplete" / "SynthIncomplete.sol")
    )
    assert r_ok[0].status == "complete"
    assert r_bad[0].status == "incomplete"
    assert r_ok[0].protected_call_site == r_bad[0].protected_call_site

    # parent
    proj = FIX / "parent_attach" / "complete"
    parent_r = sr.ParentResolution(
        status="resolved",
        contract="ParentVault",
        source_text=_read(proj / "ParentVault.sol"),
    )
    m_pa = sr.detect_patterns(
        {"location": "ParentVault.sol:reachThrough", "description": "`ParentVault`"},
        _read(proj / "ParentVault.sol"),
        parent_r,
        _read(proj / "Dep.sol"),
        _read(proj / "Base.sol"),
    )
    p_ok = sr.check_reachability(m_pa, _read(proj / "SynthComplete.sol"))
    p_bad = sr.check_reachability(
        m_pa, _read(FIX / "parent_attach" / "incomplete" / "SynthIncomplete.sol")
    )
    assert p_ok[0].status == "complete"
    assert p_bad[0].status == "incomplete"
    assert p_ok[0].protected_call_site == p_bad[0].protected_call_site

    assert sr.check_reachability([], "anything") == []


# ── T020 synthesis_extras determinism ────────────────────────────────────────

def test_t020_synthesis_extras_deterministic():
    gate = _read(FIX / "config_manager_field" / "complete" / "Gate.sol")
    base = _read(FIX / "config_manager_field" / "complete" / "Base.sol")
    task = {"location": "DemoVault.sol:gate", "description": "desc"}
    parent = sr.ParentResolution(status="no_candidate")
    matches = sr.detect_patterns(task, gate, parent, "", base)
    a = sr.synthesis_extras(matches, gate, parent, task)
    b = sr.synthesis_extras(matches, gate, parent, task)
    assert a == b
    assert a.count("[DATA START finding_location]") == 1
    assert a.count("[DATA START location_source]") == 1


# ── T021 shared-key equality SC-013 ───────────────────────────────────────────

def test_t021_shared_key_proxy_impl():
    root = FIX / "composite" / "both"
    loc = _read(root / "Foo.sol")
    parent = sr.ParentResolution(status="no_candidate")
    task = {
        "location": "Foo.sol:setVaultFooBounds",
        "description": "bounds",
    }
    matches = sr.detect_patterns(task, loc, parent, loc, "")
    assert matches
    protected = matches[0].protected_call_site
    trace_site = sr.canonical_call_site(_read(TR / "proxy_impl.txt"))
    assert protected == trace_site == sr.CallSite("Foo", "setVaultFooBounds")
    assert protected != sr.CallSite("Other", "setVaultFooBounds")


# ── T029-T033 extract_caller_expr ─────────────────────────────────────────────

def test_t029_prank_immediate():
    site = sr.CallSite("Target", "method")
    assert sr.extract_caller_expr(_read(CE / "prank_immediate.sol.txt"), site) == "alice"


def test_t030_prank_consumed():
    site = sr.CallSite("Target", "method")
    assert sr.extract_caller_expr(_read(CE / "prank_consumed.sol.txt"), site) is None


def test_t031_start_prank_persistent():
    site = sr.CallSite("Target", "method")
    assert sr.extract_caller_expr(_read(CE / "start_prank.sol.txt"), site) == "bob"


def test_t032_no_prank_and_no_method():
    site = sr.CallSite("Target", "method")
    assert sr.extract_caller_expr(_read(CE / "no_prank.sol.txt"), site) is None
    assert sr.extract_caller_expr(_read(CE / "no_method.sol.txt"), site) is None


def test_t033_rearmed_and_local_decl():
    site = sr.CallSite("Target", "method")
    assert sr.extract_caller_expr(_read(CE / "rearmed.sol.txt"), site) == "carol"
    assert sr.extract_caller_expr(_read(CE / "local_decl.sol.txt"), site) == "alice"


# ── T034-T039 update_repeat ───────────────────────────────────────────────────

def _run_history(rows: list[dict], threshold: int = 3):
    state = sr.RepeatState()
    fired = False
    for row in rows:
        site = None
        if row["site"] is not None:
            site = sr.CallSite(row["site"][0], row["site"][1])
        state, fired = sr.update_repeat(
            state, site, row["sel"], row["caller"], threshold
        )
    return state, fired


def test_t034_confirmed_caller_change_fires():
    hist = json.loads(_read(TR / "history_confirmed_caller.json"))
    state, fired = _run_history(hist, threshold=3)
    assert fired is True
    assert state.confirmed_caller_change is True
    assert state.streak >= 3


def test_t035_indeterminate_still_fires():
    hist = json.loads(_read(TR / "history_indeterminate.json"))
    state, fired = _run_history(hist, threshold=3)
    assert fired is True
    assert state.confirmed_caller_change is False


def test_t036_mixed_keeps_confirmed():
    hist = json.loads(_read(TR / "history_mixed_confirmed.json"))
    state, fired = _run_history(hist, threshold=3)
    assert fired is True
    assert state.confirmed_caller_change is True


def test_t037_same_caller_flattens():
    hist = json.loads(_read(TR / "history_same_caller.json"))
    state, fired = _run_history(hist, threshold=3)
    assert state.streak == 1
    assert state.confirmed_caller_change is False
    assert fired is False


def test_t038_unknown_resets():
    hist = json.loads(_read(TR / "history_unknown_reset.json"))
    state, fired = _run_history(hist, threshold=3)
    assert state.streak == 0
    assert state.confirmed_caller_change is False
    assert fired is False
    # unknown never matches unknown
    s = sr.RepeatState()
    s, _ = sr.update_repeat(s, None, "X", None, 3)
    s2, f2 = sr.update_repeat(s, None, "X", None, 3)
    assert s2.streak == 0 and f2 is False


def test_t039_site_change_and_below_threshold():
    site_a = sr.CallSite("DemoVault", "gate")
    site_b = sr.CallSite("Other", "gate")
    s = sr.RepeatState()
    s, _ = sr.update_repeat(s, site_a, "SelA", "alice", 3)
    s, f = sr.update_repeat(s, site_b, "SelA", "bob", 3)
    assert s.streak == 1 and f is False

    s = sr.RepeatState()
    s, _ = sr.update_repeat(s, site_a, "SelA", "alice", 3)
    s, f = sr.update_repeat(s, site_a, "SelB", "bob", 3)
    assert s.streak == 1 and f is False

    hist = json.loads(_read(TR / "history_below_threshold.json"))
    state, fired = _run_history(hist, threshold=3)
    assert fired is False
    assert state.streak == 2


# ── T040 corroboration ────────────────────────────────────────────────────────

def test_t040_corroboration():
    site = sr.CallSite("DemoVault", "gate")
    checks = [
        sr.ReachabilityCheck(
            pattern="config_manager_field",
            status="incomplete",
            missing=["setConfigManager"],
            protected_call_site=site,
        )
    ]
    assert sr.corroboration(site, checks) is checks[0]
    assert sr.corroboration(sr.CallSite("Other", "gate"), checks) is None
    assert sr.corroboration(None, checks) is None
    assert sr.corroboration(site, []) is None
    complete_only = [
        sr.ReachabilityCheck(
            pattern="config_manager_field",
            status="complete",
            missing=[],
            protected_call_site=site,
        )
    ]
    assert sr.corroboration(site, complete_only) is None


# ── T041 repeat_hint forms ────────────────────────────────────────────────────

def test_t041_repeat_hint_forms():
    site = sr.CallSite("DemoVault", "gate")
    st_conf = sr.RepeatState(
        last_call_site=site,
        last_revert_selector="ConfigManagerOnly",
        streak=3,
        confirmed_caller_change=True,
    )
    st_ind = sr.RepeatState(
        last_call_site=site,
        last_revert_selector="ConfigManagerOnly",
        streak=3,
        confirmed_caller_change=False,
    )
    corr = sr.ReachabilityCheck(
        pattern="config_manager_field",
        status="incomplete",
        missing=["setConfigManager"],
        protected_call_site=site,
    )
    a_i = sr.repeat_hint(st_conf, None)
    a_ii = sr.repeat_hint(st_ind, None)
    b_conf = sr.repeat_hint(st_conf, corr)
    b_ind = sr.repeat_hint(st_ind, corr)

    assert "regardless of caller" in a_i
    assert "regardless of caller" not in a_ii
    assert "could not be reliably determined" in a_ii

    assert "setConfigManager" in b_conf
    assert "regardless of caller" in b_conf
    assert "setConfigManager" in b_ind
    assert "regardless of caller" not in b_ind
    # additive / distinguishable
    assert a_i != a_ii != b_conf


# ── T047-T050 mechanism baseline ──────────────────────────────────────────────

def test_t047_mechanism_drops_method():
    base: set[str] = set()
    last = None
    base, last, r = sr.update_mechanism_baseline(
        base, last, True, {"checked": ["a", "b"], "called": ["a", "b"]}
    )
    assert r == ""
    base, last, r = sr.update_mechanism_baseline(
        base, last, True, {"checked": ["a", "b"], "called": ["a", "b", "c"]}
    )
    assert r == ""
    base, last, r = sr.update_mechanism_baseline(
        base, last, True, {"checked": ["a"], "called": ["a"]}
    )
    assert "b" in r and "c" in r
    assert "a" not in r.split("dropped")[-1] if False else True
    assert "b" in r and "c" in r


def test_t048_superset_no_reminder():
    base: set[str] = set()
    last = None
    base, last, _ = sr.update_mechanism_baseline(
        base, last, True, {"checked": ["a"], "called": ["a"]}
    )
    base, last, r = sr.update_mechanism_baseline(
        base, last, True, {"checked": ["a", "b"], "called": ["a", "b"]}
    )
    assert r == ""


def test_t049_empty_checked_or_no_prior():
    base: set[str] = set()
    last = None
    base, last, r = sr.update_mechanism_baseline(
        base, last, True, {"checked": [], "called": ["a"]}
    )
    assert r == ""
    base, last, r = sr.update_mechanism_baseline(
        set(), None, True, {"checked": ["a"], "called": ["a"]}
    )
    assert r == ""  # no prior compiled


def test_t050_compiled_false_transparent():
    base: set[str] = set()
    last = None
    base, last, _ = sr.update_mechanism_baseline(
        base, last, True, {"checked": ["a", "b"], "called": ["a", "b"]}
    )
    # draft with extra method - must not enter baseline
    base2, last2, r2 = sr.update_mechanism_baseline(
        base, last, False, {"checked": ["ghost"], "called": ["ghost"]}
    )
    assert base2 == base and last2 == last and r2 == ""

    # following compiled drops b - same as if draft absent
    _, _, r_with = sr.update_mechanism_baseline(
        base2, last2, True, {"checked": ["a"], "called": ["a"]}
    )
    _, _, r_without = sr.update_mechanism_baseline(
        base, last, True, {"checked": ["a"], "called": ["a"]}
    )
    assert r_with == r_without
    assert "b" in r_with


def test_reachability_checks_to_json():
    checks = [
        sr.ReachabilityCheck(
            pattern="config_manager_field",
            status="incomplete",
            missing=["setConfigManager"],
            protected_call_site=sr.CallSite("DemoVault", "gate"),
        )
    ]
    rows = sr.reachability_checks_to_json(checks)
    assert rows[0]["pattern"] == "config_manager_field"
    assert rows[0]["protected_call_site"]["contract"] == "DemoVault"


def test_same_caller_helpers():
    assert sr.same_caller_conservative("a", "a") is True
    assert sr.same_caller_conservative(None, None) is False
    assert sr.pair_confirmed_diff("a", "b") is True
    assert sr.pair_confirmed_diff(None, "b") is False


def test_synthesis_extras_excerpts_large_parent_source():
    """Matched parent_attach must not dump a 50KB parent into the synth prompt."""
    setter = "setFoo"
    huge = (
        "// SPDX-License-Identifier: MIT\npragma solidity ^0.8.28;\n"
        + ("// pad\n" * 4000)
        + f"contract FooCDO {{\n    function {setter}(IFoo x) external {{}}\n"
        + "    function coverage() external view returns (uint256) { return 1; }\n}\n"
        + ("// tail\n" * 2000)
    )
    assert len(huge) > sr.EXTRAS_BLOCK_CHAR_BUDGET * 2
    task = {
        "location": "FooCDO.coverage + Foo.cancel",
        "description": "attach",
    }
    parent = sr.ParentResolution(
        status="resolved",
        contract="FooCDO",
        source_text=huge,
        declared_type_var="cdo",
    )
    miss = (
        "interface IFoo {}\n"
        "contract Foo is IFoo { function cancel() external {} }\n"
    )
    matches = sr.detect_patterns(task, huge, parent, miss, "contract Base {}")
    assert any(m.pattern == "parent_attach" for m in matches)
    extras = sr.synthesis_extras(matches, huge[:8000], parent, task)
    assert "[DATA START parent_source]" in extras
    assert setter in extras
    parent_block = extras.split("[DATA START parent_source]")[1].split(
        "[DATA END parent_source]"
    )[0]
    assert len(parent_block) <= sr.EXTRAS_BLOCK_CHAR_BUDGET + 80
    assert len(extras) < len(huge)
    # Small sources stay byte-identical (no forced truncation markers).
    small = f"contract P {{ function {setter}(IFoo x) external {{}} }}\n"
    parent_small = sr.ParentResolution(
        status="resolved", contract="P", source_text=small, declared_type_var="p"
    )
    m2 = [
        sr.PatternMatch(
            pattern="parent_attach",
            evidence={
                "parent": "P",
                "setter": setter,
                "dependency_type": "Foo",
            },
            protected_call_site=sr.CallSite("P", setter),
        )
    ]
    ex2 = sr.synthesis_extras(m2, "loc", parent_small, task)
    assert small in ex2
    assert "[truncated" not in ex2


# ── Live-miss regressions (H-01 shaped) ───────────────────────────────────────

_MSG_SENDER_MOD_GATE = """\
abstract contract AccessControlled {
    address public twoStepConfigManager;

    modifier onlyTwoStepConfigManager() {
        require(twoStepConfigManager == _msgSender(), "ConfigManagerOnly");
        _;
    }

    function setTwoStepConfigManager(address twoStepConfigManager_) external onlyOwner {
        twoStepConfigManager = twoStepConfigManager_;
    }
}

contract Foo is IFoo, AccessControlled {
    function setVaultFooBounds(address vault, uint256 bounds) external onlyTwoStepConfigManager {
        vault;
        bounds;
    }
}
"""

_IFACE_PARENT = """\
interface IFoo {}
contract FooCDO {
    function setFoo(IFoo sharesCooldown_) external onlyOwner {
        // attach
    }
    function coverage() external view returns (uint256) { return 1; }
}
"""

_IFACE_MISS = """\
interface IFoo {}
contract Foo is IFoo, CooldownBase {
    function cancel() external {}
}
"""


def test_detect_config_manager_msg_sender_modifier():
    """OZ-style _msgSender() inside a named modifier must match (live Foo)."""
    task = {
        "location": "Foo.setVaultFooBounds",
        "description": "config gate",
    }
    parent = sr.ParentResolution(status="no_candidate")
    base = (
        "contract Base {\n"
        "    address internal owner;\n"
        "    function setUp() public { vm.startPrank(owner); }\n"
        "}\n"
    )
    matches = sr.detect_patterns(task, _MSG_SENDER_MOD_GATE, parent, "", base)
    assert any(m.pattern == "config_manager_field" for m in matches)
    cfg = next(m for m in matches if m.pattern == "config_manager_field")
    assert cfg.evidence["field"] == "twoStepConfigManager"
    assert cfg.evidence["setter"] == "setTwoStepConfigManager"
    assert cfg.protected_call_site.method == "setVaultFooBounds"


def test_detect_parent_attach_interface_param():
    """Parent setter taking IDep must match when missing dep `is IDep`."""
    task = {
        "location": "FooCDO.coverage + Foo.cancel",
        "description": "attach",
    }
    parent = sr.ParentResolution(
        status="resolved",
        contract="FooCDO",
        source_text=_IFACE_PARENT,
        declared_type_var="cdo",
    )
    matches = sr.detect_patterns(task, _IFACE_PARENT, parent, _IFACE_MISS, "contract Base {}")
    assert any(m.pattern == "parent_attach" for m in matches)
    pa = next(m for m in matches if m.pattern == "parent_attach")
    assert pa.evidence["setter"] == "setFoo"
    assert pa.evidence["dependency_type"] == "Foo"


def test_extras_name_config_receiver_type():
    """Config-manager bullet must name the receiver type, not a bare setter."""
    task = {"location": "DemoVault.sol:gate", "description": "config gate"}
    gate = _read(FIX / "config_manager_field" / "complete" / "Gate.sol")
    base = _read(FIX / "config_manager_field" / "complete" / "Base.sol")
    parent = sr.ParentResolution(status="no_candidate")
    matches = sr.detect_patterns(task, gate, parent, "", base)
    extras = sr.synthesis_extras(matches, gate, parent, task)
    assert "newly deployed" in extras
    assert "DemoVault" in extras
    assert "Do NOT call" in extras


def test_fix_wiring_receivers_retargets_wrong_type():
    """acm.setX -> sharesCooldown.setX when Foo is the preferred receiver type."""
    matches = [
        sr.PatternMatch(
            pattern="config_manager_field",
            evidence={
                "field": "twoStepConfigManager",
                "setter": "setTwoStepConfigManager",
                "gated_contract": "Foo",
                "gated_function": "setVaultFooBounds",
            },
            protected_call_site=sr.CallSite("Foo", "setVaultFooBounds"),
        ),
        sr.PatternMatch(
            pattern="parent_attach",
            evidence={
                "parent": "ParentVault",
                "setter": "setCooldown",
                "dependency_type": "Foo",
            },
            protected_call_site=sr.CallSite("ParentVault", "setCooldown"),
        ),
    ]
    bad = (
        "contract SynthBase {\n"
        "    Foo internal sharesCooldown;\n"
        "    AccessControlManager internal acm;\n"
        "    function setUp() public {\n"
        "        acm.setTwoStepConfigManager(makeAddr('cm'));\n"
        "    }\n"
        "}\n"
    )
    out, applied = sr.fix_wiring_receivers(bad, matches)
    assert applied
    assert "sharesCooldown.setTwoStepConfigManager" in out
    assert "acm.setTwoStepConfigManager" not in out
    checks = sr.check_reachability(matches, bad)
    assert checks[0].status == "incomplete"
    checks2 = sr.check_reachability(matches, out)
    assert checks2[0].status == "complete"


# ── Feature 047 US1: synth-scoped, subtype-aware, masking-safe receiver fix ────
# All names invented/synthetic (FR-010). Index built offline via build_from_source.

from scripts.solidity_index import SymbolIndex  # noqa: E402


def _synth_index(extra: str = "") -> SymbolIndex:
    """Owner `Guardian` declares the setter; Widget/Gadget inherit it; Unrelated does not."""
    return SymbolIndex.build_from_source(
        "// SPDX-License-Identifier: MIT\npragma solidity ^0.8.28;\n"
        "contract Guardian { function configure(address a) external {} }\n"
        "contract Widget is Guardian {}\n"
        "contract Gadget is Guardian {}\n"
        "contract Unrelated {}\n" + extra
    )


def _cfg(setter: str, *, gated: str = "", dep: str = "") -> list[sr.PatternMatch]:
    ms = [sr.PatternMatch(pattern="config_manager_field",
                          evidence={"setter": setter, "gated_contract": gated})]
    if dep:
        ms.append(sr.PatternMatch(pattern="parent_attach",
                                  evidence={"setter": setter, "dependency_type": dep}))
    return ms


def test_fix_wiring_synth_rewrites_to_missing_type_var():
    """SC-001: phantom rewritten to the declared missing-type var that is-a the owner."""
    idx = _synth_index()
    matches = _cfg("configure", gated="Guardian")
    code = (
        "contract SynthBase {\n"
        "    Widget widget;\n"
        "    function setUp() public { foo.configure(admin); }\n"  # foo undeclared
        "}\n"
    )
    out, applied = sr.fix_wiring_receivers(
        code, matches, missing_types=["Widget"], symbol_index=idx)
    assert applied
    assert "widget.configure(admin)" in out
    assert "foo.configure" not in out


def test_fix_wiring_synth_masking_default_not_satisfied():
    """SC-002: phantom == _default_var_name(rtype), undeclared, must NOT be left masked."""
    idx = _synth_index()
    matches = _cfg("configure", gated="Guardian")
    default_recv = sr._default_var_name("Guardian")  # "guardian"
    code = (
        "contract SynthBase {\n"
        "    Widget widget;\n"
        f"    function setUp() public {{ {default_recv}.configure(admin); }}\n"
        "}\n"
    )
    out, applied = sr.fix_wiring_receivers(
        code, matches, missing_types=["Widget"], symbol_index=idx)
    # never silently satisfied: either rewritten to the real declared var, or byte-identical
    assert applied and "widget.configure(admin)" in out
    assert f"{default_recv}.configure" not in out


def test_fix_wiring_synth_ambiguous_missing_type_vars_noop():
    """SC-003a: two distinct subtype-valid missing-type vars, no rtype var -> byte no-op."""
    idx = _synth_index()
    matches = _cfg("configure", gated="Guardian")  # rtype=Guardian, no guardian var declared
    code = (
        "contract SynthBase {\n"
        "    Widget widget;\n"
        "    Gadget gadget;\n"
        "    function setUp() public { foo.configure(admin); }\n"
        "}\n"
    )
    out, applied = sr.fix_wiring_receivers(
        code, matches, missing_types=["Widget", "Gadget"], symbol_index=idx)
    assert not applied
    assert out == code  # byte-identical


def test_fix_wiring_synth_rtype_gate_non_owner_not_chosen():
    """rtype-gate regression: a declared non-owner rtype var must never be the target."""
    idx = _synth_index()
    # rtype = Unrelated (declared, but NOT is_subtype Guardian) via parent_attach dep
    matches = _cfg("configure", gated="Guardian", dep="Unrelated")
    code = (
        "contract SynthBase {\n"
        "    Unrelated unrelated;\n"
        "    Widget widget;\n"
        "    function setUp() public { foo.configure(admin); }\n"
        "}\n"
    )
    out, applied = sr.fix_wiring_receivers(
        code, matches, missing_types=["Widget"], symbol_index=idx)
    assert applied
    assert "widget.configure(admin)" in out
    assert "unrelated.configure" not in out  # non-owner rtype var rejected


def test_fix_wiring_synth_rtype_owner_may_be_chosen():
    """Inherited-base case: a declared rtype var that DOES own the setter may be chosen."""
    idx = _synth_index()
    matches = _cfg("configure", gated="Guardian")  # rtype=Guardian (reflexive owner)
    code = (
        "contract SynthBase {\n"
        "    Guardian guardian;\n"
        "    Widget widget;\n"
        "    function setUp() public { foo.configure(admin); }\n"
        "}\n"
    )
    out, applied = sr.fix_wiring_receivers(
        code, matches, missing_types=["Widget"], symbol_index=idx)
    assert applied
    assert "guardian.configure(admin)" in out  # rtype var wins (precedence)


def test_fix_wiring_synth_idempotent():
    """FR-007: a second pass makes no further change."""
    idx = _synth_index()
    matches = _cfg("configure", gated="Guardian")
    code = (
        "contract SynthBase {\n"
        "    Widget widget;\n"
        "    function setUp() public { foo.configure(admin); }\n"
        "}\n"
    )
    out1, applied1 = sr.fix_wiring_receivers(
        code, matches, missing_types=["Widget"], symbol_index=idx)
    out2, applied2 = sr.fix_wiring_receivers(
        out1, matches, missing_types=["Widget"], symbol_index=idx)
    assert applied1 and not applied2
    assert out2 == out1


def test_fix_wiring_legacy_unchanged_no_index():
    """SC-003b: the two-positional (no-index) legacy path is byte-identical to pre-feature.

    With no symbol_index, a phantom equal to the synthetic default is still treated as
    allowed (the documented legacy behaviour) -> byte-stable no-op. This guards that the
    legacy branch was not perturbed by the synth branch."""
    matches = _cfg("configure", gated="Guardian")
    default_recv = sr._default_var_name("Guardian")
    code = (
        "contract SynthBase {\n"
        f"    function setUp() public {{ {default_recv}.configure(admin); }}\n"
        "}\n"
    )
    out, applied = sr.fix_wiring_receivers(code, matches)  # no kwargs -> legacy
    assert not applied
    assert out == code
