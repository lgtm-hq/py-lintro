"""Tests for the GHCR prune untagged utility script."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

import pytest
from assertpy import assert_that

from scripts.ci.maintenance.ghcr_prune_untagged import (
    BUILDCACHE_PACKAGES,
    GhcrClient,
    GhcrVersion,
    collect_referenced_digests,
    delete_version,
    fetch_manifest,
    list_container_versions,
    main,
    prune_buildcache_package,
    prune_package,
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
        ) -> None:  # noqa: ARG002
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
        ) -> MockOwnerResponse | Any:  # noqa: ARG002
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
        ) -> MockDeleteResponse:  # noqa: ARG002
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
        ) -> DummyResp | MockOwnerResponse:  # noqa: ARG002
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
        ) -> MockDeleteResponse:  # noqa: ARG002
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
        ) -> MockDeleteResponse:  # noqa: ARG002
            return MockDeleteResponse(status_code=500)

    try:
        # DummyClient is a test mock that only implements delete().
        # Pass base_path to avoid owner type lookup (DummyClient doesn't implement get()).
        # Cast to GhcrClient - the mock only implements delete() which is sufficient here
        delete_version(
            client=cast(GhcrClient, DummyClient()),
            owner="owner",
            version_id=1,
            base_path="https://api.github.com/users/owner/packages/container",
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
        missing_packages=["lintro-tools", *BUILDCACHE_PACKAGES],
    )

    mock_httpx = type(
        "MockHttpx",
        (),
        {"Client": mock_client, "HTTPStatusError": httpx.HTTPStatusError},
    )

    monkeypatch.setenv("GITHUB_TOKEN", "x")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/name")
    monkeypatch.setenv("GHCR_PRUNE_PROTECT_REFERENCED", "0")
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
        missing_packages=["lintro-tools", *BUILDCACHE_PACKAGES],
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
    monkeypatch.setenv("GHCR_PRUNE_PROTECT_REFERENCED", "0")
    monkeypatch.setattr(mod, "httpx", mock_httpx)

    rc = main()
    assert_that(rc).is_equal_to(0)
    # Keep 2 newest untagged (100, 200). Would delete only 300; dry-run prevents it
    assert_that(deleted).is_equal_to([])


# =============================================================================
# Buildcache retention tests
# =============================================================================


def _now_minus(days: int) -> str:
    """Return an ISO-8601 UTC timestamp for ``now - days``.

    Args:
        days: Days to subtract from current UTC time.

    Returns:
        Timestamp formatted with trailing ``Z``.
    """
    return (
        (datetime.now(UTC) - timedelta(days=days))
        .isoformat()
        .replace(
            "+00:00",
            "Z",
        )
    )


def _buildcache_client(
    versions_data: list[dict[str, Any]],
    deleted: list[int],
) -> GhcrClient:
    """Build a GhcrClient mock returning ``versions_data`` for any package.

    Args:
        versions_data: Raw API version dicts to return on GET.
        deleted: Mutable list collecting deleted version IDs.

    Returns:
        Mock client typed as ``GhcrClient``.
    """

    class _Resp:
        def __init__(self) -> None:
            self.status_code = 200
            self.headers: dict[str, str] = {}

        def raise_for_status(self) -> None:
            return

        def json(self) -> Any:
            return self._payload  # type: ignore[attr-defined]

    class _Client:
        def get(
            self,
            url: str,
            *,
            headers: Mapping[str, str] | None = None,
        ) -> _Resp:  # noqa: ARG002
            r = _Resp()
            if "/users/" in url and "/packages/" not in url:
                r._payload = {"type": "User"}  # type: ignore[attr-defined]
            else:
                r._payload = versions_data  # type: ignore[attr-defined]
            return r

        def delete(
            self,
            url: str,
            *,
            headers: Mapping[str, str] | None = None,
        ) -> _Resp:  # noqa: ARG002
            deleted.append(int(url.rstrip("/").split("/")[-1]))
            r = _Resp()
            r.status_code = 204
            return r

    return cast(GhcrClient, _Client())


def test_buildcache_preserves_main_tag() -> None:
    """`main` tag survives regardless of age."""
    deleted: list[int] = []
    client = _buildcache_client(
        versions_data=[
            {
                "id": 1,
                "created_at": _now_minus(365),
                "metadata": {"container": {"tags": ["main"]}},
            },
        ],
        deleted=deleted,
    )

    n = prune_buildcache_package(
        client=client,
        owner="owner",
        package_name="py-lintro-buildcache",
        dry_run=False,
        min_age_days=7,
        pr_age_days=14,
    )
    assert_that(n).is_equal_to(0)
    assert_that(deleted).is_equal_to([])


def test_buildcache_deletes_old_pr_tag() -> None:
    """`pr-<N>` tags older than pr_age_days are deleted."""
    deleted: list[int] = []
    client = _buildcache_client(
        versions_data=[
            {
                "id": 42,
                "created_at": _now_minus(30),
                "metadata": {"container": {"tags": ["pr-890"]}},
            },
        ],
        deleted=deleted,
    )

    n = prune_buildcache_package(
        client=client,
        owner="owner",
        package_name="py-lintro-buildcache",
        dry_run=False,
        min_age_days=7,
        pr_age_days=14,
    )
    assert_that(n).is_equal_to(1)
    assert_that(deleted).is_equal_to([42])


def test_buildcache_deletes_old_mq_tag() -> None:
    """`mq-<run_id>` tags older than pr_age_days are deleted."""
    deleted: list[int] = []
    client = _buildcache_client(
        versions_data=[
            {
                "id": 77,
                "created_at": _now_minus(30),
                "metadata": {"container": {"tags": ["mq-123456"]}},
            },
        ],
        deleted=deleted,
    )

    n = prune_buildcache_package(
        client=client,
        owner="owner",
        package_name="py-lintro-buildcache",
        dry_run=False,
        min_age_days=7,
        pr_age_days=14,
    )
    assert_that(n).is_equal_to(1)
    assert_that(deleted).is_equal_to([77])


def test_buildcache_deletes_old_dispatch_tag() -> None:
    """`dispatch-<run_id>` tags older than pr_age_days are deleted."""
    deleted: list[int] = []
    client = _buildcache_client(
        versions_data=[
            {
                "id": 88,
                "created_at": _now_minus(30),
                "metadata": {"container": {"tags": ["dispatch-9999"]}},
            },
        ],
        deleted=deleted,
    )

    n = prune_buildcache_package(
        client=client,
        owner="owner",
        package_name="py-lintro-buildcache",
        dry_run=False,
        min_age_days=7,
        pr_age_days=14,
    )
    assert_that(n).is_equal_to(1)
    assert_that(deleted).is_equal_to([88])


def test_buildcache_preserves_young_pr_tag() -> None:
    """`pr-<N>` tags younger than pr_age_days are preserved."""
    deleted: list[int] = []
    client = _buildcache_client(
        versions_data=[
            {
                "id": 7,
                "created_at": _now_minus(3),
                "metadata": {"container": {"tags": ["pr-1"]}},
            },
        ],
        deleted=deleted,
    )

    n = prune_buildcache_package(
        client=client,
        owner="owner",
        package_name="py-lintro-buildcache",
        dry_run=False,
        min_age_days=7,
        pr_age_days=14,
    )
    assert_that(n).is_equal_to(0)
    assert_that(deleted).is_equal_to([])


def test_buildcache_deletes_old_untagged() -> None:
    """Untagged buildcache versions older than min_age_days delete."""
    deleted: list[int] = []
    client = _buildcache_client(
        versions_data=[
            {
                "id": 99,
                "created_at": _now_minus(30),
                "metadata": {"container": {"tags": []}},
            },
        ],
        deleted=deleted,
    )

    n = prune_buildcache_package(
        client=client,
        owner="owner",
        package_name="py-lintro-buildcache",
        dry_run=False,
        min_age_days=7,
        pr_age_days=14,
    )
    assert_that(n).is_equal_to(1)
    assert_that(deleted).is_equal_to([99])


def test_buildcache_protects_mixed_tags() -> None:
    """A version tagged with both `main` and `pr-<N>` is fully protected."""
    deleted: list[int] = []
    client = _buildcache_client(
        versions_data=[
            {
                "id": 5,
                "created_at": _now_minus(60),
                "metadata": {"container": {"tags": ["main", "pr-3"]}},
            },
        ],
        deleted=deleted,
    )

    n = prune_buildcache_package(
        client=client,
        owner="owner",
        package_name="py-lintro-buildcache",
        dry_run=False,
        min_age_days=7,
        pr_age_days=14,
    )
    assert_that(n).is_equal_to(0)
    assert_that(deleted).is_equal_to([])


def test_main_iterates_buildcache_packages(monkeypatch: pytest.MonkeyPatch) -> None:
    """`main()` invokes buildcache prune for every BUILDCACHE_PACKAGES entry.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    import httpx

    import scripts.ci.maintenance.ghcr_prune_untagged as mod

    seen: list[str] = []

    class _Resp:
        def __init__(self, payload: Any, status: int = 200) -> None:
            self._payload = payload
            self.status_code = status
            self.headers: dict[str, str] = {}

        def raise_for_status(self) -> None:
            if self.status_code == 404:
                raise httpx.HTTPStatusError(
                    message="Not Found",
                    request=httpx.Request("GET", "http://test"),
                    response=httpx.Response(404),
                )

        def json(self) -> Any:
            return self._payload

    class _Client:
        def __init__(
            self,
            headers: dict[str, str],
            timeout: int,
        ) -> None:  # noqa: ARG002
            return

        def __enter__(self) -> _Client:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def get(self, url: str, headers: dict[str, str]) -> _Resp:  # noqa: ARG002
            if "/users/" in url and "/packages/" not in url:
                return _Resp({"type": "User"})
            for name in (*BUILDCACHE_PACKAGES, "py-lintro", "lintro-tools"):
                if f"/{name}/" in url:
                    seen.append(name)
                    break
            return _Resp([])

        def delete(self, url: str, headers: dict[str, str]) -> _Resp:  # noqa: ARG002
            return _Resp(None, status=204)

    mock_httpx = type(
        "MockHttpx",
        (),
        {"Client": _Client, "HTTPStatusError": httpx.HTTPStatusError},
    )

    monkeypatch.setenv("GITHUB_TOKEN", "x")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/name")
    monkeypatch.setattr(mod, "httpx", mock_httpx)

    rc = main()
    assert_that(rc).is_equal_to(0)
    for pkg in BUILDCACHE_PACKAGES:
        assert_that(seen).contains(pkg)
    assert_that(seen).contains("py-lintro")
    assert_that(seen).contains("lintro-tools")


