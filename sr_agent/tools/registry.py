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


# ── Descriptions are constants. Hash is computed once and hardcoded. ─────────
# To add a tool: write description, compute hash with _hash(description),
# paste the hex string below. Never change description without updating hash.

_D_READ_FILE = (
    "Read the content of a source file within the audit scope. "
    "Returns raw text. Path must be within the configured audit root."
)

_D_SEARCH_CODE = (
    "Search for a pattern across all Solidity files in the audit scope. "
    "Returns a list of matching locations (file:line). Pattern is a literal string or regex."
)

_D_BUILD_GRAPH = (
    "Build a call graph and data-flow graph for the audit scope. "
    "Returns a structured JSON graph. Used by Stage 1 for SIG construction."
)

_D_RUN_SLITHER = (
    "Run Slither static analyser on a target file or directory inside a sandboxed Docker container. "
    "Detectors must be selected from the SlitherDetector enum — no arbitrary detector strings. "
    "Returns structured JSON findings."
)

_D_RUN_MYTHRIL = (
    "Run Mythril symbolic execution on a target contract inside a sandboxed Docker container. "
    "Returns structured JSON findings. May take up to 5 minutes per contract."
)

_D_RUN_AUDITOR_SKILL = (
    "Invoke a named auditor skill (AuditorSkillType enum) on a specific target file. "
    "Skills are pre-defined analysis routines — no arbitrary code execution."
)

_D_ANALYZE_TRANSACTIONS = (
    "Fetch and analyse on-chain transactions for a contract address via Alchemy archive node. "
    "Limit: 10000 blocks per call. Returns structured summary of suspicious patterns."
)

_D_DECOMPILE_BYTECODE = (
    "Decompile EVM bytecode for a given contract address using a local decompiler. "
    "Used when source code is unavailable. Returns decompiled pseudo-Solidity."
)

_D_WRITE_POC = (
    "Write a Proof-of-Concept exploit test in Solidity to tests/poc/. "
    "Requires prior human out-of-band confirmation. Deploys only to local Anvil."
)

_D_RUN_TESTS = (
    "Run Foundry test suite (forge test) for a specific test file inside a sandboxed container. "
    "Requires prior human out-of-band confirmation."
)

_D_DEPLOY_TEST_CONTRACT = (
    "Deploy a contract to local Anvil testnet only. "
    "Cannot deploy to mainnet or any live network. Requires human out-of-band confirmation."
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
    "Escalate to human operator with a structured reason from the EscalationTrigger enum. "
    "Used for irreversible actions, contradicting findings, or unknown patterns."
)


TOOL_REGISTRY: dict[str, ToolDefinition] = {
    t.name: t for t in [
        ToolDefinition("read_file",                  _D_READ_FILE,                  "read_only",     _hash(_D_READ_FILE)),
        ToolDefinition("search_code",                _D_SEARCH_CODE,                "read_only",     _hash(_D_SEARCH_CODE)),
        ToolDefinition("build_graph",                _D_BUILD_GRAPH,                "read_only",     _hash(_D_BUILD_GRAPH)),
        ToolDefinition("run_slither",                _D_RUN_SLITHER,                "read_only",     _hash(_D_RUN_SLITHER)),
        ToolDefinition("run_mythril",                _D_RUN_MYTHRIL,                "read_only",     _hash(_D_RUN_MYTHRIL)),
        ToolDefinition("run_auditor_skill",          _D_RUN_AUDITOR_SKILL,          "read_only",     _hash(_D_RUN_AUDITOR_SKILL)),
        ToolDefinition("analyze_transactions",       _D_ANALYZE_TRANSACTIONS,       "read_only",     _hash(_D_ANALYZE_TRANSACTIONS)),
        ToolDefinition("decompile_bytecode",         _D_DECOMPILE_BYTECODE,         "read_only",     _hash(_D_DECOMPILE_BYTECODE)),
        ToolDefinition("write_poc",                  _D_WRITE_POC,                  "write_execute", _hash(_D_WRITE_POC)),
        ToolDefinition("run_tests",                  _D_RUN_TESTS,                  "write_execute", _hash(_D_RUN_TESTS)),
        ToolDefinition("deploy_test_contract",       _D_DEPLOY_TEST_CONTRACT,       "write_execute", _hash(_D_DEPLOY_TEST_CONTRACT)),
        ToolDefinition("write_memory",               _D_WRITE_MEMORY,               "memory",        _hash(_D_WRITE_MEMORY)),
        ToolDefinition("request_human_confirmation", _D_REQUEST_HUMAN_CONFIRMATION, "control",       _hash(_D_REQUEST_HUMAN_CONFIRMATION)),
        ToolDefinition("escalate",                   _D_ESCALATE,                   "control",       _hash(_D_ESCALATE)),
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
