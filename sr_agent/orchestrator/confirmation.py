"""Out-of-band human confirmation channel (US4).

Irreversible WRITE_EXECUTE actions cannot be approved by the agent itself.
The agent writes a *pending* request to confirmations/<id>.json and blocks,
polling the file. Approval can only arrive through a separate process — a human
running `sr-agent confirm <id> --approve` — so a compromised or injected agent
cannot self-approve.

Fail-safe by construction:
  - a timeout is treated as REJECTED, never approved-by-default
  - a missing/unreadable request file keeps polling until timeout → rejected
  - the agent process never writes "approved" — only the out-of-band CLI does
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from uuid import uuid4

from sr_agent.models.action import Action

logger = logging.getLogger(__name__)


class ConfirmationStatus(str, Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


@dataclass
class ConfirmationRequest:
    confirmation_id: str
    action_type: str
    params: dict
    created_at: str
    path: Path


def _request_path(confirmations_dir: Path, confirmation_id: str) -> Path:
    return confirmations_dir / f"{confirmation_id}.json"


def request_confirmation(action: Action, confirmations_dir: Path) -> ConfirmationRequest:
    """Write a pending confirmation request for an irreversible action."""
    confirmations_dir.mkdir(parents=True, exist_ok=True)
    confirmation_id = str(uuid4())
    created_at = datetime.now(timezone.utc).isoformat()

    payload = {
        "confirmation_id": confirmation_id,
        "action_type": action.action_type.value,
        "params": action.params,
        "created_at": created_at,
        "status": ConfirmationStatus.pending.value,
        "decided_at": None,
        "decided_reason": None,
    }
    path = _request_path(confirmations_dir, confirmation_id)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    logger.info(
        "OOB confirmation requested: %s for action %s",
        confirmation_id, action.action_type.value,
    )
    return ConfirmationRequest(
        confirmation_id=confirmation_id,
        action_type=action.action_type.value,
        params=action.params,
        created_at=created_at,
        path=path,
    )


def _read_status(path: Path) -> ConfirmationStatus:
    """Read the current status, treating any unreadable state as pending."""
    if not path.exists():
        return ConfirmationStatus.pending
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return ConfirmationStatus(data.get("status", "pending"))
    except Exception:
        # Unparseable or unknown status — keep waiting (fail-safe: eventual reject)
        return ConfirmationStatus.pending


def check_confirmation(
    confirmation_id: str,
    confirmations_dir: Path,
    timeout_s: float = 300.0,
    poll_interval_s: float = 2.0,
) -> ConfirmationStatus:
    """Block until the request is approved/rejected, or timeout.

    Returns ConfirmationStatus.approved only if the out-of-band channel set it.
    A timeout returns ConfirmationStatus.rejected (fail-safe) and is recorded
    on the request file so the audit trail shows why the action was blocked.
    """
    path = _request_path(confirmations_dir, confirmation_id)
    deadline = time.monotonic() + timeout_s

    while True:
        status = _read_status(path)
        if status in (ConfirmationStatus.approved, ConfirmationStatus.rejected):
            logger.info("Confirmation %s resolved: %s", confirmation_id, status.value)
            return status

        if time.monotonic() >= deadline:
            _record_decision(path, ConfirmationStatus.rejected, reason="timeout")
            logger.warning(
                "Confirmation %s timed out after %ss — fail-safe reject",
                confirmation_id, timeout_s,
            )
            return ConfirmationStatus.rejected

        time.sleep(poll_interval_s)


def _record_decision(path: Path, status: ConfirmationStatus, reason: str | None = None) -> None:
    """Persist a decision onto the request file (used by CLI and timeout)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        data = {}
    data["status"] = status.value
    data["decided_at"] = datetime.now(timezone.utc).isoformat()
    data["decided_reason"] = reason
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def resolve_confirmation(
    confirmation_id: str,
    confirmations_dir: Path,
    approve: bool,
    reason: str | None = None,
) -> ConfirmationStatus:
    """Out-of-band decision entry point — called by the `sr-agent confirm` CLI.

    This is the ONLY path that may set 'approved'. It is invoked from a separate
    process than the agent loop, which is what makes the channel out-of-band.
    """
    path = _request_path(confirmations_dir, confirmation_id)
    if not path.exists():
        raise FileNotFoundError(f"No pending confirmation: {confirmation_id}")

    status = ConfirmationStatus.approved if approve else ConfirmationStatus.rejected
    _record_decision(path, status, reason=reason or ("approved" if approve else "rejected"))
    logger.info("Confirmation %s set to %s out-of-band", confirmation_id, status.value)
    return status


def load_request(confirmation_id: str, confirmations_dir: Path) -> dict:
    """Return the raw request payload for display (`sr-agent confirm --show`)."""
    path = _request_path(confirmations_dir, confirmation_id)
    if not path.exists():
        raise FileNotFoundError(f"No confirmation: {confirmation_id}")
    return json.loads(path.read_text(encoding="utf-8"))
