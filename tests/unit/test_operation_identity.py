"""Deterministic operation identity (feature 003, FR-005 / D22 / SC-012).

`operation_id` used to be a fresh UUID per attempt. That cannot make an effect
idempotent: if the process dies after the relay request is created and before the
checkpoint is written, the id exists nowhere durable, so a restart mints a new one
and asks the same question twice. Deriving it from `transition_key` removes the
window entirely -- there is nothing to persist because the id is a function of
inputs the kernel already has.
"""
import json
import subprocess
import sys
from pathlib import Path

from sr_agent.memory.canonical import derive_operation_id, derive_transition_key

REPO_ROOT = Path(__file__).resolve().parents[2]

BASE = {
    "session_id": "sess-1",
    "action_id": "run_slither",
    "params": {"target": "contracts/Vault.sol"},
    "chunk_id": "chunk-0",
    "expected_revision": 3,
    "scope_generation": 1,
}


def _derive(**overrides) -> str:
    args = {**BASE, **overrides}
    return str(derive_operation_id(derive_transition_key(**args)))


def test_same_inputs_same_id():
    assert _derive() == _derive()


def test_id_is_a_uuid5_of_the_transition_key():
    key = derive_transition_key(**BASE)
    assert derive_operation_id(key).version == 5


def test_every_identity_input_changes_the_id():
    """Each input is load-bearing: dropping one from the key would alias transitions."""
    baseline = _derive()
    assert _derive(session_id="sess-2") != baseline
    assert _derive(action_id="run_mythril") != baseline
    assert _derive(params={"target": "contracts/Token.sol"}) != baseline
    assert _derive(chunk_id="chunk-1") != baseline
    assert _derive(expected_revision=4) != baseline
    # scope_generation is why a rebind cannot reuse a pre-rebind effect identity (D27).
    assert _derive(scope_generation=2) != baseline


def test_no_randomness_across_processes():
    """A cold process derives the same id -- the property SC-012 rests on."""
    script = (
        "import json,sys;"
        "from sr_agent.memory.canonical import derive_transition_key,derive_operation_id;"
        "v=json.loads(sys.argv[1]);"
        "print(str(derive_operation_id(derive_transition_key(**v))))"
    )
    ids = set()
    for _ in range(2):
        proc = subprocess.run(
            [sys.executable, "-c", script, json.dumps(BASE)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        ids.add(proc.stdout.strip())
    assert ids == {_derive()}
