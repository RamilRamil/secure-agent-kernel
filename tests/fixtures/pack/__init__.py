"""In-repo kernel fixture pack (feature 048, T017).

Exposes ``FIXTURE_PACK`` — a minimal, kernel-only ``CapabilityPack`` the kernel
MI tests run against instead of the real ``AUDIT_PACK``, so those guarantees can
be proven in Repo A with no audit code present (FR-005, SC-007).
"""
from __future__ import annotations

from tests.fixtures.pack.fixture_pack import (
    DO_THING,
    FIXTURE_PACK,
    FIXTURE_PRIVILEGED_STATUSES,
    FixtureFinding,
    FixtureSession,
)

__all__ = [
    "DO_THING",
    "FIXTURE_PACK",
    "FIXTURE_PRIVILEGED_STATUSES",
    "FixtureFinding",
    "FixtureSession",
]
