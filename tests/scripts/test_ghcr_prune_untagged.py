"""Tests for the GHCR prune untagged utility script."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, cast

import pytest
from assertpy import assert_that

from scripts.ci.maintenance.ghcr_prune_untagged import (
    GhcrClient,
    GhcrVersion,
    delete_version,
    list_container_versions,
    main,
)

if TYPE_CHECKING:
    from types import TracebackType


# =============================================================================
# Shared Mock Classes
# =============================================================================


class MockOwnerResponse:
    """Mock response for owner type lookup."""

    def __init__(self) -> None:
        """Initialize mock response with default status code 200."""
        self.status_code = 200
        self.headers: dict[str, str] = {}

    def raise_for_status(self) -> None:
        """No-op since this is a successful response."""
        return

    def json(self) -> dict[str, str]:
        """Return mock user type data."""
        return {"type": "User"}


class MockDeleteResponse:
    """Mock response for delete operations."""

    def __init__(self, status_code: int = 204) -> None:
        """Initialize mock response with configurable status code."""
        self.status_code = status_code

    def raise_for_status(self) -> None:  # pragma: no cover
        """Raise error for unexpected status codes."""
        if self.status_code not in (204, 404):
            raise RuntimeError("boom")


def make_versions_response(
    versions_data: list[dict[str, Any]],
    status_code: int = 200,
) -> type:
    """Factory for mock GET response with version data.

    Args:
        versions_data: List of version dictionaries to return.
        status_code: HTTP status code for the response.

    Returns:
        Mock response class.
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
    """Factory for mock httpx.Client with configurable behavior.

    Args:
        versions_data: Version data to return from GET requests.
        deleted: List to append deleted version IDs to.
        missing_packages: Package names that should return 404.

    Returns:
        Mock client class.
    """
    missing = missing_packages or []
    versions_resp_cls = make_versions_response(versions_data)
    not_found_resp_cls = make_versions_response([], status_code=404)

    class _MockClient:  # noqa: N801 - intentional class name for factory pattern
        def __init__(
            self,
            headers: dict[str, str],
            timeout: int,
        ) -> None:  # noqa: ARG002 -- mock matches real Client interface
            pass

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
        ) -> (
            MockOwnerResponse | Any
        ):  # noqa: ARG002 -- mock matches real Client.get signature
            # Owner type lookup returns a dict with "type" field
            if "/users/" in url and "/packages/" not in url:
                return MockOwnerResponse()
            # Check for missing packages
            for pkg in missing:
                if pkg in url:
                    return not_found_resp_cls()
            return versions_resp_cls()

        def delete(
            self,
            url: str,
            headers: dict[str, str],
        ) -> (
            MockDeleteResponse
        ):  # noqa: ARG002 -- mock matches real Client.delete signature
            deleted.append(int(url.rstrip("/").split("/")[-1]))
            return MockDeleteResponse()

    return _MockClient


# =============================================================================
# Tests
# =============================================================================


def test_version_dataclass() -> None:
    """Construct ``GhcrVersion`` and validate fields are populated."""
    v = GhcrVersion(id=123, tags=["latest"])
    assert_that(v.id).is_equal_to(123)
    assert_that(v.tags).is_equal_to(["latest"])


