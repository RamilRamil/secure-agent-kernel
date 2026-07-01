"""Knowledge base tests (T059). Deterministic lexical scorer; no models."""
import pytest
from pathlib import Path

from sr_agent.memory.knowledge import KnowledgeBase, KnowledgeChunk


@pytest.fixture
def kb_root(tmp_path: Path) -> Path:
    (tmp_path / "vuln").mkdir()
    (tmp_path / "vuln" / "reentrancy.md").write_text(
        "# Reentrancy\n\nExternal call before state update lets the callee re-enter.\n"
        "## Fix\n\nUse checks-effects-interactions: update balance then transfer.\n"
    )
    (tmp_path / "vuln" / "oracle.md").write_text(
        "# Oracle manipulation\n\nPrice read then transfer enables flash-loan manipulation.\n"
    )
    return tmp_path


def test_search_ranks_relevant_chunk_first(kb_root):
    kb = KnowledgeBase(root=kb_root)
    results = kb.search("reentrancy external call state update")
    assert results
    assert "Reentrancy" in results[0].heading or "reenter" in results[0].text.lower()


def test_search_returns_knowledge_chunks(kb_root):
    kb = KnowledgeBase(root=kb_root)
    results = kb.search("oracle price manipulation")
    assert all(isinstance(c, KnowledgeChunk) for c in results)
    assert any("Oracle" in c.heading for c in results)


def test_category_filters_scope(kb_root):
    (kb_root / "other").mkdir()
    (kb_root / "other" / "misc.md").write_text("# Misc\n\nreentrancy appears here too.\n")
    kb = KnowledgeBase(root=kb_root)
    results = kb.search("reentrancy", category="vuln")
    assert all(c.source.startswith("vuln/") for c in results)


def test_top_k_limits_results(kb_root):
    kb = KnowledgeBase(root=kb_root)
    assert len(kb.search("transfer", top_k=1)) <= 1


def test_empty_root_returns_empty(tmp_path):
    assert KnowledgeBase(root=tmp_path / "nope").search("anything") == []


def test_injected_embedder_used(kb_root):
    # a fake embedder: vector = [count of 'oracle', count of 'reentrancy']
    def fake_embed(text: str):
        t = text.lower()
        return [float(t.count("oracle")), float(t.count("reentr"))]
    kb = KnowledgeBase(root=kb_root, embedder=fake_embed)
    results = kb.search("oracle")
    assert results
    assert "Oracle" in results[0].heading


def test_seeded_pattern_doc_is_searchable():
    root = Path(__file__).resolve().parents[2] / "knowledge"
    kb = KnowledgeBase(root=root)
    results = kb.search("checks effects interactions reentrancy", category="vulnerability-patterns")
    assert results and any("reentrancy" in c.source.lower() for c in results)
