from __future__ import annotations

from enum import Enum

from sr_agent.config import config


class TaskType(str, Enum):
    stage1_discovery = "stage1_discovery"
    stage2_check = "stage2_check"
    stage3_synthesis = "stage3_synthesis"
    poc_writing = "poc_writing"


# Maps task type to the model name from config
MODEL_CONFIG: dict[TaskType, str] = {
    TaskType.stage1_discovery: config.stage1_model,
    TaskType.stage2_check: config.stage2_model,
    TaskType.stage3_synthesis: config.stage3_model,
    TaskType.poc_writing: config.poc_model,
}

# Tasks that MUST use extended thinking — security requirement, not preference
REQUIRES_EXTENDED_THINKING: frozenset[TaskType] = frozenset({
    TaskType.stage1_discovery,
    TaskType.stage3_synthesis,
})


class ModelRouter:
    """Route a task type to the correct LLM client.

    Claude Opus → Stage 1/3 (extended thinking mandatory)
    Qwen3-4B local → Stage 2 (fine-tuned, $0/call, code stays local)
    """

    def route(self, task_type: TaskType) -> str:
        return MODEL_CONFIG[task_type]

    def requires_thinking(self, task_type: TaskType) -> bool:
        return task_type in REQUIRES_EXTENDED_THINKING


router = ModelRouter()
