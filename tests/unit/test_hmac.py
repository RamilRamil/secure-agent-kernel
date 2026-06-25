import pytest

from sr_agent.memory.hmac import sign, verify

SECRET = b"test-secret-key-32-bytes-exactly!"
OTHER_SECRET = b"different-secret-key-32-bytes!!"


def test_sign_verify_roundtrip():
    fields = {"record_id": "abc", "project_id": "proj1", "finding": {"severity": "high"}}
    sig = sign(fields, SECRET)
    assert verify(fields, sig, SECRET)


def test_tampered_field_fails():
    fields = {"record_id": "abc", "project_id": "proj1"}
    sig = sign(fields, SECRET)

    tampered = {**fields, "project_id": "evil-project"}
    assert not verify(tampered, sig, SECRET)


def test_different_secret_fails():
    fields = {"record_id": "abc", "project_id": "proj1"}
    sig = sign(fields, SECRET)
    assert not verify(fields, sig, OTHER_SECRET)


def test_field_order_invariant():
    """Signature must not depend on dict insertion order."""
    fields_a = {"a": 1, "b": 2}
    fields_b = {"b": 2, "a": 1}
    assert sign(fields_a, SECRET) == sign(fields_b, SECRET)


def test_empty_fields():
    sig = sign({}, SECRET)
    assert verify({}, sig, SECRET)
