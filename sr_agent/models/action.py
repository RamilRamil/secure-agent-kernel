from __future__ import annotations

from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field


class ActionType(str, Enum):
    # ── READ-ONLY ────────────────────────────────────────────────────────
    read_file = "read_file"
    search_code = "search_code"
    build_graph = "build_graph"
    run_slither = "run_slither"
    run_mythril = "run_mythril"
    run_auditor_skill = "run_auditor_skill"
    analyze_transactions = "analyze_transactions"
    decompile_bytecode = "decompile_bytecode"
    # ── WRITE / EXECUTE (require out-of-band human confirmation) ─────────
    write_poc = "write_poc"
    run_tests = "run_tests"
    deploy_test_contract = "deploy_test_contract"
    # ── MEMORY / CONTROL ─────────────────────────────────────────────────
    write_memory = "write_memory"
    request_human_confirmation = "request_human_confirmation"
    escalate = "escalate"


class ActionClass(str, Enum):
    read_only = "read_only"
    write_execute = "write_execute"
    memory = "memory"
    control = "control"


# Which action types belong to which class
ACTION_CLASS_MAP: dict[ActionType, ActionClass] = {
    ActionType.read_file: ActionClass.read_only,
    ActionType.search_code: ActionClass.read_only,
    ActionType.build_graph: ActionClass.read_only,
    ActionType.run_slither: ActionClass.read_only,
    ActionType.run_mythril: ActionClass.read_only,
    ActionType.run_auditor_skill: ActionClass.read_only,
    ActionType.analyze_transactions: ActionClass.read_only,
    ActionType.decompile_bytecode: ActionClass.read_only,
    ActionType.write_poc: ActionClass.write_execute,
    ActionType.run_tests: ActionClass.write_execute,
    ActionType.deploy_test_contract: ActionClass.write_execute,
    ActionType.write_memory: ActionClass.memory,
    ActionType.request_human_confirmation: ActionClass.control,
    ActionType.escalate: ActionClass.control,
}

REVERSIBLE: dict[ActionType, bool] = {
    ActionType.read_file: True,
    ActionType.search_code: True,
    ActionType.build_graph: True,
    ActionType.run_slither: True,
    ActionType.run_mythril: True,
    ActionType.run_auditor_skill: True,
    ActionType.analyze_transactions: True,
    ActionType.decompile_bytecode: True,
    ActionType.write_poc: False,
    ActionType.run_tests: False,
    ActionType.deploy_test_contract: False,
    ActionType.write_memory: False,
    ActionType.request_human_confirmation: True,
    ActionType.escalate: True,
}


class ValidationStatus(str, Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class ValidationResult(BaseModel):
    status: ValidationStatus
    rejection_reason: str | None = None


class Action(BaseModel):
    action_id: str = Field(default_factory=lambda: str(uuid4()))
    action_type: ActionType
    params: dict = Field(default_factory=dict)

    # Derived from action_type — orchestrator fills these
    action_class: ActionClass | None = None
    is_reversible: bool | None = None

    # Validation state
    validation_status: ValidationStatus = ValidationStatus.pending
    rejection_reason: str | None = None
    human_confirmation: bool | None = None   # None = not required
