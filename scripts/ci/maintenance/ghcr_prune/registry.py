"""GHCR Docker Registry helpers.

Token exchange, manifest fetch, OCI Referrers walk, and the aggregate
``collect_referenced_digests`` used to compute the SLSA / multi-arch
protection set.

Transient failures (HTTP error, 5xx, malformed JSON) raise
:class:`RegistryFetchError` so callers can distinguish "really not there"
(genuine 404 → empty result) from "we can't tell" (must skip pruning).
"""

from __future__ import annotations

import base64
from typing import Any, NamedTuple

import httpx
from loguru import logger

from .protocols import GhcrClient
from .version import GhcrVersion

_MANIFEST_ACCEPT = ", ".join(
    [
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    ],
)
_REFERRERS_ACCEPT = "application/vnd.oci.image.index.v1+json"


class RegistryFetchError(Exception):
    """Raised on transient registry errors (network, 5xx, malformed JSON).

    Caller should treat this as "protection set is incomplete" and refuse to
    prune. Genuine 404 responses (the manifest really is not there) do NOT
    raise.
    """


class ProtectionResult(NamedTuple):
    """Outcome of :func:`collect_referenced_digests`.

    ``complete=False`` means at least one registry call failed transiently;
    callers must treat ``digests`` as a strict undercount and skip pruning
    that package.
    """

    digests: set[str]
    complete: bool


def _exchange_registry_token(
    client: GhcrClient,
    owner: str,
    package_name: str,
    github_token: str,
) -> str | None:
    """Exchange ``GITHUB_TOKEN`` for a ghcr.io registry pull bearer token.

    GHCR rejects the GitHub API token directly on its registry endpoints; the
    Docker registry token-exchange flow returns a short-lived bearer scoped
    to the requested package.

    Args:
        client: Authenticated HTTP client (only its ``get`` is used).
        owner: Package owner (user/org).
        package_name: Container package name.
        github_token: A token with ``read:packages`` scope.

    Returns:
        Registry bearer token string, or ``None`` if the exchange fails or
        returns an unparseable / token-less response.
    """
    auth = base64.b64encode(f"x:{github_token}".encode()).decode()
    url = (
        "https://ghcr.io/token"
        f"?service=ghcr.io&scope=repository:{owner}/{package_name}:pull"
    )
    try:
        resp = client.get(url, headers={"Authorization": f"Basic {auth}"})
        resp.raise_for_status()
    except httpx.HTTPError as e:
        logger.warning(
            "Registry token exchange failed for {}/{}: {}",
            owner,
            package_name,
            e,
        )
        return None
    try:
        data: Any = resp.json()
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    token = data.get("token") or data.get("access_token")
    return str(token) if token else None


def fetch_manifest(
    client: GhcrClient,
    owner: str,
    package_name: str,
    digest: str,
    registry_token: str,
) -> dict[str, Any] | None:
    """Fetch a manifest from ghcr.io by digest.

    Returns ``None`` only on a genuine 404 (manifest is not there). Any other
    error (network, 5xx, malformed JSON, non-dict payload) raises
    :class:`RegistryFetchError` so the caller can short-circuit pruning.

    Args:
        client: Authenticated HTTP client.
        owner: Package owner.
        package_name: Container package name.
        digest: Manifest digest including ``sha256:`` prefix.
        registry_token: Bearer token from :func:`_exchange_registry_token`.

    Returns:
        Parsed manifest JSON, or ``None`` if the manifest is genuinely absent.

    Raises:
        RegistryFetchError: Transient error; protection set is incomplete.
    """
    url = f"https://ghcr.io/v2/{owner}/{package_name}/manifests/{digest}"
    headers = {
        "Authorization": f"Bearer {registry_token}",
        "Accept": _MANIFEST_ACCEPT,
    }
    try:
        resp = client.get(url, headers=headers)
    except httpx.HTTPError as e:
        msg = f"Manifest fetch failed for {digest}: {e}"
        logger.warning(msg)
        raise RegistryFetchError(msg) from e
    if resp.status_code == 404:
        return None
    if resp.status_code >= 400:
        msg = f"Manifest fetch returned {resp.status_code} for {digest}"
        logger.warning(msg)
        raise RegistryFetchError(msg)
    try:
        data: Any = resp.json()
    except ValueError as e:
        msg = f"Manifest body for {digest} is not valid JSON"
        logger.warning(msg)
        raise RegistryFetchError(msg) from e
    if not isinstance(data, dict):
        msg = f"Manifest body for {digest} is not a JSON object"
        logger.warning(msg)
        raise RegistryFetchError(msg)
    return data


