"""Boundary check (feature 004, SC-001): the kernel imports nothing from packs.

The machine-checkable form of US1. "Kernel" = every `sr_agent/**/*.py` EXCEPT
`sr_agent/packs/**` and the composition root `sr_agent/cli.py`. No kernel file
may import a module under `sr_agent.packs`. pack→kernel imports are expected;
only kernel→pack is forbidden.

Uses `ast` (not grep) so the string "sr_agent.packs" in a comment/docstring/or
this test's own data never counts as an import. See
specs/004-kernel-pack-boundary/contracts/boundary-check.md.

Starts green (nothing is under packs/ yet). As audit modules relocate into
packs/, any kernel file still importing them becomes a violation — the
invert-before-move discipline keeps this at 0 at every committed checkpoint.
"""
from __future__ import annotations

import ast
from pathlib import Path

SR_AGENT = Path(__file__).resolve().parents[2] / "sr_agent"
REPO = SR_AGENT.parent
PACK_ROOT = "sr_agent.packs"
# The composition root is neither kernel nor pack — it is the one place allowed
# to import the pack and wire it in.
COMPOSITION_ROOTS = {"sr_agent/cli.py"}


def _kernel_files() -> list[tuple[Path, str]]:
    out: list[tuple[Path, str]] = []
    for p in sorted(SR_AGENT.rglob("*.py")):
        parts = p.relative_to(SR_AGENT).parts
        if "packs" in parts:  # pack code — pack→kernel imports are allowed
            continue
        rel = "sr_agent/" + "/".join(parts)
        if rel in COMPOSITION_ROOTS:
            continue
        out.append((p, rel))
    return out


def _module_dotted(path: Path) -> tuple[str, bool]:
    """Return (dotted module name, is_package_init)."""
    parts = list(path.relative_to(REPO).with_suffix("").parts)
    is_init = parts[-1] == "__init__"
    if is_init:
        parts = parts[:-1]
    return ".".join(parts), is_init