# =============================================================================
# Reference-digest protection tests (SLSA + multi-arch children)
# =============================================================================


class _ManifestResp:
    """Configurable mock registry response carrying a JSON body."""

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
        """No-op for 2xx; matches httpx behaviour for non-error responses."""
        return

    def json(self) -> Any:
        """Return the configured payload."""
        return self._payload


def _registry_client(manifests: dict[str, dict[str, Any]]) -> GhcrClient:
    """Build a mock client that serves manifests by digest.

    Args:
        manifests: Mapping of digest -> manifest body.

    Returns:
        Mock GhcrClient.
    """

    class _Client:
        def get(
            self,
            url: str,
            *,
            headers: Mapping[str, str] | None = None,
        ) -> _ManifestResp:  # noqa: ARG002
            for digest, body in manifests.items():
                if digest in url:
                    return _ManifestResp(body)
            return _ManifestResp({}, status_code=404)

    return cast(GhcrClient, _Client())


def test_fetch_manifest_returns_none_on_404() -> None:
    """A 404 response yields ``None`` rather than an exception."""
    client = _registry_client({})
    result = fetch_manifest(
        client=client,
        owner="owner",
        package_name="pkg",
        digest="sha256:dead",
        registry_token="test-bearer",  # noqa: S106 # nosec B106
    )
    assert_that(result).is_none()


