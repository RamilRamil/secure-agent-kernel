from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sr_agent.models.session import Session

# Per-model context window limits (in tokens).
# Stage 2 (Qwen3-4B) gets one function at a time — its 32K window is sufficient.
# Stage 1/3 (Claude Opus) can hold the full SIG + all findings.
CONTEXT_LIMITS: dict[str, int] = {
    "claude-opus-4-8": 180_000,   # leave headroom for thinking + response
    "sr-stage2": 28_000,           # Qwen3-4B fine-tuned — one function per call
    "qwen3:4b": 28_000,
    "qwen3-coder": 28_000,
}

# Sentinel markers that wrap all external data before it enters LLM context.
# The wrapping makes it visually and structurally clear to the model (and to us)
# what is trusted orchestrator instruction vs untrusted external data.
_DATA_START = "[DATA START tool={tool} path={path} flags={flags}]"
_DATA_END = "[DATA END]"


def wrap_data(content: str, tool: str, path: str, flags: list[str] | None = None) -> str:
    """Wrap external data in sentinel markers before including in LLM context.

    Every tool output, memory record, and file read must pass through here.
    The flags field carries sanitization warnings (e.g. 'homoglyph_detected').
    """
    flags_str = ",".join(flags) if flags else ""
    header = _DATA_START.format(tool=tool, path=path, flags=flags_str)
    return f"{header}\n{content}\n{_DATA_END}"


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token for English/code."""
    return len(text) // 4


def build_messages(
    session: "Session",
    system_prompt: str,
    tool_output: str | None = None,
    knowledge_chunks: list[str] | None = None,
    session_facts: str | None = None,
    model: str = "claude-opus-4-8",
) -> list[dict]:
    """Assemble the messages list for an LLM call.

    Priority order for truncation (lowest priority dropped first):
    1. knowledge_chunks (background context)
    2. older tool outputs
    3. session_facts (chat grounding, R6) — highest priority, dropped last
    4. system_prompt is never truncated

    ``session_facts`` (chat mode) is orchestrator-authored grounding — the bound
    project, known finding ids, recent tool summaries — DATA-wrapped like every
    other external input and included on every turn so a long session stays
    consistent about its own scope even past the local model's window.
    """
    limit = CONTEXT_LIMITS.get(model, 28_000)
    messages: list[dict] = []

    # Session facts — chat grounding, highest priority (included every turn).
    if session_facts:
        messages.append({
            "role": "user",
            "content": wrap_data(session_facts, tool="session_facts", path="grounding"),
        })

    # Knowledge chunks — background context, lowest priority
    if knowledge_chunks:
        kb_content = "\n\n".join(
            wrap_data(chunk, tool="knowledge_base", path="kb") for chunk in knowledge_chunks
        )
        if _estimate_tokens(kb_content) < limit // 3:
            messages.append({"role": "user", "content": kb_content})

    # Tool output — current step result
    if tool_output:
        messages.append({"role": "user", "content": tool_output})

    return messages
