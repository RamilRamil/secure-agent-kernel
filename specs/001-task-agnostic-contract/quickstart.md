# Quickstart — task-agnostic action contract (feature 001)

How to run the four deliverable tests locally. **No network and no Docker** are
needed: every test here is a pure-Python kernel test driven against the in-repo
[`FIXTURE_PACK`](../../tests/fixtures/pack/) (no audit code present).

## 1. Environment

```bash
cd secure-agent-kernel
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
```

`SR_SECRET_KEY` **must be set and must be hex** (it is parsed with
`bytes.fromhex`). Any 64 hex chars work for tests:

```bash
export SR_SECRET_KEY=00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff
```

> A non-hex value fails at import with `ValueError: non-hexadecimal number...`.

## 2. The four pinned tests

| deliverable | what it proves | file |
|-------------|----------------|------|
| **SC-005** (primary positive) | a pack DOMAIN id (`do_thing`) resolves to its `ActionSpec` and the kernel derives OOB-confirm from `action_class`; the kernel-generic ids (`write_memory`, `read_file`) resolve though the pack declares none | `tests/unit/test_open_taxonomy_sc005.py` |
| **SC-009** (chat path) | on `run_turn`, an id in neither `KERNEL_GENERIC_ACTIONS ∪ pack.actions` is fed back as inert `[DATA]`; a `write_execute` domain id pauses for OOB confirmation identically to the batch path | `tests/security/test_chat_mi_scenarios.py` |
| **H4** (hostile) | an empty/reduced `privileged_statuses` gates exactly the declared set — empty ⇒ "nothing privileged", not "gate disabled" | `tests/security/test_hostile_pack.py` |
| **B6** (anti-regression latch) | `action_type` is an open `str`; no domain id/status is operative in `sr_agent/` (AST-checked); the kernel-generic and loop-terminal sets are exact and disjoint; `PackContext` has no `poc_*`/`audit_root` field | `tests/architecture/test_kernel_pack_boundary.py` |

Run just these:

```bash
pytest \
  tests/unit/test_open_taxonomy_sc005.py \
  tests/security/test_chat_mi_scenarios.py \
  tests/security/test_hostile_pack.py \
  tests/architecture/test_kernel_pack_boundary.py \
  -q
```

Or the whole kernel suite (also fast, no network/Docker):

```bash
pytest -q
```

## 3. Prove B6 is a real latch (optional, T017)

B6 must go **red** if a domain id ever re-enters the kernel. To confirm the latch
without leaving cruft, append a probe to a kernel source file, run B6b, then
remove **only that line** (do NOT `git checkout` the file — that discards other
uncommitted work):

```bash
printf '\n_PROBE = "write_poc"\n' >> sr_agent/models/action.py
pytest tests/architecture/test_kernel_pack_boundary.py::test_B6b_no_domain_action_id_in_kernel -q   # expect FAIL
# then delete the appended _PROBE line and re-run — expect PASS
```

## Notes

- These tests never call the paid Claude API: chat/relay/fixture paths are
  offline by construction (Constitution V). `ANTHROPIC_API_KEY` is not required.
- The MI-resistance suite (`tests/security/test_mi_resistance.py`) enforces the
  status gate through a real `EpisodicMemory` write bound to the pack's declared
  privileged set — a "blocked" result means the record was genuinely refused, not
  merely reported blocked.
