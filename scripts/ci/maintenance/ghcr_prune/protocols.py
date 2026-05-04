"""HTTP client + response protocols used across the prune package.

Defining a structural ``GhcrClient`` lets tests substitute lightweight fakes
without depending on ``httpx`` internals.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol


class _ResponseProto(Protocol):
    headers: Mapping[str, str]
    status_code: int

    def raise_for_status(self) -> None: ...

    def json(self) -> Any: ...


class GhcrClient(Protocol):
    """Protocol for the GHCR HTTP client (``httpx.Client``-compatible)."""

    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = ...,
    ) -> _ResponseProto:
        """Send GET request to ``url``."""
        ...

    def delete(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = ...,
    ) -> _ResponseProto:
        """Send DELETE request to ``url``."""
        ...