def test_collect_referenced_digests_image_index() -> None:
    """Image-index children become referenced digests."""
    manifest = {
        "mediaType": "application/vnd.oci.image.index.v1+json",
        "manifests": [
            {"digest": "sha256:child-amd64"},
            {"digest": "sha256:child-arm64"},
            {"digest": "sha256:provenance"},
        ],
    }
    client = _registry_client({"sha256:parent": manifest})
    versions = [
        GhcrVersion(id=1, tags=["v1.0.0"], name="sha256:parent"),
        GhcrVersion(id=2, tags=[], name="sha256:child-amd64"),
    ]

    refs = collect_referenced_digests(
        client=client,
        owner="owner",
        package_name="pkg",
        versions=versions,
        registry_token="test-bearer",  # noqa: S106 # nosec B106
    )
    assert_that(refs).contains(
        "sha256:child-amd64",
        "sha256:child-arm64",
        "sha256:provenance",
    )


def test_collect_referenced_digests_subject_referrer() -> None:
    """OCI ``subject`` digests become referenced digests."""
    manifest = {
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "subject": {"digest": "sha256:attested"},
    }
    client = _registry_client({"sha256:attestation": manifest})
    versions = [
        GhcrVersion(id=1, tags=["sha-abc"], name="sha256:attestation"),
    ]

    refs = collect_referenced_digests(
        client=client,
        owner="owner",
        package_name="pkg",
        versions=versions,
        registry_token="test-bearer",  # noqa: S106 # nosec B106
    )
    assert_that(refs).contains("sha256:attested")


