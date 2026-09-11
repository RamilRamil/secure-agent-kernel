import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class KernelConfig:
    """Task-agnostic configuration owned by the secure-agent kernel (feature 048).

    Exactly the 13 kernel fields (data-model.md ownership table rows 1–13). Audit
    material — chain keys, cloned-target workspaces, the SmartGraphical engine — and
    model-routing slot selection are NOT here: they live in the audit pack's
    AuditConfig, which composes this. The kernel never reads an audit field and never
    hard-codes a model slot; routing model ids arrive by injection (a Mapping[str,str]),
    so the kernel stays shape-agnostic. See specs/048-repo-split/ (Configuration ownership).
    """

    # LLM APIs
    anthropic_api_key: str
    # Optional Gemini key (spec 018) — the operator frontend can also supply one
    # at runtime, which takes precedence. Empty by default; the core loop never
    # needs it (Constitution V).
    gemini_api_key: str
    # Optional OpenRouter key (spec 020) — same posture as gemini_api_key.
    openrouter_api_key: str

    # Memory integrity — HMAC key as raw bytes
    secret_key: bytes

    # Storage
    memory_root: Path
    # Rollback anchor (feature 006) — monotonic per-project watermark held OUTSIDE
    # memory_root, on an access boundary the memory-write adversary cannot cross.
    # None (unset) leaves the rollback guard inert (mechanism vs. wiring, like the
    # writer lease). A writer-role deployment SHOULD set SR_ANCHOR_ROOT; the guarantee
    # is only as strong as the anchor_root vs memory_root access separation the
    # operator provides. MUST NOT be inside memory_root (EpisodicMemory enforces this).
    anchor_root: Path | None
    knowledge_root: Path
    confirmations_root: Path
    relay_root: Path
    # Experiential knowledge loop (feature 014) — candidate queue for pending lessons.
    # Promoted lessons live under knowledge_root/lessons/; this is the pending side.
    lessons_root: Path

    # Observability — optional
    langfuse_secret_key: str
    langfuse_public_key: str
    langfuse_host: str
    langfuse_enabled: bool


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise EnvironmentError(f"Required environment variable {name!r} is not set")
    return value


def load_kernel_config() -> KernelConfig:
    langfuse_secret = os.environ.get("LANGFUSE_SECRET_KEY", "")
    langfuse_public = os.environ.get("LANGFUSE_PUBLIC_KEY", "")

    return KernelConfig(
        # Optional: the core loop runs on local model / relay (Constitution V).
        # Only the ClaudeClient path (non-chat audit stages) needs this, and it
        # errors clearly at construction if it's missing.
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
        gemini_api_key=os.environ.get("GEMINI_API_KEY", ""),
        openrouter_api_key=os.environ.get("OPENROUTER_API_KEY", ""),
        secret_key=bytes.fromhex(_require("SR_SECRET_KEY")),
        memory_root=Path(os.environ.get("SR_MEMORY_ROOT", "./memory")),
        anchor_root=(
            Path(os.environ["SR_ANCHOR_ROOT"]) if os.environ.get("SR_ANCHOR_ROOT") else None
        ),
        knowledge_root=Path(os.environ.get("SR_KNOWLEDGE_ROOT", "./knowledge")),
        confirmations_root=Path(os.environ.get("SR_CONFIRMATIONS_ROOT", "./confirmations")),
        relay_root=Path(os.environ.get("SR_RELAY_ROOT", "./relay")),
        lessons_root=Path(os.environ.get("SR_LESSONS_ROOT", "./lessons")),
        langfuse_secret_key=langfuse_secret,
        langfuse_public_key=langfuse_public,
        langfuse_host=os.environ.get("LANGFUSE_HOST", "http://localhost:3000"),
        langfuse_enabled=bool(langfuse_secret and langfuse_public),
    )


# Module-level singleton — loaded once at import time.
# In tests, patch os.environ before importing or use load_kernel_config() directly.
config: KernelConfig = load_kernel_config()
