"""Feature 013 US2: pin the SourceType trust-hierarchy ordering.

Principle I (docs/../constitution.md) is authoritative on the trust hierarchy —
`human_input` > `tool_output` > (`external_llm_output` / `human_relayed_tool`) >
`llm_inference`, i.e. model/relay output must NEVER outrank human input or tool
output. That ordering lives as `TRUST_LEVELS` in `sr_agent/models/memory.py` and
was, until now, unguarded by a test: a silent reranking would be a constitution-level
security regression no other test would catch. This pins it.
"""
from sr_agent.models.memory import SourceType, TRUST_LEVELS


def test_trust_hierarchy_ordering_is_pinned():
    r = TRUST_LEVELS
    # human input is the most trusted; a model/relay output must never reach it.
    assert r[SourceType.human_input] > r[SourceType.tool_output]
    # tool output outranks any model/relay output.
    assert r[SourceType.tool_output] > r[SourceType.external_llm_output]
    # relayed output (Claude via the manual bridge) is external_llm_output-tier —
    # automation != authoring; it must NOT be promoted above it.
    assert r[SourceType.external_llm_output] == r[SourceType.human_relayed_tool]
    # raw model inference is the least trusted.
    assert r[SourceType.external_llm_output] > r[SourceType.llm_inference]
    # every source type has a rank (no unranked, silently-trusted source).
    assert set(r) == set(SourceType)


def test_a_reorder_would_violate_the_relations():
    """Documents the failure mode: raising external_llm_output above tool_output (the
    exact promotion Principle I forbids) breaks the pinned relation."""
    tampered = dict(TRUST_LEVELS)
    tampered[SourceType.external_llm_output] = tampered[SourceType.tool_output] + 1
    assert not (tampered[SourceType.tool_output] > tampered[SourceType.external_llm_output])
