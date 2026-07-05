"""Ephemeral Docker sandbox for running untrusted tools (US4, T042).

Static analysers and Foundry tests run on attacker-influenced contract code,
so they execute inside a throwaway container with the network disabled and
capabilities dropped. The container is destroyed after every run.

Security defaults (all overridable only by the orchestrator, never the LLM):
  --rm                       destroy container after run
  --network none             no network egress (default)
  --cap-drop ALL             drop all Linux capabilities
  --security-opt no-new-privileges
  --pids-limit               cap process count (fork-bomb guard)
  --memory / --cpus          resource limits
  contract mounts read-only  source cannot be modified in place
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


class SandboxError(Exception):
    pass


class SandboxTimeout(SandboxError):
    pass


class SandboxUnavailable(SandboxError):
    """Docker is not installed or the daemon is not reachable."""


@dataclass
class Mount:
    host_path: Path
    container_path: str
    read_only: bool = True

    def to_arg(self) -> str:
        mode = "ro" if self.read_only else "rw"
        return f"{Path(self.host_path).resolve()}:{self.container_path}:{mode}"


@dataclass
class SandboxResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


@dataclass
class DockerSandbox:
    default_timeout_s: float = 120.0
    memory_limit: str = "512m"
    cpus: str = "1.0"
    pids_limit: int = 256
    docker_bin: str = "docker"
    extra_security_opts: list[str] = field(default_factory=list)

    def _ensure_available(self) -> None:
        if shutil.which(self.docker_bin) is None:
            raise SandboxUnavailable(f"{self.docker_bin!r} not found on PATH")

    def run(
        self,
        image: str,
        command: list[str],
        mounts: list[Mount] | None = None,
        timeout_s: float | None = None,
        network: str = "none",
        workdir: str | None = None,
        env: dict[str, str] | None = None,
    ) -> SandboxResult:
        """Run a command in an ephemeral, network-isolated container.

        `network` and `env` default to full isolation / no env — the secure agent
        never relaxes them. They exist only for opt-in tooling (e.g. a mainnet-fork
        PoC run in the standalone workability harness), which must pass them explicitly.
        `env` values are injected via `-e` and are NOT logged."""
        self._ensure_available()
        timeout_s = timeout_s if timeout_s is not None else self.default_timeout_s
        container_name = f"sr-sandbox-{uuid.uuid4().hex[:12]}"

        argv = [
            self.docker_bin, "run", "--rm",
            "--name", container_name,
            "--network", network,
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--pids-limit", str(self.pids_limit),
            "--memory", self.memory_limit,
            "--cpus", self.cpus,
        ]
        for opt in self.extra_security_opts:
            argv += ["--security-opt", opt]
        for mount in mounts or []:
            argv += ["-v", mount.to_arg()]
        for key, value in (env or {}).items():
            argv += ["-e", f"{key}={value}"]   # value not logged (may be a secret RPC URL)
        if workdir:
            argv += ["-w", workdir]
        argv.append(image)
        argv += command

        logger.info("Sandbox run: image=%s network=%s cmd=%s", image, network, command)

        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=timeout_s
            )
        except subprocess.TimeoutExpired:
            # subprocess timeout kills the docker client, not the container —
            # explicitly kill the named container so nothing lingers.
            self._force_kill(container_name)
            logger.warning("Sandbox timed out after %ss — container killed", timeout_s)
            raise SandboxTimeout(f"Sandbox exceeded {timeout_s}s")
        except FileNotFoundError as e:
            raise SandboxUnavailable(str(e)) from e

        return SandboxResult(
            exit_code=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            timed_out=False,
        )

    def _force_kill(self, container_name: str) -> None:
        try:
            subprocess.run(
                [self.docker_bin, "kill", container_name],
                capture_output=True, text=True, timeout=15,
            )
        except Exception:
            logger.debug("Failed to kill container %s (may have already exited)", container_name)
