"""Principal — the identity a memory partition and a session belong to.

Kernel module (feature 004, R4). `Principal` is a generic identity concept —
user_id / platform / project_id — that was historically mislocated in
`models/audit.py`. The memory-isolation boundary (`memory/episodic.py`) and the
`Session` protocol reference it, so it belongs in the kernel, not the audit pack.
"""
from __future__ import annotations

from pydantic import BaseModel


class Principal(BaseModel):
    user_id: str
    platform: str   # "cli" | "api" | "webhook"
    project_id: str
