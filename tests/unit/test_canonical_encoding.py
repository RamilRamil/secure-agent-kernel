"""Canonical encoding golden vector (feature 003, FR-005a / SC-016).

`transition_key` and `operation_id` are derived from validated action params, and
the derivation has to survive a process restart byte-for-byte (D22): a relay request
created before a crash is only idempotent if the id is recomputed identically
afterwards. "Sorted JSON" is not a specification -- it says nothing about Unicode
normalization, separators, absent-vs-null, or int-vs-float rendering. This module
pins all of it against literals and re-runs the derivation in a *fresh interpreter*,
because an in-process assertion cannot catch a value that depends on interpreter
state (hash seed, locale, a module-level cache).
"""
import json
import subprocess
import sys
import unicodedata
from pathlib import Path

import pytest

from sr_agent.memory.canonical import (
    PROTOCOL_UUID_NAMESPACE,
    canonical_bytes,
    derive_operation_id,
    derive_transition_key,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

# One fixed transition, reused by every assertion below and by the subprocess.
VECTOR = {
    "session_id": "sess-1",
    "action_id": "run_slither",
    "params": {"target": "contracts/Vault.sol", "analyzer_version": "0.10.0"},
    "chunk_id": "chunk-0",
    "expected_revision": 3,
    "scope_generation": 1,
}


def _key_and_id(vector: dict) -> tuple[str, str]:
    key = derive_transition_key(
        session_id=vector["session_id"],
        action_id=vector["action_id"],
        params=vector["params"],
        chunk_id=vector["chunk_id"],
        expected_revision=vector["expected_revision"],
        scope_generation=vector["scope_generation"],
    )
    return key, str(derive_operation_id(key))


# ── Encoding shape ──────────────────────────────────────────────────────────


def test_namespace_is_a_source_literal():
    """The UUIDv5 namespace is versioned with the protocol, never derived at runtime."""
    assert str(PROTOCOL_UUID_NAMESPACE) == "6f9619ff-8b86-d011-b42d-00c04fc964ff"


def test_no_spaces_in_separators():
    encoded = canonical_bytes({"a": 1, "b": "x"}).decode("utf-8")
    assert encoded == '{"__enc__":1,"v":{"a":1,"b":"x"}}'


def test_keys_sorted_by_code_point():
    encoded = canonical_bytes({"b": 1, "A": 2, "a": 3}).decode("utf-8")
    # Code-point order is "A" < "a" < "b"; a locale-aware sort would disagree.
    assert encoded.index('"A"') < encoded.index('"a"') < encoded.index('"b"')


def test_nfc_normalization_of_keys_and_values():
    """NFC/NFD spellings of the same text must not produce two different digests."""
    nfc = unicodedata.normalize("NFC", "café")
    nfd = unicodedata.normalize("NFD", "café")
    assert nfc != nfd  # different code points, same text
    assert canonical_bytes({nfc: nfc}) == canonical_bytes({nfd: nfd})


def test_absent_and_explicit_null_are_distinguished():
    """An omitted optional key and an explicit null are different inputs."""
    assert canonical_bytes({"a": 1}) != canonical_bytes({"a": 1, "b": None})


def test_int_is_not_rendered_as_float():
    assert canonical_bytes({"n": 1}) != canonical_bytes({"n": 1.0})


def test_non_finite_floats_are_rejected():
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError):
            canonical_bytes({"n": bad})


def test_encoding_version_is_inside_the_digest_input():
    """A future encoding change must not be able to collide with today's bytes."""
    encoded = canonical_bytes({"a": 1}).decode("utf-8")
    assert '"__enc__"' in encoded


# ── Golden vector ───────────────────────────────────────────────────────────


def test_golden_vector_literals():
    key, operation_id = _key_and_id(VECTOR)
    assert key == (
        "ef09c7312e64e33e8fa297bab3ef547b13230ec84818d9d5eb5f03a8ac2ea9a6"
    )
    assert operation_id == "d3229ffb-bb60-505d-81bb-96d6591baa47"


def test_golden_vector_survives_a_fresh_interpreter():
    """Recomputed in a new process: same bytes, same key, same operation_id (D22).

    A crash between creating a relay request and writing the checkpoint leaves the
    id nowhere durable, so the *only* thing that prevents a duplicate request is
    that a cold process derives it again identically.
    """
    script = (
        "import json,sys;"
        "from sr_agent.memory.canonical import derive_transition_key,derive_operation_id;"
        "v=json.loads(sys.argv[1]);"
        "k=derive_transition_key(session_id=v['session_id'],action_id=v['action_id'],"
        "params=v['params'],chunk_id=v['chunk_id'],"
        "expected_revision=v['expected_revision'],scope_generation=v['scope_generation']);"
        "print(json.dumps([k,str(derive_operation_id(k))]))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script, json.dumps(VECTOR)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(proc.stdout) == list(_key_and_id(VECTOR))
