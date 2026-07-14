"""Spec 021 US1/US2: the clone helper — URL validation + token-out-of-argv.

The `git` call is mocked; no network, no real token.
"""
from __future__ import annotations

import os

os.environ.setdefault("SR_SECRET_KEY", "00" * 32)

import ast
import subprocess
from pathlib import Path

import pytest

import frontend.backend.clone as clone
from frontend.backend.clone import CloneError, clone_repo, validate_repo_url

_TOKEN = "ghp_supersecret_token_value"


# ── URL validation (FR-004) ───────────────────────────────────────────────────

@pytest.mark.parametrize("url", [
    "https://github.com/org/repo.git",
    "http://example.com/x/y",
    "git://host/repo",
])
def test_valid_urls_accepted(url):
    assert validate_repo_url(url) == url


@pytest.mark.parametrize("url", [
    "file:///etc/passwd", "/home/me/proj", "ssh://git@host/repo",
    "git@github.com:org/repo.git", "https://", "", "   ",
])
def test_invalid_urls_rejected(url):
    with pytest.raises(CloneError):
        validate_repo_url(url)


# ── Clone argv + token hygiene (FR-003/006) ──────────────────────────────────

@pytest.fixture
def captured(monkeypatch):
    box = {}

    def fake_run(argv, capture_output=None, text=None, env=None):
        box["argv"] = argv
        box["env"] = dict(env or {})
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(clone.subprocess, "run", fake_run)
    return box


def test_private_clone_keeps_token_out_of_argv_and_url(captured, tmp_path):
    clone_repo("https://github.com/org/private.git", tmp_path, token=_TOKEN)
    argv = captured["argv"]
    # The token appears NOWHERE in argv (not in a flag, not in the URL arg).
    assert all(_TOKEN not in str(a) for a in argv), argv
    # It is wired via the child env + GIT_ASKPASS instead.
    assert captured["env"].get("SR_GIT_TOKEN") == _TOKEN
    assert "GIT_ASKPASS" in captured["env"]
    # URL carries only the non-secret username.
    url_arg = argv[-2]
    assert "x-access-token@" in url_arg and _TOKEN not in url_arg


def test_clone_flags_are_safe(captured, tmp_path):
    clone_repo("https://github.com/org/repo.git", tmp_path)
    argv = captured["argv"]
    assert "--depth" in argv and "1" in argv
    assert "--no-tags" in argv and "--single-branch" in argv
    assert "core.hooksPath=/dev/null" in argv
    assert "--recurse-submodules" not in argv


def test_public_clone_sets_no_askpass_no_token(captured, tmp_path):
    clone_repo("https://github.com/org/repo.git", tmp_path)   # no token
    assert "GIT_ASKPASS" not in captured["env"]
    assert "SR_GIT_TOKEN" not in captured["env"]
    assert "x-access-token@" not in captured["argv"][-2]


def test_askpass_helper_is_deleted(captured, tmp_path):
    clone_repo("https://github.com/org/repo.git", tmp_path, token=_TOKEN)
    assert not Path(captured["env"]["GIT_ASKPASS"]).exists()   # cleaned up in finally


def test_failed_clone_raises_and_cleans_up(monkeypatch, tmp_path):
    def fake_run(argv, capture_output=None, text=None, env=None):
        Path(argv[-1]).mkdir(parents=True, exist_ok=True)   # partial dir
        return subprocess.CompletedProcess(argv, 128, stdout="", stderr="fatal: auth failed")
    monkeypatch.setattr(clone.subprocess, "run", fake_run)
    with pytest.raises(CloneError) as ei:
        clone_repo("https://github.com/org/private.git", tmp_path)
    assert "authentication" in str(ei.value).lower()
    assert not any(tmp_path.iterdir())   # partial workspace removed


# ── C1 (FR-010): no new package ──────────────────────────────────────────────

def test_clone_module_is_stdlib_only():
    src = Path(clone.__file__).read_text(encoding="utf-8")
    roots = set()
    for node in ast.parse(src).body:
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    forbidden = {"git", "github", "requests", "httpx", "pygit2"}  # 'git' = GitPython pkg
    assert not (roots & forbidden), f"clone.py must be stdlib-only; found {roots & forbidden}"
