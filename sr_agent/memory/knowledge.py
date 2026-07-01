"""Knowledge base over the `knowledge/` directory tree (T059).

Retrieves relevant reference chunks (vulnerability patterns, project-specific
requirements, known-issue notes) to inform analysis and PoC generation. This is
a READ-ONLY reference corpus — NOT the agent's decision memory. Chunks are data:
whoever surfaces them to an LLM wraps them in [DATA START]..[DATA END].

Ranking uses a deterministic lexical scorer by default (no dependency, fully
testable). An optional embedder (a local Ollama embedding model, injected) gives
semantic ranking when available; without it the lexical fallback is used.
"""
from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# An embedder maps text -> a vector; None means "use the lexical fallback".
Embedder = Callable[[str], list[float]]


@dataclass
class KnowledgeChunk:
    source: str          # file path relative to the knowledge root
    heading: str
    text: str
    score: float = 0.0


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall((text or "").lower())


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


@dataclass
class KnowledgeBase:
    root: Path
    embedder: Embedder | None = None

    def _load_chunks(self, category: str | None = None) -> list[KnowledgeChunk]:
        base = self.root / category if category else self.root
        if not base.exists():
            return []
        chunks: list[KnowledgeChunk] = []
        for md in sorted(base.rglob("*.md")):
            try:
                text = md.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            rel = str(md.relative_to(self.root))
            for heading, body in _split_sections(text):
                if body.strip():
                    chunks.append(KnowledgeChunk(source=rel, heading=heading, text=body.strip()))
        return chunks

    def search(
        self, query: str, category: str | None = None, top_k: int = 5
    ) -> list[KnowledgeChunk]:
        """Return the top_k most relevant chunks for a query."""
        chunks = self._load_chunks(category)
        if not chunks:
            return []

        if self.embedder is not None:
            self._score_semantic(query, chunks)
        else:
            self._score_lexical(query, chunks)

        ranked = sorted(chunks, key=lambda c: c.score, reverse=True)
        return [c for c in ranked if c.score > 0][:top_k]

    def _score_lexical(self, query: str, chunks: list[KnowledgeChunk]) -> None:
        q = set(_tokens(query))
        for chunk in chunks:
            toks = _tokens(chunk.heading + " " + chunk.text)
            if not toks or not q:
                chunk.score = 0.0
                continue
            overlap = sum(1 for t in toks if t in q)
            chunk.score = overlap / math.sqrt(len(toks))  # length-normalized

    def _score_semantic(self, query: str, chunks: list[KnowledgeChunk]) -> None:
        try:
            qvec = self.embedder(query)
            for chunk in chunks:
                chunk.score = _cosine(qvec, self.embedder(chunk.heading + " " + chunk.text))
        except Exception as e:  # embedder failed — fall back to lexical
            logger.warning("embedder failed (%s); using lexical fallback", e)
            self._score_lexical(query, chunks)


def _split_sections(text: str) -> list[tuple[str, str]]:
    """Split markdown into (heading, body) sections by `#`-prefixed lines."""
    sections: list[tuple[str, str]] = []
    heading = ""
    body: list[str] = []
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            if heading or body:
                sections.append((heading, "\n".join(body)))
            heading = line.lstrip("# ").strip()
            body = []
        else:
            body.append(line)
    if heading or body:
        sections.append((heading, "\n".join(body)))
    return sections


def make_ollama_embedder(
    model: str = "nomic-embed-text", host: str = "http://localhost:11434"
) -> Embedder:
    """Build an embedder backed by a local Ollama embedding model (stdlib http).

    Kept optional and injectable so the knowledge base works (lexically) without
    any model pulled.
    """
    import json
    import urllib.request

    def embed(text: str) -> list[float]:
        payload = json.dumps({"model": model, "prompt": text}).encode("utf-8")
        req = urllib.request.Request(
            f"{host}/api/embeddings", data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read()).get("embedding", [])

    return embed