def fetch_referrers(
    client: GhcrClient,
    owner: str,
    package_name: str,
    digest: str,
    registry_token: str,
) -> list[dict[str, Any]]:
    """Return descriptors from the OCI Referrers API for ``digest``.

    The Referrers API (``GET /v2/<name>/referrers/<digest>``, OCI v1.1)
    returns an image-index listing every manifest whose ``subject`` points
    at ``digest`` — how cosign / in-toto attestations are discovered when
    they have no tag of their own.

    Returns ``[]`` only on a genuine 404 (registry without OCI 1.1 support
    or no referrers exist). Any other failure raises
    :class:`RegistryFetchError`.

    Args:
        client: Authenticated HTTP client.
        owner: Package owner.
        package_name: Container package name.
        digest: Subject digest including ``sha256:`` prefix.
        registry_token: Bearer token from :func:`_exchange_registry_token`.

    Returns:
        List of referrer descriptors (each a dict with at least ``digest``);
        empty when the endpoint returns 404.

    Raises:
        RegistryFetchError: Transient error; protection set is incomplete.
    """
    url = f"https://ghcr.io/v2/{owner}/{package_name}/referrers/{digest}"
    headers = {
        "Authorization": f"Bearer {registry_token}",
        "Accept": _REFERRERS_ACCEPT,
    }
    try:
        resp = client.get(url, headers=headers)
    except httpx.HTTPError as e:
        msg = f"Referrers fetch failed for {digest}: {e}"
        logger.warning(msg)
        raise RegistryFetchError(msg) from e
    if resp.status_code == 404:
        return []
    if resp.status_code >= 400:
        msg = f"Referrers fetch returned {resp.status_code} for {digest}"
        logger.warning(msg)
        raise RegistryFetchError(msg)
    try:
        data: Any = resp.json()
    except ValueError as e:
        msg = f"Referrers body for {digest} is not valid JSON"
        logger.warning(msg)
        raise RegistryFetchError(msg) from e
    if not isinstance(data, dict):
        msg = f"Referrers body for {digest} is not a JSON object"
        logger.warning(msg)
        raise RegistryFetchError(msg)
    descriptors = data.get("manifests") or []
    return [d for d in descriptors if isinstance(d, dict)]


def collect_referenced_digests(
    client: GhcrClient,
    owner: str,
    package_name: str,
    versions: list[GhcrVersion],
    registry_token: str,
) -> ProtectionResult:
    """Collect digests referenced by any tagged manifest in ``versions``.

    For each tagged version, fetches its manifest and the Referrers index,
    and records:

    - Every ``manifests[].digest`` entry (image-index / manifest-list children
      — multi-arch, provenance / SBOM children of a tagged index).
    - Every descriptor returned by the OCI Referrers API for the tagged
      digest (untagged cosign / in-toto attestations whose ``subject``
      points at the tagged image).
    - The ``subject.digest`` of the tagged manifest itself, when present
      (defense-in-depth for older attestation layouts).

    If any registry call fails transiently, the returned ``complete`` flag
    is ``False``. Callers must treat ``digests`` as an undercount and skip
    pruning the package on that run.

    Args:
        client: Authenticated HTTP client.
        owner: Package owner.
        package_name: Container package name.
        versions: All known versions for the package.
        registry_token: Bearer token for ghcr.io.

    Returns:
        :class:`ProtectionResult` with the digest set and the completeness
        flag.
    """
    referenced: set[str] = set()
    complete = True
    for v in versions:
        if not v.tags:
            continue
        if not v.name.startswith("sha256:"):
            continue
        try:
            manifest = fetch_manifest(
                client=client,
                owner=owner,
                package_name=package_name,
                digest=v.name,
                registry_token=registry_token,
            )
        except RegistryFetchError:
            complete = False
            manifest = None
        if manifest is not None:
            for child in manifest.get("manifests") or []:
                digest = child.get("digest") if isinstance(child, dict) else None
                if isinstance(digest, str):
                    referenced.add(digest)
            subject = manifest.get("subject")
            if isinstance(subject, dict):
                subject_digest = subject.get("digest")
                if isinstance(subject_digest, str):
                    referenced.add(subject_digest)
        try:
            descriptors = fetch_referrers(
                client=client,
                owner=owner,
                package_name=package_name,
                digest=v.name,
                registry_token=registry_token,
            )
        except RegistryFetchError:
            complete = False
            descriptors = []
        for descriptor in descriptors:
            ref_digest = descriptor.get("digest")
            if isinstance(ref_digest, str):
                referenced.add(ref_digest)
    return ProtectionResult(digests=referenced, complete=complete)
