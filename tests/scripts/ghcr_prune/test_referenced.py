"""Tests for SLSA / multi-arch referenced-digest protection.

Covers ``fetch_manifest``, ``collect_referenced_digests``, the
``referenced_digests`` flow in ``prune_package``, and the end-to-end
``main()`` path that walks tagged manifests before pruning.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

import httpx
import pytest
from assertpy import assert_that

import scripts.ci.maintenance.ghcr_prune_untagged as mod
from scripts.ci.maintenance.ghcr_prune_untagged import (
    BUILDCACHE_PACKAGES,
    GhcrClient,
    GhcrVersion,
    collect_referenced_digests,
    fetch_manifest,
    fetch_referrers,
    main,
    prune_package,
)

from ._mocks import (
    ManifestResp,
    MockDeleteResponse,
    MockOwnerResponse,
    make_mock_client,
    make_versions_response,
    now_minus,
    registry_client,
)

# Fake bearer used in tests; not a real credential.
_TEST_REGISTRY_TOKEN = "test-bearer"  # noqa: S105 # nosec B105


def test_fetch_manifest_returns_none_on_404() -> None:
    """A 404 response yields ``None`` rather than an exception."""
    client = registry_client(manifests={})
    result = fetch_manifest(
        client=client,
        owner="owner",
        package_name="pkg",
        digest="sha256:dead",
        registry_token=_TEST_REGISTRY_TOKEN,
    )
    assert_that(result).is_none()


def _route_client(routes: dict[str, Any]) -> GhcrClient:
    """Build a mock client matching URL substrings to JSON payloads.

    The first matching key wins, so callers should order specific paths
    (``/referrers/``) before generic ones (``/manifests/``).

    Args:
        routes: Mapping of URL substring -> JSON body for ``ManifestResp``.

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
            for needle, body in routes.items():
                if needle in url:
                    return ManifestResp(payload=body)
            return ManifestResp(payload={}, status_code=404)

    return cast(GhcrClient, _Client())


def test_fetch_referrers_returns_descriptors() -> None:
    """Referrers index ``manifests`` list is returned as descriptor dicts."""
    referrers_index: dict[str, Any] = {
        "mediaType": "application/vnd.oci.image.index.v1+json",
        "manifests": [
            {
                "digest": "sha256:cosign-sig",
                "artifactType": "application/vnd.dev.cosign.simplesigning.v1+json",
            },
            {"digest": "sha256:slsa-attestation"},
        ],
    }
    client = _route_client(routes={"/referrers/sha256:img": referrers_index})

    descriptors = fetch_referrers(
        client=client,
        owner="owner",
        package_name="pkg",
        digest="sha256:img",
        registry_token=_TEST_REGISTRY_TOKEN,
    )
    assert_that([d["digest"] for d in descriptors]).is_equal_to(
        ["sha256:cosign-sig", "sha256:slsa-attestation"],
    )


def test_fetch_referrers_returns_empty_on_404() -> None:
    """A 404 (registry without OCI 1.1 support) yields an empty list."""
    client = _route_client(routes={})
    result = fetch_referrers(
        client=client,
        owner="owner",
        package_name="pkg",
        digest="sha256:any",
        registry_token=_TEST_REGISTRY_TOKEN,
    )
    assert_that(result).is_empty()


def test_collect_referenced_digests_includes_referrers() -> None:
    """Untagged referrers (e.g. cosign signatures) are protected."""
    image_manifest: dict[str, Any] = {
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
    }
    referrers_index: dict[str, Any] = {
        "manifests": [
            {"digest": "sha256:cosign-sig"},
            {"digest": "sha256:in-toto-attestation"},
        ],
    }
    # /referrers/ routed before /manifests/ so the more specific path wins.
    client = _route_client(
        routes={
            "/referrers/sha256:img": referrers_index,
            "/manifests/sha256:img": image_manifest,
        },
    )
    versions = [GhcrVersion(id=1, tags=["v1"], name="sha256:img")]

    refs = collect_referenced_digests(
        client=client,
        owner="owner",
        package_name="pkg",
        versions=versions,
        registry_token=_TEST_REGISTRY_TOKEN,
    )
    assert_that(refs).contains("sha256:cosign-sig", "sha256:in-toto-attestation")


def test_fetch_manifest_returns_none_on_non_dict_payload() -> None:
    """Non-object JSON (e.g. a list) yields ``None`` rather than crashing."""
    # registry_client serves whatever payload type ManifestResp wraps.
    client = registry_client(manifests={"sha256:weird": [1, 2, 3]})
    result = fetch_manifest(
        client=client,
        owner="owner",
        package_name="pkg",
        digest="sha256:weird",
        registry_token=_TEST_REGISTRY_TOKEN,
    )
    assert_that(result).is_none()


