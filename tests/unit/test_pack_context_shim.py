"""Transitional `audit_root` → `scope_root` shim on PackContext (US4 / D3).

The rename lands in kernel PR-1, but a pack pinned to the pre-rename kernel still
reads `ctx.audit_root`. The `__getattr__` shim delegates to `scope_root` with a
DeprecationWarning until araratsec PR-2 migrates the call-sites; PR-3 (T027)
removes the shim. These tests pin the shim's behavior so its removal is a
deliberate, visible change — and so a regression that drops the delegation is
caught before it breaks a downstream pack.
"""
import warnings
from pathlib import Path

import pytest

from sr_agent.orchestrator.pack import PackContext


def _ctx(tmp_path: Path) -> PackContext:
    return PackContext(scope_root=tmp_path, sandbox=object(), wrap_data=lambda *a, **k: "")


def test_audit_root_delegates_to_scope_root(tmp_path):
    ctx = _ctx(tmp_path)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        assert ctx.audit_root == ctx.scope_root == tmp_path


def test_audit_root_access_warns_deprecation(tmp_path):
    ctx = _ctx(tmp_path)
    with pytest.warns(DeprecationWarning, match="audit_root is deprecated"):
        _ = ctx.audit_root


def test_unknown_attribute_still_raises(tmp_path):
    """The shim answers ONLY `audit_root`; every other missing attr raises
    AttributeError (no accidental catch-all, no recursion via scope_root)."""
    ctx = _ctx(tmp_path)
    with pytest.raises(AttributeError):
        _ = ctx.not_a_real_field
