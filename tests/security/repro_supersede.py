"""Reproduction for the four supersede-resurrection scenarios.

Run:  .venv/bin/python tests/security/repro_supersede.py
Prints one line per scenario: the texts the model would actually see.
"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from sr_agent.memory.episodic import EpisodicMemory
from sr_agent.models.memory import MemoryRecord, SourceType
from sr_agent.models.principal import Principal

SECRET = b"repro-secret-key-32-bytes-exact!"
PRINCIPAL = Principal(user_id="u", platform="cli", project_id="proj1")


def _fact(text: str, target: str = "Vault.sol", **kw) -> MemoryRecord:
    return MemoryRecord(
        project_id="proj1",
        target=target,
        source_type=kw.pop("source_type", SourceType.tool_output),
        tool="run_slither",
        session_id="s1",
        payload={"text": text},
        payload_kind="fact",
        **kw,
    )


def _texts(records) -> list[str]:
    return [r.payload["text"] for r in records]


def _fresh() -> tuple[EpisodicMemory, Path]:
    d = Path(tempfile.mkdtemp())
    return EpisodicMemory(d, SECRET), d


def _planted_then_corrected(mem):
    planted = mem.write(_fact("X is SAFE (planted)"))
    correction = mem.write(
        _fact(
            "CORRECTION: X is VULNERABLE",
            source_type=SourceType.human_input,
            supersedes=planted.record_id,
        )
    )
    return planted, correction


def scenario_1_baseline():
    mem, d = _fresh()
    _planted_then_corrected(mem)
    out = _texts(mem.load("proj1", "Vault.sol"))
    shutil.rmtree(d)
    return out


def scenario_2_correction_tampered():
    mem, d = _fresh()
    _planted_then_corrected(mem)
    path = mem._path("proj1", "Vault.sol")
    lines = path.read_text().splitlines()
    data = json.loads(lines[-1])          # the correction
    data["payload"] = {"text": "noise"}   # break its signature, keep the line
    lines[-1] = json.dumps(data)
    path.write_text("\n".join(lines) + "\n")
    out = _texts(mem.load("proj1", "Vault.sol"))
    shutil.rmtree(d)
    return out


def scenario_3_correction_deleted():
    mem, d = _fresh()
    _planted_then_corrected(mem)
    path = mem._path("proj1", "Vault.sol")
    lines = path.read_text().splitlines()
    path.write_text("\n".join(lines[:-1]) + "\n")   # drop the correction line
    out = _texts(mem.load("proj1", "Vault.sol"))
    shutil.rmtree(d)
    return out


def _cross_target_store():
    mem, d = _fresh()
    stale = mem.write(_fact("stale fact in t1", target="t1.sol"))
    mem.write(
        _fact(
            "CORRECTION filed under t2",
            target="t2.sol",
            source_type=SourceType.human_input,
            supersedes=stale.record_id,
        )
    )
    return mem, d


def scenario_4_cross_target_load():
    """Principal-scoped single-target read — the correction lives under t2."""
    mem, d = _cross_target_store()
    out = _texts(mem.load("proj1", "t1.sol", principal=PRINCIPAL))
    shutil.rmtree(d)
    return out


def scenario_4b_cross_target_load_no_principal():
    """Same read with no principal: a single-file API, single-file evidence."""
    mem, d = _cross_target_store()
    out = _texts(mem.load("proj1", "t1.sol"))
    shutil.rmtree(d)
    return out


def scenario_5_cross_target_principal():
    mem, d = _cross_target_store()
    out = _texts(mem.load_for_principal(PRINCIPAL))
    shutil.rmtree(d)
    return out


def scenario_6_paraphrase():
    """The same claim restated in other words, with no `supersedes` link.

    Nothing here is tampered: the human correction is filed honestly, but it
    can only name one record_id. The paraphrase it does not name survives, and
    so does the original. This is the semantic limit, not an integrity bug.
    """
    mem, d = _fresh()
    mem.write(_fact("X is SAFE"))
    mem.write(_fact("Contract X poses no risk"))     # same claim, other words
    mem.write(_fact("CORRECTION: X is VULNERABLE", source_type=SourceType.human_input))
    out = _texts(mem.load("proj1", "Vault.sol"))
    shutil.rmtree(d)
    return out


SCENARIOS = [
    ("1. baseline                ", scenario_1_baseline),
    ("2. correction tampered     ", scenario_2_correction_tampered),
    ("3. correction deleted      ", scenario_3_correction_deleted),
    ("4. cross-target load       ", scenario_4_cross_target_load),
    ("4b. cross-target, no princ.", scenario_4b_cross_target_load_no_principal),
    ("5. load_for_principal      ", scenario_5_cross_target_principal),
    ("6. paraphrase, no supersede", scenario_6_paraphrase),
]


if __name__ == "__main__":
    for label, fn in SCENARIOS:
        print(f"{label} -> {fn()}")
