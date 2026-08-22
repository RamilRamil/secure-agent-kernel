"""The provenance fields must not weaken the store (kernel/005, FR-015, D005-3).

`fields_for_hmac()` is `model_dump(exclude={"hmac"})`. Adding fields to
`MemoryRecord` therefore changes the signed shape of EVERY record, including the
ones already on disk: they would dump with the new keys set to `None`, fail
verification, and be silently dropped. The whole store would read as empty.

kernel/004 accepted exactly that for its own shape change. Repeating it here
would also destroy the case the field design depends on — a record written before
this feature is `unknown`, and there would be no such records left to read.

So the fields are excluded from the signed dict when unset, and these tests are
the proof that the exclusion is real and that it does not open a door.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sr_agent.memory import hmac as hmac_module
from sr_agent.memory.episodic import EpisodicMemory
from sr_agent.models.dispatch import DispatchStatus
from sr_agent.models.memory import MemoryRecord, SourceType
from sr_agent.models.principal import Principal

SECRET = bytes.fromhex("ab" * 32)
PROJECT = "proj1"
#: Written with the pre-005 code and committed. It cannot be regenerated after
#: this feature lands, which is the whole point of it being a fixture.
PRE_005 = Path(__file__).parent.parent / "fixtures" / "pre_005_store"


def _principal() -> Principal:
    return Principal(user_id="u", platform="cli", project_id=PROJECT)


# ── T003: a store written before this feature still verifies ─────────────────


def test_a_pre_005_store_still_loads(tmp_path: Path) -> None:
    import shutil
    root = tmp_path / "store"
    shutil.copytree(PRE_005, root)

    records = EpisodicMemory(root, SECRET).load_for_principal(_principal())

    assert len(records) == 3, "the pre-005 store did not survive the shape change"
    assert {r.finding["finding_id"] for r in records if r.finding} == {"F-1", "F-2"}


def test_pre_005_records_read_as_unknown_not_as_unresolved(tmp_path: Path) -> None:
    """Absence is not a claim. A record written before this feature says nothing
    about its turn, and reading it as "no action resolved" would invent an
    observation the kernel never made (D005-7)."""
    import shutil
    root = tmp_path / "store"
    shutil.copytree(PRE_005, root)

    for r in EpisodicMemory(root, SECRET).load_for_principal(_principal()):
        assert r.action_resolution is None
        assert r.action_operation_id is None
        assert r.action_dispatch_status is None


# ── T010: the direction of the only reachable manipulation ───────────────────


def _finding(**kw) -> MemoryRecord:
    base = dict(
        project_id=PROJECT, target="Vault.sol",
        source_type=SourceType.external_llm_output, session_id="sess-1",
        finding={"finding_id": "F-1", "location": "Vault.sol:10"},
    )
    base.update(kw)
    return MemoryRecord(**base)


def _lines(root: Path) -> list[dict]:
    path = root / PROJECT / "Vault.sol.jsonl"
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def _rewrite(root: Path, rows: list[dict]) -> None:
    path = root / PROJECT / "Vault.sol.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_stripping_the_fields_from_a_stamped_record_does_not_verify(tmp_path: Path) -> None:
    """The exclusion is conditional on the field being UNSET at signing time.

    An earlier draft of this feature reasoned that an attacker could therefore
    strip the fields from a stored line and leave a record that still verified,
    reading as `unknown` — a downgrade, harmless because both states are
    non-proof-eligible. That reasoning was wrong, and this test is where it was
    caught: a record signed WITH the fields has them inside the HMAC, so removing
    them breaks the signature and the record is dropped silently, exactly like
    any other tamper.

    The real property is the stronger one. Unset fields are outside the signed
    dict, which is what keeps pre-005 stores verifying; set fields are signed
    like every other field. There is no downgrade path, only deletion — and
    deletion is what kernel/004's chain exists to detect.
    """
    memory = EpisodicMemory(tmp_path, SECRET)
    memory.write(_finding(
        action_resolution="resolved", action_operation_id="OP-1",
        action_dispatch_status=DispatchStatus.ran,
    ))

    rows = _lines(tmp_path)
    for key in ("action_resolution", "action_operation_id", "action_dispatch_status"):
        rows[0].pop(key)
    _rewrite(tmp_path, rows)

    assert EpisodicMemory(tmp_path, SECRET).load_for_principal(_principal()) == []


def test_the_removal_is_visible_to_the_composition_chain(tmp_path: Path) -> None:
    """Dropping the record is not the end of the story: the signed chain head
    still attests that a record was there, so the tamper surfaces to the operator
    rather than passing as a store that simply never held it (kernel/004 FR-002).

    Without this assertion the test above would read as "tampering makes evidence
    quietly disappear", which is the opposite of what the store guarantees.
    """
    memory = EpisodicMemory(tmp_path, SECRET)
    memory.write(_finding(
        action_resolution="resolved", action_operation_id="OP-1",
        action_dispatch_status=DispatchStatus.ran,
    ))
    rows = _lines(tmp_path)
    rows[0].pop("action_dispatch_status")
    _rewrite(tmp_path, rows)

    report = EpisodicMemory(tmp_path, SECRET).verify_integrity(PROJECT)
    assert report.chain_breaks


@pytest.mark.parametrize("forged", [
    {"action_resolution": "resolved", "action_operation_id": "OP-1",
     "action_dispatch_status": "ran"},
    {"action_resolution": "resolved", "action_operation_id": "OP-1",
     "action_dispatch_status": "ran", "resolves_record_id": "rec-x"},
], ids=["plain", "with-back-reference"])
def test_forging_a_resolved_stamp_does_not_verify(forged: dict, tmp_path: Path) -> None:
    """The move that would matter — turning a hypothesis into a proof-eligible
    record — requires signing the enlarged dict, and is not available without the
    key. Silent drop, per Constitution I: no warning, no oracle."""
    memory = EpisodicMemory(tmp_path, SECRET)
    memory.write(_finding())                       # written with NO provenance

    rows = _lines(tmp_path)
    rows[0].update(forged)
    _rewrite(tmp_path, rows)

    assert EpisodicMemory(tmp_path, SECRET).load_for_principal(_principal()) == []


def test_the_signed_shape_of_a_bare_record_is_unchanged(tmp_path: Path) -> None:
    """The mechanism behind T003, asserted directly so a regression is located
    here rather than in a fixture that stops loading for some other reason."""
    bare = _finding()
    signed = bare.fields_for_hmac()
    for field in ("action_resolution", "action_operation_id",
                  "action_dispatch_status", "resolves_record_id"):
        assert field not in signed

    stamped = _finding(action_resolution="unresolved")
    assert "action_resolution" in stamped.fields_for_hmac()
    assert "action_operation_id" not in stamped.fields_for_hmac()


def test_a_stamped_record_is_tamper_evident(tmp_path: Path) -> None:
    """Once set, the fields ARE signed: flipping `unresolved` to `resolved` on a
    stored line breaks the signature."""
    memory = EpisodicMemory(tmp_path, SECRET)
    memory.write(_finding(action_resolution="unresolved"))

    rows = _lines(tmp_path)
    rows[0]["action_resolution"] = "resolved"
    _rewrite(tmp_path, rows)

    assert EpisodicMemory(tmp_path, SECRET).load_for_principal(_principal()) == []


# ── T024: a forged pending record cannot attract a resolution ────────────────


def test_an_unverifiable_pending_finding_gets_no_resolution_record(tmp_path: Path) -> None:
    """The resume-side lookup goes through the normal verified read path.

    A record written under the wrong key is not *rejected* by the lookup — it is
    not there at all, because verification drops it silently (Constitution I: no
    warning, no oracle). So an attacker who can append to the file cannot plant a
    `pending` finding and have the kernel file a `resolved` outcome onto it,
    which would be a signed record vouching for something the kernel never saw.

    Asserting on the count alone would pass against a hook that had stopped
    working, so an honest pending record is planted alongside and must still be
    resolved.
    """
    import os
    os.environ.setdefault("SR_SECRET_KEY", "00" * 32)
    from sr_agent.llm_core.schemas import AgentAction, FindingPayload
    from sr_agent.models.dispatch import DispatchResult
    from sr_agent.orchestrator.loop import FindingProvenance, OrchestratorLoop
    from tests.unit.test_finding_provenance_paths import _pack, _paused, _StubReasoning
    from sr_agent.models.chat import ChatSession
    from sr_agent.orchestrator.executor import KernelActionExecutor
    from sr_agent.orchestrator.lease import WriterLease

    session = ChatSession(
        principal=Principal(user_id="u", platform="cli", project_id=PROJECT),
        scope_root=str(tmp_path), include=["*"],
    )
    lease = WriterLease(tmp_path, SECRET)
    lease.acquire(PROJECT, session.session_id)
    memory = EpisodicMemory(tmp_path, SECRET, lease=lease)
    loop = OrchestratorLoop(
        session, memory, tmp_path, pack=_pack(_paused),
        reasoning_provider=_StubReasoning([
            AgentAction(next_action="do_thing", tool_params={},
                        finding=FindingPayload(finding_id="F-1", location="Vault.sol:42",
                                               function_name="withdraw", severity="high")),
        ]),
        confirmations_dir=tmp_path / "conf",
    )
    loop._executor = KernelActionExecutor(
        memory=memory, scope_root=tmp_path, pack_id="fixture",
        pack_contract_version="1", confirmations_dir=tmp_path / "conf",
        relay_dir=tmp_path / "relay",
    )
    loop.run_turn(user_message="look", system_prompt="p")

    honest = _lines(tmp_path)[0]
    operation_id = honest["action_operation_id"]

    forged = dict(honest)
    forged["record_id"] = "forged-1"
    forged["finding"] = {"finding_id": "F-EVIL", "location": "Vault.sol:1"}
    forged["hmac"] = "00" * 32
    _rewrite(tmp_path, _lines(tmp_path) + [forged])

    loop._resolve_paused_findings(DispatchResult(status=DispatchStatus.ran, body="done"))

    survivors = EpisodicMemory(tmp_path, SECRET).load_for_principal(_principal())
    resolutions = [r for r in survivors if r.resolves_record_id]
    assert len(resolutions) == 1, "the forged pending record attracted a resolution"
    assert resolutions[0].finding["finding_id"] == "F-1"
