import hashlib
import hmac
import json
from typing import Any


def _canonical(fields: dict[str, Any]) -> bytes:
    """Produce a stable, deterministic byte representation of record fields.

    json.dumps with sort_keys=True ensures that field insertion order
    does not affect the signature — same record always produces same bytes.
    """
    return json.dumps(fields, sort_keys=True, default=str).encode("utf-8")


def sign(fields: dict[str, Any], secret_key: bytes) -> str:
    """Return hex-encoded HMAC-SHA256 over the canonical form of fields."""
    return hmac.new(secret_key, _canonical(fields), hashlib.sha256).hexdigest()


def verify(fields: dict[str, Any], expected_hmac: str, secret_key: bytes) -> bool:
    """Verify HMAC in constant time. Returns False (not raises) on mismatch.

    Uses compare_digest to prevent timing attacks — an attacker observing
    response latency cannot recover the secret key byte-by-byte.
    """
    computed = sign(fields, secret_key)
    return hmac.compare_digest(computed, expected_hmac)


def constant_time_equals(a: str, b: str) -> bool:
    """Constant-time comparison of two hex digests.

    Used for chain links (a record's `chain_prev` against the previous record's
    signature) and for the signed chain head. These compare signature material,
    so they go through compare_digest for the same reason verify() does.
    """
    return hmac.compare_digest(a, b)
