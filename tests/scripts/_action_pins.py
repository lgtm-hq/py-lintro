"""Derive the repo's pinned action SHAs from the workflow files themselves.

Hardcoding ``owner/repo@sha`` literals in tests (#2432) made every Renovate
action bump red on its own: Renovate rewrites the ``uses:`` refs it can see and
cannot see a constant in a test module, so the bump landed with the workflows
correct and the tests still comparing against the previous SHA (e.g. #2423).

The workflow files are therefore the source of truth. This module reads every
``uses:`` line under ``.github/workflows`` and ``.github/actions``, groups the
refs by action, and asserts the invariants that actually matter: a third-party
action is pinned to a full 40-character commit SHA, carries a ``# vX.Y.Z``
version comment, and is pinned to the *same* SHA everywhere it is used.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from assertpy import assert_that

REPO_ROOT = Path(__file__).resolve().parents[2]

#: A ``uses:`` line, optionally the first entry of a step list.
_USES_RE = re.compile(r"^\s*(?:-\s+)?uses:\s+(?P<ref>\S+)(?P<rest>.*)$")
#: A full commit SHA, the only ref shape a third-party action may be pinned to.
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
#: The trailing ``# v1.2.3`` comment Renovate maintains next to each pin.
_VERSION_COMMENT_RE = re.compile(r"^#\s*v\d+(?:\.\d+)*")
#: Refs resolved inside this repository; they are not pinned and never can be.
_LOCAL_PREFIXES = ("./", "../")


@dataclass(frozen=True)
class ActionUse:
    """A single third-party ``uses:`` reference found in a workflow file.

    Attributes:
        path: File the reference was read from.
        lineno: 1-based line number of the reference.
        action: Action identifier, ``owner/repo`` or ``owner/repo/path``.
        ref: Everything after the ``@``, normally a 40-character commit SHA.
        comment: Trailing comment on the line, empty when there is none.
    """

    path: Path
    lineno: int
    action: str
    ref: str
    comment: str

    @property
    def location(self) -> str:
        """Return a ``file:line`` label for assertion messages.

        Returns:
            The file name and line number of this reference.
        """
        return f"{self.path.name}:{self.lineno}"


def workflow_paths(*, root: Path = REPO_ROOT) -> list[Path]:
    """Return every workflow and composite-action file, in stable order.

    Args:
        root: Repository root to scan.

    Returns:
        Sorted list of YAML files under ``.github/workflows`` and
        ``.github/actions``.
    """
    github_dir = root / ".github"
    paths: set[Path] = set()
    for directory in (github_dir / "workflows", github_dir / "actions"):
        for suffix in ("yml", "yaml"):
            paths.update(directory.rglob(f"*.{suffix}"))
    return sorted(paths)


def iter_action_uses(*, root: Path = REPO_ROOT) -> list[ActionUse]:
    """Return every third-party ``uses:`` reference in the repo's workflows.

    Local references (``./.github/...``) are skipped: they resolve inside the
    repository and have nothing to pin.

    Args:
        root: Repository root to scan.

    Returns:
        Every third-party reference found, in file then line order.
    """
    uses: list[ActionUse] = []
    for path in workflow_paths(root=root):
        uses.extend(_file_action_uses(path))
    return uses


def _file_action_uses(path: Path) -> list[ActionUse]:
    """Return the third-party ``uses:`` references in one file.

    Args:
        path: Workflow or composite-action file to read.

    Returns:
        Every third-party reference in the file, in line order.
    """
    uses: list[ActionUse] = []
    for lineno, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        match = _USES_RE.match(line)
        if match is None:
            continue
        ref = match.group("ref").strip("\"'")
        if ref.startswith(_LOCAL_PREFIXES) or "@" not in ref:
            continue
        action, _, pin = ref.partition("@")
        uses.append(
            ActionUse(
                path=path,
                lineno=lineno,
                action=action,
                ref=pin.strip("\"'"),
                comment=match.group("rest").strip(),
            ),
        )
    return uses


def pinned_action_shas(*, root: Path = REPO_ROOT) -> dict[str, str]:
    """Return the commit SHA each third-party action is pinned to.

    Asserts, over every ``uses:`` reference in the repository, that the ref is
    a 40-character commit SHA followed by a ``# vX.Y.Z``-style version comment,
    and that every workflow pins a given action to the same SHA.

    Args:
        root: Repository root to scan.

    Returns:
        Mapping of ``owner/repo[/path]`` to the 40-character commit SHA.
    """
    uses = iter_action_uses(root=root)
    assert_that(uses).described_as("no third-party `uses:` refs found").is_not_empty()

    unpinned = [
        f"{use.location}: {use.action}@{use.ref}"
        for use in uses
        if not _SHA_RE.match(use.ref)
    ]
    assert_that(unpinned).described_as(
        "third-party actions must be pinned to a full 40-character commit SHA",
    ).is_empty()

    uncommented = [
        f"{use.location}: {use.action} ({use.comment or 'no comment'})"
        for use in uses
        if not _VERSION_COMMENT_RE.match(use.comment)
    ]
    assert_that(uncommented).described_as(
        "each pinned action needs a trailing `# vX.Y.Z` version comment",
    ).is_empty()

    pins: dict[str, str] = {}
    drift: list[str] = []
    for use in uses:
        pinned = pins.setdefault(use.action, use.ref)
        if pinned != use.ref:
            drift.append(f"{use.location}: {use.action}@{use.ref} != {pinned}")
    assert_that(drift).described_as(
        "every workflow must pin an action to the same commit SHA",
    ).is_empty()
    return pins


@cache
def _repo_pins() -> dict[str, str]:
    """Return the repo's derived pins, computed once per session.

    Returns:
        Mapping of ``owner/repo[/path]`` to its pinned commit SHA.
    """
    return pinned_action_shas()


def action_pin(action: str, *, root: Path | None = None) -> str:
    """Return the ``owner/repo@sha`` reference the repo pins ``action`` to.

    Args:
        action: Action identifier, ``owner/repo`` or ``owner/repo/path``.
        root: Repository root to scan; defaults to the cached repo scan.

    Returns:
        The pinned reference, ready to compare against a workflow ``uses:``.
    """
    pins = _repo_pins() if root is None else pinned_action_shas(root=root)
    assert_that(pins).described_as(
        f"{action} is not used by any workflow",
    ).contains_key(action)
    return f"{action}@{pins[action]}"


def actions_used_in(path: Path) -> set[str]:
    """Return the third-party actions a single workflow file references.

    Args:
        path: Workflow or composite-action file to read.

    Returns:
        The ``owner/repo[/path]`` identifiers used by that file.
    """
    return {use.action for use in _file_action_uses(path)}
