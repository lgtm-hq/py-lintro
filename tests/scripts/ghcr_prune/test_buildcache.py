"""Tests for buildcache retention rules.

Buildcache packages preserve ``main`` permanently and reap ephemeral
``pr-<N>`` / ``mq-<run>`` / ``dispatch-<run>`` tags after ``pr_age_days``.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from assertpy import assert_that

import scripts.ci.maintenance.ghcr_prune.cli as mod
from scripts.ci.maintenance.ghcr_prune_untagged import (
    BUILDCACHE_PACKAGES,
    main,
    prune_buildcache_package,
)

from ._mocks import (
    ManifestResp,
    MockDeleteResponse,
    MockOwnerResponse,
    make_versions_response,
    now_minus,
    registry_client,
)


@pytest.mark.parametrize(
    "tag",
    ["main"],
)
def test_buildcache_preserves_main_tag(tag: str) -> None:
    """``main`` is never deleted regardless of age.

    Args:
        tag: Tag string to apply (parametrized for future expansion).
    """
    deleted: list[int] = []
    client = _buildcache_client(
        versions_data=[
            {
                "id": 1,
                "created_at": now_minus(days=365),
                "metadata": {"container": {"tags": [tag]}},
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


@pytest.mark.parametrize(
    ("tag", "expect_deleted_id"),
    [
        ("pr-890", 42),
        ("mq-123456", 42),
        ("dispatch-9999", 42),
    ],
)
def test_buildcache_deletes_old_ephemeral_tags(
    tag: str,
    expect_deleted_id: int,
) -> None:
    """Each ephemeral tag family is reaped when older than ``pr_age_days``.

    Args:
        tag: Ephemeral tag value.
        expect_deleted_id: Expected version ID to be deleted.
    """
    deleted: list[int] = []
    client = _buildcache_client(
        versions_data=[
            {
                "id": 42,
                "created_at": now_minus(days=30),
                "metadata": {"container": {"tags": [tag]}},
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
    assert_that(deleted).is_equal_to([expect_deleted_id])


def test_buildcache_preserves_young_pr_tag() -> None:
    """Ephemeral tags younger than ``pr_age_days`` survive."""
    deleted: list[int] = []
    client = _buildcache_client(
        versions_data=[
            {
                "id": 7,
                "created_at": now_minus(days=3),
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
    """Untagged buildcache versions are reaped after ``min_age_days``."""
    deleted: list[int] = []
    client = _buildcache_client(
        versions_data=[
            {
                "id": 99,
                "created_at": now_minus(days=30),
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
    """Mixed ephemeral + non-ephemeral tags fully protect a version."""
    deleted: list[int] = []
    client = _buildcache_client(
        versions_data=[
            {
                "id": 5,
                "created_at": now_minus(days=60),
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


def test_main_iterates_buildcache_packages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``main()`` invokes prune for every package in ``BUILDCACHE_PACKAGES``.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    seen: list[str] = []

    class _Client:
        def __init__(
            self,
            # match httpx.Client signature; mock ignores both.
            headers: dict[str, str],  # noqa: ARG002 — match httpx signature
            timeout: int,  # noqa: ARG002 — match httpx signature
        ) -> None:
            return

        def __enter__(self) -> _Client:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        # GhcrClient protocol requires headers; this mock keys on url only.
        def get(
            self,
            url: str,
            headers: dict[str, str],  # noqa: ARG002 — protocol-required
        ) -> Any:
            if "/users/" in url and "/packages/" not in url:
                return MockOwnerResponse()
            for name in (
                *BUILDCACHE_PACKAGES,
                "py-lintro",
                "lintro-tools",
            ):
                if f"/{name}/" in url:
                    seen.append(name)
                    break
            return make_versions_response(versions_data=[])()

        def delete(
            self,
            url: str,  # noqa: ARG002 — recorded only when delete asserted on
            headers: dict[str, str],  # noqa: ARG002 — protocol-required
        ) -> Any:
            return MockDeleteResponse()

    mock_httpx = type(
        "MockHttpx",
        (),
        {"Client": _Client, "HTTPStatusError": httpx.HTTPStatusError},
    )
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/name")
    monkeypatch.setenv("GHCR_PRUNE_PROTECT_REFERENCED", "0")
    monkeypatch.setattr(mod, "httpx", mock_httpx)

    rc = main()
    assert_that(rc).is_equal_to(0)
    for pkg in BUILDCACHE_PACKAGES:
        assert_that(seen).contains(pkg)
    assert_that(seen).contains("py-lintro")
    assert_that(seen).contains("lintro-tools")


def _buildcache_client(
    versions_data: list[dict[str, Any]],
    deleted: list[int],
) -> Any:
    """Build a mock client that returns ``versions_data`` and records deletes.

    Args:
        versions_data: Versions to return on GET.
        deleted: List populated with deleted version IDs.

    Returns:
        Mock GhcrClient instance.
    """

    class _Client:
        def get(
            self,
            url: str,
            *,
            # protocol-required; mock keys on url only.
            headers: dict[str, str] | None = None,  # noqa: ARG002 — protocol-required
        ) -> Any:
            if "/users/" in url and "/packages/" not in url:
                return MockOwnerResponse()
            return ManifestResp(payload=versions_data)

        def delete(
            self,
            url: str,
            *,
            headers: dict[str, str] | None = None,  # noqa: ARG002 — protocol-required
        ) -> MockDeleteResponse:
            deleted.append(int(url.rstrip("/").split("/")[-1]))
            return MockDeleteResponse()

    return _Client()


__all__: list[str] = []
