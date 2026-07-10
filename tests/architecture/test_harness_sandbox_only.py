"""Feature 013 US3: the PoC harness executes attacker-influenced PoC/forge code ONLY
through the network-isolated sandbox.

The constitution requires attacker-influenced code (forge test, PoCs) to run only
inside the network-isolated, capability-dropped Docker sandbox. The harness honors
this by routing every PoC/forge run through `run_tests` (which uses `DockerSandbox`);
its only DIRECT subprocesses are benign diff/VCS tools — `git` (the mutation-verify
`git apply`, the `git ls-files` tracked-file scan) and `patch` (mutation-verify's
fallback diff applier) — which never execute target/PoC code. This test pins that: a
future direct `subprocess.run(["forge", "test", ...])` (or any non-benign exec) added
to the harness fails here.
"""
import ast
from pathlib import Path

_HARNESS = Path(__file__).resolve().parents[2] / "scripts" / "poc_queue_runner.py"

# Benign diff/VCS tools the harness may call directly — they read/apply diffs and list
# tracked files; they never execute target or PoC code. Anything else (forge, sh, the
# PoC itself) MUST go through run_tests → DockerSandbox.
_ALLOWED_DIRECT = {"git", "patch"}


def _subprocess_commands(source: str) -> list[str | None]:
    """The first argv token of every subprocess.run/Popen call in `source`.
    None means the call's command isn't a plain list-literal (itself suspicious for a
    static guard — the harness's real calls are all list literals)."""
    tree = ast.parse(source)
    cmds: list[str | None] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if not (isinstance(f, ast.Attribute) and f.attr in ("run", "Popen")
                and isinstance(f.value, ast.Name) and f.value.id == "subprocess"):
            continue
        if not node.args:
            cmds.append(None)
            continue
        arg0 = node.args[0]
        if isinstance(arg0, ast.List) and arg0.elts and isinstance(arg0.elts[0], ast.Constant):
            cmds.append(arg0.elts[0].value)
        else:
            cmds.append(None)
    return cmds


def test_harness_runs_only_benign_direct_subprocesses():
    cmds = _subprocess_commands(_HARNESS.read_text(encoding="utf-8"))
    assert cmds, "expected the harness to contain subprocess calls to check"
    for cmd in cmds:
        assert cmd in _ALLOWED_DIRECT, (
            f"harness runs a non-benign direct subprocess {cmd!r} — PoC/forge execution "
            f"must go through run_tests (DockerSandbox), not a direct subprocess")


def test_guard_would_catch_a_direct_forge_exec():
    """The guard must FAIL on a hypothetical direct forge execution added to the
    harness — otherwise it's not actually protecting anything."""
    snippet = 'import subprocess\nsubprocess.run(["forge", "test", "--match-path", "x"])\n'
    cmds = _subprocess_commands(snippet)
    assert cmds == ["forge"]
    assert not all(c in _ALLOWED_DIRECT for c in cmds)  # the guard rejects it
