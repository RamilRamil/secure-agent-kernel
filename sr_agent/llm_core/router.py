from __future__ import annotations

from typing import Mapping


class ModelRouter:
    """Resolve a caller-defined role to a model id (feature 048).

    Shape-agnostic: the kernel router owns NO role vocabulary and NO fixed number
    of slots. It holds whatever ``Mapping[str, str]`` the composing application
    injects and returns ``routing[role]``. A missing role surfaces as ``KeyError``
    — never masked by a default — so a routing gap fails loudly at the call site
    instead of silently selecting the wrong model.
    """

    def __init__(self, routing: Mapping[str, str]) -> None:
        self._routing = routing

    def route(self, role: str) -> str:
        return self._routing[role]
