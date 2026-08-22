"""The only production path that calls pack.dispatch (feature 003, FR-018).

Chat and batch both come through here so a committed transition cannot be
duplicated on one surface and forgotten on the other. The executor derives
identity, skips dispatch when the transition is already committed, persists
payloads only when the status is not `pending`, and on `pending` writes exactly
one `pause_checkpoint` -- then drops the writer flock by pausing the lease.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from sr_agent.memory.canonical import canonical_digest, derive_operation_id, derive_transition_key
from sr_agent.models.action import Action, ValidationStatus
from sr_agent.models.dispatch import (
    ActionSnapshot,
    DispatchResult,
    DispatchStatus,
    PendingKind,
    PendingWait,
)
from sr_agent.models.memory import MemoryRecord, SourceType
from sr_agent.orchestrator.action import validate_action
from sr_agent.orchestrator.confirmation import request_confirmation_if_absent
from sr_agent.orchestrator.context import wrap_data
from sr_agent.orchestrator.pack import PackContext

if TYPE_CHECKING:
    from sr_agent.memory.episodic import EpisodicMemory
    from sr_agent.orchestrator.pack import CapabilityPack

_PENDING_STATUS = {
    PendingKind.external_response: "paused_relay",
    PendingKind.human_confirmation: "paused_confirmation",
    PendingKind.local_model_retry: "blocked_local_unavailable",
}


class ResumeError(Exception):
    """Resume cannot continue and will not guess. The message says why."""
    pass


class KernelActionExecutor:
    def __init__(
        self,
        *,
        memory: "EpisodicMemory",
        scope_root: Path,
        pack_id: str,
        pack_contract_version: str,
        confirmations_dir: Path | None = None,
        relay_dir: Path | None = None,
        sandbox: Any = None,
    ) -> None:
        self._memory = memory
        self._scope_root = Path(scope_root)
        self._pack_id = pack_id
        self._pack_contract_version = pack_contract_version
        self._confirmations_dir = confirmations_dir
        self._relay_dir = relay_dir
        self._sandbox = sandbox if sandbox is not None else object()

    # ── Identity ────────────────────────────────────────────────────────────

    def derive_ids(self, session, action: Action) -> tuple[str, str, int]:
        session_id = session.session_id
        project_id = session.principal.project_id
        generation = int(getattr(session, "scope_generation", 0) or 0)
        revision = self._memory.session_revision(project_id, session_id)
        key = derive_transition_key(
            session_id=session_id,
            action_id=action.action_type,
            params=dict(action.params),
            expected_revision=revision,
            scope_generation=generation,
        )
        return key, str(derive_operation_id(key)), revision

    def _context(self, operation_id: str, transition_key: str, session=None) -> PackContext:
        policy = None
        if session is not None:
            include = getattr(session, "include", None) or []
            if getattr(session, "scope_root", None) and include:
                try:
                    from sr_agent.orchestrator.scope import ContentScopePolicy
                    policy = ContentScopePolicy.from_session(session)
                except Exception:
                    policy = None
        return PackContext(
            scope_root=self._scope_root,
            sandbox=self._sandbox,
            wrap_data=wrap_data,
            operation_id=operation_id,
            transition_key=transition_key,
            scope_policy=policy,
        )

    def execute_batch(self, pack: "CapabilityPack", session, action: Action, **kwargs) -> DispatchResult:
        """Named kernel-side seam for `sr-agent audit`-style batch (FR-018 / T065)."""
        return self.execute(pack, session, action, **kwargs)

    @staticmethod
    def _as_result(raw: object) -> DispatchResult:
        if isinstance(raw, DispatchResult):
            return raw
        if isinstance(raw, str):
            return DispatchResult(status=DispatchStatus.ran, body=raw)
        raise TypeError(f"dispatch must return DispatchResult or str, got {type(raw).__name__}")

    # ── Execute ─────────────────────────────────────────────────────────────

    def execute(
        self,
        pack: "CapabilityPack",
        session,
        action: Action,
        *,
        turn_id: str | None = None,
        user_message: str = "",
        system_prompt_id: str = "",
        system_prompt_hash: str = "",
        tool_calls_used: int = 0,
        phase: str = "dispatch",
    ) -> DispatchResult:
        validated = validate_action(action, self._scope_root, pack)
        if validated.status is ValidationStatus.rejected:
            return DispatchResult(
                status=DispatchStatus.error,
                body=validated.rejection_reason or "rejected",
            )

        transition_key, operation_id, revision = self.derive_ids(session, action)
        project_id = session.principal.project_id

        existing = self._memory.find_committed_bundle(
            project_id, session.session_id, operation_id, transition_key
        )
        if existing is not None:
            return DispatchResult(status=DispatchStatus.ran, body="already committed")

        if action.human_confirmation is False and self._confirmations_dir is not None:
            request_confirmation_if_absent(action, self._confirmations_dir, operation_id)
            pending = PendingWait(kind=PendingKind.human_confirmation, correlation_id=operation_id)
            result = DispatchResult(status=DispatchStatus.pending, body="awaiting confirmation", pending=pending)
            self.write_pause_checkpoint(
                session, result, action, transition_key, operation_id, revision,
                turn_id=turn_id, user_message=user_message,
                system_prompt_id=system_prompt_id, system_prompt_hash=system_prompt_hash,
                tool_calls_used=tool_calls_used, phase=phase,
            )
            return result

        ctx = self._context(operation_id, transition_key, session)
        result = self._as_result(pack.dispatch(action, ctx))

        if result.status is DispatchStatus.pending:
            self.write_pause_checkpoint(
                session, result, action, transition_key, operation_id, revision,
                turn_id=turn_id, user_message=user_message,
                system_prompt_id=system_prompt_id, system_prompt_hash=system_prompt_hash,
                tool_calls_used=tool_calls_used, phase=phase,
            )
            return result

        self._memory.commit_if_absent(
            project_id=project_id,
            target=str(action.params.get("target") or action.action_type),
            session_id=session.session_id,
            tool=action.action_type,
            operation_id=operation_id,
            transition_key=transition_key,
            expected_revision=revision,
            payloads=result.payloads,
        )
        return result

    # ── Pause ───────────────────────────────────────────────────────────────

    def write_pause_checkpoint(
        self,
        session,
        result: DispatchResult,
        action: Action,
        transition_key: str,
        operation_id: str,
        expected_revision: int,
        *,
        turn_id: str | None,
        user_message: str,
        system_prompt_id: str,
        system_prompt_hash: str,
        tool_calls_used: int,
        phase: str,
    ) -> MemoryRecord:
        """Exactly one record. After this returns, the session is paused."""
        pending = result.pending
        status = _PENDING_STATUS[pending.kind] if pending else "paused_relay"
        identity = getattr(session, "content_identity", None)
        snapshot = ActionSnapshot(
            action_type=action.action_type,
            params=dict(action.params),
            pack_id=self._pack_id,
            pack_contract_version=self._pack_contract_version,
            scope_generation=int(getattr(session, "scope_generation", 0) or 0),
            content_identity_digest=getattr(identity, "digest", None),
            transition_key=transition_key,
            operation_id=operation_id,
        )
        payload = {
            "turn_id": turn_id or str(uuid4()),
            "phase": phase,
            "user_message": user_message,
            "system_prompt_id": system_prompt_id,
            "system_prompt_hash": system_prompt_hash,
            "last_dispatch_operation_id": operation_id,
            "pending": pending.model_dump(mode="json") if pending else None,
            "tool_calls_used": tool_calls_used,
            "pending_relay_request_id": (
                pending.correlation_id if pending and pending.kind is PendingKind.external_response else None
            ),
            "expected_session_revision": expected_revision,
            "session_status": status,
            "action_snapshot": snapshot.model_dump(mode="json"),
            "scope_generation": snapshot.scope_generation,
        }
        record = self._memory.write(
            MemoryRecord(
                project_id=session.principal.project_id,
                target=f"chat:{session.session_id}",
                source_type=SourceType.tool_output,
                tool="orchestrator",
                session_id=session.session_id,
                payload_kind="pause_checkpoint",
                payload=payload,
                checkpoint=payload,
            )
        )
        if hasattr(session, "continuation"):
            try:
                from sr_agent.models.session import PauseContinuation
                session.continuation = PauseContinuation.model_validate(payload)
            except (ValueError, AttributeError):
                pass
        if hasattr(session, "status"):
            try:
                session.status = status
            except (ValueError, AttributeError):
                pass
        if pending and pending.kind is PendingKind.external_response and hasattr(session, "pending_relay_request_id"):
            try:
                session.pending_relay_request_id = pending.correlation_id
            except (ValueError, AttributeError):
                pass
        if pending and pending.kind is PendingKind.human_confirmation and hasattr(session, "pending_confirmation_id"):
            try:
                session.pending_confirmation_id = pending.correlation_id
            except (ValueError, AttributeError):
                pass
        lease = getattr(self._memory, "_lease", None)
        if lease is not None:
            lease.pause(session.principal.project_id, session.session_id)
        return record

    def latest_checkpoint(self, session) -> MemoryRecord | None:
        records = [
            r
            for r in self._memory.load(session.principal.project_id, f"chat:{session.session_id}")
            if r.payload_kind == "pause_checkpoint"
        ]
        if not records:
            return None
        return max(records, key=lambda r: r.log_sequence or 0)

    # ── Resume ──────────────────────────────────────────────────────────────

    def resume(
        self,
        pack: "CapabilityPack",
        session,
        response_body: dict | None = None,
    ) -> DispatchResult:
        checkpoint = self.latest_checkpoint(session)
        if checkpoint is None or not checkpoint.payload:
            raise ResumeError("No pause_checkpoint for this session; cannot resume.")
        payload = checkpoint.payload
        snap_data = payload.get("action_snapshot")
        if not snap_data:
            raise ResumeError("Checkpoint has no Action snapshot; cannot rebuild the action.")
        snapshot = ActionSnapshot.model_validate(snap_data)
        if snapshot.pack_id != self._pack_id:
            raise ResumeError(
                f"Checkpoint pack_id={snapshot.pack_id!r} does not match the "
                f"wired pack {self._pack_id!r}."
            )
        if snapshot.pack_contract_version != self._pack_contract_version:
            raise ResumeError(
                f"Checkpoint pack_contract_version={snapshot.pack_contract_version!r} "
                f"does not match {self._pack_contract_version!r}. The kernel will "
                "not resolve an alternative pack version."
            )
        action = Action(action_type=snapshot.action_type, params=dict(snapshot.params))
        key, operation_id, _revision = self.derive_ids(session, action)
        if key != snapshot.transition_key or operation_id != snapshot.operation_id:
            raise ResumeError(
                "Re-derived transition_key / operation_id do not match the snapshot."
            )
        if canonical_digest(dict(snapshot.params)) != canonical_digest(dict(action.params)):
            raise ResumeError("Snapshot params digest does not rematch.")

        if response_body is not None:
            self.ingest_pending_response(session, body=response_body, checkpoint=checkpoint)

        pending = payload.get("pending") or {}
        if pending.get("kind") == PendingKind.human_confirmation.value and snap_data is None:
            raise ResumeError("A correlation id alone does not authorize execution.")

        ctx = self._context(snapshot.operation_id, snapshot.transition_key, session)
        result = self._as_result(pack.dispatch(action, ctx))
        if result.status is DispatchStatus.pending:
            if result.pending and result.pending.correlation_id != pending.get("correlation_id"):
                raise ResumeError(
                    "Repeated pending must reuse the same correlation_id."
                )
            return result

        revision = self._memory.session_revision(session.principal.project_id, session.session_id)
        self._memory.commit_if_absent(
            project_id=session.principal.project_id,
            target=str(action.params.get("target") or action.action_type),
            session_id=session.session_id,
            tool=action.action_type,
            operation_id=snapshot.operation_id,
            transition_key=snapshot.transition_key,
            expected_revision=revision,
            payloads=result.payloads,
        )
        for name, value in (
            ("status", "active"),
            ("pending_relay_request_id", None),
            ("pending_confirmation_id", None),
        ):
            if hasattr(session, name):
                try:
                    setattr(session, name, value)
                except (ValueError, AttributeError):
                    pass
        return result

    def ingest_pending_response(
        self,
        session,
        *,
        body: dict,
        checkpoint: MemoryRecord | None = None,
    ) -> MemoryRecord:
        ckpt = checkpoint or self.latest_checkpoint(session)
        if ckpt is None or not ckpt.payload:
            raise ResumeError("No pause_checkpoint to ingest a response against.")
        pending = ckpt.payload.get("pending") or {}
        return self._memory.put_external_response_if_absent(
            project_id=session.principal.project_id,
            target=f"chat:{session.session_id}",
            session_id=session.session_id,
            operation_id=ckpt.payload["last_dispatch_operation_id"],
            correlation_id=pending["correlation_id"],
            body=body,
        )

    def execute_confirmed_by_correlation(self, correlation_id: str) -> None:
        raise ResumeError(
            f"Correlation id {correlation_id!r} alone does not authorize execution. "
            "Resume requires the Action snapshot from the pause_checkpoint."
        )
