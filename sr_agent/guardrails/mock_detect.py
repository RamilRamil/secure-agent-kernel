"""Mock / stub detection for PoC tests (T052).

A finding is only as strong as the PoC that proves it. A test that fakes state,
mocks return values, or never actually asserts the exploit is not real evidence.
This pure string-matching guardrail flags such tests and routes the finding to
FindingStatus.mock_review — it never auto-confirms a mocked PoC. No LLM.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sr_agent.models.finding import FindingStatus

# Substring -> reason. Each indicates the test may not prove a real exploit.
MOCK_PATTERNS: dict[str, str] = {
    "vm.mockCall": "mocked_external_call",     # fakes a call's return value
    "vm.store(": "direct_storage_write",        # bypasses contract logic
    "vm.etch(": "code_replacement",             # swaps bytecode under test
    "vm.assume(": "input_overconstraint",       # may hide real reachability
    "not implemented": "unimplemented_stub",    # placeholder PoC body
    "assertTrue(true": "trivial_assertion",     # always-true, proves nothing
    "// TODO": "incomplete_marker",             # acknowledged incomplete
}


@dataclass
class TestQuality:
    is_mock: bool
    flags: list[str] = field(default_factory=list)
    status: FindingStatus = FindingStatus.confirmed


def check_test_realism(test_code: str) -> TestQuality:
    """Flag mock/stub patterns in PoC test code.

    Returns FindingStatus.mock_review if any pattern is present (the finding
    needs a human to confirm the PoC is real), else FindingStatus.confirmed.
    """
    flags = [label for pattern, label in MOCK_PATTERNS.items() if pattern in test_code]
    if flags:
        return TestQuality(is_mock=True, flags=flags, status=FindingStatus.mock_review)
    return TestQuality(is_mock=False, flags=[], status=FindingStatus.confirmed)
