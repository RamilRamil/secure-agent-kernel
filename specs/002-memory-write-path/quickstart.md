# Quickstart — kernel-owned `write_memory` + `memory_write` events (feature 002)

How to exercise both halves of this feature locally. No network and no Docker:
everything here is pure-Python kernel code driven against the in-repo
[`FIXTURE_PACK`](../../tests/fixtures/pack/).

## 1. Environment

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
```

`SR_SECRET_KEY` must be set and must be hex (it is parsed with `bytes.fromhex`).
Any 64 hex chars work for tests:

```bash
export SR_SECRET_KEY=00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff
```

> The repo has no `python` on PATH in the usual dev setup — use `.venv/bin/python`
> and `.venv/bin/pytest` explicitly.

## 2. Attach a sink and watch every durable write

The sink is bound at construction, exactly like `privileged_statuses` and the
writer lease. The composition root does this; nothing inside `sr_agent`
constructs an `EpisodicMemory`.

```python
from sr_agent.memory.episodic import EpisodicMemory

trace: list[dict] = []

memory = EpisodicMemory(
    memory_root=tmp_path,
    secret_key=SECRET,
    privileged_statuses=pack.privileged_statuses,   # D5
    lease=lease,                                    # feature 003
    event_sink=trace.append,                        # feature 002, FR-007
)
```

Every successful append now appears in `trace` — model notes, findings, chat
turns, dispatch commits, external responses, pause checkpoints. No writer is
exempt (D9.1). See [contracts/memory-write-event.md](./contracts/memory-write-event.md)
for the full shape and the guarantees.

For a unified operator trace, pass the **same** callable to `OrchestratorLoop`:

```python
loop = OrchestratorLoop(..., memory=memory, event_sink=trace.append)
```

## 3. Prove the sink cannot break a write (FR-008)

```python
def hostile_sink(event):
    raise RuntimeError("observer exploded")

memory = EpisodicMemory(tmp_path, SECRET, event_sink=hostile_sink)
record = memory.write(MemoryRecord(
    project_id="proj1", target="Vault.sol", session_id="sess-1",
    source_type=SourceType.tool_output,
    payload_kind="note", payload={"note": "anything"},
))                                       # succeeds; the exception is swallowed
assert record.hmac is not None
assert memory.load("proj1", "Vault.sol") == [record]
```

Observability is not a transaction. A sink that raises is logged at DEBUG and
otherwise ignored.

## 4. Propose a `write_memory` and read the note back

The model path, end to end. Note that `pack.dispatch` is never called for this id
(FR-001) — the fixture pack raises if it is.

```python
from sr_agent.models.action import Action

result = executor.execute(
    pack, session,
    Action(action_type="write_memory", params={
        "target": "Vault.sol",
        "note": "Vault.withdraw has no reentrancy guard",
    }),
)
assert result.status is DispatchStatus.ran

notes = memory.load(session.principal.project_id, "Vault.sol")
assert notes[-1].source_type is SourceType.llm_inference   # kernel-set, FR-002
assert notes[-1].payload_kind == "model_note"
assert notes[-1].payload["note"].startswith("Vault.withdraw")
```

The note is filed under the target the action names — the same expression that
already builds a `dispatch_commit`'s target (D11). Omit `target` and it falls back
to the action id. `project_id` is never readable from params, so the target cannot
leave the project.

## 5. Prove forged provenance is refused, not ignored (FR-002, FR-004)

```python
for forged in ("source_type", "hmac", "supersedes", "status_change", "session_id"):
    result = executor.execute(
        pack, session,
        Action(action_type="write_memory",
               params={"target": "Vault.sol", "note": "x", forged: "human_input"}),
    )
    assert result.status is DispatchStatus.error        # rejected, not silently stripped

assert all(r.source_type is not SourceType.human_input
           for r in memory.load(project_id, "Vault.sol"))
```

Rejection rather than stripping is deliberate (D12): a stripped attempt is
indistinguishable from an attempt never made, which makes the defence untestable.

## 6. A note never reaches the pack's snapshot (D10)

```python
snap = memory.snapshot(project_id=project_id, session_id=session.session_id)
assert all(item.kind != "model_note" for item in snap.items)
```

Notes are durable, loadable, and observable — they are simply not premises. A
model note is `llm_inference`; feeding it into the pack's projection would let the
model's own prose steer what the agent does next.

## 7. Run the feature's tests

```bash
.venv/bin/python -m pytest tests/unit/test_write_memory_validation.py tests/unit/test_write_memory_path.py tests/unit/test_write_memory_not_a_transition.py tests/unit/test_memory_write_event.py tests/security/test_write_memory_forgery.py -v
```

The gates that must stay green (FR-009, FR-006, D8):

```bash
.venv/bin/python -m pytest tests/security/ tests/architecture/ -q
```

`tests/architecture/test_single_dispatch_path.py` is the one to watch: it fails if
the interception is ever moved out of `KernelActionExecutor` into the loop.
