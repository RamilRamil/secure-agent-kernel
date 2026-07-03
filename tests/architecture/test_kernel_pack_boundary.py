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