def test_collect_referenced_digests_image_index() -> None:
    """Image-index children become referenced digests."""
    manifest: dict[str, Any] = {
        "mediaType": "application/vnd.oci.image.index.v1+json",
        "manifests": [
            {"digest": "sha256:child-amd64"},
            {"digest": "sha256:child-arm64"},
            {"digest": "sha256:provenance"},
        ],
    }
    client = registry_client(manifests={"sha256:parent": manifest})
    versions = [
        GhcrVersion(id=1, tags=["v1.0.0"], name="sha256:parent"),
        GhcrVersion(id=2, tags=[], name="sha256:child-amd64"),
    ]

    refs = collect_referenced_digests(
        client=client,
        owner="owner",
        package_name="pkg",
        versions=versions,
        registry_token=_TEST_REGISTRY_TOKEN,
    )
    assert_that(refs).contains(
        "sha256:child-amd64",
        "sha256:child-arm64",
        "sha256:provenance",
    )


def test_collect_referenced_digests_subject_referrer() -> None:
    """OCI ``subject`` digests become referenced digests."""
    manifest: dict[str, Any] = {
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "subject": {"digest": "sha256:attested"},
    }
    client = registry_client(manifests={"sha256:attestation": manifest})
    versions = [GhcrVersion(id=1, tags=["sha-abc"], name="sha256:attestation")]

    refs = collect_referenced_digests(
        client=client,
        owner="owner",
        package_name="pkg",
        versions=versions,
        registry_token=_TEST_REGISTRY_TOKEN,
    )
    assert_that(refs).contains("sha256:attested")


def test_collect_referenced_digests_skips_untagged() -> None:
    """Untagged versions are not walked (tagged manifests drive the search)."""
    manifest: dict[str, Any] = {
        "manifests": [{"digest": "sha256:would-be-child"}],
    }
    client = registry_client(manifests={"sha256:untagged-parent": manifest})
    versions = [GhcrVersion(id=1, tags=[], name="sha256:untagged-parent")]

    refs = collect_referenced_digests(
        client=client,
        owner="owner",
        package_name="pkg",
        versions=versions,
        registry_token=_TEST_REGISTRY_TOKEN,
    )
    assert_that(refs).is_empty()


def test_prune_package_skips_referenced_digests() -> None:
    """``referenced_digests`` digests are preserved; orphan untagged is reaped."""
    deleted: list[int] = []
    old = now_minus(days=30)
    versions_data = [
        {
            "id": 10,
            "name": "sha256:tagged",
            "created_at": old,
            "metadata": {"container": {"tags": ["v1"]}},
        },
        {
            "id": 11,
            "name": "sha256:slsa-child",
            "created_at": old,
            "metadata": {"container": {"tags": []}},
        },
        {
            "id": 12,
            "name": "sha256:orphan",
            "created_at": old,
            "metadata": {"container": {"tags": []}},
        },
    ]
    mock_client_cls = make_mock_client(
        versions_data=versions_data,
        deleted=deleted,
    )

    rc = prune_package(
        client=cast(GhcrClient, mock_client_cls(headers={}, timeout=30)),
        owner="owner",
        package_name="py-lintro",
        dry_run=False,
        min_age_days=7,
        keep_n=0,
        referenced_digests={"sha256:slsa-child"},
    )
    assert_that(rc).is_equal_to(1)
    assert_that(deleted).is_equal_to([12])


def test_main_protects_slsa_children_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: ``main()`` walks tagged manifests and protects children.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    deleted: list[int] = []
    old = now_minus(days=30)
    versions_data: list[dict[str, Any]] = [
        {
            "id": 1,
            "name": "sha256:tagged-index",
            "created_at": old,
            "metadata": {"container": {"tags": ["v1.0.0"]}},
        },
        {
            "id": 2,
            "name": "sha256:slsa-attestation",
            "created_at": old,
            "metadata": {"container": {"tags": []}},
        },
        {
            "id": 3,
            "name": "sha256:image-layer",
            "created_at": old,
            "metadata": {"container": {"tags": []}},
        },
        {
            "id": 4,
            "name": "sha256:orphan",
            "created_at": old,
            "metadata": {"container": {"tags": []}},
        },
    ]
    index_manifest: dict[str, Any] = {
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
                return ManifestResp(payload={"token": fake_token})
            if "/v2/" in url and "/manifests/" in url:
                if "sha256:tagged-index" in url:
                    return ManifestResp(payload=index_manifest)
                return ManifestResp(payload={}, status_code=404)
            for pkg in ("lintro-tools", *BUILDCACHE_PACKAGES):
                if pkg in url:
                    return make_versions_response(
                        versions_data=[],
                        status_code=404,
                    )()
            return make_versions_response(versions_data=versions_data)()

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
    # slsa-attestation (id=2) and image-layer (id=3) survive; orphan (id=4) goes.
    assert_that(deleted).is_equal_to([4])