def test_collect_referenced_digests_skips_untagged() -> None:
    """Untagged versions are not walked (tagged manifests drive the search)."""
    manifest = {
        "manifests": [{"digest": "sha256:would-be-child"}],
    }
    client = _registry_client({"sha256:untagged-parent": manifest})
    versions = [
        GhcrVersion(id=1, tags=[], name="sha256:untagged-parent"),
    ]

    refs = collect_referenced_digests(
        client=client,
        owner="owner",
        package_name="pkg",
        versions=versions,
        registry_token="test-bearer",  # noqa: S106 # nosec B106
    )
    assert_that(refs).is_empty()


def test_prune_package_skips_referenced_digests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Untagged versions whose digest is in ``referenced_digests`` are kept.

    Args:
        monkeypatch: Pytest monkeypatch fixture for the API client mock.
    """
    deleted: list[int] = []
    versions_data = [
        {
            "id": 10,
            "name": "sha256:tagged",
            "created_at": "2025-01-01T00:00:00Z",
            "metadata": {"container": {"tags": ["v1"]}},
        },
        {
            "id": 11,
            "name": "sha256:slsa-child",
            "created_at": "2025-01-01T00:00:00Z",
            "metadata": {"container": {"tags": []}},
        },
        {
            "id": 12,
            "name": "sha256:orphan",
            "created_at": "2025-01-01T00:00:00Z",
            "metadata": {"container": {"tags": []}},
        },
    ]
    mock_client_cls = make_mock_client(
        versions_data=versions_data,
        deleted=deleted,
    )
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/name")

    rc = prune_package(
        client=cast(GhcrClient, mock_client_cls(headers={}, timeout=30)),
        owner="owner",
        package_name="py-lintro",
        dry_run=False,
        min_age_days=7,
        keep_n=0,
        referenced_digests={"sha256:slsa-child"},
    )
    # Only the orphan untagged version is deleted; slsa-child is preserved.
    assert_that(rc).is_equal_to(1)
    assert_that(deleted).is_equal_to([12])


def test_main_protects_slsa_children_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: ``main()`` protects SLSA child digests via registry walk.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    import httpx

    import scripts.ci.maintenance.ghcr_prune_untagged as mod

    deleted: list[int] = []
    versions_data = [
        {
            "id": 1,
            "name": "sha256:tagged-index",
            "created_at": "2025-01-01T00:00:00Z",
            "metadata": {"container": {"tags": ["v1.0.0"]}},
        },
        {
            "id": 2,
            "name": "sha256:slsa-attestation",
            "created_at": "2025-01-01T00:00:00Z",
            "metadata": {"container": {"tags": []}},
        },
        {
            "id": 3,
            "name": "sha256:image-layer",
            "created_at": "2025-01-01T00:00:00Z",
            "metadata": {"container": {"tags": []}},
        },
        {
            "id": 4,
            "name": "sha256:orphan",
            "created_at": "2025-01-01T00:00:00Z",
            "metadata": {"container": {"tags": []}},
        },
    ]
    index_manifest = {
        "mediaType": "application/vnd.oci.image.index.v1+json",
        "manifests": [
            {"digest": "sha256:slsa-attestation"},
            {"digest": "sha256:image-layer"},
        ],
    }

    class _Client:
        def __init__(
            self,
            headers: dict[str, str],
            timeout: int,
        ) -> None:  # noqa: ARG002
            return

        def __enter__(self) -> _Client:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def get(self, url: str, headers: dict[str, str]) -> Any:  # noqa: ARG002
            if "/users/" in url and "/packages/" not in url:
                return MockOwnerResponse()
            if url.startswith("https://ghcr.io/token"):
                fake_token = "registry-bearer"  # noqa: S105 # nosec B105
                return _ManifestResp({"token": fake_token})
            if "/v2/" in url and "/manifests/" in url:
                if "sha256:tagged-index" in url:
                    return _ManifestResp(index_manifest)
                return _ManifestResp({}, status_code=404)
            for pkg in ("lintro-tools", *BUILDCACHE_PACKAGES):
                if pkg in url:
                    return make_versions_response([], status_code=404)()
            return make_versions_response(versions_data)()

        def delete(self, url: str, headers: dict[str, str]) -> Any:  # noqa: ARG002
            deleted.append(int(url.rstrip("/").split("/")[-1]))
            return MockDeleteResponse()

    mock_httpx = type(
        "MockHttpx",
        (),
        {"Client": _Client, "HTTPStatusError": httpx.HTTPStatusError},
    )

    monkeypatch.setenv("GITHUB_TOKEN", "ghs_test")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/name")
    monkeypatch.setattr(mod, "httpx", mock_httpx)

    rc = main()
    assert_that(rc).is_equal_to(0)
    # slsa-attestation (id=2) and image-layer (id=3) are referenced and survive.
    # Only the orphan untagged version (id=4) is deleted.
    assert_that(deleted).is_equal_to([4])
