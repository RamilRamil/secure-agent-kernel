"""Optional OpenRouter reasoning backend (spec 020) — GLM via OpenRouter.

A paid, OPTIONAL, explicitly-selected provider (Constitution V) — never required,
never a silent fallback. A drop-in for the local client: it implements exactly the
two methods `ChatReasoningProvider` uses — `generate()` and `ready()` — so it slots
into `ChatReasoningProvider(local=…)` unchanged. Output flows through the same
`ChatTurn`, which stamps `external_llm_output`, so the trust status is correct by
construction (Constitution I).

OpenRouter is an OpenAI-compatible gateway; this client talks to its
`chat/completions` endpoint with the Python STANDARD LIBRARY only (urllib) — NO new
package (no `openai` SDK). `ready()` performs NO network/paid call (it checks that a
key is present); a bad key surfaces on the first real `generate()`.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass

BASE_URL = "https://openrouter.ai/api/v1/chat/completions"

# Curated OpenRouter model slugs (verified on the live /models list), GLM first.
OPENROUTER_MODELS: list[str] = [
    "z-ai/glm-5.2",
]


class OpenRouterUnavailable(Exception):
    """The OpenRouter path can't run: no key configured, or a failed/unparseable call."""


@dataclass
class OpenRouterClient:
    """Duck-compatible with LocalClient for `ready()` and `generate()`."""

    api_key: str
    model: str = OPENROUTER_MODELS[0]
    timeout_s: float = 120.0

    def ready(self) -> bool:
        """True iff a key is configured. No network call."""
        return bool(self.api_key)

    def generate(
        self, prompt: str, fmt: str | None = None, options: dict | None = None
    ) -> str:
        """Single-turn generation over OpenRouter's OpenAI-compatible endpoint.

        `fmt="json"` requests JSON output via `response_format`. Returns the
        assistant message text. Any HTTP/network/parse failure is normalized to
        `OpenRouterUnavailable` so the caller sees one clear error type.
        """
        if not self.api_key:
            raise OpenRouterUnavailable("no OpenRouter API key configured")
        body: dict = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
        }
        if fmt == "json":
            body["response_format"] = {"type": "json_object"}
        # Honor the caller's output-token budget (was silently dropped): map `num_predict`
        # to OpenRouter's `max_tokens`. Without it the provider default applies — and a
        # reasoning model can spend the whole default on reasoning tokens, returning EMPTY
        # `content` (the extraction failure that surfaced with deepseek-v3.2-exp).
        if options and options.get("num_predict"):
            body["max_tokens"] = int(options["num_predict"])
        req = urllib.request.Request(
            BASE_URL,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
                data = json.loads(r.read())
            # OpenRouter can return HTTP 200 with an error body (provider-side rate limit,
            # moderation, upstream fault) and NO `choices` — surface its real message instead
            # of a cryptic `KeyError: 'choices'` so the operator can tell rate-limit from bug.
            if "choices" not in data:
                err = data.get("error")
                msg = (err.get("message") if isinstance(err, dict) else err) or str(data)[:300]
                raise OpenRouterUnavailable(f"OpenRouter returned no choices: {msg}")
            message = (data["choices"][0] or {}).get("message", {})
            # Reasoning models (e.g. nemotron) may leave `content` empty and put text under
            # `reasoning`/`reasoning_content` — fall back to those before giving up.
            return (message.get("content")
                    or message.get("reasoning")
                    or message.get("reasoning_content") or "")
        except (urllib.error.URLError, OSError, ValueError, KeyError, IndexError) as e:
            raise OpenRouterUnavailable(f"OpenRouter generate failed: {e}") from e
