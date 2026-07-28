"""Feature 041 - scaffold API inventory (offline, target-free)."""
from __future__ import annotations

from pathlib import Path

from scripts.scaffold_api_inventory import (
    HINT_EXCERPT_BUDGET,
    INVENTORY_BUDGET,
    build_inventory,
    derive_or_omit,
    hint_excerpt,
    render_inventory,
    resolve_parent_edge,
)

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "scaffold_api"


def _map_dir(d: Path) -> dict[Path, str]:
    return {p.resolve(): p.read_text(encoding="utf-8") for p in d.rglob("*.sol")}


def test_same_file_parent_distance_1_sc012():
    leaf = FIX / "same_file" / "Leaf.sol"
    inv = build_inventory(leaf, file_map=_map_dir(FIX / "same_file"))
    assert inv is not None
    assert inv.leaf_contract == "Leaf"
    names = {(s.name, s.distance, s.declaring_contract) for s in inv.state_vars}
    assert ("parentAsset", 1, "Parent") in names
    assert ("leafOnly", 0, "Leaf") in names
    assert not any(s.name == "secret" for s in inv.state_vars)
    assert not inv.unresolved_parents


def test_sc013_same_file_short_circuits_import():
    leaf = FIX / "same_file_plus_import" / "Leaf.sol"
    inv = build_inventory(leaf, file_map=_map_dir(FIX / "same_file_plus_import"))
    assert inv is not None
    assert not inv.unresolved_parents
    assert any(s.name == "sameFileAsset" and s.distance == 1 for s in inv.state_vars)
    assert not any(s.name == "importedTwin" for s in inv.state_vars)


def test_three_level_neighbor_imports_sc011():
    leaf = FIX / "three_level" / "Leaf.sol"
    inv = build_inventory(leaf, file_map=_map_dir(FIX / "three_level"))
    assert inv is not None
    assert any(s.name == "gpAsset" and s.distance == 2 for s in inv.state_vars)
    assert any(s.name == "parentAsset" and s.distance == 1 for s in inv.state_vars)
    assert not any(u.name == "Grandparent" for u in inv.unresolved_parents)


def test_dup_base_only_imported_sc009():
    leaf = FIX / "dup_base" / "Leaf.sol"
    inv = build_inventory(leaf, file_map=_map_dir(FIX / "dup_base"))
    assert inv is not None
    assert any(s.name == "correctAsset" for s in inv.state_vars)
    assert not any(s.name == "wrongAsset" for s in inv.state_vars)


def test_alias_import_sc009():
    leaf = FIX / "alias_import" / "Leaf.sol"
    inv = build_inventory(leaf, file_map=_map_dir(FIX / "alias_import"))
    assert inv is not None
    assert any(s.name == "aliasedAsset" and s.distance == 1 for s in inv.state_vars)
    assert any(h.name == "setUpFoo" and h.section == "lifecycle" for h in inv.lifecycle)


def test_transitive_remapping_sc009():
    leaf = FIX / "transitive_remap" / "Leaf.sol"
    remappings = [f"gp/={(FIX / 'transitive_remap' / 'lib').resolve()}/"]
    inv = build_inventory(
        leaf,
        file_map=_map_dir(FIX / "transitive_remap"),
        remappings=remappings,
    )
    assert inv is not None
    assert any(s.name == "remappedGp" and s.distance == 2 for s in inv.state_vars)


def test_missing_parent_partial():
    leaf = FIX / "ambiguous" / "Leaf.sol"
    inv = build_inventory(leaf, file_map=_map_dir(FIX / "ambiguous"))
    assert inv is not None
    assert inv.degraded
    assert any(u.name == "MissingParent" and u.reason == "missing" for u in inv.unresolved_parents)
    assert "unresolved_parents" in inv.rendered_body


def test_private_parent_excluded_lifecycle_callable_sc001_sc010():
    leaf = FIX / "private_parent" / "Leaf.sol"
    inv = build_inventory(leaf, file_map=_map_dir(FIX / "private_parent"))
    assert inv is not None
    assert not any(s.name == "hidden" for s in inv.state_vars)
    assert any(s.name == "visibleInternal" for s in inv.state_vars)
    leaf2 = FIX / "same_file" / "Leaf.sol"
    inv2 = build_inventory(leaf2, file_map=_map_dir(FIX / "same_file"))
    assert any(h.name == "setUp" for h in inv2.lifecycle)
    assert not any(h.name == "setUp" for h in inv2.callable_helpers)
    assert any(h.name == "_deployThing" for h in inv2.callable_helpers)
    assert any(h.name == "_grantRole" for h in inv2.callable_helpers)


def test_overloads_sc014():
    leaf = FIX / "overloads" / "Leaf.sol"
    inv = build_inventory(leaf, file_map=_map_dir(FIX / "overloads"))
    assert inv is not None
    sigs = sorted(h.canonical_param_types for h in inv.callable_helpers if h.name == "_deployX")
    assert sigs == ["address", "uint256"]
    body = inv.rendered_body
    assert "_deployX(address)" in body
    assert "_deployX(uint256)" in body
    assert body.index("_deployX(address)") < body.index("_deployX(uint256)")


def test_truncation_sc005():
    leaf = FIX / "truncation" / "Leaf.sol"
    inv = build_inventory(leaf, file_map=_map_dir(FIX / "truncation"))
    assert inv is not None
    assert len(inv.rendered_body) <= INVENTORY_BUDGET
    again = render_inventory(inv)
    assert again == inv.rendered_body
    assert "v00" in inv.rendered_body


