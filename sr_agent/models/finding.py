from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Severity(str, Enum):
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"
    informational = "informational"


class FindingStatus(str, Enum):
    open = "open"
    confirmed = "confirmed"
    mock_review = "mock_review"
    unverified = "unverified"
    false_positive = "false_positive"


class PoCStatus(str, Enum):
    passed = "passed"
    failed = "failed"
    mock_review = "mock_review"


class BastetTag(str, Enum):
    """46-tag taxonomy from the Bastet dataset (2606.03387).

    Tags map to vulnerability categories used during Stage 2 CheckRunner.
    Stage 2 model outputs exactly one tag per finding — enum enforcement
    prevents hallucinated categories from entering memory.
    """
    # Access control
    access_control = "access-control"
    missing_access_control = "missing-access-control"
    incorrect_access_control = "incorrect-access-control"
    # Reentrancy
    reentrancy = "reentrancy"
    read_only_reentrancy = "read-only-reentrancy"
    cross_function_reentrancy = "cross-function-reentrancy"
    # Arithmetic
    arithmetic = "arithmetic"
    overflow_underflow = "overflow-underflow"
    precision_loss = "precision-loss"
    rounding_error = "rounding-error"
    # Oracle / price manipulation
    oracle_manipulation = "oracle-manipulation"
    price_manipulation = "price-manipulation"
    flash_loan_attack = "flash-loan-attack"
    # MEV / frontrunning
    frontrunning = "frontrunning"
    sandwich_attack = "sandwich-attack"
    mev = "mev"
    # Logic errors
    logic_error = "logic-error"
    incorrect_calculation = "incorrect-calculation"
    wrong_assumption = "wrong-assumption"
    missing_check = "missing-check"
    # State management
    state_corruption = "state-corruption"
    incorrect_state_update = "incorrect-state-update"
    missing_state_update = "missing-state-update"
    # Denial of service
    denial_of_service = "denial-of-service"
    gas_exhaustion = "gas-exhaustion"
    unbounded_loop = "unbounded-loop"
    # Signature / authentication
    signature_replay = "signature-replay"
    missing_signature_verification = "missing-signature-verification"
    signature_malleability = "signature-malleability"
    # Initialisation
    uninitialized_variable = "uninitialized-variable"
    missing_initialization = "missing-initialization"
    # Upgradability
    upgradability = "upgradability"
    storage_collision = "storage-collision"
    delegatecall_injection = "delegatecall-injection"
    # Token / ERC standard
    erc20_compliance = "erc20-compliance"
    fee_on_transfer = "fee-on-transfer"
    rebasing_token = "rebasing-token"
    # External calls
    unchecked_return_value = "unchecked-return-value"
    arbitrary_external_call = "arbitrary-external-call"
    # Centralization
    centralization_risk = "centralization-risk"
    admin_privilege = "admin-privilege"
    # Timing
    timestamp_dependence = "timestamp-dependence"
    block_number_dependence = "block-number-dependence"
    # Misc
    hardcoded_address = "hardcoded-address"
    missing_event = "missing-event"
    informational = "informational"


class Finding(BaseModel):
    finding_id: str                         # "CRIT-001", "HIGH-002"
    location: str                           # "Vault.sol:47"
    function_name: str

    # Classification
    bastet_tag: BastetTag | None = None
    severity: Severity
    status: FindingStatus = FindingStatus.open

    # AttackPathGNN preconditions (12 total: 8 GNN + 4 DeFi-specific)
    # Keys 1-12, True = precondition is active for this finding
    preconditions: dict[int, bool] = Field(default_factory=dict)
    mitigations_present: list[str] = Field(default_factory=list)

    # Evidence
    poc_path: str | None = None
    poc_status: PoCStatus | None = None

    # Stage 3 combination chain
    combined_with: list[str] = Field(default_factory=list)
