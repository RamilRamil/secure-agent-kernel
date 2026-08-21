from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from sr_agent.memory import hmac as hmac_module
from sr_agent.models.memory import MemoryRecord, SourceType

if TYPE_CHECKING:
    from sr_agent.models.principal import Principal

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
    ) -> None:
        self._root = memory_root
        self._secret_key = secret_key
        # Privileged-status set composed by the kernel from the active pack's
        # `privileged_statuses` and BOUND here at session construction (D5).
        # Immutable for the session — no model turn or tool result may widen or
        # narrow it. Empty means "this pack has no privileged statuses" (H4),
        # NOT "gate disabled".
        self._privileged_statuses = frozenset(privileged_statuses)

    def _path(self, project_id: str, target: str) -> Path:
        safe_target = target.replace("/", "_").replace(":", "__")
        return self._root / project_id / f"{safe_target}.jsonl"

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

        path = self._path(record.project_id, record.target)
        seq, chain_prev = self._chain_tip(path)

        # Position and back-link are kernel-set and go INSIDE the signature, so
        # the record's place in the file is authenticated along with its content.
        record = record.model_copy(update={"seq": seq, "chain_prev": chain_prev})

        # Orchestrator computes the HMAC — LLM never touches this field
        fields = record.fields_for_hmac()
        signature = hmac_module.sign(fields, self._secret_key)
        record = record.model_copy(update={"hmac": signature})

        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(record.model_dump_json() + "\n")
        self._write_head(record.project_id, path.stem, seq + 1, signature)

        return record

    def _chain_tip(self, path: Path) -> tuple[int, str | None]:
        """Return (next seq, previous hmac) for the file, or refuse to append.

        The tip is taken from the reconstructed chain, not from the file's last
        line: a raw tail read would let anyone able to append a line choose what
        the next genuine record links to. If the chain does not reconstruct, the
        write is refused rather than silently re-based onto a fresh chain, which
        would leave a store nobody can check afterwards.
        """
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
        """
        project_dir = self._root / project_id
        if not project_dir.exists():
            return []

        paths = sorted(project_dir.glob("*.jsonl"))
        head = self._read_head(project_id) or {}
        # A target named by the head with no file left on disk is a deletion, so
        # it has to be walked too — globbing alone would never notice it.
        for stem in sorted(head):
            candidate = project_dir / f"{stem}.jsonl"
            if candidate not in paths:
                paths.append(candidate)

        records: list[MemoryRecord] = []
        for path in paths:
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
                return []
            records.extend(found)
        return records

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