def _imported_modules(path: Path) -> set[str]:
    """All absolute module targets a file imports (relative imports resolved)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    dotted, is_init = _module_dotted(path)
    dotted_parts = dotted.split(".")
    # The package a relative import is anchored to: the module's own package,
    # except an __init__ module *is* its package.
    pkg_parts = dotted_parts if is_init else dotted_parts[:-1]

    targets: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                targets.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                if node.module:
                    targets.add(node.module)
            else:  # relative: strip (level-1) trailing components from the package
                base = pkg_parts[: len(pkg_parts) - (node.level - 1)]
                mod = ([node.module] if node.module else [])
                if base or mod:
                    targets.add(".".join(base + mod))
    return targets


def _violations() -> list[str]:
    out: list[str] = []
    for path, rel in _kernel_files():
        for target in _imported_modules(path):
            if target == PACK_ROOT or target.startswith(PACK_ROOT + "."):
                out.append(f"{rel} -> {target}")
    return sorted(out)


def test_kernel_does_not_import_packs() -> None:
    violations = _violations()
    if violations:
        print(f"\nkernel→pack import violations: {len(violations)}")
        for v in violations:
            print(f"  {v}")
    assert not violations, (
        f"{len(violations)} kernel→pack import(s) — the kernel must not import "
        f"sr_agent.packs (SC-001). See printout above."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Feature 048 / US4 — the config+routing seam.
# Extends the 004 import guard (B1) with:
#   B2  no kernel module references an audit-owned config field name
#   B3  no kernel routing module carries a stage/poc slot identifier or sr-stage2
#   B4  the kernel routing surface accepts an arbitrary Mapping[str,str]
# and widens the file scope to tests/** (a stray audit import in a kernel test
# must be caught — that is what keeps the filter-repo path-set honest, T007).
# Written test-first: B2/B3/B4 are RED on current code and go green as T009–T013
# land. See specs/048-repo-split/contracts/boundary-check.md.
# ─────────────────────────────────────────────────────────────────────────────

import re

# The 5 audit-owned config fields (ownership table rows 18–22). No kernel module
# may read these (cli.py composition root is exempt in the monorepo; it moves to
# the audit repo in Phase D).
AUDIT_FIELD_NAMES = (
    "alchemy_api_key", "tenderly_api_key", "workspaces_root", "git_token",
    "smartgraphical_root",
)

# The three kernel routing modules (contracts/boundary-check.md B3).
KERNEL_ROUTING_MODULES = (
    "llm_core/router.py",
    "llm_core/claude_client.py",
    "orchestrator/loop.py",
)

# Model-routing slot/role identifiers + the audit-stage default literal. These are
# the *routing* tokens only — `poc_dir`/`poc_generator` (PoC-execution paths in
# loop.py) are legitimate and deliberately NOT matched (they are not model slots).
ROUTING_SLOT_PATTERNS = (
    r"\bstage1\b", r"\bstage2\b", r"\bstage3\b",
    r"\bpoc_model\b", r"\bpoc_writing\b",
    r"sr-stage2",
)


# The kernel-designated test tree — EXACTLY the filter-repo carve path-set
# (quickstart §1a): tests/{unit,security,architecture} + the new tests/fixtures/pack.
# tests/integration and the rest of tests/fixtures are audit-only and never carved
# into the kernel, so they are out of scope here. tests/audit/** (post-T015 audit
# relocations) is also excluded.
KERNEL_TEST_DIRS = ("unit", "security", "architecture")
KERNEL_FIXTURE_SUBDIR = ("fixtures", "pack")


def _kernel_test_files() -> list[tuple[Path, str]]:
    """Kernel test modules — scope for B1 over the kernel test tree (T007).

    During Phase A (monorepo) tests/{unit,architecture} still hold audit tests;
    T015 relocates them to tests/audit/**. Until then this guard is RED for those
    strays — it exists now so the relocation is verified, not assumed.
    """
    tests_root = REPO / "tests"
    out: list[tuple[Path, str]] = []
    for p in sorted(tests_root.rglob("*.py")):
        parts = p.relative_to(tests_root).parts
        top = parts[0]
        in_kernel_dir = top in KERNEL_TEST_DIRS
        in_kernel_fixture = parts[:2] == KERNEL_FIXTURE_SUBDIR
        if not (in_kernel_dir or in_kernel_fixture):
            continue  # tests/integration, other fixtures, tests/audit → not kernel carve
        out.append((p, "tests/" + "/".join(parts)))
    return out


def _kernel_source_files() -> list[tuple[Path, str]]:
    return _kernel_files()  # sr_agent/** minus packs/** minus cli.py


def test_no_kernel_module_reads_an_audit_owned_field() -> None:
    """B2 (FR-002 / SC-008): audit config fields are invisible to the kernel."""
    field_res = {name: re.compile(rf"\b{re.escape(name)}\b") for name in AUDIT_FIELD_NAMES}
    violations: list[str] = []
    for path, rel in _kernel_source_files():
        src = path.read_text(encoding="utf-8")
        for name, rx in field_res.items():
            if rx.search(src):
                violations.append(f"{rel}: {name}")
    assert not violations, (
        "kernel modules must not reference audit-owned config fields (B2):\n  "
        + "\n  ".join(sorted(violations))
    )


def test_no_kernel_routing_module_carries_a_stage_or_poc_slot() -> None:
    """B3 (FR-014/FR-015 / SC-008/009): routing is shape-agnostic, no audit stages."""
    patterns = [re.compile(p) for p in ROUTING_SLOT_PATTERNS]
    violations: list[str] = []
    for relmod in KERNEL_ROUTING_MODULES:
        path = SR_AGENT / relmod
        if not path.exists():  # router.py may be deleted as dead code — then nothing to flag
            continue
        src = path.read_text(encoding="utf-8")
        for rx in patterns:
            for m in rx.finditer(src):
                line = src[: m.start()].count("\n") + 1
                violations.append(f"sr_agent/{relmod}:{line}: {m.group(0)!r}")
    assert not violations, (
        "kernel routing modules must not contain stage/poc slot identifiers or the "
        "sr-stage2 default (B3 — shape-agnostic, not renamed):\n  "
        + "\n  ".join(violations)
    )


def test_kernel_routing_surface_accepts_arbitrary_role_map() -> None:
    """B4 (SC-009): the kernel router resolves any Mapping[str,str], no fixed roles."""
    from sr_agent.llm_core.router import ModelRouter

    synthetic = {"a": "m1", "b": "m2", "c": "m3", "d": "m4", "e": "m5"}
    r = ModelRouter(synthetic)
    for role, model in synthetic.items():
        assert r.route(role) == model, f"role {role!r} must resolve to {model!r}"
    # A missing role is a caller bug surfaced honestly — never a silent default.
    try:
        r.route("no_such_role")
    except KeyError:
        pass
    else:
        raise AssertionError("missing role must raise KeyError, not fall back to a default")


def test_kernel_test_tree_has_no_pack_imports() -> None:
    """B1 over tests/** (T007): a kernel test importing packs is a boundary breach.

    RED during Phase A until T015 relocates audit tests to tests/audit/**; the
    guard exists now so the relocation is verified, not assumed.
    """
    violations: list[str] = []
    for path, rel in _kernel_test_files():
        for target in _imported_modules(path):
            if target == PACK_ROOT or target.startswith(PACK_ROOT + "."):
                violations.append(f"{rel} -> {target}")
    assert not violations, (
        f"{len(violations)} kernel-test → pack import(s) — audit tests must live under "
        f"tests/audit/** (T015), not the kernel test tree:\n  " + "\n  ".join(sorted(violations))
    )
