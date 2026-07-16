"""Optional Gemini reasoning backend (spec 018).

A paid, OPTIONAL, explicitly-selected provider — never required, never a silent
fallback (Constitution V). It is a drop-in for the local client: it implements
exactly the two methods `ChatReasoningProvider` calls — `generate()` and
`ready()` — so it slots into `ChatReasoningProvider(local=…)` unchanged. Output
flows through the same `ChatTurn`, which stamps `external_llm_output`, so the
trust status is correct by construction (Constitution I).

The `google-genai` SDK is an OPTIONAL dependency (`pip install '.[gemini]'`),
imported LAZILY here — this module imports fine with the SDK absent, and only
constructing/using a live client needs it. `ready()` performs NO network/paid
call (it checks SDK-importable + key-present); a bad key surfaces on the first
real `generate()`.
"""
from __future__ import annotations

from dataclasses import dataclass

# Curated flash-tier models — the routine/worker tier. A static list keeps the UI
# dropdown offline/deterministic, but it ROTS: verify against the LIVE API, never
# against SDK doc examples.
#
# Verified 2026-07-15 by probing the real API with a live key (spec 018's original
# list was built from SDK docs and was 100% wrong for a current account):
#   gemini-2.5-flash / 2.5-flash-lite -> 404 "no longer available to new users"
#   gemini-2.0-flash                  -> quota exceeded
#   gemini-3.5-flash                  -> does not exist in the live model list at all
# Note: `models.list()` is NOT proof of usability — it still lists 2.5-flash, which
# 404s on generateContent. Only an actual generate call tells the truth.
SIMPLE_MODELS: list[str] = [
    "gemini-3-flash-preview",   # verified working; default for routine work
    "gemini-3.1-flash-lite",    # verified working; cheaper
]


class GeminiUnavailable(Exception):
    """The Gemini path can't run: SDK not installed, or no key configured."""


def _sdk():
    """Lazily import the google-genai SDK. Raises GeminiUnavailable if absent."""
    try:
        from google import genai
        from google.genai import types
    except ImportError as e:
        raise GeminiUnavailable(
            "Gemini provider requires the google-genai SDK — install it with: "
            "pip install '.[gemini]'  (or: uv pip install google-genai)"
        ) from e
    return genai, types


@dataclass
class GeminiClient:
    """Duck-compatible with LocalClient for `ready()` and `generate()`."""

    api_key: str
    model: str = SIMPLE_MODELS[0]

    def ready(self) -> bool:
        """True iff the SDK is importable AND a key is configured. No network call."""
        if not self.api_key:
            return False
        try:
            _sdk()
        except GeminiUnavailable:
            return False
        return True

    def generate(
        self, prompt: str, fmt: str | None = None, options: dict | None = None
    ) -> str:
        """Single-turn generation. `fmt="json"` requests JSON output (parallels
        the local client's grammar-constrained JSON). Returns the text string.

        Any SDK/auth/network/quota failure is wrapped in GeminiUnavailable so the
        caller surfaces one clear error type instead of a leaking SDK exception.
        """
        if not self.api_key:
            raise GeminiUnavailable("no Gemini API key configured")
        genai, types = _sdk()
        config = None
        if fmt == "json":
            config = types.GenerateContentConfig(response_mime_type="application/json")
        try:
            client = genai.Client(api_key=self.api_key)
            resp = client.models.generate_content(
                model=self.model, contents=prompt, config=config
            )
            return resp.text or ""
        except GeminiUnavailable:
            raise
        except Exception as e:  # SDK/auth/network/quota — normalize to one type
            raise GeminiUnavailable(f"Gemini generate failed: {e}") from e
