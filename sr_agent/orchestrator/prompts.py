"""Trusted system-prompt registry (feature 003, FR-020 / D18).

Kernel-owned as a mechanism: resume and `run_turn` load instruction bytes only
from here. Pack-owned as content: the pack registers its own prompt text, so no
audit-domain body lives in kernel source (Constitution III).

This is not episodic memory. Entries are never wrapped as prior-turn DATA and
never read back from a `pause_checkpoint`. The checkpoint stores only
`system_prompt_id` / `system_prompt_hash` as a reference; an attacker-controlled
hash is a mismatch, not an instruction.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass


class PromptRegistryError(Exception):
    """Missing id/version or a hash that does not match the registered body."""


def prompt_digest(body: str) -> str:
    """SHA-256 hex of the UTF-8 prompt body. The checkpoint stores this, not the body."""
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PromptEntry:
    prompt_id: str
    version: str
    body: str
    digest: str


class PromptRegistry:
    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], PromptEntry] = {}

    def register(self, prompt_id: str, body: str, version: str = "1") -> PromptEntry:
        if not prompt_id or not body:
            raise PromptRegistryError("prompt_id and body are required")
        entry = PromptEntry(
            prompt_id=prompt_id,
            version=version,
            body=body,
            digest=prompt_digest(body),
        )
        self._entries[(prompt_id, version)] = entry
        return entry

    def get(self, prompt_id: str, version: str | None = None) -> PromptEntry:
        if version is None:
            matches = [e for (pid, _ver), e in self._entries.items() if pid == prompt_id]
            if not matches:
                raise PromptRegistryError(f"unknown system_prompt_id {prompt_id!r}")
            if len(matches) > 1:
                raise PromptRegistryError(
                    f"system_prompt_id {prompt_id!r} is ambiguous without version"
                )
            return matches[0]
        entry = self._entries.get((prompt_id, version))
        if entry is None:
            raise PromptRegistryError(
                f"unknown system_prompt_id {prompt_id!r} version {version!r}"
            )
        return entry

    def load(
        self,
        prompt_id: str,
        expected_hash: str,
        version: str | None = None,
    ) -> str:
        """Return registered bytes. Never returns `expected_hash` as the body.

        A checkpoint hash is a reference check. Attacker-controlled DATA in that
        field fails closed (mismatch or missing id) and is not executed.
        """
        entry = self.get(prompt_id, version)
        if entry.digest != expected_hash:
            raise PromptRegistryError(
                "system_prompt_hash does not match the registry; "
                "the checkpoint hash is not used as the instruction"
            )
        return entry.body
