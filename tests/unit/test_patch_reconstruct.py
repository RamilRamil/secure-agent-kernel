"""Feature 025 US3/US4: reconstruct an applyable patch from an illustrative report diff.

The whole bug being fixed is that something *looked* like a patch and no tool would take it — so
every SUCCESS case here asserts the REAL `git apply` accepts the output against a temp git repo,
not that a string matches an expected blob (a string test would have passed on the illustrative diff
too, catching nothing). The REFUSAL cases pin the load-bearing safety property: a wrong location is a
wrong `verified` signal, worse than none.

Offline: no model, no network. Every fixture is INVENTED and reproduces only the SHAPE of an
illustrative diff — no audited-target name or path enters this repo.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.patch_reconstruct import ReconstructionRefused, reconstruct


def _git_repo(tmp_path: Path, files: dict[str, str]) -> Path:
    """A real temp git repo with `files` committed — the ground `git apply` runs against."""
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    subprocess.run(["git", "init", "-q", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
                   cwd=tmp_path, check=True)
    return tmp_path


def _applies(repo: Path, patch: str) -> bool:
    """True iff the REAL `git apply` accepts `patch` — the exact acceptance production depends on."""
    r = subprocess.run(["git", "apply", "--unsafe-paths", "-p1", "-"],
                       cwd=repo, input=patch, text=True, capture_output=True)
    return r.returncode == 0


def _reader(files: dict[str, str]):
    return lambda rel: files.get(rel)


# ── US3: reconstruction produces a patch REAL git apply accepts ──────────────

def test_exact_anchor_single_hunk_applies(tmp_path):
    src = "contract V {\n    struct T {\n        uint64 a;\n        uint64 b;\n    }\n}\n"
    block = ("--- a/V.sol\n+++ b/V.sol\n"
             "@@ struct T {\n         uint64 a;\n         uint64 b;\n+        uint64 c;\n     }")
    patch = reconstruct(block, _reader({"V.sol": src}))
    repo = _git_repo(tmp_path, {"V.sol": src})
    assert _applies(repo, patch)


def test_deep_indentation_removal_applies(tmp_path):
    # Live trap B: removal lines carry deep source indentation that must match verbatim.
    src = ("contract V {\n    function f() public {\n"
           "                uint x = 1;\n                uint y = 2;\n    }\n}\n")
    block = ("--- a/V.sol\n+++ b/V.sol\n"
             "@@ function f() public {\n"
             "-                uint x = 1;\n"
             "+                uint x = 3;\n"
             "                 uint y = 2;")
    patch = reconstruct(block, _reader({"V.sol": src}))
    repo = _git_repo(tmp_path, {"V.sol": src})
    assert _applies(repo, patch)


def test_no_trailing_context_applies(tmp_path):
    # Live trap C: removals + additions, near-identical, with no trailing context line.
    src = ("contract V {\n    function g() internal {\n"
           "        uint n = a++;\n        k = n;\n    }\n}\n")
    block = ("--- a/V.sol\n+++ b/V.sol\n"
             "@@ function g() internal {\n"
             "         uint n = a++;\n"
             "-        k = n;\n"
             "+        k = n + 1;")
    patch = reconstruct(block, _reader({"V.sol": src}))
    repo = _git_repo(tmp_path, {"V.sol": src})
    assert _applies(repo, patch)


def test_multiple_hunks_one_file_applies(tmp_path):
    src = ("contract V {\n"
           "    struct T {\n        uint64 a;\n    }\n"
           "    function f() public {\n        uint x = 1;\n    }\n}\n")
    block = ("--- a/V.sol\n+++ b/V.sol\n"
             "@@ struct T {\n         uint64 a;\n+        uint64 b;\n     }\n"
             "@@ function f() public {\n         uint x = 1;\n+        uint y = 2;\n     }")
    patch = reconstruct(block, _reader({"V.sol": src}))
    repo = _git_repo(tmp_path, {"V.sol": src})
    assert _applies(repo, patch)


def test_reconstruction_is_deterministic(tmp_path):
    src = "contract V {\n    struct T {\n        uint64 a;\n    }\n}\n"
    block = "--- a/V.sol\n+++ b/V.sol\n@@ struct T {\n         uint64 a;\n+        uint64 b;\n     }"
    r = _reader({"V.sol": src})
    assert reconstruct(block, r) == reconstruct(block, r)


# ── US4: refusal rather than a wrong guess ───────────────────────────────────

def _block(anchor: str, body: str, path: str = "V.sol") -> str:
    return f"--- a/{path}\n+++ b/{path}\n@@ {anchor}\n{body}"


def test_abbreviated_anchor_refuses(tmp_path):
    # Live trap A: `@@ function f(...) internal {` — the `(...)` ellipsis exists nowhere verbatim.
    # This is the one real block expected to REFUSE by design.
    src = "contract V {\n    function f(uint a, uint b) internal {\n        x = 1;\n    }\n}\n"
    block = _block("function f(...) internal {", "         x = 1;\n+        y = 2;")
    with pytest.raises(ReconstructionRefused) as e:
        reconstruct(block, _reader({"V.sol": src}))
    assert e.value.reason == "anchor_not_found"


def test_absent_anchor_refuses(tmp_path):
    src = "contract V {\n    struct T {\n        uint64 a;\n    }\n}\n"
    block = _block("struct Nonexistent {", "         uint64 a;\n+        uint64 b;")
    with pytest.raises(ReconstructionRefused) as e:
        reconstruct(block, _reader({"V.sol": src}))
    assert e.value.reason == "anchor_not_found"


def test_ambiguous_anchor_refuses(tmp_path):
    # Two identical lines → the anchor cannot resolve to one place; never pick.
    src = "contract V {\n    uint x;\n    uint x;\n}\n"
    block = _block("uint x;", "+    uint y;")
    with pytest.raises(ReconstructionRefused) as e:
        reconstruct(block, _reader({"V.sol": src}))
    assert e.value.reason == "anchor_ambiguous"


def test_context_line_not_verbatim_refuses(tmp_path):
    src = "contract V {\n    struct T {\n        uint64 a;\n    }\n}\n"
    # context claims `uint64 zzz;` which is not in the source
    block = _block("struct T {", "         uint64 zzz;\n+        uint64 b;")
    with pytest.raises(ReconstructionRefused) as e:
        reconstruct(block, _reader({"V.sol": src}))
    assert e.value.reason == "context_mismatch"


def test_missing_file_refuses(tmp_path):
    block = _block("struct T {", "         uint64 a;\n+        uint64 b;", path="Gone.sol")
    with pytest.raises(ReconstructionRefused) as e:
        reconstruct(block, _reader({"V.sol": "contract V {}\n"}))
    assert e.value.reason == "file_not_found"


def test_one_refusing_hunk_refuses_the_whole_fix(tmp_path):
    # First hunk is fine, second's anchor is absent → the ENTIRE fix refuses (FR-012):
    # a partially applied fix is a wrong signal, not a partial one.
    src = ("contract V {\n    struct T {\n        uint64 a;\n    }\n"
           "    function f() public {\n        uint x = 1;\n    }\n}\n")
    block = ("--- a/V.sol\n+++ b/V.sol\n"
             "@@ struct T {\n         uint64 a;\n+        uint64 b;\n     }\n"
             "@@ struct Nonexistent {\n         uint x = 1;\n+        uint y = 2;")
    with pytest.raises(ReconstructionRefused) as e:
        reconstruct(block, _reader({"V.sol": src}))
    assert e.value.reason == "anchor_not_found"
