"""The `audit_root` → `scope_root` transitional shim is REMOVED (PR-3 / T027).

The rename landed in kernel PR-1 with a `__getattr__` shim that delegated
`ctx.audit_root` → `scope_root` with a DeprecationWarning, keeping a pack pinned
to the pre-rename kernel working until araratsec PR-2 migrated its call-sites.
araratsec PR-2 is merged (no `ctx.audit_root` remains), so PR-3 removed the shim.

These tests are the anti-regression latch for that removal: `audit_root` is now
just an unknown attribute and must raise `AttributeError` like any other — no
lingering delegation, no accidental catch-all.
"""
from pathlib import Path

import pytest

from sr_agent.orchestrator.pack import PackContext


def _ctx(tmp_path: Path) -> PackContext:
    return PackContext(scope_root=tmp_path, sandbox=object(), wrap_data=lambda *a, **k: "")


def test_scope_root_is_the_field(tmp_path):
    ctx = _ctx(tmp_path)
    assert ctx.scope_root == tmp_path


def test_audit_root_no_longer_resolves(tmp_path):
    """The deprecation window is closed: the old name raises, it does not delegate."""
    ctx = _ctx(tmp_path)
    with pytest.raises(AttributeError):
        _ = ctx.audit_root


def test_unknown_attribute_raises(tmp_path):
    ctx = _ctx(tmp_path)
    with pytest.raises(AttributeError):
        _ = ctx.not_a_real_field
