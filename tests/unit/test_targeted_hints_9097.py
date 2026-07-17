"""Feature 024 US1/US2: `_targeted_hints` repairs a redeclaration collision (Error 9097).

A PoC that inherits the project's test scaffold and then re-declares an identifier the base
already declares gets `Identifier already declared` — an error the layer had NO fix for, so the
model hit the same wall every attempt (live run 2026-07-16: 3/3 attempts, then `exhausted`).

US1: name the identifier, name where the prior declaration lives, offer both routes.
US2: fire ONLY on this signature — never on `Identifier not found` or anything else.

Offline: no model, no Docker, no network. Every fixture is INVENTED and reproduces only the
SHAPE of solc output — no audited-target name, path, or contract enters this repo.
"""
from scripts.poc_queue_runner import _targeted_hints


def _redecl_block(name="vaultKeeper", base_type="KeeperRegistry", poc_type="KeeperRegistry",
                  base_file="test/demo/DemoDeploy.t.sol", poc_file="audit/poc/7.t.sol"):
    """A realistic Error (9097) block: primary pointer at one declaration, `Note:` at the other,
    both underlined. Mirrors the live shape, with invented names."""
    return (
        f"Error (9097): Identifier already declared.\n"
        f"  --> {base_file}:58:5:\n"
        f"   |\n"
        f"58 |     {base_type} internal {name};\n"
        f"   |     {'^' * (len(base_type) + len(name) + 11)}\n"
        f"Note: The previous declaration is here:\n"
        f"  --> {poc_file}:24:5:\n"
        f"   |\n"
        f"24 |     {poc_type} public {name};\n"
        f"   |     {'^' * (len(poc_type) + len(name) + 9)}\n"
    )


def _hint(out, **kw):
    return _targeted_hints(out, kw.pop("callable_api", ""), kw.pop("file_map", ""), "", None,
                           kw.pop("scaffold", ""))


# ── US1: the collision is repaired in one shot ───────────────────────────────

def test_redeclaration_hint_names_the_identifier():
    h = _hint(_redecl_block(name="vaultKeeper"))
    assert "vaultKeeper" in h
    assert "redeclare" in h.lower() or "declar" in h.lower()


def test_lowercase_initial_type_still_recognized():
    # Live trap A: a real collision used a lowercase-initial type (shape reproduced here).
    # `_STATE_VAR_TYPE_RE`'s `[A-Z]\w*` would miss this; `_BASE_STATE_VAR_RE` must not.
    h = _hint(_redecl_block(name="pairProvider", base_type="sDemoPairProvider",
                            poc_type="sDemoPairProvider"))
    assert "pairProvider" in h


def test_differing_types_offers_the_rename_route():
    # Live trap B: base and PoC declare the same NAME with DIFFERENT types, so
    # "use the inherited one" may not typecheck — the rename route must be offered.
    h = _hint(_redecl_block(name="pairProvider", base_type="sDemoPairProvider",
                            poc_type="DemoPairProvider"))
    assert "pairProvider" in h
    assert "rename" in h.lower()


def test_names_the_declaring_location_not_the_pocs_own_file():
    h = _hint(_redecl_block(base_file="test/demo/DemoDeploy.t.sol", poc_file="audit/poc/7.t.sol"))
    assert "test/demo/DemoDeploy.t.sol" in h
    assert "audit/poc/7.t.sol" not in h   # never point the model at its own file


def test_hint_still_fires_when_no_declaring_location_is_derivable():
    # Only the PoC's own file is named → no base location to report, but the collision is real.
    h = _hint(_redecl_block(base_file="audit/poc/7.t.sol", poc_file="audit/poc/7.t.sol"))
    assert "vaultKeeper" in h


def test_unparseable_declaration_falls_back_to_generic_no_invented_name():
    # A collision that is not a visibility-qualified state var (here: a function).
    out = ("Error (9097): Identifier already declared.\n"
           "  --> test/demo/DemoDeploy.t.sol:12:5:\n"
           "   |\n"
           "12 |     function setUp() public {\n"
           "   |     ^^^^^^^^^^^^^^^^^^^^^^^^\n")
    h = _hint(out)
    assert h                              # generic guidance is still emitted
    assert "redeclare" in h.lower()
    assert "setUp" not in h               # never invent/misname the colliding identifier


# ── US2: never fires on anything else ────────────────────────────────────────

def test_identifier_not_found_does_not_trigger_redeclaration_hint():
    # 7920 shares the word "Identifier" — the matcher must key on the full phrase.
    out = 'Error (7920): Identifier not found or not unique.\n  --> audit/poc/7.t.sol:9:9:\n'
    h = _hint(out)
    assert "already declared" not in h.lower()
    assert "An identifier is undefined" in h        # the pre-existing 7920 hint still fires


def test_clean_output_yields_no_redeclaration_hint():
    assert "already declared" not in _hint("Ran 1 test for audit/poc/7.t.sol:PoC\n").lower()


def test_identical_triggers_are_deduplicated():
    out = _redecl_block() + "\n" + _redecl_block()
    h = _hint(out)
    assert h.lower().count("do not redeclare") == 1


def test_coexisting_errors_are_all_kept():
    out = (_redecl_block(name="vaultKeeper")
           + '\nError (6275): Source "Missing.sol" not found\n'
           + '\nError (9582): Member "totalDebt" not found or not visible after '
             'argument-dependent lookup in contract DemoVault.\n')
    h = _hint(out)
    assert "vaultKeeper" in h                        # ours was added
    assert "Missing" in h                            # 6275 survived
    assert "totalDebt" in h                          # 9582 survived
