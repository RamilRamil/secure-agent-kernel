"""Durable scope binding (feature 003, FR-012 / FR-013 / FR-013a, SC-006).

A session that forgets what it was bound to, or that treats git HEAD as the
content, will happily resume against a different tree at the same path. The
kernel therefore stores a canonical `scope_root` plus a worktree digest of the
files it will actually read -- and it never invents `"."` when the root is
missing, because that is how a resume from another cwd silently audits the
wrong project.
"""
from pathlib import Path

import pytest

from sr_agent.models.chat import ChatSession
from sr_agent.models.principal import Principal
from sr_agent.orchestrator.scope import (
    MAX_SCOPE_FILE_BYTES,
    MAX_SCOPE_FILES,
    ContentScopePolicy,
    ScopeBudgetExceeded,
    ScopeUnboundError,
    ScopeVerificationError,
    bind_scope,
    digest_worktree,
    restore_scope_root,
    verify_content_identity,
)


def _session() -> ChatSession:
    return ChatSession(principal=Principal(user_id="u", platform="cli", project_id="proj1"))


def _tree(tmp_path: Path) -> Path:
    (tmp_path / "contracts").mkdir()
    (tmp_path / "contracts" / "Vault.sol").write_text("contract Vault {}", encoding="utf-8")
    (tmp_path / "script").mkdir()
    (tmp_path / "script" / "Deploy.sol").write_text("contract Deploy {}", encoding="utf-8")
    return tmp_path


def _policy(root: Path, include=("contracts/**",), runtime=()) -> ContentScopePolicy:
    return ContentScopePolicy(
        scope_root=root,
        include=include,
        runtime_state_roots=tuple(Path(p) for p in runtime),
    )


# ── Bind / restore ──────────────────────────────────────────────────────────


def test_bind_stores_canonical_root_and_digest(tmp_path):
    root = _tree(tmp_path).resolve()
    session = bind_scope(_session(), _policy(root))
    assert session.scope_root == str(root)
    assert session.scope_generation == 1
    assert session.content_identity is not None
    assert session.content_identity.digest
    assert session.content_identity.file_count == 1   # only contracts/


def test_restore_returns_the_bound_root_not_cwd(tmp_path, monkeypatch):
    root = _tree(tmp_path).resolve()
    session = bind_scope(_session(), _policy(root))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert restore_scope_root(session) == root


def test_missing_scope_root_is_refused_not_defaulted_to_dot():
    """The Path('.') defect: a resume with no recorded root must not invent one."""
    with pytest.raises(ScopeUnboundError):
        restore_scope_root(_session())


def test_missing_path_fails_closed(tmp_path):
    root = _tree(tmp_path).resolve()
    session = bind_scope(_session(), _policy(root))
    session.scope_root = str(root / "gone")
    with pytest.raises(ScopeVerificationError):
        verify_content_identity(session)


def test_digest_mismatch_fails_closed(tmp_path):
    root = _tree(tmp_path).resolve()
    session = bind_scope(_session(), _policy(root))
    (root / "contracts" / "Vault.sol").write_text("contract Mutated {}", encoding="utf-8")
    with pytest.raises(ScopeVerificationError):
        verify_content_identity(session)


def test_dirty_file_fails_even_when_git_head_is_unchanged(tmp_path):
    """Git HEAD is metadata, not identity (D10). A dirty include-set file must
    fail resume, because HEAD-only identity is exactly the hole that lets an
    operator (or an attacker) change what will be read without changing the id.
    """
    root = _tree(tmp_path).resolve()
    git = root / ".git"
    git.mkdir()
    (git / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    refs = git / "refs" / "heads"
    refs.mkdir(parents=True)
    (refs / "main").write_text("deadbeef" * 5 + "\n", encoding="utf-8")

    session = bind_scope(_session(), _policy(root))
    assert session.content_identity.git_head_sha is not None
    head_before = session.content_identity.git_head_sha

    (root / "contracts" / "Vault.sol").write_text("contract Dirty {}", encoding="utf-8")
    with pytest.raises(ScopeVerificationError):
        verify_content_identity(session)
    assert session.content_identity.git_head_sha == head_before


# ── Budget ──────────────────────────────────────────────────────────────────


def test_over_file_count_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr("sr_agent.orchestrator.scope.MAX_SCOPE_FILES", 2)
    root = tmp_path
    (root / "contracts").mkdir()
    for i in range(3):
        (root / "contracts" / f"C{i}.sol").write_text("x", encoding="utf-8")
    with pytest.raises(ScopeBudgetExceeded) as excinfo:
        digest_worktree(_policy(root, include=("contracts/**",)))
    assert "2" in str(excinfo.value) and "3" in str(excinfo.value)


def test_oversize_single_file_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr("sr_agent.orchestrator.scope.MAX_SCOPE_FILE_BYTES", 8)
    root = tmp_path
    (root / "contracts").mkdir()
    (root / "contracts" / "Vault.sol").write_text("too-big-for-the-limit", encoding="utf-8")
    with pytest.raises(ScopeBudgetExceeded):
        digest_worktree(_policy(root))


def test_budget_constants_are_the_fixed_values():
    from sr_agent.orchestrator.scope import MAX_SCOPE_BYTES

    assert (MAX_SCOPE_FILES, MAX_SCOPE_FILE_BYTES, MAX_SCOPE_BYTES) == (
        10000, 8 * 1024 * 1024, 100 * 1024 * 1024,
    )
