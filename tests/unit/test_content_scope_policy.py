"""Include set is a security boundary, not a hashing optimisation (D20 / FR-013b).

A file inside `scope_root` but outside the bound include set must be neither
hashed nor readable. Otherwise mutating `script/Deploy.sol` after a detach
leaves a file the digest never covered and `read_file` will still serve -- the
exact hole SC-006a exists to close.
"""
from pathlib import Path

import pytest

from sr_agent.orchestrator.scope import (
    ContentScopePolicy,
    digest_worktree,
    sandbox_manifest,
)
from sr_agent.tools.readonly import ReadOnlyToolError, read_file, search_code


def _tree(tmp_path: Path) -> Path:
    (tmp_path / "contracts").mkdir()
    (tmp_path / "contracts" / "Vault.sol").write_text(
        "function withdraw() public {}\n", encoding="utf-8"
    )
    (tmp_path / "script").mkdir()
    (tmp_path / "script" / "Deploy.sol").write_text(
        "function deploy() public {}\n", encoding="utf-8"
    )
    (tmp_path / "relay").mkdir()
    (tmp_path / "relay" / "out.json").write_text('{"ok":true}', encoding="utf-8")
    return tmp_path


def _policy(root: Path) -> ContentScopePolicy:
    return ContentScopePolicy(
        scope_root=root,
        include=("contracts/**",),
        runtime_state_roots=(root / "relay",),
    )


# ── Reads ───────────────────────────────────────────────────────────────────


def test_read_file_serves_an_included_path(tmp_path):
    root = _tree(tmp_path)
    assert "withdraw" in read_file(root / "contracts" / "Vault.sol", root, policy=_policy(root))


def test_read_file_refuses_in_scope_but_outside_include_set(tmp_path):
    root = _tree(tmp_path)
    with pytest.raises(ReadOnlyToolError, match="include set"):
        read_file(root / "script" / "Deploy.sol", root, policy=_policy(root))


def test_read_file_refuses_runtime_state_roots(tmp_path):
    root = _tree(tmp_path)
    with pytest.raises(ReadOnlyToolError, match="runtime"):
        read_file(root / "relay" / "out.json", root, policy=_policy(root))


def test_search_code_enumerates_only_the_include_set(tmp_path):
    """Walking the passed root would leak `script/Deploy.sol` into the hits."""
    root = _tree(tmp_path)
    hits = search_code("function", root, file_ext=".sol", policy=_policy(root))
    files = {h.file for h in hits}
    assert files == {"contracts/Vault.sol"}


def test_sandbox_manifest_is_the_same_set(tmp_path):
    root = _tree(tmp_path)
    manifest = sandbox_manifest(_policy(root))
    assert manifest == ("contracts/Vault.sol",)


# ── Digest and the SC-006a scenario ─────────────────────────────────────────


def test_digest_covers_only_the_include_set(tmp_path):
    root = _tree(tmp_path)
    identity = digest_worktree(_policy(root))
    assert identity.file_count == 1
    (root / "script" / "Deploy.sol").write_text("function mutated() public {}\n", encoding="utf-8")
    assert digest_worktree(_policy(root)).digest == identity.digest


def test_mutated_undigested_file_is_not_readable(tmp_path):
    """SC-006a: after detach, mutating a file outside the include set must not
    leave it both undigested and readable. The read is refused; the digest is
    unchanged. There is no third outcome in which the file is served.
    """
    root = _tree(tmp_path)
    policy = _policy(root)
    before = digest_worktree(policy)

    (root / "script" / "Deploy.sol").write_text("function steal() public {}\n", encoding="utf-8")

    with pytest.raises(ReadOnlyToolError):
        read_file(root / "script" / "Deploy.sol", root, policy=policy)
    assert digest_worktree(policy).digest == before.digest
