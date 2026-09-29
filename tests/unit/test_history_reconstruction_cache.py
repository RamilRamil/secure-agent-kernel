"""Direct coverage for the chat history-reconstruction read seam.

`load_turns` and `render_roadmap` had NO callers and NO tests anywhere in the
repo (`load_session` is exercised by test_session_compat / test_pause_checkpoint
but the other two were not). They are the kernel's public read API for an
out-of-process caller — the audit pack / `sr-agent` CLI in Repo B — and there is
no in-repo composition root that wires them up. These tests pin their behaviour
directly, and pin the specific claim spec/004 makes about them:

    "load() now walks the project directory on every call, and chat_session
     calls it three times per history reconstruction."

The claim's *implied* remedy is the verified-log cache (`_ProjectView`), which
must absorb the 2nd and 3rd of those reads while the writer lease is held.
Nothing measured that before; `test_history_reconstruction_reuses_project_cache
_across_reads` does, and fails if the cache ever stops being consulted.
"""
from pathlib import Path

from sr_agent.memory.episodic import EpisodicMemory
from sr_agent.models.chat import ChatSession, ChatTurn, PoCStatusEvent
from sr_agent.models.principal import Principal
from sr_agent.orchestrator.chat_session import (
    load_session,
    load_turns,
    record_poc_status,
    render_roadmap,
    save_session,
    save_turn,
)
from sr_agent.orchestrator.lease import WriterLease


SECRET = bytes.fromhex("ab" * 32)
PROJECT = "proj1"


def _memory(tmp_path: Path) -> tuple[EpisodicMemory, ChatSession]:
    lease = WriterLease(tmp_path, SECRET)
    memory = EpisodicMemory(tmp_path, SECRET, lease=lease)
    session = ChatSession(
        principal=Principal(user_id="u", platform="cli", project_id=PROJECT)
    )
    lease.acquire(PROJECT, session.session_id)
    return memory, session


# ── load_turns ───────────────────────────────────────────────────────────────


def test_load_turns_returns_turns_in_session_order(tmp_path: Path):
    memory, session = _memory(tmp_path)
    save_session(session, memory)
    save_turn(session, ChatTurn(session_id=session.session_id, user_message="one"), memory)
    save_turn(session, ChatTurn(session_id=session.session_id, user_message="two"), memory)
    save_turn(session, ChatTurn(session_id=session.session_id, user_message="three"), memory)

    turns = load_turns(session.session_id, PROJECT, memory)

    # Order comes from the snapshot's turn_ids, not from unordered memory.load().
    assert [t.user_message for t in turns] == ["one", "two", "three"]
    assert [t.turn_id for t in turns] == session.turn_ids


def test_load_turns_on_unknown_session_is_empty(tmp_path: Path):
    memory, _ = _memory(tmp_path)
    assert load_turns("no-such-session", PROJECT, memory) == []


# ── render_roadmap ─────────────────────────────────────────────────────────────


def test_render_roadmap_shows_latest_status_and_skip_reason(tmp_path: Path):
    memory, session = _memory(tmp_path)
    save_session(session, memory)
    # Two events for the same finding: the later status must win.
    record_poc_status(session, PoCStatusEvent(finding_id="H-1", status="written"), memory)
    record_poc_status(
        session,
        PoCStatusEvent(finding_id="H-1", status="passed", poc_path="poc/h1.t.sol"),
        memory,
    )
    # A skipped finding must always carry its reason into the table.
    record_poc_status(
        session,
        PoCStatusEvent(finding_id="H-2", status="skipped", skip_reason="no harness yet"),
        memory,
    )

    roadmap = render_roadmap(session.session_id, PROJECT, memory)

    assert "| H-1 | passed | poc/h1.t.sol |" in roadmap
    assert "| H-2 | skipped | no harness yet |" in roadmap
    assert "written" not in roadmap  # superseded by the later 'passed'


def test_render_roadmap_with_no_poc_activity(tmp_path: Path):
    memory, session = _memory(tmp_path)
    save_session(session, memory)
    assert render_roadmap(session.session_id, PROJECT, memory) == "No PoC activity recorded yet."


# ── the spec/004 read-cost claim ───────────────────────────────────────────────


def test_history_reconstruction_reuses_project_cache_across_reads(tmp_path: Path):
    """The 2nd and 3rd load() of a reconstruction are served from `_cache`.

    Scenario from spec/004: one active session, one held WriterLease, several
    durable records, then `load_turns` (which calls load() directly AND through
    `load_session`) and `render_roadmap` (a third load()). With the lease held,
    none of those three reads may go back to disk — they must all be answered by
    the same cached `_ProjectView`. If the cache stops being consulted this test
    fails: the read count would climb to three instead of staying at zero.
    """
    memory, session = _memory(tmp_path)
    save_session(session, memory)
    save_turn(session, ChatTurn(session_id=session.session_id, user_message="one"), memory)
    save_turn(session, ChatTurn(session_id=session.session_id, user_message="two"), memory)
    record_poc_status(
        session,
        PoCStatusEvent(finding_id="H-1", status="passed", poc_path="poc/h1.t.sol"),
        memory,
    )

    # The writes above ran under the lease, so the verified-log view is warm.
    assert memory._cache_is_usable(PROJECT)
    view_before = memory._cache[PROJECT]

    # Count every genuine per-file disk read+verify from this point on.
    reads = 0
    original_read = memory._read_authenticated

    def counting_read(path):
        nonlocal reads
        reads += 1
        return original_read(path)

    memory._read_authenticated = counting_read

    turns = load_turns(session.session_id, PROJECT, memory)      # load() x2
    roadmap = render_roadmap(session.session_id, PROJECT, memory)  # load() x1
    reloaded = load_session(session.session_id, PROJECT, memory)   # load() x1

    # Sanity: the reconstruction actually produced the history.
    assert [t.user_message for t in turns] == ["one", "two"]
    assert "H-1" in roadmap and "passed" in roadmap
    assert reloaded is not None and reloaded.turn_ids == session.turn_ids

    # The claim: none of those load()s re-read the store while the lease is held.
    assert reads == 0, (
        f"history reconstruction re-read the store {reads} time(s) despite a held "
        "lease — the _ProjectView cache is no longer absorbing repeat reads"
    )
    # And they were served from the very same view object, not a rebuilt one.
    assert memory._cache[PROJECT] is view_before