def test_list_container_versions_parses_minimal_structure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Parse a minimal response structure into version objects.

    Args:
        monkeypatch: Pytest monkeypatch fixture (not used).
    """

    class DummyResp:
        def __init__(self, data: list[dict[str, Any]]) -> None:
            self._data = data
            self.headers: dict[str, str] = {}

        def raise_for_status(self) -> None:  # pragma: no cover
            return

        def json(self) -> list[dict[str, Any]]:
            return self._data

    class DummyClient:
        def get(
            self,
            url: str,
            *,
            headers: Mapping[str, str] | None = None,
        ) -> (
            DummyResp | MockOwnerResponse
        ):  # noqa: ARG002 -- mock matches real Client.get signature
            # Owner type lookup returns a dict
            if "/users/" in url and "/packages/" not in url:
                return MockOwnerResponse()
            # Package versions returns a list
            return DummyResp(
                data=[
                    {"id": 1, "metadata": {"container": {"tags": ["latest"]}}},
                    {"id": 2, "metadata": {"container": {"tags": []}}},
                ],
            )

    # DummyClient is a test mock implementing only the methods needed for this test
    # Cast to GhcrClient - the mock only implements get() which is sufficient here
    versions = list_container_versions(
        client=cast(GhcrClient, DummyClient()),
        owner="owner",
    )
    assert_that([v.id for v in versions]).is_equal_to([1, 2])
    assert_that(versions[0].tags).is_equal_to(["latest"])
    assert_that(versions[1].tags).is_equal_to([])


def test_delete_version_calls_delete(monkeypatch: pytest.MonkeyPatch) -> None:
    """Call delete and ensure correct endpoint is used.

    Args:
        monkeypatch: Pytest monkeypatch fixture (not used).
    """
    calls: list[tuple[str, Mapping[str, str]]] = []

    class DummyClient:
        def delete(
            self,
            url: str,
            *,
            headers: Mapping[str, str] | None = None,
        ) -> (
            MockDeleteResponse
        ):  # noqa: ARG002 -- mock matches real Client.delete signature
            calls.append((url, headers or {}))
            return MockDeleteResponse()

    # DummyClient is a test mock that only implements delete().
    # Pass base_path to avoid owner type lookup (DummyClient doesn't implement get()).
    # Cast to GhcrClient - the mock only implements delete() which is sufficient here
    delete_version(
        client=cast(GhcrClient, DummyClient()),
        owner="owner",
        version_id=42,
        base_path="https://api.github.com/users/owner/packages/container",
    )
    assert_that(calls).is_not_empty()
    assert_that(calls[0][0]).contains("versions/42")


def test_delete_version_raises_on_non_204_non_404() -> None:
    """Raise when the delete operation returns an unexpected status code.

    Raises:
        AssertionError: If the expected RuntimeError is not raised.
    """

    class DummyClient:
        def delete(
            self,
            url: str,
            *,
            headers: Mapping[str, str] | None = None,
        ) -> (
            MockDeleteResponse
        ):  # noqa: ARG002 -- mock matches real Client.delete signature
            return MockDeleteResponse(status_code=500)

    try:
        # DummyClient is a test mock that only implements delete().
        # Pass base_path to avoid owner type lookup
        # (DummyClient doesn't implement get()).
        # Cast: mock only implements delete(), sufficient here
        delete_version(
            client=cast(GhcrClient, DummyClient()),
            owner="owner",
            version_id=1,
            base_path="https://api.github.com/users/owner/packages/container",  # noqa: E501 -- test URL intentionally long
        )
    except RuntimeError:
        return
    raise AssertionError("Expected RuntimeError on non-204/404 response")


def test_main_deletes_only_untagged(monkeypatch: pytest.MonkeyPatch) -> None:
    """Delete only untagged versions using the main entry point.

    Args:
        monkeypatch: Pytest monkeypatch fixture for environment and client.
    """
    import httpx

    import scripts.ci.maintenance.ghcr_prune_untagged as mod

    deleted: list[int] = []
    versions_data = [
        {
            "id": 11,
            "created_at": "2025-08-24T10:00:00Z",
            "metadata": {"container": {"tags": ["latest"]}},
        },
        {
            "id": 22,
            "created_at": "2025-08-24T09:00:00Z",
            "metadata": {"container": {"tags": []}},
        },
        {
            "id": 33,
            "created_at": "2025-08-24T08:00:00Z",
            "metadata": {"container": {"tags": ["0.4.1"]}},
        },
        {
            "id": 44,
            "created_at": "2025-08-24T07:00:00Z",
            "metadata": {"container": {"tags": []}},
        },
    ]

    mock_client = make_mock_client(
        versions_data=versions_data,
        deleted=deleted,
        missing_packages=["lintro-tools"],
    )

    mock_httpx = type(
        "MockHttpx",
        (),
        {"Client": mock_client, "HTTPStatusError": httpx.HTTPStatusError},
    )

    monkeypatch.setenv("GITHUB_TOKEN", "x")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/name")
    monkeypatch.setattr(mod, "httpx", mock_httpx)

    rc = main()
    assert_that(rc).is_equal_to(0)
    # Only untagged IDs 22 and 44 should be deleted (from py-lintro package only)
    assert_that(sorted(deleted)).is_equal_to([22, 44])


def test_main_respects_keep_n_and_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """Respect keep-N and dry-run options when pruning.

    Args:
        monkeypatch: Pytest monkeypatch fixture for environment and client.
    """
    import httpx

    import scripts.ci.maintenance.ghcr_prune_untagged as mod

    deleted: list[int] = []
    # 3 untagged versions
    versions_data = [
        {
            "id": 100,
            "created_at": "2025-08-24T12:00:00Z",
            "metadata": {"container": {"tags": []}},
        },
        {
            "id": 200,
            "created_at": "2025-08-24T11:00:00Z",
            "metadata": {"container": {"tags": []}},
        },
        {
            "id": 300,
            "created_at": "2025-08-24T10:00:00Z",
            "metadata": {"container": {"tags": []}},
        },
    ]

    mock_client = make_mock_client(
        versions_data=versions_data,
        deleted=deleted,
        missing_packages=["lintro-tools"],
    )

    mock_httpx = type(
        "MockHttpx",
        (),
        {"Client": mock_client, "HTTPStatusError": httpx.HTTPStatusError},
    )

    # Dry-run with keep 2 -> no deletions performed
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/name")
    monkeypatch.setenv("GHCR_PRUNE_DRY_RUN", "1")
    monkeypatch.setenv("GHCR_PRUNE_KEEP_UNTAGGED_N", "2")
    monkeypatch.setattr(mod, "httpx", mock_httpx)

    rc = main()
    assert_that(rc).is_equal_to(0)
    # Keep 2 newest untagged (100, 200). Would delete only 300; dry-run prevents it
    assert_that(deleted).is_equal_to([])