def test_derive_or_omit_fr014_sc015():
    a = FIX / "multi_path_a.sol"
    b = FIX / "multi_path_b.sol"
    inv, reason = derive_or_omit([a, b])
    assert inv is None
    assert reason == "ambiguous_leaf"

    inv2, reason2 = derive_or_omit([])
    assert inv2 is None and reason2 is None

    inv3, reason3 = derive_or_omit(
        [FIX / "overloads" / "Leaf.sol"],
        file_map=_map_dir(FIX / "overloads"),
    )
    assert inv3 is not None and reason3 is None

    inv4, reason4 = derive_or_omit([FIX / "no_such.sol"])
    assert inv4 is None and reason4 == "leaf_unparseable"


def test_rebuild_stability_sc001():
    leaf = FIX / "same_file" / "Leaf.sol"
    fm = _map_dir(FIX / "same_file")
    a = build_inventory(leaf, file_map=fm)
    b = build_inventory(leaf, file_map=fm)
    assert a is not None and b is not None
    assert a.rendered_body == b.rendered_body


def test_hint_excerpt_budget():
    leaf = FIX / "truncation" / "Leaf.sol"
    inv = build_inventory(leaf, file_map=_map_dir(FIX / "truncation"))
    assert inv is not None
    ex = hint_excerpt(inv)
    assert len(ex) <= HINT_EXCERPT_BUDGET


def test_resolve_parent_edge_same_file_unit():
    leaf = FIX / "same_file" / "Leaf.sol"
    src = leaf.read_text(encoding="utf-8")
    edge = resolve_parent_edge(
        child_path=leaf, child_source=src, parent_name="Parent",
        file_map=_map_dir(FIX / "same_file"),
    )
    assert edge.via == "same_file"
    assert edge.resolved_path == leaf.resolve()


def test_data_block_markers():
    leaf = FIX / "overloads" / "Leaf.sol"
    inv = build_inventory(leaf, file_map=_map_dir(FIX / "overloads"))
    assert inv is not None
    block = inv.data_block
    assert block.startswith("[DATA START scaffold_api]")
    assert block.endswith("[DATA END scaffold_api]")


def test_prompt_templates_include_scaffold_api_placeholder():
    from scripts import poc_queue_runner as pqr
    assert "{scaffold_api}" in pqr.DRAFT_PROMPT
    assert "{scaffold_api}" in pqr.FIX_PROMPT


def test_draft_fix_prompts_sc004_presence_absence():
    from scripts.poc_queue_runner import (
        DRAFT_PROMPT, FIX_PROMPT, _scaffold_api_field, _grounding,
    )
    leaf = FIX / "overloads" / "Leaf.sol"
    inv = build_inventory(leaf, file_map=_map_dir(FIX / "overloads"))
    source, scaf = _grounding(Path("."), "X.sol", "scaffold text", "")
    kwargs = dict(
        fid="H-01", title="t", location="X.sol", description="d", ident="H01",
        source=source, scaffold=scaf, example="(none)", files="(none)",
        callable="(none)", exploit_quality_checklist="CL",
    )
    with_inv = DRAFT_PROMPT.format(**kwargs, scaffold_api=_scaffold_api_field(inv))
    without = DRAFT_PROMPT.format(**kwargs, scaffold_api="")
    assert "[DATA START scaffold_api]" in with_inv
    assert "[DATA START scaffold_api]" not in without
    fix_with = FIX_PROMPT.format(
        **{**kwargs, "previous": "prev", "error": "err"},
        scaffold_api=_scaffold_api_field(inv),
    )
    fix_without = FIX_PROMPT.format(
        **{**kwargs, "previous": "prev", "error": "err"},
        scaffold_api="",
    )
    assert "[DATA START scaffold_api]" in fix_with
    assert "[DATA START scaffold_api]" not in fix_without


def test_undeclared_hint_with_and_without_inventory():
    from scripts import poc_queue_runner as pqr
    leaf = FIX / "overloads" / "Leaf.sol"
    inv = build_inventory(leaf, file_map=_map_dir(FIX / "overloads"))
    baseline = pqr._targeted_hints('Error: Identifier not found "foo"', "", "")
    assert "ALREADY exposes" not in baseline
    with_inv = pqr._targeted_hints(
        'Error: Identifier not found "x"', "", "", inventory=inv,
    )
    assert "ALREADY exposes" in with_inv
    assert "scaffold_api excerpt" in with_inv
    assert baseline == pqr._targeted_hints(
        'Error: Identifier not found "foo"', "", "", inventory=None,
    )


def test_refresh_inventory_stamped_omit_and_partial():
    from scripts import poc_queue_runner as pqr
    events = []

    def log(e):
        events.append(pqr._stamp(e, run_id="r1", model="m1", code_version="c1"))

    a = FIX / "multi_path_a.sol"
    b = FIX / "multi_path_b.sol"
    inv = pqr._refresh_scaffold_inventory([a, b], Path("."), log, "H-01")
    assert inv is None
    assert events[-1]["event"] == "scaffold_api_omit"
    assert events[-1]["reason"] == "ambiguous_leaf"
    assert events[-1]["run_id"] == "r1"
    assert "terminal" not in events[-1]

    events.clear()
    leaf = FIX / "ambiguous" / "Leaf.sol"
    # build via refresh needs path on disk
    inv2 = pqr._refresh_scaffold_inventory([leaf], Path("."), log, "H-01")
    assert inv2 is not None and inv2.degraded
    assert events[-1]["event"] == "scaffold_api_partial"
    assert events[-1]["run_id"] == "r1"
    assert "terminal" not in events[-1]
