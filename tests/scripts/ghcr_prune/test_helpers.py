"""Tests for ``ghcr_prune_untagged`` primitive helpers.

Covers ``GhcrVersion`` dataclass, ``list_container_versions``, and
``delete_version`` happy/error paths.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

import pytest
from assertpy import assert_that

from scripts.ci.maintenance.ghcr_prune_untagged import (
    GhcrClient,
    GhcrVersion,
    delete_version,
    list_container_versions,
)

from ._mocks import MockDeleteResponse, MockOwnerResponse


def test_version_dataclass() -> None:
    """Construct ``GhcrVersion`` and validate field defaults are populated."""
    v = GhcrVersion(id=123, tags=["latest"])
    assert_that(v.id).is_equal_to(123)
    assert_that(v.tags).is_equal_to(["latest"])


@pytest.mark.parametrize(
    "raw",
    [
        {"id": 1, "metadata": None},
        {"id": 1, "metadata": "not-a-dict"},
        {"id": 1, "metadata": {"container": None}},
        {"id": 1, "metadata": {"container": "not-a-dict"}},
        {"id": 1, "metadata": {"container": {"tags": None}}},
        {"id": 1, "metadata": {"container": {"tags": "not-a-list"}}},
    ],
)
def test_list_container_versions_handles_malformed_metadata(
    raw: dict[str, Any],
) -> None:
    """Malformed metadata yields an empty ``tags`` list, never AttributeError.

    Args:
        raw: Raw API item with various malformed ``metadata`` shapes.
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
            if "/users/" in url and "/packages/" not in url:
                return MockOwnerResponse()
            return DummyResp(data=[raw])

    versions = list_container_versions(
        client=cast(GhcrClient, DummyClient()),
        owner="owner",
    )
    assert_that(versions).is_length(1)
    assert_that(versions[0].tags).is_equal_to([])


def test_list_container_versions_parses_minimal_structure() -> None:
    """Parse a minimal API response into ``GhcrVersion`` objects."""

    class DummyResp:
        def __init__(self, data: list[dict[str, Any]]) -> None:
            self._data = data
            self.headers: dict[str, str] = {}

        def raise_for_status(self) -> None:  # pragma: no cover - happy path
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
            if "/users/" in url and "/packages/" not in url:
                return MockOwnerResponse()
            return DummyResp(
                data=[
                    {"id": 1, "metadata": {"container": {"tags": ["latest"]}}},
                    {"id": 2, "metadata": {"container": {"tags": []}}},
                ],
            )

    versions = list_container_versions(
        client=cast(GhcrClient, DummyClient()),
        owner="owner",
    )
    assert_that([v.id for v in versions]).is_equal_to([1, 2])
    assert_that(versions[0].tags).is_equal_to(["latest"])
    assert_that(versions[1].tags).is_equal_to([])


def test_delete_version_calls_delete() -> None:
    """``delete_version`` issues DELETE against the version endpoint."""
    calls: list[tuple[str, Mapping[str, str]]] = []

    class DummyClient:
        def delete(
            self,
            url: str,
            *,
            headers: Mapping[str, str] | None = None,
        ) -> MockDeleteResponse:
            calls.append((url, headers or {}))
            return MockDeleteResponse()

    delete_version(
        client=cast(GhcrClient, DummyClient()),
        owner="owner",
        version_id=42,
        base_path="https://api.github.com/users/owner/packages/container",
    )
    assert_that(calls).is_not_empty()
    assert_that(calls[0][0]).contains("versions/42")


def test_delete_version_raises_on_non_204_non_404() -> None:
    """Unexpected delete status (e.g. 500) propagates as ``RuntimeError``."""

    class DummyClient:
        def delete(
            self,
            url: str,  # noqa: ARG002
            *,
            headers: Mapping[str, str] | None = None,  # noqa: ARG002
        ) -> MockDeleteResponse:
            return MockDeleteResponse(status_code=500)

    with pytest.raises(RuntimeError):
        delete_version(
            client=cast(GhcrClient, DummyClient()),
            owner="owner",
            version_id=1,
            base_path="https://api.github.com/users/owner/packages/container",
        )
