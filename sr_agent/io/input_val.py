from __future__ import annotations

import re
from pathlib import Path

from sr_agent.models.audit import AuditInput


class InputValidationError(Exception):
    pass


def validate_filepath(path: Path, root_dir: Path) -> Path:
    """Ensure path exists and does not escape root_dir (path traversal guard)."""
    try:
        resolved = path.resolve()
        root_resolved = root_dir.resolve()
    except OSError as e:
        raise InputValidationError(f"Cannot resolve path {path}: {e}") from e

    if not resolved.exists():
        raise InputValidationError(f"Path does not exist: {path}")

    # is_relative_to guards against ../../etc/passwd style traversal
    if not resolved.is_relative_to(root_resolved):
        raise InputValidationError(
            f"Path '{path}' escapes allowed root '{root_dir}'"
        )
    return resolved


_EIP55_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


def validate_eip55(address: str) -> str:
    """Validate EIP-55 checksummed Ethereum address."""
    if not _EIP55_RE.match(address):
        raise InputValidationError(f"Not a valid Ethereum address: {address!r}")

    # EIP-55 checksum: keccak256 of lowercase hex, uppercase if nibble >= 8
    from eth_utils import to_checksum_address  # web3 dependency
    try:
        checksummed = to_checksum_address(address)
    except Exception as e:
        raise InputValidationError(f"Invalid EIP-55 address: {e}") from e

    if checksummed != address and address != address.lower():
        raise InputValidationError(
            f"Address fails EIP-55 checksum. Did you mean {checksummed}?"
        )
    return checksummed


def validate_audit_input(audit_input: AuditInput, audit_root: Path) -> None:
    """Full validation gate before starting an audit session."""
    if audit_input.path is not None:
        resolved = validate_filepath(audit_input.path, audit_root)
        sol_files = list(resolved.rglob("*.sol"))
        if not sol_files:
            raise InputValidationError(
                f"No .sol files found under {audit_input.path}"
            )

    if audit_input.address is not None:
        validate_eip55(audit_input.address)

    for excl in audit_input.exclude_paths:
        if audit_input.path and not excl.is_relative_to(audit_input.path):
            raise InputValidationError(
                f"exclude_path '{excl}' is not under audit path '{audit_input.path}'"
            )
