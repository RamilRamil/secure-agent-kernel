"""Feature 015 US1: `_extract_solidity` pulls real Solidity from prose-wrapped model output.

Anchors on the first Solidity token and the last brace so leading/trailing chain-of-thought
and markdown fences are dropped; a reply with no Solidity token → "" (caller fails the
draft/fix instead of writing a prose-only or empty PoC).
"""
import scripts.poc_queue_runner as pqr

_SOL = "// SPDX-License-Identifier: MIT\npragma solidity ^0.8.28;\ncontract P { function test_x() public {} }"


def test_clean_fenced_block_unchanged():
    out = pqr._extract_solidity(f"```solidity\n{_SOL}\n```")
    assert out == _SOL


def test_bare_solidity_unchanged():
    assert pqr._extract_solidity(_SOL) == _SOL


def test_leading_prose_plus_fenced_block_dropped():
    r = f"Looking at the compilation errors, let me fix it:\n\n```solidity\n{_SOL}\n```\nThat should work."
    out = pqr._extract_solidity(r)
    assert out == _SOL
    assert "Looking at" not in out and "```" not in out and "That should" not in out


def test_leading_prose_plus_bare_solidity_span():
    out = pqr._extract_solidity(f"From the error I can see the issue.\n\n{_SOL}")
    assert out == _SOL


def test_trailing_prose_after_last_brace_dropped():
    out = pqr._extract_solidity(f"{_SOL}\n\nThis exploit proves the cooldown bypass.")
    assert out == _SOL


def test_trailing_block_comment_kept():
    # a trailing `/* Proof Explanation */` is valid Solidity and must survive
    r = f"{_SOL}\n/*\n * ## Proof Explanation\n * step 1 ...\n */"
    out = pqr._extract_solidity(r)
    assert out.endswith("*/") and "Proof Explanation" in out


def test_prose_only_is_empty():
    assert pqr._extract_solidity("Let me analyze what's wrong with the previous attempt.") == ""


def test_empty_reply_is_empty():
    assert pqr._extract_solidity("") == ""
    assert pqr._extract_solidity("   \n\n  ") == ""
