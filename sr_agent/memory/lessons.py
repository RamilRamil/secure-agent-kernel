"""Experiential knowledge loop (feature 014) — the kernel mechanism.

The harness *proposes* a lesson candidate (capture); the operator *promotes* it via the
out-of-band `sr-agent lessons` CLI (the ONLY caller of `promote`); a promoted lesson is
HMAC-signed and retrieved at build time DATA-wrapped, as a suggestion — never control.

Two stores, mirroring the confirmation/memory trust split:
  - candidate queue  : lessons_root/pending/<sig_id>.json   (UNSIGNED, untrusted proposal)
  - promoted lessons : knowledge_root/lessons/<sig_id>.md    (read by KnowledgeBase)
                     + knowledge_root/lessons/_manifest.jsonl (one signed record each)

Trust (constitution): a lesson keeps an immutable `origin = llm_inference` (a model
drafted it — honest audit); the human's promotion grants `authorization = human_input`
(Principle IV — admits it to the applied KB). Authorization governs KB membership, NOT
instruction-power: retrieval DATA-wraps every lesson regardless of tier (Principle I).
Signing/verify reuse `memory/hmac.py`; a lesson failing verification is dropped silently
at retrieval (no tamper oracle), and reported by `verify()`.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from sr_agent.memory import hmac as hmac_module
from sr_agent.memory.knowledge import Embedder, KnowledgeBase

logger = logging.getLogger(__name__)

# The knowledge-tree subdirectory promoted lessons live in (KnowledgeBase category).
LESSONS_CATEGORY = "lessons"
_MANIFEST_NAME = "_manifest.jsonl"

# DATA-wrap markers — the kernel's Principle-I convention for untrusted reference text.
DATA_START = "[DATA START]"
DATA_END = "[DATA END]"


def sig_id(trigger_signature: Sequence[str]) -> str:
    """Stable dedup/correlation key: sha256 over the canonical sorted signature."""
    canonical = json.dumps(sorted(trigger_signature), default=str).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()[:16]


@dataclass
class LessonCandidate:
    """A proposed, not-yet-trusted lesson awaiting human review (unsigned)."""
    sig_id: str
    trigger_signature: list[str]
    symptom: str
    fix: str
    category: str
    provenance: dict  # {origin: "llm_inference", finding_id, attempt, captured_at}
    status: str = "pending"

    @classmethod
    def create(cls, trigger_signature: Sequence[str], symptom: str, fix: str,
               category: str, finding_id: str | None = None,
               attempt: int | None = None) -> "LessonCandidate":
        sig = sorted(trigger_signature)
        return cls(
            sig_id=sig_id(sig),
            trigger_signature=sig,
            symptom=symptom,
            fix=fix,
            category=category,
            provenance={
                "origin": "llm_inference",  # immutable audit: a model drafted this
                "finding_id": finding_id,
                "attempt": attempt,
                "captured_at": datetime.now(timezone.utc).isoformat(),
            },
        )


@dataclass
class PromotedLesson:
    """A human-approved, HMAC-signed lesson in the retrievable corpus."""
    sig_id: str
    category: str
    content: str
    content_hash: str
    hmac: str
    promoted_at: str
    origin: str = "llm_inference"      # never rewritten by promotion (honest audit)
    authorization: str = "human_input"  # granted by the human's Principle-IV act


@dataclass
class LessonIntegrityReport:
    total: int = 0
    valid: int = 0
    invalid: int = 0
    per_lesson: dict[str, str] = field(default_factory=dict)  # sig_id -> "OK"/"INVALID"

    @property
    def has_invalid(self) -> bool:
        return self.invalid > 0


def _render_lesson_md(cand: LessonCandidate) -> str:
    trigger = "; ".join(cand.trigger_signature)
    return (
        f"# Lesson {cand.sig_id} (category: {cand.category})\n\n"
        f"**Trigger**: {trigger}\n"
        f"**Symptom**: {cand.symptom}\n"
        f"**Fix**: {cand.fix}\n"
    )


class LessonStore:
    """The kernel mechanism. `promote` is the sole writer of the promoted store and is
    called ONLY from the `sr-agent lessons` CLI (out-of-band). The harness may `capture`
    and `retrieve`, never `promote` (pinned by tests/architecture/test_lessons_promote_gate.py)."""

    def __init__(self, lessons_root: Path, knowledge_root: Path, secret_key: bytes,
                 embedder: Embedder | None = None) -> None:
        self._pending = Path(lessons_root) / "pending"
        self._corpus = Path(knowledge_root) / LESSONS_CATEGORY
        self._manifest = self._corpus / _MANIFEST_NAME
        self._secret = secret_key
        self._kb = KnowledgeBase(root=Path(knowledge_root), embedder=embedder)

    # --- paths ---
    def _pending_path(self, sid: str) -> Path:
        return self._pending / f"{sid}.json"

    def _lesson_md(self, sid: str) -> Path:
        return self._corpus / f"{sid}.md"

    # --- signing (reuse memory/hmac.py) ---
    def _fields(self, sid: str, category: str, content: str) -> dict:
        return {"sig_id": sid, "category": category, "content": content}

    def _sign(self, sid: str, category: str, content: str) -> str:
        return hmac_module.sign(self._fields(sid, category, content), self._secret)

    def _verify(self, sid: str, category: str, content: str, mac: str) -> bool:
        return hmac_module.verify(self._fields(sid, category, content), mac, self._secret)

    # --- capture (harness; best-effort, never raises) ---
    def capture(self, candidate: LessonCandidate) -> bool:
        """Write a candidate iff none pending and not already promoted (dedup by sig_id).
        Returns True if newly written. MUST NOT raise (FR-001)."""
        try:
            sid = candidate.sig_id
            if self._pending_path(sid).exists() or self._is_promoted(sid):
                return False
            self._pending.mkdir(parents=True, exist_ok=True)
            self._pending_path(sid).write_text(
                json.dumps(asdict(candidate), indent=2), encoding="utf-8")
            return True
        except Exception as e:  # best-effort — a capture failure never breaks a run
            logger.warning("lesson capture failed (%s); continuing", e)
            return False

    # --- manifest helpers ---
    def _manifest_records(self) -> list[dict]:
        if not self._manifest.exists():
            return []
        out: list[dict] = []
        for line in self._manifest.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
        return out

    def _is_promoted(self, sid: str) -> bool:
        return any(r.get("sig_id") == sid for r in self._manifest_records())

    # --- review (CLI) ---
    def list_pending(self) -> list[LessonCandidate]:
        if not self._pending.exists():
            return []
        out: list[LessonCandidate] = []
        for f in sorted(self._pending.glob("*.json")):
            try:
                out.append(LessonCandidate(**json.loads(f.read_text(encoding="utf-8"))))
            except Exception:
                continue
        return out

    def show(self, sid: str) -> LessonCandidate | None:
        p = self._pending_path(sid)
        if not p.exists():
            return None
        try:
            return LessonCandidate(**json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            return None

    def add(self, candidate: LessonCandidate) -> bool:
        """Operator seeding affordance (G1): queue a hand-authored candidate.
        Unlike capture this is an explicit operator action (raises on failure)."""
        self._pending.mkdir(parents=True, exist_ok=True)
        self._pending_path(candidate.sig_id).write_text(
            json.dumps(asdict(candidate), indent=2), encoding="utf-8")
        return True

    def dismiss(self, sid: str) -> bool:
        p = self._pending_path(sid)
        if p.exists():
            p.unlink()
            return True
        return False

    def promote(self, sid: str, edited_content: str | None = None) -> PromotedLesson:
        """THE gate. Sole writer of the promoted store; called ONLY from the CLI.
        Sets authorization=human_input (Principle IV) while preserving origin=llm_inference.
        `edited_content` lets the operator supply amended markdown at approval."""
        cand = self.show(sid)
        if cand is None and edited_content is None:
            raise FileNotFoundError(f"no pending candidate {sid!r}")
        category = cand.category if cand else "poc-compile"
        content = edited_content if edited_content is not None else _render_lesson_md(cand)

        self._corpus.mkdir(parents=True, exist_ok=True)
        self._lesson_md(sid).write_text(content, encoding="utf-8")
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        record = {
            "sig_id": sid,
            "category": category,
            "content_hash": content_hash,
            "hmac": self._sign(sid, category, content),
            "promoted_at": datetime.now(timezone.utc).isoformat(),
            "origin": "llm_inference",      # honest audit — never rewritten
            "authorization": "human_input",  # the human's Principle-IV act
        }
        with self._manifest.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
        self.dismiss(sid)  # remove the pending candidate
        return PromotedLesson(
            sig_id=sid, category=category, content=content,
            content_hash=content_hash, hmac=record["hmac"],
            promoted_at=record["promoted_at"])

    # --- integrity report (CLI `lessons verify`) ---
    def verify(self) -> LessonIntegrityReport:
        rep = LessonIntegrityReport()
        for r in self._manifest_records():
            sid = r.get("sig_id", "")
            rep.total += 1
            ok = self._lesson_ok(r)
            rep.per_lesson[sid] = "OK" if ok else "INVALID"
            rep.valid += 1 if ok else 0
            rep.invalid += 0 if ok else 1
        return rep

    def _lesson_ok(self, record: dict) -> bool:
        sid = record.get("sig_id", "")
        md = self._lesson_md(sid)
        if not md.exists():
            return False
        content = md.read_text(encoding="utf-8")
        if hashlib.sha256(content.encode("utf-8")).hexdigest() != record.get("content_hash"):
            return False
        return self._verify(sid, record.get("category", ""), content, record.get("hmac", ""))

    # --- retrieval (harness draft/fix; DATA-wrapped, inert when empty) ---
    def retrieve(self, context: str, top_k: int = 3) -> list[str]:
        """Category-scoped search over promoted lessons, verifying each and dropping
        unverified silently. Returns DATA-wrapped strings, or [] when nothing relevant/
        verified (SC-007 inert-when-empty). Never raises."""
        try:
            verified = {r.get("sig_id") for r in self._manifest_records() if self._lesson_ok(r)}
            if not verified:
                return []
            out: list[str] = []
            for ch in self._kb.search(context, category=LESSONS_CATEGORY, top_k=top_k * 2):
                stem = Path(ch.source).stem
                if stem == _MANIFEST_NAME.rsplit(".", 1)[0] or stem not in verified:
                    continue  # drop unverified/non-lesson silently
                out.append(f"{DATA_START}\n{ch.text}\n{DATA_END}")
                if len(out) >= top_k:
                    break
            return out
        except Exception as e:  # retrieval must never break drafting
            logger.warning("lesson retrieve failed (%s); returning none", e)
            return []
