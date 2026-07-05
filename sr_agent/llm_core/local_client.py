"""Local LLM client via Ollama (T057).

Free, local, no paid API: talks to an Ollama server (default localhost:11434).
The model's text output is parsed by the SAME adapter as the manual relay, so a
local model and Claude produce findings identically — and a local response is
still external_llm_output (automation != authoring), gated by every guardrail.

Stdlib only (urllib) — no extra dependency.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass


logger = logging.getLogger(__name__)

DEFAULT_MODEL = "qwen2.5-coder:3b"
DEFAULT_HOST = "http://localhost:11434"

# Stage 2 model preference (T091): the fine-tuned MI-resistant model if it has
# been built (`ollama create sr-stage2`), else the best stock model available.
STAGE2_MODEL = "sr-stage2"
STAGE2_FALLBACK = "qwen3:4b"


class ModelUnavailableError(Exception):
    pass


@dataclass
class LocalClient:
    model: str = DEFAULT_MODEL
    host: str = DEFAULT_HOST
    # Generation can be slow on small hardware — measured ~8 min/PoC for
    # qwen2.5-coder:3b. 180s was too short and caused spurious timeouts; a real
    # PoC-drafting turn should escalate to relay rather than rely on this being
    # fast (research R10/R11).
    timeout_s: float = 600.0

    def _matched_tags_entries(self, tags: dict) -> list[dict]:
        """`/api/tags` entries matching `self.model`, by the same tag rule
        `available()` has always used: an explicit tag matches exactly
        (`qwen2.5-coder:3b` must NOT match `qwen2.5-coder:7b`); an untagged name
        matches any tag of that base. Shared so `supports_tools()` (spec 008)
        reads `capabilities` off the SAME matched entries `available()` checks
        for existence, rather than a second, possibly-drifting implementation of
        this matching rule."""
        models = tags.get("models", [])
        if ":" in self.model:
            return [m for m in models if m.get("name", "") == self.model]
        return [m for m in models
                if m.get("name", "") == self.model or m.get("name", "").startswith(self.model + ":")]

    def available(self) -> bool:
        """Liveness: the Ollama server is up and the model is pulled.

        Cheap (checks /api/tags). NOT sufficient to decide FR-011 "unavailable" —
        a wedged server serves /api/tags fine while every generate hangs. Use
        ready() for that decision. See ready() and research R10.
        """
        try:
            with urllib.request.urlopen(f"{self.host}/api/tags", timeout=5) as r:
                tags = json.loads(r.read())
        except Exception:
            return False
        return bool(self._matched_tags_entries(tags))

    def supports_tools(self) -> bool:
        """Whether this model reports native tool-calling support (spec 008
        research.md R2) — i.e. `"tools"` appears in its `/api/tags` entry's
        `capabilities` list. Verified present for `qwen2.5-coder:7b`/`:3b`
        (local) and `qwen3-coder:30b` (Kaggle-hosted, the model actually used
        for this project's live PoC-drafting runs)."""
        try:
            with urllib.request.urlopen(f"{self.host}/api/tags", timeout=5) as r:
                tags = json.loads(r.read())
        except Exception:
            return False
        return any("tools" in m.get("capabilities", []) for m in self._matched_tags_entries(tags))

    def warm(self, timeout_s: float = 1200.0, keep_alive: str = "30m", retries: int = 2) -> bool:
        """Load the model into memory and keep it resident.

        Cold load of a larger model (over a cloud-GPU tunnel, e.g. Kaggle +
        cloudflared) can take minutes — far longer than ready()'s short probe. Call
        once at session start so the first real turn's readiness check doesn't fail
        on the load. keep_alive holds it in memory across the session's turns.

        Two defenses against a free `cloudflared` quick tunnel's ~60-100s
        idle-connection cutoff (docs/roadmap.md gotcha #11), confirmed to actually
        recur here (2026-07-06): the FIRST warm() call against a cold model reports
        "could not warm" while the model load is still genuinely in progress
        server-side; an immediate retry then succeeds near-instantly because the
        model finished loading despite the tunnel having already cut the client's
        view of the first call.
        1. Stream the request (matches `generate()`'s already-fixed pattern) so any
           bytes Ollama does emit keep the tunnel connection looking active, instead
           of the single `stream: false` blocking read that sends nothing at all
           until the whole (cold-load-inclusive) response is ready.
        2. Retry on failure — a cut connection does not mean the load failed; it
           means we lost visibility into it. Retrying is the actual fix that's been
           reliably observed to work, not just a mitigation.
        """
        body = {
            "model": self.model, "prompt": "ok", "stream": True,
            "options": {"num_predict": 1}, "keep_alive": keep_alive,
        }
        req = urllib.request.Request(
            f"{self.host}/api/generate",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        for attempt in range(retries + 1):
            saw_done = False
            try:
                with urllib.request.urlopen(req, timeout=timeout_s) as r:
                    for raw_line in r:
                        line = raw_line.strip()
                        if not line:
                            continue
                        obj = json.loads(line)
                        if obj.get("done"):
                            saw_done = True
            except Exception:
                logger.debug("LocalClient.warm attempt %d/%d failed for %s",
                            attempt + 1, retries + 1, self.model)
                continue
            if saw_done:
                return True
        return False

    def ready(self, probe_timeout_s: float = 15.0) -> bool:
        """Readiness: the model can actually produce output right now.

        A minimal `num_predict=1` generate probe with a short timeout — catches
        the reachable-but-wedged case `available()` misses (research R10). This is
        the check FR-011 keys on: "unavailable" means "fails ready()".
        """
        if not self.available():
            return False
        body = {
            "model": self.model, "prompt": "ok", "stream": False,
            "options": {"num_predict": 1},
        }
        req = urllib.request.Request(
            f"{self.host}/api/generate",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=probe_timeout_s) as r:
                data = json.loads(r.read())
        except Exception:
            return False
        return isinstance(data.get("response"), str)

    @classmethod
    def for_stage2(
        cls,
        preferred: str = STAGE2_MODEL,
        fallback: str = STAGE2_FALLBACK,
        host: str = DEFAULT_HOST,
    ) -> "LocalClient":
        """Resolve the Stage 2 client (T091): prefer the fine-tuned `sr-stage2`
        model, fall back to a stock model if it hasn't been built yet.

        Falls back when the preferred model isn't pulled but the fallback is, or
        when Ollama is unreachable entirely (so `.available()` still gates it).
        """
        # Prefer the fine-tuned model, then qwen2.5-coder:7b (far more reliable at
        # tool-selection than the 3b — live smoke: 3b failed to extract a path into
        # read_file, 7b succeeded), then the stock fallback, then whatever base is
        # actually pulled — so chat works out of the box on a machine with only 3b.
        for name in (preferred, "qwen2.5-coder:7b", fallback, DEFAULT_MODEL):
            candidate = cls(model=name, host=host)
            if candidate.available():
                return candidate
        return cls(model=preferred, host=host)

    def generate(
        self, prompt: str, fmt: str | None = None, options: dict | None = None
    ) -> str:
        """Single-turn generation. Raises ModelUnavailableError if unreachable.

        fmt="json" enables Ollama's grammar-constrained JSON decoding, which
        guarantees syntactically valid JSON (small models otherwise leave inner
        quotes unescaped and break the parser).

        `options` maps to Ollama's generate `options` (e.g. {"num_ctx": 16384}).
        The num_ctx default is small (2048) — a long prompt (a full audit
        report, a big source file) is silently truncated unless num_ctx is
        raised here, so any long-context caller MUST set it.

        Always streams (NDJSON) rather than requesting one final blob: a
        `stream: false` call sends the client ZERO bytes until generation
        fully completes, which a proxy/tunnel between the caller and Ollama
        (e.g. a free `cloudflared` quick tunnel — see docs/roadmap.md gotcha
        #11) reads as one long idle connection and cuts after ~60-100s,
        regardless of `timeout_s` here. The cut connection then yields a
        truncated-but-still-JSON-parseable partial response with `done:
        false`, which was previously accepted silently as real output
        (root-caused 2026-07-02 — a PoC draft truncated mid-statement).
        Streaming keeps bytes flowing continuously, which proxies read as
        "active", and lets us explicitly detect and reject a cut-short
        stream (never saw `done: true`) instead of returning garbage.
        """
        body: dict = {"model": self.model, "prompt": prompt, "stream": True}
        if fmt:
            body["format"] = fmt
        if options:
            body["options"] = options
        payload = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            f"{self.host}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        chunks: list[str] = []
        saw_done = False
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
                for raw_line in r:
                    line = raw_line.strip()
                    if not line:
                        continue
                    obj = json.loads(line)
                    chunks.append(obj.get("response", ""))
                    if obj.get("done"):
                        saw_done = True
                        break
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise ModelUnavailableError(f"Ollama generate failed: {e}") from e
        if not saw_done:
            raise ModelUnavailableError(
                "Ollama stream ended before done:true — connection was likely "
                "cut mid-generation by a proxy/tunnel (see roadmap gotcha #11)"
            )
        return "".join(chunks)

    def chat(self, messages: list[dict], tools: list[dict] | None = None,
              options: dict | None = None) -> dict:
        """Single tool-calling-capable turn via Ollama's native `/api/chat`
        (spec 008 research.md R3/R5) — the transport for the agentic
        `lookup_symbol` round-trip, parallel to `generate()`'s `/api/generate`.

        Streams for the identical reason `generate()` does (roadmap gotcha
        #11/#12): a `stream: false` call sends zero bytes until the whole
        response — including a tool-calling turn — completes, which a tunnel
        reads as an idle connection and cuts. `tool_calls` typically arrives
        whole in the chunk that also carries `done: true`, not incrementally,
        so the last non-empty `tool_calls` seen wins.

        Returns the assembled final message: `{"role": "assistant", "content":
        str, "tool_calls": list[dict]}` (`tool_calls` empty when the model
        didn't call a tool this turn).
        """
        body: dict = {"model": self.model, "messages": messages, "stream": True}
        if tools:
            body["tools"] = tools
        if options:
            body["options"] = options
        payload = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            f"{self.host}/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        chunks: list[str] = []
        tool_calls: list[dict] = []
        saw_done = False
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
                for raw_line in r:
                    line = raw_line.strip()
                    if not line:
                        continue
                    obj = json.loads(line)
                    msg = obj.get("message") or {}
                    chunks.append(msg.get("content", ""))
                    if msg.get("tool_calls"):
                        tool_calls = msg["tool_calls"]
                    if obj.get("done"):
                        saw_done = True
                        break
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise ModelUnavailableError(f"Ollama chat failed: {e}") from e
        if not saw_done:
            raise ModelUnavailableError(
                "Ollama chat stream ended before done:true — connection was "
                "likely cut mid-generation by a proxy/tunnel (see roadmap gotcha #11)"
            )
        return {"role": "assistant", "content": "".join(chunks), "tool_calls": tool_calls}


