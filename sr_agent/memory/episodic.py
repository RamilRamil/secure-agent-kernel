from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Sequence

from sr_agent.memory import hmac as hmac_module
from sr_agent.memory.canonical import canonical_bytes, canonical_digest
from sr_agent.models.dispatch import MAX_SNAPSHOT_BYTES, MAX_SNAPSHOT_ITEMS
from sr_agent.models.memory import MemoryRecord, SourceType

if TYPE_CHECKING:
    from sr_agent.models.dispatch import DispatchPayload, MemorySnapshot
    from sr_agent.models.principal import Principal
    from sr_agent.orchestrator.lease import WriterLease

logger = logging.getLogger(__name__)


class MemoryWriteError(Exception):
    pass


class MemoryChainError(MemoryWriteError):
    """Raised on write when a target file's signed chain head is unusable.

    Fail-closed: appending onto a chain we cannot verify would produce a store
    whose composition nobody can check afterwards.
    """
    pass


@dataclass
class IntegrityReport:
    """Result of an out-of-band integrity scan (`sr-agent memory verify`)."""
    project_id: str
    total: int = 0
    valid: int = 0
    invalid: int = 0
    # target stem -> (total, valid, invalid)
    per_target: dict[str, tuple[int, int, int]] = field(default_factory=dict)
    # target stem -> why its chain does not reconstruct. Composition damage is
    # reported separately from signature damage: `invalid` counts records that
    # were ALTERED, this counts files whose record SET no longer matches what
    # was signed — the removal case, which no per-record count can show.
    chain_breaks: dict[str, str] = field(default_factory=dict)

    @property
    def has_invalid(self) -> bool:
        return self.invalid > 0

    @property
    def has_chain_break(self) -> bool:
        return bool(self.chain_breaks)


class MemoryCompositionBreak(Exception):
    """Raised when a snapshot is requested over a project whose chain is broken.

    Distinct from returning an empty list: to a pack, "no prior state" reads as a
    fresh session, which is exactly the story an attacker who truncated the log
    would like told.
    """
    pass


class SnapshotWatermarkError(Exception):
    """Raised when a snapshot is requested above the current append watermark."""
    pass


class SnapshotCapacityExceeded(Exception):
    """Raised when a snapshot would exceed a fixed capacity limit."""
    pass


@dataclass
class _ProjectView:
    """The verified project log plus the two summaries the append path needs.

    The summaries exist so that appending stays O(1) instead of re-deriving the
    chain tip and the highest sequence by walking every record each time. Both
    are maintained incrementally by the same call that appends, so they cannot
    drift from the records they summarize.
    """
    records: list[MemoryRecord]
    tips: dict[str, tuple[int, str | None]]   # target stem -> (length, last hmac)
    max_log_sequence: int
    # Whether the sequence set has been checked for duplicates and gaps. Done
    # once per rescan and on the write path only, so a read never fails on it.
    sequences_validated: bool = False


class ExternalResponseConflict(MemoryWriteError):
    """Raised when a response arrives that disagrees with one already ingested.

    Fail-closed and no preference for the newer bytes: the stored record is what
    the paused turn was told, so replacing it would rewrite a decision the agent
    may already have acted on.
    """
    pass


class PrincipalMismatch(Exception):
    """Raised when a record's project_id does not match the active principal.

    This is a hard isolation boundary — a write or load that crosses principals
    is a security violation, not a recoverable condition.
    """
    pass


