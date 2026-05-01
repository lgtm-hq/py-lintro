"""Reusable HTTP mock helpers for GHCR prune tests.

Private helpers (not pytest fixtures): the prune script accepts a
``GhcrClient`` protocol implementation, so each test wires a small fake
client tailored to the URLs it exercises. These helpers consolidate the
boilerplate.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from scripts.ci.maintenance.ghcr_prune_untagged import GhcrClient

if TYPE_CHECKING:
    from types import TracebackType


class MockOwnerResponse:
    """Mock response for owner-type lookup (``/users/<owner>``)."""

    def __init__(self) -> None:
        """Initialize with HTTP 200 and an empty header map."""
        self.status_code = 200
        self.headers: dict[str, str] = {}

    def raise_for_status(self) -> None:
        """No-op (success)."""
        return

    def json(self) -> dict[str, str]:
        """Return a fixed ``{"type": "User"}`` payload."""
        return {"type": "User"}


class MockDeleteResponse:
    """Mock response for ``DELETE`` on a version endpoint."""

    def __init__(self, status_code: int = 204) -> None:
        """Initialize with the given status code.

        Args:
            status_code: HTTP status code to report.
        """
        self.status_code = status_code

    def raise_for_status(self) -> None:  # pragma: no cover - error path
        """Raise ``RuntimeError`` for unexpected status codes.

        Raises:
            RuntimeError: When status is neither 204 nor 404.
        """
        if self.status_code not in (204, 404):
            raise RuntimeError("boom")


def make_versions_response(
    versions_data: list[dict[str, Any]],
    status_code: int = 200,
) -> type:
    """Build a mock ``GET`` response class returning ``versions_data``.

    Args:
        versions_data: List of raw version dicts to return as JSON.
        status_code: HTTP status code; ``404`` raises ``HTTPStatusError``.

    Returns:
        Mock response class (call to instantiate).
    """
    import httpx

    class MockVersionsResponse:
        def __init__(self) -> None:
            self.status_code = status_code
            self.headers: dict[str, str] = {}

        def raise_for_status(self) -> None:
            if self.status_code == 404:
                raise httpx.HTTPStatusError(
                    message="Not Found",
                    request=httpx.Request("GET", "http://test"),
                    response=httpx.Response(404),
                )

        def json(self) -> list[dict[str, Any]]:
            return versions_data

    return MockVersionsResponse


def make_mock_client(
    versions_data: list[dict[str, Any]],
    deleted: list[int],
    missing_packages: list[str] | None = None,
) -> type:
    """Build a mock ``httpx.Client`` class for prune ``main()`` tests.

    The mock supports owner-type lookup, version listing, and version
    deletion. ``missing_packages`` simulates a 404 for the named packages.

    Args:
        versions_data: Versions to return for any non-missing package.
        deleted: List populated with deleted version IDs as a side effect.
        missing_packages: Package names that should respond with 404.

    Returns:
        Mock client class (call with ``headers=`` and ``timeout=``).
    """
    missing = missing_packages or []
    versions_resp_cls = make_versions_response(versions_data=versions_data)
    not_found_resp_cls = make_versions_response(
        versions_data=[],
        status_code=404,
    )

    class _MockClient:  # noqa: N801 — factory class name
        def __init__(
            self,
            headers: dict[str, str],
            timeout: int,
        ) -> None:  # noqa: ARG002
            return

        def __enter__(self) -> _MockClient:
            return self

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            tb: TracebackType | None,
        ) -> None:
            return None

        def get(
            self,
            url: str,
            headers: dict[str, str],
        ) -> MockOwnerResponse | Any:  # noqa: ARG002
            if "/users/" in url and "/packages/" not in url:
                return MockOwnerResponse()
            for pkg in missing:
                if pkg in url:
                    return not_found_resp_cls()
            return versions_resp_cls()

        def delete(
            self,
            url: str,
            headers: dict[str, str],
        ) -> MockDeleteResponse:  # noqa: ARG002
            deleted.append(int(url.rstrip("/").split("/")[-1]))
            return MockDeleteResponse()

    return _MockClient


class ManifestResp:
    """Mock registry response carrying a JSON manifest body."""

    def __init__(self, payload: Any, status_code: int = 200) -> None:
        """Initialize.

        Args:
            payload: JSON body to return.
            status_code: HTTP status code.
        """
        self._payload = payload
        self.status_code = status_code
        self.headers: dict[str, str] = {}

    def raise_for_status(self) -> None:
        """No-op for 2xx."""
        return

    def json(self) -> Any:
        """Return the configured payload."""
        return self._payload


def registry_client(manifests: dict[str, dict[str, Any]]) -> GhcrClient:
    """Build a mock client that serves manifests by digest substring.

    Args:
        manifests: Mapping of digest -> manifest body.

    Returns:
        Mock ``GhcrClient`` instance.
    """

    class _Client:
        def get(
            self,
            url: str,
            *,
            headers: Mapping[str, str] | None = None,
        ) -> ManifestResp:  # noqa: ARG002
            for digest, body in manifests.items():
                if digest in url:
                    return ManifestResp(payload=body)
            return ManifestResp(payload={}, status_code=404)

    return cast(GhcrClient, _Client())


def now_minus(days: int) -> str:
    """Return an ISO-8601 UTC timestamp for ``now - days``.

    Args:
        days: Days to subtract from current UTC time.

    Returns:
        Timestamp string ending in ``Z``.
    """
    return (
        (datetime.now(UTC) - timedelta(days=days))
        .isoformat()
        .replace(
            "+00:00",
            "Z",
        )
    )
