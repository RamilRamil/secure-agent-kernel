"""Mock/stub PoC detection tests (T052)."""
from sr_agent.guardrails.mock_detect import check_test_realism
from sr_agent.models.finding import FindingStatus

_REAL_POC = """
function test_exploit() public {
    vault.deposit{value: 1 ether}();
    Attacker a = new Attacker(vault);
    a.attack();
    assertGt(address(a).balance, 1 ether);
}
"""


def test_real_poc_is_confirmed():
    q = check_test_realism(_REAL_POC)
    assert not q.is_mock
    assert q.flags == []
    assert q.status is FindingStatus.confirmed


def test_mockcall_flagged():
    q = check_test_realism("vm.mockCall(oracle, abi.encode(price));")
    assert q.is_mock
    assert "mocked_external_call" in q.flags
    assert q.status is FindingStatus.mock_review


def test_unimplemented_stub_flagged():
    q = check_test_realism('revert("PoC not implemented");')
    assert "unimplemented_stub" in q.flags
    assert q.status is FindingStatus.mock_review


def test_trivial_assertion_flagged():
    q = check_test_realism("assertTrue(true);")
    assert "trivial_assertion" in q.flags


def test_todo_marker_flagged():
    q = check_test_realism("// TODO: finish the exploit path")
    assert "incomplete_marker" in q.flags


def test_multiple_patterns_collected():
    code = "vm.store(addr, slot, val); vm.assume(x > 0); // TODO"
    q = check_test_realism(code)
    assert {"direct_storage_write", "input_overconstraint", "incomplete_marker"} <= set(q.flags)
    assert q.status is FindingStatus.mock_review
