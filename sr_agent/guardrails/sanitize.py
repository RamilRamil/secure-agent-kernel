from __future__ import annotations

import base64
import binascii
import re
import unicodedata
from dataclasses import dataclass, field


@dataclass
class SanitizeResult:
    normalized: str
    flags: list[str] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        return len(self.flags) == 0


# Patterns that may indicate encoding-based injection attempts
_BASE64_RE = re.compile(r"(?:[A-Za-z0-9+/]{40,}={0,2})")
_MORSE_RE = re.compile(r"(?:[.\-]{2,}\s+){4,}")
_ZERO_WIDTH_RE = re.compile(r"[​‌‍﻿⁠]")
_OVERLONG_TOKEN_RE = re.compile(r"\S{200,}")  # single token > 200 chars


def sanitize(raw: str) -> SanitizeResult:
    """Normalize and flag suspicious encoding patterns in external content.

    This function normalizes but does NOT block. Flags are added to the
    [DATA START] header so the LLM sees them — the orchestrator decides
    whether flagged content warrants escalation.

    Normalization:
    - NFKC Unicode: collapses homoglyphs (Cyrillic а → Latin a, etc.)
    - Strips zero-width characters (invisible text injection)

    Detection (does not modify content beyond normalization):
    - Base64 blocks ≥ 40 chars: may encode hidden instructions
    - Morse-like sequences: rare in Solidity, suspicious in memory
    - Overlong tokens: may exploit tokenizer edge cases
    """
    flags: list[str] = []

    # Remove zero-width characters — they are invisible and serve no
    # legitimate purpose in Solidity source or audit findings
    if _ZERO_WIDTH_RE.search(raw):
        flags.append("zero_width_chars")
        raw = _ZERO_WIDTH_RE.sub("", raw)

    # NFKC normalization: homoglyphs → canonical ASCII equivalents
    normalized = unicodedata.normalize("NFKC", raw)
    if normalized != raw:
        flags.append("homoglyph_detected")

    # Detect (but do not remove) suspicious patterns
    if _BASE64_RE.search(normalized):
        # Attempt to decode — if it decodes to an instruction-shaped string it's more suspicious.
        # Scan ALL blocks, not just the first 3: the cap was ATTACKER-CONTROLLED — prepending a few
        # benign base64 blocks would push a malicious one past the window and defeat detection.
        found_instruction = False
        for match in _BASE64_RE.findall(normalized):
            try:
                decoded = base64.b64decode(match + "==").decode("utf-8", errors="ignore")
            except (binascii.Error, ValueError):
                continue  # not valid base64 → not a decodable instruction (narrow: don't swallow all)
            if any(kw in decoded.lower() for kw in ("ignore", "disregard", "instead", "system")):
                flags.append("base64_instruction_pattern")
                found_instruction = True
                break
        if not found_instruction:
            flags.append("base64_block_present")

    if _MORSE_RE.search(normalized):
        flags.append("morse_pattern")

    if _OVERLONG_TOKEN_RE.search(normalized):
        flags.append("overlong_token")

    return SanitizeResult(normalized=normalized, flags=flags)
