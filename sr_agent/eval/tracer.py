"""Langfuse observability wrapper (Phase 9, T075).

A thin, optional wrapper over the Langfuse SDK for tracing LLM calls (model,
input, output, token usage, latency). Tracing is pure observability: it never
reads from or writes to episodic/knowledge memory, and it never gates the
audit — any failure (package missing, server unreachable, disabled) degrades
to a silent no-op.

Deliberately does NOT import `sr_agent.config` — leaf modules (llm_core,
tools) stay importable in unit tests without ANTHROPIC_API_KEY/SR_SECRET_KEY
set. The orchestrator/CLI layer builds a `Tracer` from config and threads it
down as a parameter, the same way `smartgraphical_root` is threaded through
`pipeline.start_audit`.
"""
from __future__ import annotations

import logging
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator

logger = logging.getLogger(__name__)


@dataclass
class LangfuseTrace:
    """Handle for an in-flight trace; `span` is None when tracing is off.

    `span` is the root `LangfuseSpan` for the trace (SDK v4's OTel-based
    client has no standalone trace object — a trace is just the id shared by
    a tree of spans/generations), kept open for the lifetime of the `trace()`
    context manager so `generation()` can nest observations under it.
    """

    span: Any = None
    trace_id: str | None = None


class Tracer:
    """`Tracer.trace(name, session_id)` -> context manager yielding a trace;
    `Tracer.generation(trace, ...)` logs one LLM call against it.

    No-op (both methods become cheap pass-throughs) unless constructed with a
    secret_key + public_key AND the `langfuse` package is importable. Targets
    the langfuse>=3 OTel-based client API (`start_as_current_observation`),
    not the removed v2 `.trace()`/`.generation()` stateful-client methods.
    """

    def __init__(
        self,
        secret_key: str = "",
        public_key: str = "",
        host: str = "http://localhost:3000",
    ) -> None:
        self._client = None
        if secret_key and public_key:
            try:
                from langfuse import Langfuse

                self._client = Langfuse(
                    secret_key=secret_key, public_key=public_key, host=host
                )
            except Exception as e:  # package missing / server unreachable
                logger.info("Langfuse tracing disabled (%s: %s)", type(e).__name__, e)
                self._client = None

    @property
    def enabled(self) -> bool:
        return self._client is not None

    @contextmanager
    def trace(self, name: str, session_id: str) -> Iterator[LangfuseTrace]:
        if not self._client:
            yield LangfuseTrace()
            return
        # Setup (creating the root span) is isolated in its own try/except so
        # SDK failures degrade to untraced instead of ever swallowing an
        # exception raised by the caller's own audit code below.
        try:
            cm = self._client.start_as_current_observation(
                name=name, as_type="span",
                metadata={"session_id": session_id} if session_id else None,
            )
            span = cm.__enter__()
            trace_id = self._client.get_current_trace_id()
        except Exception as e:
            logger.warning("Langfuse trace() failed, continuing untraced: %s", e)
            yield LangfuseTrace()
            return
        try:
            yield LangfuseTrace(span=span, trace_id=trace_id)
        finally:
            exc_info = sys.exc_info()
            try:
                cm.__exit__(*exc_info)
            except Exception:
                pass
            try:
                self._client.flush()
            except Exception:
                pass

    def generation(
        self,
        trace: LangfuseTrace,
        name: str,
        model: str,
        input: Any,
        output: Any,
        usage: dict | None = None,
        metadata: dict | None = None,
    ) -> None:
        if not self._client or trace.span is None:
            return
        try:
            with trace.span.start_as_current_observation(
                name=name, as_type="generation", model=model,
                input=input, output=output,
                usage_details=usage, metadata=metadata,
            ):
                pass
        except Exception as e:
            logger.warning("Langfuse generation() failed, continuing untraced: %s", e)

    def get_prompt(self, name: str, fallback: str) -> str:
        """Fetch a named prompt from Langfuse Prompt Management (T079).

        `fallback` (the hardcoded prompt already in the calling module) is
        returned unchanged if tracing is disabled or the fetch fails for any
        reason — prompt management is best-effort versioning/observability,
        never a hard dependency for the LLM call that consumes the prompt.
        """
        if not self._client:
            return fallback
        try:
            return self._client.get_prompt(name, fallback=fallback).prompt
        except Exception as e:
            logger.info("Langfuse get_prompt(%r) failed, using built-in prompt (%s: %s)", name, type(e).__name__, e)
            return fallback

    def get_prompt_versioned(self, name: str, fallback: str) -> tuple[str, int | None]:
        """Like `get_prompt`, but also returns the fetched prompt's VERSION so a
        caller can record which version produced a result (feature 012). Additive
        — `get_prompt` above is unchanged for its existing callers. Returns
        `(fallback, None)` on every disabled/error path — the version is never
        fabricated; a fallback-sourced prompt has no version."""
        if not self._client:
            return fallback, None
        try:
            p = self._client.get_prompt(name, fallback=fallback)
            return p.prompt, getattr(p, "version", None)
        except Exception as e:
            logger.info("Langfuse get_prompt_versioned(%r) failed, using built-in prompt (%s: %s)",
                        name, type(e).__name__, e)
            return fallback, None


# Safe default for call sites that don't wire a real Tracer through — never
# touches config or the network.
NOOP_TRACER = Tracer()


def noop_tracer() -> Tracer:
    return NOOP_TRACER
