"""Integration tests for ``ghcr_prune_untagged.main()`` (non-buildcache).

Reference-protection (``GHCR_PRUNE_PROTECT_REFERENCED``) is disabled in these
tests; the SLSA-aware paths live in ``test_referenced.py``.
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
)

from ._mocks import make_mock_client, now_minus


@pytest.fixture
def stub_main_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set the env baseline expected by ``main()`` and disable ref protection.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/name")
    monkeypatch.setenv("GHCR_PRUNE_PROTECT_REFERENCED", "0")


def _patch_httpx(
    monkeypatch: pytest.MonkeyPatch,
    *,
    versions_data: list[dict[str, Any]],
    deleted: list[int],
) -> None:
    """Replace the module's ``httpx`` with a mock that returns ``versions_data``.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        versions_data: Versions to return for the prod package.
        deleted: List populated with deleted version IDs.
    """
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
    monkeypatch.setattr(mod, "httpx", mock_httpx)


def test_main_deletes_only_untagged(
    monkeypatch: pytest.MonkeyPatch,
    stub_main_env: None,  # noqa: ARG001 — autouse-style positional fixture
) -> None:
    """Only untagged versions are deleted; tagged ones are protected.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        stub_main_env: Fixture that sets the baseline env.
    """
    deleted: list[int] = []
    # Timestamps are relative to "now" so the >7-day age guard fires
    # regardless of when the test runs.
    old = now_minus(days=30)
    versions_data = [
        {
            "id": 11,
            "created_at": old,
            "metadata": {"container": {"tags": ["latest"]}},
        },
        {
            "id": 22,
            "created_at": old,
            "metadata": {"container": {"tags": []}},
        },
        {
            "id": 33,
            "created_at": old,
            "metadata": {"container": {"tags": ["0.4.1"]}},
        },
        {
            "id": 44,
            "created_at": old,
            "metadata": {"container": {"tags": []}},
        },
    ]
    _patch_httpx(monkeypatch, versions_data=versions_data, deleted=deleted)

    rc = main()
    assert_that(rc).is_equal_to(0)
    # Only untagged IDs 22 and 44 should be deleted (from py-lintro only).
    assert_that(sorted(deleted)).is_equal_to([22, 44])


def test_main_respects_keep_n_and_dry_run(
    monkeypatch: pytest.MonkeyPatch,
    stub_main_env: None,  # noqa: ARG001
) -> None:
    """Dry-run + keep-N together perform no deletions.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        stub_main_env: Fixture that sets the baseline env.
    """
    deleted: list[int] = []
    # Stagger ages so keep-N selects predictably newest -> oldest.
    versions_data = [
        {
            "id": 100,
            "created_at": now_minus(days=10),
            "metadata": {"container": {"tags": []}},
        },
        {
            "id": 200,
            "created_at": now_minus(days=20),
            "metadata": {"container": {"tags": []}},
        },
        {
            "id": 300,
            "created_at": now_minus(days=30),
            "metadata": {"container": {"tags": []}},
        },
    ]
    _patch_httpx(monkeypatch, versions_data=versions_data, deleted=deleted)
    monkeypatch.setenv("GHCR_PRUNE_DRY_RUN", "1")
    monkeypatch.setenv("GHCR_PRUNE_KEEP_UNTAGGED_N", "2")

    rc = main()
    assert_that(rc).is_equal_to(0)
    # Keep 2 newest untagged (100, 200); 300 candidate but dry-run blocks delete.
    assert_that(deleted).is_equal_to([])
