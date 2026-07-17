"""Feature 024 US3: `_targeted_hints` stops misdirecting when the "missing member" is real.

Live run 2026-07-16, finding-2: the PoC reached a REAL state variable of the inherited scaffold
base through a wrong qualifier (`someContract.thatVar()`). The 9582 hint answered "that contract
has no such member — use its real functions", which is TRUE and MISDIRECTING: it sent the model
hunting a substitute on the wrong contract. Two attempts died there; the fix was to drop the
qualifier. "Not a member of Y" does not mean "not real".

The refinement fires ONLY on positive evidence from the scaffold. The byte-identical assertions
below are the regression guard on this pre-existing entry — the only one feature 024 touches.

Offline: no model, no Docker, no network. Every fixture is INVENTED.
"""
from scripts.poc_queue_runner import _targeted_hints

_ERR = ('Error (9582): Member "vaultKeeper" not found or not visible after '
        'argument-dependent lookup in contract DemoVault.\n'
        '  --> audit/poc/7.t.sol:14:67:\n')


def _scaffold(*decls):
    body = "\n".join(f"    {d}" for d in decls)
    return ("// [test_scaffold] the project's PoC base — INHERIT the contract `DemoDeploy`\n"
            f"contract DemoDeploy is BaseSetup {{\n{body}\n"
            "    function setUp() public virtual {}\n}\n")


def _hint(out, scaffold="", callable_api=""):
    return _targeted_hints(out, callable_api, "", "", None, scaffold)


# ── US3: positive evidence → the refined, unqualified-use instruction ────────

def test_base_state_var_reached_via_wrong_qualifier_gets_unqualified_fix():
    h = _hint(_ERR, scaffold=_scaffold("KeeperRegistry internal vaultKeeper;"))
    assert "vaultKeeper" in h
    assert "directly" in h.lower() or "unqualified" in h.lower()
    assert "DemoVault." in h            # the qualifier to drop is named


def test_lowercase_initial_type_in_scaffold_is_recognized():
    # Same live trap as 9097: casing carries no meaning.
    h = _hint(_ERR, scaffold=_scaffold("sDemoKeeperRegistry internal vaultKeeper;"))
    assert "directly" in h.lower() or "unqualified" in h.lower()


# ── US3: no positive evidence → today's text, BYTE-IDENTICAL ────────────────

def _legacy_text(sigs=""):
    """Exactly what the entry emits today, reproduced here so a drift is a red test."""
    if sigs:
        return f"`DemoVault` has NO member `vaultKeeper`. Use only its real functions:\n{sigs}"
    return "`DemoVault` has no member `vaultKeeper` — use a real function from [callable_api]."


def test_commented_out_declaration_is_not_evidence():
    # A commented-out decl must not be mistaken for a real one — this gate must not misfire.
    scaffold = _scaffold("// KeeperRegistry internal vaultKeeper;")
    assert _hint(_ERR, scaffold=scaffold) == _legacy_text()


def test_name_absent_from_scaffold_returns_legacy_text_byte_identical():
    scaffold = _scaffold("DemoOracle internal oracle;")
    assert _hint(_ERR, scaffold=scaffold) == _legacy_text()


def test_empty_scaffold_returns_legacy_text_byte_identical():
    assert _hint(_ERR, scaffold="") == _legacy_text()


def test_called_without_the_scaffold_argument_at_all_is_back_compatible():
    # Older callers pass no scaffold; the new parameter must be inert for them.
    assert _targeted_hints(_ERR, "", "", "") == _legacy_text()


def test_legacy_signature_listing_path_is_untouched():
    api = "contract DemoVault\n  function totalDebt() external view returns (uint256)"
    out = _targeted_hints(_ERR, api, "", "", None, "")
    assert out == _legacy_text(sigs=_sigs(api))


def _sigs(api):
    from scripts.poc_queue_runner import _sigs_for
    return _sigs_for(api, "DemoVault")
