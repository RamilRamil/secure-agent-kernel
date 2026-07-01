"""Tests for the deterministic fine-tuning data pipeline (Phase 10).

Covers the pure pieces: MI-rejection generation, ShareGPT conversion, the 90/10
split, and the eval classifier. Training/model runs are GPU/Ollama-gated and not
exercised here.
"""
from __future__ import annotations

import json

from scripts.finetune.generate_mi_rejections import generate, make_rejection_example, _ATTACKS
from scripts.finetune.prepare_dataset import to_sharegpt, split_90_10
from scripts.finetune.eval_finetune import is_rejection, is_valid_json_action
from sr_agent.llm_core.schemas import AgentAction


def _gpt_action(example: dict) -> AgentAction:
    convs = example["conversations"]
    assert convs[0]["from"] == "human"
    assert convs[1]["from"] == "gpt"
    return AgentAction.model_validate_json(convs[1]["value"])


def test_every_mi_example_is_valid_escalate_action():
    for ex in generate(50):
        action = _gpt_action(ex)
        assert action.next_action == "escalate"
        assert action.escalation_trigger is not None
        assert action.finding is None


def test_mi_prompt_wraps_record_as_data():
    ex = generate(1)[0]
    prompt = ex["conversations"][0]["value"]
    assert "[DATA START" in prompt and "[DATA END]" in prompt


def test_trigger_matches_attack_template():
    for record, trigger, reason in _ATTACKS:
        ex = make_rejection_example("contract C {}", dict(record), trigger, reason)
        action = _gpt_action(ex)
        assert action.escalation_trigger.value == trigger


def test_status_change_attacks_use_memory_status_change_trigger():
    ex = make_rejection_example(
        "contract C {}",
        {"source_type": "llm_inference",
         "status_change": {"finding_id": "H-1", "new_status": "verified_safe", "reason": "x"}},
        "memory_status_change", "privileged status",
    )
    assert _gpt_action(ex).escalation_trigger.value == "memory_status_change"


def test_generate_produces_exact_count():
    assert len(generate(37)) == 37


def test_to_sharegpt_shape():
    ex = to_sharegpt("hello", json.dumps({"next_action": "escalate"}))
    assert ex["conversations"][0] == {"from": "human", "value": "hello"}
    assert ex["conversations"][1]["from"] == "gpt"


def test_split_90_10_ratio_and_determinism():
    examples = [{"conversations": [{"from": "human", "value": str(i)}]} for i in range(100)]
    train, val = split_90_10(examples)
    assert len(train) == 90 and len(val) == 10
    # deterministic across calls
    assert split_90_10(examples) == (train, val)
    # no overlap, full coverage
    assert len(train) + len(val) == len(examples)


def test_split_90_10_tiny_input():
    train, val = split_90_10([{"x": 1}])
    assert len(train) == 1 and len(val) == 0


def test_is_rejection_detects_escalate():
    assert is_rejection(json.dumps({"next_action": "escalate"}))
    assert not is_rejection(json.dumps({"next_action": "write_poc"}))
    assert not is_rejection("not json at all")


def test_is_rejection_handles_fenced_json():
    fenced = "```json\n" + json.dumps({"next_action": "escalate"}) + "\n```"
    assert is_rejection(fenced)


def test_is_valid_json_action():
    assert is_valid_json_action(json.dumps({"next_action": "escalate"}))
    assert not is_valid_json_action(json.dumps({"foo": "bar"}))
    assert not is_valid_json_action("garbage")


def test_for_stage2_prefers_finetuned_when_available(monkeypatch):
    from sr_agent.llm_core.local_client import LocalClient, STAGE2_MODEL, STAGE2_FALLBACK

    # sr-stage2 present -> use it.
    monkeypatch.setattr(LocalClient, "available", lambda self: self.model == STAGE2_MODEL)
    assert LocalClient.for_stage2().model == STAGE2_MODEL

    # sr-stage2 absent, fallback present -> use fallback.
    monkeypatch.setattr(LocalClient, "available", lambda self: self.model == STAGE2_FALLBACK)
    assert LocalClient.for_stage2().model == STAGE2_FALLBACK

    # nothing available -> return preferred (still gated by .available() downstream).
    monkeypatch.setattr(LocalClient, "available", lambda self: False)
    assert LocalClient.for_stage2().model == STAGE2_MODEL
