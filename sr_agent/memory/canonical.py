"""Canonical encoding and deterministic operation identity (feature 003).

Two ids key the whole durable path: `transition_key` (what transition is this) and
`operation_id` (what effect does it authorize). Both are *derived*, never minted, so
that a process which crashes mid-effect recomputes the same id on restart instead of
issuing a second request against the outside world (FR-005, D22).

Derivation is only as stable as its encoder, so the encoder is pinned here rather
than left to `json.dumps` defaults. `sr_agent/memory/hmac.py` has its own looser
`_canonical` for record signatures; it is deliberately not reused. That one accepts
`default=str`, which would silently stringify an unexpected object and make two
different params digest alike -- acceptable for signing bytes the kernel just built,
not acceptable for deciding whether an effect already happened.
"""
import hashlib
import json
import math
import unicodedata
import uuid
from typing import Any

# Bumped only when the rules below change. It is inside the digest input so that a
# future encoding can never produce bytes that collide with today's.
ENCODING_VERSION = 1

# A constant, not a runtime-derived value: a namespace computed from anything
# environmental would make ids differ between machines, which is the exact failure
# the derivation exists to prevent.
PROTOCOL_UUID_NAMESPACE = uuid.UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")

_ABSENT = object()


def _normalize(value: Any) -> Any:
    """Recursively apply the encoding rules, rejecting anything unrepresentable."""
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"non-finite float is not canonically encodable: {value!r}")
        return value
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"non-string key is not canonically encodable: {key!r}")
            normalized_key = unicodedata.normalize("NFC", key)
            if normalized_key in normalized:
                raise ValueError(f"keys collide after NFC normalization: {key!r}")
            normalized[normalized_key] = _normalize(item)
        return {k: normalized[k] for k in sorted(normalized)}
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    raise ValueError(f"type is not canonically encodable: {type(value).__name__}")


def canonical_bytes(value: Any) -> bytes:
    """Encode a value to the one byte sequence this protocol considers canonical.

    UTF-8, NFC-normalized strings and keys, keys sorted by code point, separators
    without spaces, ints never rendered as floats, non-finite floats rejected, and an
    absent key distinguished from an explicit null (nothing is filled in).
    """
    payload = {"__enc__": ENCODING_VERSION, "v": _normalize(value)}
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=False,
    ).encode("utf-8")


def canonical_digest(value: Any) -> str:
    """SHA-256 over `canonical_bytes`, hex-encoded."""
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def derive_transition_key(
    *,
    session_id: str,
    action_id: str,
    params: dict[str, Any],
    chunk_id: str | None = None,
    expected_revision: int | None = None,
    scope_generation: int = 0,
) -> str:
    """Identify one transition of one session.

    `scope_generation` is part of the identity so that rebinding the scope cannot
    resurrect an effect authorized against the previous scope (D27), and
    `expected_revision` is included so that the same action retried against a moved
    session is a different transition rather than a silent overwrite.
    """
    return canonical_digest(
        {
            "session_id": session_id,
            "action_id": action_id,
            "params_digest": canonical_digest(params),
            "chunk_id": chunk_id,
            "expected_revision": expected_revision,
            "scope_generation": scope_generation,
        }
    )


def derive_operation_id(transition_key: str) -> uuid.UUID:
    """UUIDv5 of the transition key under the protocol namespace."""
    return uuid.uuid5(PROTOCOL_UUID_NAMESPACE, transition_key)
