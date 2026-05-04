"""GitHub Packages REST API helpers.

Listing, deleting, owner-type lookup, and base-path resolution. Pagination
follows the standard ``Link: <...>; rel="next"`` header.
"""

from __future__ import annotations

import os
from typing import Any

from loguru import logger

from .protocols import GhcrClient
from .version import GhcrVersion


def get_repo_owner_repo() -> tuple[str, str]:
    """Return ``(owner, repo)`` parsed from ``GITHUB_REPOSITORY`` env.

    Falls back to ``lgtm-hq/py-lintro`` for local invocations.

    Returns:
        Tuple of ``(owner, repo)``.
    """
    repo = os.environ.get("GITHUB_REPOSITORY", "lgtm-hq/py-lintro")
    owner, name = repo.split("/", 1)
    return owner, name


def _parse_link_header(link_header: str | None) -> str | None:
    """Extract the ``rel="next"`` URL from a GitHub Link header.

    Args:
        link_header: Raw ``Link`` header value.

    Returns:
        Next-page URL or ``None``.
    """
    if not link_header:
        return None
    for part in link_header.split(","):
        candidate = part.strip()
        if 'rel="next"' in candidate:
            start = candidate.find("<")
            end = candidate.find(">")
            if start != -1 and end != -1:
                return candidate[start + 1 : end]
    return None


def _parse_version_item(item: dict[str, Any]) -> GhcrVersion | None:
    """Parse one raw API version dict into a :class:`GhcrVersion`.

    Args:
        item: Dictionary from the GitHub Packages API response.

    Returns:
        :class:`GhcrVersion` or ``None`` if the entry is malformed.
    """
    vid_raw = item.get("id")
    if vid_raw is None:
        logger.error(
            "API response missing 'id' field for item with created_at: {}",
            item.get("created_at", "unknown"),
        )
        return None
    try:
        vid = int(vid_raw)
    except (ValueError, TypeError) as e:
        logger.error(
            "Invalid 'id' value '{}' for item with created_at: {} - {}",
            vid_raw,
            item.get("created_at", "unknown"),
            e,
        )
        return None
    metadata = item.get("metadata")
    container = metadata.get("container") if isinstance(metadata, dict) else None
    raw_tags = container.get("tags") if isinstance(container, dict) else None
    tags = list(raw_tags) if isinstance(raw_tags, list) else []
    return GhcrVersion(
        id=vid,
        tags=tags,
        created_at=str(item.get("created_at", "")),
        name=str(item.get("name", "")),
    )


def _get_owner_type(client: GhcrClient, owner: str) -> str:
    """Return ``"Organization"`` or ``"User"`` for ``owner`` via the API.

    Args:
        client: Authenticated HTTP client.
        owner: GitHub user or organisation name.

    Returns:
        ``"Organization"`` or ``"User"``.
    """
    resp = client.get(
        f"https://api.github.com/users/{owner}",
        headers={"Accept": "application/vnd.github+json"},
    )
    resp.raise_for_status()
    data: dict[str, Any] = resp.json()
    return str(data.get("type", "User"))


def resolve_base_path(client: GhcrClient, owner: str) -> str:
    """Return the GHCR API base path (``/orgs/`` vs ``/users/``).

    Args:
        client: Authenticated HTTP client.
        owner: Repository owner.

    Returns:
        Base URL up to ``/packages/container``.
    """
    owner_type = _get_owner_type(client=client, owner=owner)
    if owner_type == "Organization":
        return f"https://api.github.com/orgs/{owner}/packages/container"
    return f"https://api.github.com/users/{owner}/packages/container"


def list_container_versions(
    client: GhcrClient,
    owner: str,
    package_name: str = "py-lintro",
    base_path: str | None = None,
) -> list[GhcrVersion]:
    """List every container version for ``package_name``, paginated.

    Args:
        client: Authenticated HTTP client.
        owner: Repository owner.
        package_name: Container package name.
        base_path: Pre-computed API base path (avoids repeated owner-type
            lookups).

    Returns:
        Parsed version list.
    """
    versions: list[GhcrVersion] = []
    if base_path is None:
        base_path = resolve_base_path(client=client, owner=owner)

    url: str | None = f"{base_path}/{package_name}/versions?per_page=100"
    while url:
        resp = client.get(url, headers={"Accept": "application/vnd.github+json"})
        resp.raise_for_status()
        data: Any = resp.json()
        if not isinstance(data, list):
            logger.warning(
                "Expected list payload from {} for {}, got {}; treating as empty",
                url,
                package_name,
                type(data).__name__,
            )
            break
        for item in data:
            if not isinstance(item, dict):
                logger.warning(
                    "Skipping non-dict version entry of type {} in {}",
                    type(item).__name__,
                    package_name,
                )
                continue
            version = _parse_version_item(item=item)
            if version is not None:
                versions.append(version)
        url = _parse_link_header(link_header=resp.headers.get("link"))
    return versions


def delete_version(
    client: GhcrClient,
    owner: str,
    version_id: int,
    package_name: str = "py-lintro",
    base_path: str | None = None,
) -> None:
    """Delete a container version by id.

    204 (success) and 404 (already gone) are both treated as success.

    Args:
        client: Authenticated HTTP client.
        owner: Repository owner.
        version_id: GHCR version id to delete.
        package_name: Container package name.
        base_path: Pre-computed API base path.
    """
    if base_path is None:
        base_path = resolve_base_path(client=client, owner=owner)
    url = f"{base_path}/{package_name}/versions/{version_id}"
    resp = client.delete(url, headers={"Accept": "application/vnd.github+json"})
    if resp.status_code not in (204, 404):
        resp.raise_for_status()
