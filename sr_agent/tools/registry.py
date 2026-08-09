from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    action_class: str       # "read_only" | "write_execute" | "memory" | "control"
    description_hash: str   # sha256(description.encode()) — verified at startup


def _hash(description: str) -> str:
    return hashlib.sha256(description.encode("utf-8")).hexdigest()


class ToolTampered(Exception):
    """Raised when a tool description hash does not match the registered value."""


# ── Kernel-generic tool descriptions only ────────────────────────────────────
# The DOMAIN analyzer tools (build_graph, run_slither, run_mythril,
# run_auditor_skill, analyze_transactions, decompile_bytecode, write_poc,
# run_tests, deploy_test_contract) have LEFT the kernel — they are declared by
# the audit capability pack (feature 002) and supplied via `CapabilityPack.tools`.
# What remains here is the generic, task-agnostic surface every pack inherits:
# the scope-bounded reads (`read_file`, `search_code` — decision D6) and the
# control/memory machinery (`write_memory`, `request_human_confirmation`,
# `escalate`). No domain vocabulary.

_D_READ_FILE = (
    "Read the content of a source file within the configured scope. "
    "Returns raw text. Path must be within the configured scope root."
)

_D_SEARCH_CODE = (
    "Search for a pattern across files within the configured scope. "
    "Returns a list of matching locations (file:line). Pattern is a literal string "
    "or regex; the caller supplies the file-extension filter."
)

_D_WRITE_MEMORY = (
    "Write a structured finding or status update to episodic memory. "
    "Content must conform to the MemoryRecord schema. HMAC is added by the orchestrator."
)

_D_REQUEST_HUMAN_CONFIRMATION = (
    "Request human review and confirmation via the out-of-band confirmation channel. "
    "Execution is suspended until the human approves or rejects."
)

_D_ESCALATE = (
    "Escalate to human operator with a structured reason. "
    "Used for irreversible actions, contradicting findings, or unknown patterns."
)


TOOL_REGISTRY: dict[str, ToolDefinition] = {
    t.name: t for t in [
        ToolDefinition("read_file",                  _D_READ_FILE,                  "read_only", _hash(_D_READ_FILE)),
        ToolDefinition("search_code",                _D_SEARCH_CODE,                "read_only", _hash(_D_SEARCH_CODE)),
        ToolDefinition("write_memory",               _D_WRITE_MEMORY,               "memory",    _hash(_D_WRITE_MEMORY)),
        ToolDefinition("request_human_confirmation", _D_REQUEST_HUMAN_CONFIRMATION, "control",   _hash(_D_REQUEST_HUMAN_CONFIRMATION)),
        ToolDefinition("escalate",                   _D_ESCALATE,                   "control",   _hash(_D_ESCALATE)),
    ]
}


def verify_all_hashes() -> None:
    """Called at orchestrator startup. Raises ToolTampered if any description was modified."""
    for name, tool in TOOL_REGISTRY.items():
        computed = _hash(tool.description)
        if computed != tool.description_hash:
            raise ToolTampered(
                f"Tool '{name}' description hash mismatch. "
                f"Expected {tool.description_hash[:16]}…, got {computed[:16]}…"
            )
