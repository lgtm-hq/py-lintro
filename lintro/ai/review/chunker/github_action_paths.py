"""Local GitHub Action path resolution for workflow ``uses:`` matching.

Resolves an action implementation file (under ``.github/actions/``) up to its
action root directory, so a changed source, build artifact, or manifest file can
be matched against a workflow ``uses: ./.github/actions/<name>`` reference.
"""

from __future__ import annotations

from pathlib import PurePosixPath

_NON_EXECUTABLE_WORKFLOW_SUFFIXES = frozenset(
    {".cfg", ".ini", ".json", ".md", ".rst", ".toml", ".txt", ".yaml", ".yml"},
)
_ACTION_BUILD_ARTIFACT_DIRS = frozenset(
    {"coverage", "dist", "lib", "node_modules", "out", "vendor"},
)
_ACTION_SOURCE_LAYOUT_DIRS = frozenset({"source", "src"})
# Manifests that define a local action's runtime dependencies or entrypoint, so
# changes to them are relevant to the workflow that invokes the action.
_ACTION_MANIFEST_NAMES = frozenset(
    {
        "package.json",
        "package-lock.json",
        "pnpm-lock.yaml",
        "bun.lock",
        "bun.lockb",
        "yarn.lock",
    },
)


def _resolve_github_action_root(*, path: PurePosixPath) -> PurePosixPath | None:
    """Walk up from an action implementation file to the local action root."""
    parts = path.parts
    if len(parts) < 3 or parts[:2] != (".github", "actions"):
        return None
    if (
        path.name in {"action.yml", "action.yaml"}
        or path.name in _ACTION_MANIFEST_NAMES
    ):
        dir_path = path.parent
    elif path.suffix.lower() in _NON_EXECUTABLE_WORKFLOW_SUFFIXES:
        return None
    elif (
        path.name == "Dockerfile"
        or bool(path.suffix)
        or path.name in {"run", "entrypoint"}
    ):
        dir_path = path.parent
    else:
        return None
    dir_parts = dir_path.parts
    artifact_index = next(
        (
            index
            for index in range(3, len(dir_parts))
            if dir_parts[index] in _ACTION_BUILD_ARTIFACT_DIRS
        ),
        None,
    )
    if artifact_index is not None:
        return PurePosixPath(*dir_parts[:artifact_index])
    source_index = next(
        (
            index
            for index in range(3, len(dir_parts))
            if dir_parts[index] in _ACTION_SOURCE_LAYOUT_DIRS
        ),
        None,
    )
    if source_index is not None:
        return PurePosixPath(*dir_parts[:source_index])
    if len(dir_path.parts) >= 3 and dir_path.parts[:2] == (".github", "actions"):
        return dir_path
    return None


def _github_action_directory(*, path: str) -> str | None:
    """Return the local action directory represented by an action implementation."""
    action_root = _resolve_github_action_root(path=PurePosixPath(path))
    if action_root is None:
        return None
    return action_root.as_posix()


def _github_action_reference_paths(*, path: str) -> list[str]:
    """Return the local action directory for workflow ``uses:`` matching."""
    action_dir = _github_action_directory(path=path)
    if action_dir is None:
        return []
    return [action_dir]