class EpisodicMemory:
    def __init__(
        self,
        memory_root: Path,
        secret_key: bytes,
        privileged_statuses: frozenset[str] = frozenset(),
        lease: "WriterLease | None" = None,
        event_sink: Callable[[dict], None] | None = None,
    ) -> None:
        self._root = memory_root
        self._secret_key = secret_key
        # Bound at construction like `privileged_statuses` (D5). A memory built
        # without a lease is a READER: `verify_integrity`, `load`, and the CLI
        # inspection paths need no writer role. Binding the lease is the
        # composition root's job for anything that appends.
        self._lease = lease
        # Live-trace sink for `memory_write` events (002, D9.2). Bound the same
        # way as `privileged_statuses` and `lease` — the composition root's job,
        # not a call-site concern. Default `None`: a memory built without one is
        # silent, not broken (FR-008).
        self._event_sink = event_sink
        # Re-entrancy guard for `_emit` (D9, T022). A sink that itself calls
        # `write` on this instance would recurse into `_emit` -- this flag
        # contains that bug rather than licensing it; the sink still MUST NOT
        # write memory (contract).
        self._emitting = False
        # Verified-log cache, valid only while this process holds the lease —
        # which is exactly what makes it sound: a lease owner is the project's
        # only writer, so the verified prefix cannot change underneath it. Without
        # a lease there is no such guarantee and no caching happens.
        self._cache: dict[str, _ProjectView] = {}
        # Privileged-status set composed by the kernel from the active pack's
        # `privileged_statuses` and BOUND here at session construction (D5).
        # Immutable for the session — no model turn or tool result may widen or
        # narrow it. Empty means "this pack has no privileged statuses" (H4),
        # NOT "gate disabled".
        self._privileged_statuses = frozenset(privileged_statuses)

    @staticmethod
    def _target_stem(target: str) -> str:
        return target.replace("/", "_").replace(":", "__")

    def _path(self, project_id: str, target: str) -> Path:
        return self._root / project_id / f"{self._target_stem(target)}.jsonl"

    # ── Signed chain head ───────────────────────────────────────────────────
    # The per-record chain (`seq` / `chain_prev`) makes a record removed from
    # the MIDDLE of a file detectable: the next record's link no longer lands.
    # It cannot detect truncation of the TAIL, because nothing left in the file
    # attests to how long the file should be — and the tail is exactly where the
    # newest correction lives. The head is that attestation: one signed file per
    # project recording, per target, how many records the chain has and what its
    # last signature is. It is signed with the same orchestrator key, so an
    # attacker with write access can destroy it but cannot forge a shorter one.

    def _head_path(self, project_id: str) -> Path:
        # Not "*.jsonl" — the record globs must never pick this up as a target.
        return self._root / project_id / "_chain_head.json"

    def _read_head(self, project_id: str) -> dict[str, dict] | None:
        """Return the verified per-target head map, or None if absent/forged."""
        path = self._head_path(project_id)
        if not path.exists():
            return None
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            signature = doc.pop("hmac", None)
        except Exception:
            return None
        if not isinstance(signature, str):
            return None
        if not hmac_module.verify(doc, signature, self._secret_key):
            return None
        targets = doc.get("targets")
        return targets if isinstance(targets, dict) else None

    def _write_head(self, project_id: str, stem: str, length: int, last_hmac: str) -> None:
        targets = self._read_head(project_id) or {}
        targets[stem] = {"length": length, "last_hmac": last_hmac}
        doc = {"project_id": project_id, "targets": targets}
        payload = dict(doc, hmac=hmac_module.sign(doc, self._secret_key))
        path = self._head_path(project_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        tmp.replace(path)   # atomic swap: never leave a half-written head

    def write(
        self,
        record: MemoryRecord,
        principal: "Principal | None" = None,
    ) -> MemoryRecord:
        """Validate, sign, and append a record to the episodic store.

        Policy checks happen here — the model layer does not enforce policy.
        If ``principal`` is given, the record's project_id must match it —
        a mismatch raises PrincipalMismatch (cross-principal write attempt).
        """
        if principal is not None and record.project_id != principal.project_id:
            raise PrincipalMismatch(
                f"Write rejected: record project_id={record.project_id!r} "
                f"!= principal project_id={principal.project_id!r}"
            )

        self._enforce_status_rules(record)
        self._require_lease(record.project_id, record.session_id)

        path = self._path(record.project_id, record.target)
        path.parent.mkdir(parents=True, exist_ok=True)

        # Repair before reading anything. A tail torn by a killed process is not
        # a record and must not be scanned, counted, or appended onto — the old
        # behaviour glued the next write onto it and lost both.
        self._recover_torn_tails(record.project_id)

        log_sequence = self._next_log_sequence(record.project_id)
        seq, chain_prev = self._chain_tip(path)

        # Position, back-link, and project-wide order are kernel-set and go
        # INSIDE the signature, so a record's place in the file and in the log is
        # authenticated along with its content.
        record = record.model_copy(
            update={"seq": seq, "chain_prev": chain_prev, "log_sequence": log_sequence}
        )

        # Orchestrator computes the HMAC — LLM never touches this field
        fields = record.fields_for_hmac()
        signature = hmac_module.sign(fields, self._secret_key)
        record = record.model_copy(update={"hmac": signature})

        self._append_durably(path, record.model_dump_json() + "\n")
        self._write_head(record.project_id, path.stem, seq + 1, signature)
        self._extend_cache(record)

        # Fires only after the append is durable — the event means "this record
        # is on disk", never "this record is being written" (D9.2). Emitting
        # any earlier would let a consumer observe a write that a later step in
        # this method could still fail to complete.
        self._emit_write(record)

        return record

    def _emit_write(self, record: MemoryRecord) -> None:
        """Fire the `memory_write` live-trace event (observability only).

        Mirrors `AgentLoop._emit`: a missing or raising sink must never affect
        the write, which has already happened by the time this runs.
        """
        if self._event_sink is None:
            return
        if self._emitting:
            # A sink that calls `write` again would otherwise recurse back into
            # this method. The guard exists to contain a broken observer, not
            # to license one -- the contract still requires a sink not to write
            # memory at all.
            return
        self._emitting = True
        try:
            self._event_sink({
                "type": "memory_write",
                "record_id": record.record_id,
                "project_id": record.project_id,
                "target": record.target,
                "session_id": record.session_id,
                "source_type": record.source_type.value,
                "payload_kind": record.payload_kind,
                "log_sequence": record.log_sequence,
            })
        except Exception:  # a broken observer must never break a write
            logger.debug("event_sink raised on memory_write; ignoring", exc_info=True)
        finally:
            self._emitting = False

    def _require_lease(self, project_id: str, session_id: str) -> None:
        """Refuse a durable append that does not belong to the project's writer.

        Covers every append uniformly — chat turns, findings, dispatch commits,
        checkpoints — because a single uncovered path is the whole guarantee.
        """
        if self._lease is None:
            return
        from sr_agent.orchestrator.lease import LeaseNotHeld

        try:
            self._lease.require_owner(project_id, session_id)
        except LeaseNotHeld as exc:
            raise MemoryWriteError(str(exc)) from exc

    # ── Verified-log cache ──────────────────────────────────────────────────

    def _cache_is_usable(self, project_id: str) -> bool:
        """Only while this process is the project's live writer.

        Deliberately narrow. Without the lease another process may be appending,
        and a cache would then serve a log that is missing records the caller has
        no way to notice — the same failure the composition chain exists to catch,
        reintroduced for speed.
        """
        if self._lease is None:
            return False
        from sr_agent.orchestrator.lease import LeaseMode

        return self._lease.effective_mode(project_id) in (
            LeaseMode.active_process,
            LeaseMode.paused_reserved,
        )

    def _extend_cache(self, record: MemoryRecord) -> None:
        view = self._cache.get(record.project_id)
        if view is None:
            return
        view.records.append(record)
        stem = self._target_stem(record.target)
        length, _ = view.tips.get(stem, (0, None))
        view.tips[stem] = (length + 1, record.hmac)
        view.max_log_sequence = max(view.max_log_sequence, record.log_sequence or 0)

    def _invalidate_cache(self, project_id: str) -> None:
        self._cache.pop(project_id, None)

    # ── Exactly-once commit (feature 003, FR-006) ───────────────────────────
    # Not pack-facing. `KernelActionExecutor` is the only intended caller: these
    # are the primitives that make a committed transition un-duplicatable, and a
    # second caller would be a second place where that can be got wrong.

    def session_revision(self, project_id: str, session_id: str) -> int:
        """How many dispatch bundles this session has committed.

        Derived by counting the log rather than kept in a counter beside it. A
        counter is a second source of truth that a crash can desynchronize from
        the records it counts -- and it would have to be updated in the same
        window the commit is trying to make atomic.

        Deliberately NOT the append watermark: other durable records (an ingested
        response, a checkpoint) advance `log_sequence` but must not move this, or
        an unrelated append would invalidate a caller's in-flight expectation.
        """
        return sum(
            1
            for record in self._all_records(project_id)
            if record.session_id == session_id
            and record.payload_kind == "dispatch_commit"
        )

    def find_committed_bundle(
        self,
        project_id: str,
        session_id: str,
        operation_id: str | None = None,
        transition_key: str | None = None,
    ) -> MemoryRecord | None:
        """Return the bundle already committed for this transition, if any.

        Callable *before* dispatch, which is what makes exactly-once reachable
        for a non-idempotent effect: deciding after the fact is too late, because
        by then the analyzer has already run a second time.

        Either id matches, so a retry cannot slip through on the one the caller
        happened not to pass.
        """
        if operation_id is None and transition_key is None:
            raise ValueError("one of operation_id / transition_key is required")
        self._recover_torn_tails(project_id)
        for record in self._all_records(project_id):
            if record.session_id != session_id:
                continue
            if record.payload_kind != "dispatch_commit":
                continue
            payload = record.payload or {}
            if operation_id is not None and payload.get("operation_id") == operation_id:
                return record
            if transition_key is not None and payload.get("transition_key") == transition_key:
                return record
        return None

    def commit_if_absent(
        self,
        *,
        project_id: str,
        target: str,
        session_id: str,
        tool: str,
        operation_id: str,
        transition_key: str,
        expected_revision: int,
        payloads: "Sequence[DispatchPayload]",
    ) -> MemoryRecord:
        """Persist a dispatch bundle exactly once, or return the existing one.

        The bundle is a single record, not one record per payload: a crash can
        then never leave half a transition durable, and there is one signature
        covering the set the pack actually returned.
        """
        existing = self.find_committed_bundle(
            project_id, session_id, operation_id, transition_key
        )
        if existing is not None:
            return existing

        self._validate_payload_limits(payloads)

        current = self.session_revision(project_id, session_id)
        if expected_revision != current:
            raise MemoryWriteError(
                f"Refusing commit for session {session_id!r}: expected revision "
                f"{expected_revision}, session is at {current}. Another commit "
                "landed first; re-read and retry."
            )

        return self.write(
            MemoryRecord(
                project_id=project_id,
                target=target,
                source_type=SourceType.tool_output,
                tool=tool,
                session_id=session_id,
                payload_kind="dispatch_commit",
                payload={
                    "operation_id": operation_id,
                    "transition_key": transition_key,
                    "payloads": [p.body for p in payloads],
                },
            )
        )

    @staticmethod
    def _validate_payload_limits(payloads: "Sequence[DispatchPayload]") -> None:
        """Refuse the WHOLE transition on oversize, never trim it.

        Dropping the offending payload and committing the rest would produce a
        bundle that looks complete and is not, and the pack has no way to notice
        the loss.
        """
        from sr_agent.models.dispatch import MAX_PAYLOAD_BODY_BYTES, MAX_PAYLOADS

        if len(payloads) > MAX_PAYLOADS:
            raise MemoryWriteError(
                f"Refusing commit: {len(payloads)} payloads exceeds the limit of "
                f"{MAX_PAYLOADS}; nothing was persisted."
            )
        for index, payload in enumerate(payloads):
            size = len(canonical_bytes(payload.body))
            if size > MAX_PAYLOAD_BODY_BYTES:
                raise MemoryWriteError(
                    f"Refusing commit: payload {index} is {size} encoded bytes, "
                    f"over the limit of {MAX_PAYLOAD_BODY_BYTES}; nothing was "
                    "persisted."
                )

    # ── External responses (feature 003, FR-002c) ───────────────────────────

    def find_external_response(
        self,
        project_id: str,
        session_id: str,
        operation_id: str,
        correlation_id: str,
    ) -> MemoryRecord | None:
        for record in self._all_records(project_id):
            if record.session_id != session_id:
                continue
            if record.payload_kind != "external_response":
                continue
            payload = record.payload or {}
            if (payload.get("operation_id"), payload.get("correlation_id")) == (
                operation_id,
                correlation_id,
            ):
                return record
        return None

    def put_external_response_if_absent(
        self,
        *,
        project_id: str,
        target: str,
        session_id: str,
        operation_id: str,
        correlation_id: str,
        body: dict,
        source_type: SourceType = SourceType.external_llm_output,
    ) -> MemoryRecord:
        """Ingest an external answer once; afterwards the record is the truth.

        A paused turn resumes by reading this record, never the source artifact.
        That is the point: re-reading the file makes an operator decision mutable
        between the pause and the resume, and nothing downstream could tell that
        a `deny` had become an `approve`.

        There is deliberately no `body_digest` parameter. A digest the caller
        asserts proves only that the caller is self-consistent; equality here is
        decided on digests computed from bytes the kernel holds (D30). The source
        artifact is never deleted or moved.
        """
        self._recover_torn_tails(project_id)
        digest = canonical_digest(body)

        stored = self.find_external_response(
            project_id, session_id, operation_id, correlation_id
        )
        if stored is not None:
            stored_body = (stored.payload or {}).get("body")
            if canonical_digest(stored_body) == digest:
                return stored
            raise ExternalResponseConflict(
                f"Response for operation {operation_id!r} / correlation "
                f"{correlation_id!r} was already ingested with a different body. "
                "The stored record stands; nothing was overwritten."
            )

        return self.write(
            MemoryRecord(
                project_id=project_id,
                target=target,
                source_type=source_type,
                session_id=session_id,
                payload_kind="external_response",
                payload={
                    "operation_id": operation_id,
                    "correlation_id": correlation_id,
                    "body": body,
                    "body_digest": digest,
                },
            )
        )

    # ── Durability ──────────────────────────────────────────────────────────

    @staticmethod
    def _append_durably(path: Path, line: str) -> None:
        """Append one line and force it to disk before returning.

        Without the fsync, a crash can leave the caller believing a commit
        happened while the bytes are still in the page cache. The kernel promises
        exactly-once on committed transitions, and that promise is only as strong
        as this call.
        """
        with path.open("a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
        try:
            dir_fd = os.open(path.parent, os.O_RDONLY)
        except OSError:
            return   # platform will not let us open a directory; the file is synced
        try:
            os.fsync(dir_fd)
        except OSError:
            pass     # directory fsync is not supported everywhere; not fatal
        finally:
            os.close(dir_fd)

    def _recover_torn_tails(self, project_id: str) -> None:
        """Truncate any partial trailing line in this project's target files.

        A complete line ends with a newline; anything after the last newline is
        a write that did not finish, so it never became a record and nothing has
        been told it committed. Dropping it is safe; keeping it is not.
        """
        for path in self._target_paths(project_id):
            if not path.exists():
                continue
            with path.open("rb+") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                if size == 0:
                    continue
                # Check the last byte before reading anything else: an intact file
                # is the overwhelmingly common case and must not cost a full read,
                # since this runs before every append.
                f.seek(size - 1)
                if f.read(1) == b"\n":
                    continue
                f.seek(0)
                cut = f.read().rfind(b"\n") + 1   # 0 when the file is one torn line
                f.truncate(cut)
                f.flush()
                os.fsync(f.fileno())
                self._invalidate_cache(path.parent.name)

    def _target_paths(self, project_id: str) -> list[Path]:
        """Every target file of a project, including ones only the head names."""
        project_dir = self._root / project_id
        if not project_dir.exists():
            return []
        paths = sorted(project_dir.glob("*.jsonl"))
        for stem in sorted(self._read_head(project_id) or {}):
            candidate = project_dir / f"{stem}.jsonl"
            if candidate not in paths:
                paths.append(candidate)
        return paths

    def _next_log_sequence(self, project_id: str) -> int:
        """Allocate the next project-wide append number.

        Derived from the log itself — max + 1 over the union of the project's
        target files — rather than from a counter or reservation file. A counter
        is a second source of truth that a crash can desynchronize from the data
        it counts, and it would have to be kept consistent with the very thing it
        is supposed to order.

        Fails closed on a duplicate or a gap. Both mean the log is no longer the
        sequence it claims to be, and a watermark over an inconsistent log would
        silently serve a snapshot missing records the caller has no way to notice.
        """
        records, break_reason = self._authenticated_project(project_id)
        if break_reason is not None:
            # Fail closed rather than treating a broken project as an empty one:
            # allocating from a log that does not verify would hand out a number
            # that may already be in use in the part we cannot read.
            raise MemoryChainError(
                f"Refusing to append to project {project_id!r}: memory composition "
                f"does not verify ({break_reason}). Run `sr-agent memory verify`."
            )
        view = self._cache.get(project_id)
        if view is not None and view.sequences_validated:
            # Growth since the check is maintained incrementally by the only
            # writer there is, so it cannot have gone ragged behind our back.
            return view.max_log_sequence + 1

        sequences = [
            record.log_sequence for record in records if record.log_sequence is not None
        ]
        if view is not None:
            view.sequences_validated = True
        if not sequences:
            return 1
        found = sorted(sequences)
        if len(set(found)) != len(found):
            duplicates = sorted({s for s in found if found.count(s) > 1})
            raise MemoryWriteError(
                f"Refusing to append to project {project_id!r}: duplicate "
                f"log_sequence {duplicates}. Run `sr-agent memory verify`."
            )
        expected = list(range(1, len(found) + 1))
        if found != expected:
            missing = sorted(set(expected) - set(found))
            raise MemoryWriteError(
                f"Refusing to append to project {project_id!r}: log_sequence is "
                f"not contiguous, missing {missing}, highest {found[-1]}. "
                "Run `sr-agent memory verify`."
            )
        return found[-1] + 1

    def _chain_tip(self, path: Path) -> tuple[int, str | None]:
        """Return (next seq, previous hmac) for the file, or refuse to append.

        The tip is taken from the reconstructed chain, not from the file's last
        line: a raw tail read would let anyone able to append a line choose what
        the next genuine record links to. If the chain does not reconstruct, the
        write is refused rather than silently re-based onto a fresh chain, which
        would leave a store nobody can check afterwards.
        """
        project_id = path.parent.name
        if self._cache_is_usable(project_id):
            self._authenticated_project(project_id)   # populates the view
            view = self._cache.get(project_id)
            if view is not None:
                return view.tips.get(path.stem, (0, None))

        verified, break_reason = self._read_authenticated(path)
        if break_reason is not None:
            raise MemoryChainError(
                f"Refusing to append to {path.name}: {break_reason}. Run "
                "`sr-agent memory verify` and repair or re-key the store first."
            )
        if not verified:
            return 0, None
        return len(verified), verified[-1].hmac

    def load(
        self,
        project_id: str,
        target: str,
        principal: "Principal | None" = None,
    ) -> list[MemoryRecord]:
        """Load records, verify each HMAC, apply supersedes chain.

        Records with invalid HMAC are silently dropped — no exception, no log
        at WARNING+ level. This avoids giving an attacker a tamper oracle.

        If ``principal`` is given, ``project_id`` must match it — a mismatch
        raises PrincipalMismatch (cross-principal read attempt).
        """
        if principal is not None and project_id != principal.project_id:
            raise PrincipalMismatch(
                f"Load rejected: requested project_id={project_id!r} "
                f"!= principal project_id={principal.project_id!r}"
            )

        # Corrections are resolved over the whole project, then the view is
        # narrowed back to the requested target. Resolving per file is what made
        # a correction filed under a different target silently ineffective. The
        # candidate set never leaves memory/<project_id>/, so widening the scope
        # to the project does not widen it past the isolation boundary.
        surviving = self._apply_supersedes(self._all_records(project_id))
        return [r for r in surviving if r.target == target]

    def load_for_principal(self, principal: "Principal") -> list[MemoryRecord]:
        """Load all records across all targets for a principal's project.

        Scoping is enforced at the directory level: only files under
        memory/<principal.project_id>/ are ever opened, so records belonging
        to another principal are never read — not even to verify their HMAC.
        """
        return self._apply_supersedes(self._all_records(principal.project_id))

    def _all_records(self, project_id: str) -> list[MemoryRecord]:
        """Authenticated records of one project, every target, in file order.

        This is the widest set a supersede may ever act on. It is bounded by the
        project directory, so a correction can reach across targets but never
        across projects.

        Returns an empty list when composition is broken. Callers that must tell
        "broken" from "empty" apart — the snapshot builder does, because serving
        an empty projection would look like a fresh session — use
        `_authenticated_project` instead.
        """
        records, break_reason = self._authenticated_project(project_id)
        return [] if break_reason is not None else records

    def _authenticated_project(
        self, project_id: str
    ) -> tuple[list[MemoryRecord], str | None]:
        """Verified records of a project, plus why composition does not hold."""
        view = self._cache.get(project_id) if self._cache_is_usable(project_id) else None
        if view is not None:
            return view.records, None

        project_dir = self._root / project_id
        if not project_dir.exists():
            return [], None

        records: list[MemoryRecord] = []
        for path in self._target_paths(project_id):
            found, break_reason = self._read_authenticated(path)
            if break_reason is not None:
                # Project-wide fail-closed, not just this file. Corrections may
                # be filed under a different target than the record they cancel,
                # so serving the other targets while one is broken would let an
                # attacker resurrect a record by damaging the file that holds its
                # correction — the same hole, one level up.
                self._report_chain_break(path, break_reason)
                logger.warning(
                    "withholding all of project %s from context — composition "
                    "of one of its targets is broken",
                    project_id,
                )
                return [], break_reason
            records.extend(found)
        if self._cache_is_usable(project_id):
            tips: dict[str, tuple[int, str | None]] = {}
            for record in records:
                stem = self._target_stem(record.target)
                length, _ = tips.get(stem, (0, None))
                tips[stem] = (length + 1, record.hmac)
            self._cache[project_id] = _ProjectView(
                records=records,
                tips=tips,
                max_log_sequence=max((r.log_sequence or 0 for r in records), default=0),
            )
        return records, None

    # ── The pack's read seam (feature 003, FR-009a) ─────────────────────────

    def snapshot(
        self,
        *,
        project_id: str,
        session_id: str,
        as_of_sequence: int | None = None,
    ) -> "MemorySnapshot":
        """Build the immutable prior-state view handed to a pack.

        The six steps below are authoritative and their ORDER is the contract.
        Two rearrangements look equivalent and are not:

        * superseding before cutting lets a correction at `S+1` retroactively
          change the snapshot at `S`, so the same watermark would name two
          different histories;
        * selecting kinds before resolving drops a correction whose own kind the
          pack does not consume, so the record it cancels silently survives.

        This is also why the existing `_apply_supersedes(_all_records(...))`
        loader is deliberately not reused here.
        """
        from sr_agent.models.dispatch import SNAPSHOT_KINDS, MemorySnapshot, SnapshotItem

        self._recover_torn_tails(project_id)

        # 1. Verify signatures and `004`'s composition chain. A break withholds
        #    the whole project rather than serving the part that still verifies —
        #    that partial view is precisely the resurrected-record picture the
        #    chain exists to expose.
        records, break_reason = self._authenticated_project(project_id)
        if break_reason is not None:
            raise MemoryCompositionBreak(
                f"Refusing to build a snapshot for project {project_id!r}: memory "
                "composition does not verify. See the operator log and run "
                "`sr-agent memory verify`."
            )

        # 2. Pin the watermark. A future value is refused, never clamped: clamping
        #    would make one watermark denote a growing history.
        current_max = max((r.log_sequence or 0 for r in records), default=0)
        if as_of_sequence is None:
            as_of_sequence = current_max
        elif as_of_sequence < 0 or as_of_sequence > current_max:
            raise SnapshotWatermarkError(
                f"Refusing snapshot at as_of_sequence={as_of_sequence}: the project "
                f"log currently reaches {current_max}. A future watermark is not "
                "served, because later appends would change what that same "
                "watermark means."
            )

        # 3-4. Cut the prefix, then resolve corrections across it PROJECT-WIDE,
        #      with every correction carrier still present. Resolution is narrowed
        #      neither by target nor by session (`004` FR-007): a correction may
        #      live under a different target than the record it cancels, so
        #      narrowing would let an attacker resurrect a record by isolating the
        #      file that holds its correction.
        prefix = [r for r in records if (r.log_sequence or 0) <= as_of_sequence]
        surviving = self._apply_supersedes(prefix)

        # 5-6. Session scoping decides what the pack RECEIVES; it is not a
        #      resolution boundary. Kind selection is last, for the same reason.
        selected = [
            r for r in surviving
            if r.session_id == session_id and self._snapshot_kind(r) in SNAPSHOT_KINDS
        ]
        selected.sort(key=lambda r: r.log_sequence or 0)

        items = tuple(
            SnapshotItem(
                record_id=r.record_id,
                log_sequence=r.log_sequence or 0,
                kind=self._snapshot_kind(r),
                source_type=r.source_type.value,
                timestamp=r.timestamp.isoformat(),
                operation_id=(r.payload or {}).get("operation_id"),
                body=self._snapshot_body(r),
            )
            for r in selected
        )
        measured_bytes = len(canonical_bytes([i.model_dump(mode="json") for i in items]))
        self._enforce_snapshot_capacity(len(items), measured_bytes)

        return MemorySnapshot(
            session_id=session_id,
            as_of_sequence=as_of_sequence,
            measured_bytes=measured_bytes,
            items=items,
        )

    @staticmethod
    def _snapshot_kind(record: MemoryRecord) -> str:
        """The record's kind for snapshot purposes.

        A finding predates `payload_kind` and lives in its own field, so it has
        no kind string of its own to read.
        """
        if record.payload_kind:
            return record.payload_kind
        return "finding" if record.finding else "other"

    @staticmethod
    def _snapshot_body(record: MemoryRecord) -> dict:
        return record.finding if record.finding else (record.payload or {})

    @staticmethod
    def _enforce_snapshot_capacity(item_count: int, measured_bytes: int) -> None:
        """Fail closed over the cap; never truncate, sample, or drop the oldest.

        A silently shortened history is wrong in the one direction the pack
        cannot detect — it would reason confidently about a past it was never
        shown, and nothing in the projection would look unusual.
        """
        if item_count > MAX_SNAPSHOT_ITEMS:
            raise SnapshotCapacityExceeded(
                f"Snapshot exceeds the item limit: {item_count} records against a "
                f"limit of {MAX_SNAPSHOT_ITEMS}. Nothing was truncated. Remedy: "
                "`complete_session`, then continue in a new session."
            )
        if measured_bytes > MAX_SNAPSHOT_BYTES:
            raise SnapshotCapacityExceeded(
                f"Snapshot exceeds the byte limit: {measured_bytes} canonical bytes "
                f"against a limit of {MAX_SNAPSHOT_BYTES}. Nothing was truncated. "
                "Remedy: `complete_session`, then continue in a new session."
            )

    def _read_authenticated(self, path: Path) -> tuple[list[MemoryRecord], str | None]:
        """Verify one target file. Returns (records, break_reason).

        Supersede resolution deliberately does NOT happen at this level. A
        correction may be filed under a different target than the record it
        overrides, and this function sees one target's file — resolving here
        made such a correction silently ineffective everywhere. Both read paths
        resolve over the whole project instead.

        Two different failures live here and they are handled differently on
        purpose:

        * A line that does not verify is dropped **silently** — no WARNING, no
          exception, no change in what the agent does. That is the tamper-oracle
          rule and it is unchanged: someone probing the store with forged lines
          learns nothing, because an unverified line makes no claims we act on.

        * The chain not reconstructing is a **composition** failure: the set of
          records is not the set that was signed. That is reported and the file
          is withheld.

        The two never get confused, because the chain is walked over verified
        records only, and a break is only ever raised by a claim that a *validly
        signed* record makes — its position and its back-link. An attacker who
        appends forged lines cannot cause a break; they are simply not in the
        walk. An attacker who removes or alters a genuine record cannot avoid
        one, because the surviving genuine records still commit to it.
        """
        project_id = path.parent.name
        head = self._read_head(project_id)
        entry = (head or {}).get(path.stem)

        if not path.exists():
            # A head entry with no file at all is a deletion, not an empty store.
            if entry and int(entry["length"]) > 0:
                return [], f"chain head expects {entry['length']} record(s), file is gone"
            return [], None

        verified: list[MemoryRecord] = []
        with path.open(encoding="utf-8") as f:
            for line_no, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    record = MemoryRecord.model_validate(data)
                except Exception:
                    logger.debug("Skipping unparseable record at line %d", line_no)
                    continue

                if record.hmac is None:
                    logger.debug("Dropping unsigned record %s", record.record_id)
                    continue

                fields = record.fields_for_hmac()
                if not hmac_module.verify(fields, record.hmac, self._secret_key):
                    # Silent drop — do not log at WARNING to avoid tamper oracle
                    logger.debug("Dropping record %s: HMAC mismatch", record.record_id)
                    continue

                verified.append(record)

        return verified, self._chain_break_reason(verified, entry)

    @staticmethod
    def _chain_break_reason(
        verified: list[MemoryRecord], entry: dict | None
    ) -> str | None:
        """Why the verified records do not reconstruct the signed chain, or None."""
        previous: MemoryRecord | None = None
        for record in verified:
            if previous is None:
                linked = record.seq == 0 and record.chain_prev is None
            else:
                linked = (
                    record.seq == (previous.seq or 0) + 1
                    and record.chain_prev is not None
                    and record.hmac is not None
                    and hmac_module.constant_time_equals(
                        record.chain_prev, previous.hmac or ""
                    )
                )
            if not linked:
                return (
                    f"record at seq={record.seq} does not follow "
                    f"seq={previous.seq if previous else None}"
                )
            previous = record

        if entry is None:
            # No signed head. An empty (or entirely unauthenticated) file is a
            # store that never got written; anything else is a head that was
            # removed, and we cannot tell how much of the file went with it.
            return None if not verified else "no verified chain head for this target"

        # The head attests a PREFIX, not an exact length: at least this many
        # records, with this signature at that position. Growth beyond it is
        # accepted because it can only be genuine — a record past the head still
        # has to carry a valid signature and a landing back-link, neither of
        # which is reachable without the key. Truncation, the case that matters,
        # is still caught, because it makes the file SHORTER than the head.
        #
        # This is also what makes the two-file write survivable: write() appends
        # the record and then updates the head, and a crash in between leaves
        # exactly one un-attested but genuine record. Demanding an exact match
        # would turn that crash into a permanently withheld project.
        expected_length = int(entry["length"])
        if len(verified) < expected_length:
            return (
                f"chain head attests {expected_length} record(s), "
                f"{len(verified)} reconstruct"
            )
        if expected_length == 0:
            return None
        anchor = verified[expected_length - 1]
        if not hmac_module.constant_time_equals(anchor.hmac or "", entry["last_hmac"]):
            return "chain head signature does not match the record at that position"
        return None

    def _report_chain_break(self, path: Path, reason: str) -> None:
        """Operator-channel signal for a composition break. Fail-closed follows.

        This is the one place the load path is allowed to be loud, and the line
        between it and the silent drop above is worth stating, because it is the
        thing someone will be tempted to "simplify" later.

        A signature failure is kept silent because reacting to it hands an
        attacker a probe: append a guess, watch what the agent does, learn
        whether the guess was right. This signal cannot be used that way — it
        does not fire on a failed signature. It fires only when records that ARE
        correctly signed disagree with each other about what the file contains,
        which is something an attacker without the key can cause but cannot
        cause *selectively* and cannot read anything out of.

        It is observable, and we accept that: the attacker learns "noticed".
        What they do not get is the thing the whole exercise was for — a store
        rolled back to a state of their choosing, presented to the agent as
        fact. One bit of "noticed" is a good trade for that. The signal goes to
        the operator log, never into model context, and the agent's own
        behaviour is simply the absence of memory.
        """
        logger.warning(
            "memory composition break in %s: %s — file withheld from context "
            "(fail-closed); run `sr-agent memory verify` for the full picture",
            path.name, reason,
        )

    def verify_integrity(self, project_id: str) -> IntegrityReport:
        """Scan all records for a project and count valid vs tampered.

        Unlike load(), this REPORTS invalid records. It is a human-run audit
        tool (`sr-agent memory verify`), not the agent's context-loading path,
        so there is no tamper-oracle concern — the operator needs the count for
        incident response.
        """
        report = IntegrityReport(project_id=project_id)
        project_dir = self._root / project_id
        if not project_dir.exists():
            return report

        head = self._read_head(project_id) or {}
        paths = sorted(project_dir.glob("*.jsonl"))
        for stem in sorted(head):
            candidate = project_dir / f"{stem}.jsonl"
            if candidate not in paths:
                paths.append(candidate)   # named by the head, gone from disk

        for path in paths:
            t_total = t_valid = t_invalid = 0
            if path.exists():
                with path.open(encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        t_total += 1
                        if self._line_is_valid(line):
                            t_valid += 1
                        else:
                            t_invalid += 1
            report.per_target[path.stem] = (t_total, t_valid, t_invalid)
            report.total += t_total
            report.valid += t_valid
            report.invalid += t_invalid

            # Composition, not content: does the record SET still reconstruct
            # the signed chain? This is what shows a removal, which the counts
            # above cannot — a deleted record leaves nothing behind to count.
            _, break_reason = self._read_authenticated(path)
            if break_reason is not None:
                report.chain_breaks[path.stem] = break_reason

        return report

    def _line_is_valid(self, line: str) -> bool:
        """True if a raw JSONL line is a well-formed, correctly-signed record."""
        try:
            record = MemoryRecord.model_validate(json.loads(line))
        except Exception:
            return False
        if record.hmac is None:
            return False
        return hmac_module.verify(record.fields_for_hmac(), record.hmac, self._secret_key)

    @staticmethod
    def _apply_supersedes(records: list[MemoryRecord]) -> list[MemoryRecord]:
        """Remove records that a newer correction overrides.

        Only records in ``records`` may cancel anything: the caller has already
        verified every one of them. Reading `supersedes` off an unauthenticated
        line would hand anyone with write access to the file a way to delete any
        record by id, without the key — strictly worse than the resurrection it
        would fix.
        """
        superseded_ids = {r.supersedes for r in records if r.supersedes}
        return [r for r in records if r.record_id not in superseded_ids]

    def _enforce_status_rules(self, record: MemoryRecord) -> None:
        """Raise if privileged status / supersedes is set by an untrusted source.

        Membership comes from the pack-declared set bound at construction
        (`self._privileged_statuses`, D5), not a kernel-hardcoded constant.
        """
        if record.supersedes and record.source_type != SourceType.human_input:
            raise MemoryWriteError(
                f"'supersedes' field requires source_type=human_input, "
                f"got {record.source_type.value!r}. "
                "Corrections to existing records require human authority."
            )

        if record.status_change is None:
            return

        new_status = record.status_change.new_status
        if new_status in self._privileged_statuses:
            if record.source_type != SourceType.human_input:
                raise MemoryWriteError(
                    f"Status '{new_status}' requires source_type=human_input, "
                    f"got {record.source_type.value!r}. "
                    "This is a security gate — only human operators may set this status."
                )
