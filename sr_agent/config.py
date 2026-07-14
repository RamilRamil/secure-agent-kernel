import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Config:
    # LLM APIs
    anthropic_api_key: str
    # Optional Gemini key (spec 018) — the operator frontend can also supply one
    # at runtime, which takes precedence. Empty by default; the core loop never
    # needs it (Constitution V).
    gemini_api_key: str
    # Optional OpenRouter key (spec 020) — same posture as gemini_api_key.
    openrouter_api_key: str
    alchemy_api_key: str
    tenderly_api_key: str

    # Memory integrity — HMAC key as raw bytes
    secret_key: bytes

    # Storage
    memory_root: Path
    knowledge_root: Path
    confirmations_root: Path
    relay_root: Path
    # Experiential knowledge loop (feature 014) — candidate queue for pending lessons.
    # Promoted lessons live under knowledge_root/lessons/; this is the pending side.
    lessons_root: Path

    # SmartGraphical engine (feature 002) — external structural+logic analyzer.
    # Empty string disables the engine; pipeline auto-skips if unset/unavailable.
    smartgraphical_root: str

    # Model routing
    stage1_model: str
    stage2_model: str
    stage3_model: str
    poc_model: str

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


def load_config() -> Config:
    langfuse_secret = os.environ.get("LANGFUSE_SECRET_KEY", "")
    langfuse_public = os.environ.get("LANGFUSE_PUBLIC_KEY", "")

    return Config(
        # Optional: the core loop runs on local model / relay (Constitution V).
        # Only the ClaudeClient path (non-chat audit stages) needs this, and it
        # errors clearly at construction if it's missing.
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
        gemini_api_key=os.environ.get("GEMINI_API_KEY", ""),
        openrouter_api_key=os.environ.get("OPENROUTER_API_KEY", ""),
        alchemy_api_key=os.environ.get("ALCHEMY_API_KEY", ""),
        tenderly_api_key=os.environ.get("TENDERLY_API_KEY", ""),
        secret_key=bytes.fromhex(_require("SR_SECRET_KEY")),
        memory_root=Path(os.environ.get("SR_MEMORY_ROOT", "./memory")),
        knowledge_root=Path(os.environ.get("SR_KNOWLEDGE_ROOT", "./knowledge")),
        confirmations_root=Path(os.environ.get("SR_CONFIRMATIONS_ROOT", "./confirmations")),
        relay_root=Path(os.environ.get("SR_RELAY_ROOT", "./relay")),
        lessons_root=Path(os.environ.get("SR_LESSONS_ROOT", "./lessons")),
        smartgraphical_root=os.environ.get("SR_SMARTGRAPHICAL_ROOT", ""),
        stage1_model=os.environ.get("SR_STAGE1_MODEL", "claude-opus-4-8"),
        stage2_model=os.environ.get("SR_STAGE2_MODEL", "sr-stage2"),
        stage3_model=os.environ.get("SR_STAGE3_MODEL", "claude-opus-4-8"),
        poc_model=os.environ.get("SR_POC_MODEL", "qwen3-coder"),
        langfuse_secret_key=langfuse_secret,
        langfuse_public_key=langfuse_public,
        langfuse_host=os.environ.get("LANGFUSE_HOST", "http://localhost:3000"),
        langfuse_enabled=bool(langfuse_secret and langfuse_public),
    )


# Module-level singleton — loaded once at import time.
# In tests, patch os.environ before importing or use load_config() directly.
config: Config = load_config()
