"""User-defined review agents declared as markdown files (issue #1245).

Teams encode house review rules as markdown files under
``.lintro/review-agents/*.md``. YAML front matter carries the machine-readable
configuration (scope globs, severity policy, strictness, optional model
override) and the markdown body carries the review instruction prose:

.. code-block:: markdown

    ---
    name: no-raw-sql
    description: SQL must go through the repository layer
    include:
      - "src/**/*.py"
    exclude:
      - "src/repositories/**"
    severity: high
    strictness: focused
    enabled: true
    ---

    Flag any direct cursor.execute/connection.execute calls with string
    literals outside the repository layer.

Front matter is split with a small hand-rolled ``---`` splitter and parsed with
the YAML loader lintro already depends on, so no new dependency is introduced.

Invalid files are never fatal: each parse or validation failure is captured as
a :class:`CustomAgentIssue` naming the offending file and field, the agent is
skipped, and the review run continues with the remaining agents.

Relation to the built-in checklist corpus (#1031): the schema below is
deliberately a superset of what a corpus row needs (scope predicate, severity,
prose). If the built-in corpus is ever externalized to authored files, it can
migrate to this same front-matter format rather than inventing a second one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum, auto
from pathlib import Path
from typing import Any

import yaml

from lintro.ai.review.enums.review_strictness import ReviewStrictness
from lintro.ai.review.finding_parser import parse_severity_label
from lintro.ai.review.glob_utils import normalize_path, path_matches_any_glob
from lintro.ai.review.models.review_finding import Severity

__all__ = [
    "CUSTOM_AGENT_DIR_PARTS",
    "CustomAgentConfigError",
    "CustomAgentDiscovery",
    "CustomAgentIssue",
    "CustomAgentSelection",
    "CustomAgentSkipReason",
    "CustomAgentSpec",
    "SelectedCustomAgent",
    "SkippedCustomAgent",
    "custom_agent_directory",
    "discover_custom_agents",
    "files_for_agent",
    "format_custom_agent_listing",
    "parse_custom_agent",
    "select_custom_agents",
    "split_front_matter",
]

CUSTOM_AGENT_DIR_PARTS: tuple[str, ...] = (".lintro", "review-agents")
"""Workspace-relative directory holding custom review agent markdown files."""

_FRONT_MATTER_FENCE = "---"
_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_KNOWN_FIELDS: frozenset[str] = frozenset(
    {
        "name",
        "description",
        "include",
        "exclude",
        "severity",
        "strictness",
        "model",
        "enabled",
    },
)


class CustomAgentSkipReason(StrEnum):
    """Why a valid custom review agent did not run for this diff.

    * **disabled** — the agent declares ``enabled: false``.
    * **no-matching-files** — no changed file matched the agent's
      ``include`` globs after ``exclude`` globs were applied.
    """

    DISABLED = auto()
    NO_MATCHING_FILES = auto()


@dataclass(frozen=True, slots=True)
class CustomAgentSpec:
    """A validated user-defined review agent.

    Attributes:
        name: Unique agent identifier, used as finding ``source`` attribution.
        description: One-line human summary of what the agent checks.
        include: Globs selecting the changed files the agent reviews.
        exclude: Globs removing files from the ``include`` set.
        severity: Severity assigned to every finding the agent reports. The
            author-declared severity policy wins over the model's own label.
        strictness: Sensitivity preset applied to this agent's pass.
        model: Optional model override for this agent's provider call.
        enabled: Whether the agent participates in review runs.
        body: Review instruction prose (markdown body, verbatim).
        path: Absolute path to the source markdown file.
    """

    name: str
    description: str
    include: tuple[str, ...]
    exclude: tuple[str, ...]
    severity: Severity
    strictness: ReviewStrictness
    model: str | None
    enabled: bool
    body: str
    path: Path


@dataclass(frozen=True, slots=True)
class CustomAgentIssue:
    """A structured configuration error for one custom agent file.

    Attributes:
        path: Absolute path to the offending markdown file.
        field: Front-matter field responsible for the failure, or a pseudo
            field such as ``front-matter`` / ``body`` when the failure is not
            attributable to a single declared key.
        message: Human-readable explanation of what is wrong.
    """

    path: Path
    field: str
    message: str

    def format(self) -> str:
        """Render the issue as a single reportable line.

        Returns:
            A string of the form ``<file>: <field> — <message>``.
        """
        return f"{self.path.name}: {self.field} — {self.message}"


class CustomAgentConfigError(Exception):
    """Raised when a custom review agent file fails schema validation.

    The offending front-matter field is carried on the ``field`` instance
    attribute so callers can report it verbatim.
    """

    def __init__(self, *, field: str, message: str) -> None:
        """Initialize the error.

        Args:
            field: Front-matter field responsible for the failure.
            message: Human-readable explanation of what is wrong.
        """
        super().__init__(message)
        self.field = field


@dataclass(frozen=True, slots=True)
class CustomAgentDiscovery:
    """Result of scanning the workspace for custom review agents.

    Attributes:
        directory: Directory that was scanned (may not exist).
        agents: Successfully parsed agents in file-name order.
        issues: Structured configuration errors for skipped files.
    """

    directory: Path
    agents: tuple[CustomAgentSpec, ...] = ()
    issues: tuple[CustomAgentIssue, ...] = ()


@dataclass(frozen=True, slots=True)
class SelectedCustomAgent:
    """A custom agent that will run, with the files it is scoped to.

    Attributes:
        agent: The agent specification.
        files: Changed files matching the agent's include/exclude globs.
    """

    agent: CustomAgentSpec
    files: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SkippedCustomAgent:
    """A valid custom agent that did not run for this diff.

    Attributes:
        agent: The agent specification.
        reason: Why the agent was skipped.
    """

    agent: CustomAgentSpec
    reason: CustomAgentSkipReason


@dataclass(frozen=True, slots=True)
class CustomAgentSelection:
    """Custom agents partitioned into those that run and those skipped.

    Attributes:
        selected: Agents scoped to at least one changed file.
        skipped: Agents that are disabled or match no changed file.
    """

    selected: tuple[SelectedCustomAgent, ...] = ()
    skipped: tuple[SkippedCustomAgent, ...] = ()


def custom_agent_directory(*, workspace_root: Path) -> Path:
    """Return the custom review agent directory for a workspace.

    Args:
        workspace_root: Absolute workspace root.

    Returns:
        The ``.lintro/review-agents`` directory path (may not exist).
    """
    return workspace_root.joinpath(*CUSTOM_AGENT_DIR_PARTS)


def split_front_matter(*, text: str) -> tuple[str, str]:
    """Split a markdown document into YAML front matter and body.

    The document must open with a ``---`` fence on its first non-empty line
    and close with a matching ``---`` fence on its own line.

    Args:
        text: Full markdown file contents.

    Returns:
        Tuple of raw front-matter YAML text and the markdown body.

    Raises:
        CustomAgentConfigError: When the opening or closing fence is missing.
    """
    stripped = text.lstrip("﻿")
    lines = stripped.splitlines()
    first_content = next(
        (index for index, line in enumerate(lines) if line.strip()),
        None,
    )
    if first_content is None or lines[first_content].strip() != _FRONT_MATTER_FENCE:
        raise CustomAgentConfigError(
            field="front-matter",
            message=("file must start with a YAML front-matter block fenced by '---'"),
        )

    for index in range(first_content + 1, len(lines)):
        if lines[index].strip() == _FRONT_MATTER_FENCE:
            front_matter = "\n".join(lines[first_content + 1 : index])
            body = "\n".join(lines[index + 1 :])
            return front_matter, body

    raise CustomAgentConfigError(
        field="front-matter",
        message="front-matter block is not closed by a '---' fence",
    )


def _load_front_matter(*, front_matter: str) -> dict[str, Any]:
    """Parse the front-matter YAML into a mapping.

    Args:
        front_matter: Raw YAML text between the ``---`` fences.

    Returns:
        The parsed mapping.

    Raises:
        CustomAgentConfigError: When the YAML is invalid, empty, or not a
            mapping, or when it declares unknown fields.
    """
    try:
        parsed = yaml.safe_load(front_matter)
    except yaml.YAMLError as error:
        raise CustomAgentConfigError(
            field="front-matter",
            message=f"front matter is not valid YAML: {error}",
        ) from error

    if parsed is None:
        raise CustomAgentConfigError(
            field="front-matter",
            message="front matter is empty",
        )
    if not isinstance(parsed, dict):
        raise CustomAgentConfigError(
            field="front-matter",
            message="front matter must be a YAML mapping",
        )

    unknown = sorted(set(parsed) - _KNOWN_FIELDS)
    if unknown:
        raise CustomAgentConfigError(
            field=unknown[0],
            message=(
                f"unknown front-matter field(s): {', '.join(unknown)}; "
                f"known fields: {', '.join(sorted(_KNOWN_FIELDS))}"
            ),
        )
    return parsed


def _require_name(*, raw: object) -> str:
    """Validate the ``name`` field.

    Args:
        raw: Raw ``name`` value from front matter.

    Returns:
        The validated agent name.

    Raises:
        CustomAgentConfigError: When the name is missing or malformed.
    """
    if raw is None:
        raise CustomAgentConfigError(field="name", message="is required")
    if not isinstance(raw, str) or not _NAME_PATTERN.match(raw.strip()):
        raise CustomAgentConfigError(
            field="name",
            message=(
                "must be a short identifier of letters, digits, '.', '_' or "
                f"'-' (got {raw!r})"
            ),
        )
    return raw.strip()


def _parse_globs(*, raw: object, field: str, required: bool) -> tuple[str, ...]:
    """Validate a glob list field.

    Args:
        raw: Raw field value from front matter.
        field: Field name, used in error messages.
        required: Whether at least one glob must be present.

    Returns:
        The validated glob patterns.

    Raises:
        CustomAgentConfigError: When the value is not a list of non-empty
            strings, or is empty while required.
    """
    if raw is None:
        if required:
            raise CustomAgentConfigError(
                field=field,
                message="is required and must list at least one glob pattern",
            )
        return ()
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        raise CustomAgentConfigError(
            field=field,
            message="must be a list of glob patterns",
        )
    patterns: list[str] = []
    for entry in raw:
        if not isinstance(entry, str) or not entry.strip():
            raise CustomAgentConfigError(
                field=field,
                message=f"glob patterns must be non-empty strings (got {entry!r})",
            )
        patterns.append(entry.strip())
    if required and not patterns:
        raise CustomAgentConfigError(
            field=field,
            message="is required and must list at least one glob pattern",
        )
    return tuple(patterns)


def _parse_severity(*, raw: object) -> Severity:
    """Validate the ``severity`` field.

    Args:
        raw: Raw ``severity`` value from front matter.

    Returns:
        The resolved severity, defaulting to ``P2``.

    Raises:
        CustomAgentConfigError: When the label is not recognized.
    """
    if raw is None:
        return Severity.P2
    resolved = parse_severity_label(raw=raw)
    if resolved is None:
        raise CustomAgentConfigError(
            field="severity",
            message=(f"unknown severity {raw!r}; use P1/P2/P3 or high/medium/low"),
        )
    return resolved


def _parse_strictness(*, raw: object) -> ReviewStrictness:
    """Validate the ``strictness`` field.

    Args:
        raw: Raw ``strictness`` value from front matter.

    Returns:
        The resolved strictness preset, defaulting to ``balanced``.

    Raises:
        CustomAgentConfigError: When the preset is not recognized.
    """
    if raw is None:
        return ReviewStrictness.BALANCED
    try:
        return ReviewStrictness(str(raw).strip().lower())
    except ValueError as error:
        allowed = ", ".join(level.value for level in ReviewStrictness)
        raise CustomAgentConfigError(
            field="strictness",
            message=f"unknown strictness {raw!r}; expected one of: {allowed}",
        ) from error


def _parse_enabled(*, raw: object) -> bool:
    """Validate the ``enabled`` field.

    Args:
        raw: Raw ``enabled`` value from front matter.

    Returns:
        The resolved flag, defaulting to True.

    Raises:
        CustomAgentConfigError: When the value is not a boolean.
    """
    if raw is None:
        return True
    if not isinstance(raw, bool):
        raise CustomAgentConfigError(
            field="enabled",
            message=f"must be a boolean (got {raw!r})",
        )
    return raw


def _parse_model(*, raw: object) -> str | None:
    """Validate the optional ``model`` field.

    ``default`` is accepted as an explicit "use the configured model" spelling
    and normalizes to no override.

    Args:
        raw: Raw ``model`` value from front matter.

    Returns:
        The model override, or None when the configured model should be used.

    Raises:
        CustomAgentConfigError: When the value is not a non-empty string.
    """
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise CustomAgentConfigError(
            field="model",
            message=f"must be a non-empty model identifier (got {raw!r})",
        )
    model = raw.strip()
    return None if model.lower() == "default" else model


def _parse_description(*, raw: object) -> str:
    """Validate the optional ``description`` field.

    Args:
        raw: Raw ``description`` value from front matter.

    Returns:
        The description text, empty when unset.

    Raises:
        CustomAgentConfigError: When the value is not a string.
    """
    if raw is None:
        return ""
    if not isinstance(raw, str):
        raise CustomAgentConfigError(
            field="description",
            message=f"must be a string (got {raw!r})",
        )
    return " ".join(raw.split())


def parse_custom_agent(*, path: Path, text: str) -> CustomAgentSpec:
    """Parse and validate one custom review agent markdown file.

    Args:
        path: Absolute path to the markdown file (used for reporting).
        text: Full file contents.

    Returns:
        The validated agent specification.

    Raises:
        CustomAgentConfigError: When the front matter or body is invalid. The
            raised error names the offending field.
    """
    front_matter, body = split_front_matter(text=text)
    data = _load_front_matter(front_matter=front_matter)

    if not body.strip():
        raise CustomAgentConfigError(
            field="body",
            message="markdown body must contain the review instruction prose",
        )

    return CustomAgentSpec(
        name=_require_name(raw=data.get("name")),
        description=_parse_description(raw=data.get("description")),
        include=_parse_globs(raw=data.get("include"), field="include", required=True),
        exclude=_parse_globs(raw=data.get("exclude"), field="exclude", required=False),
        severity=_parse_severity(raw=data.get("severity")),
        strictness=_parse_strictness(raw=data.get("strictness")),
        model=_parse_model(raw=data.get("model")),
        enabled=_parse_enabled(raw=data.get("enabled")),
        body=body.strip(),
        path=path,
    )


def discover_custom_agents(*, workspace_root: Path) -> CustomAgentDiscovery:
    """Discover custom review agents under ``.lintro/review-agents``.

    Files are read in sorted file-name order for deterministic runs. A file
    that fails validation is recorded as an issue and skipped; it never aborts
    discovery of the remaining files.

    Args:
        workspace_root: Absolute workspace root to scan.

    Returns:
        Discovery result carrying the parsed agents and structured issues.
    """
    directory = custom_agent_directory(workspace_root=workspace_root)
    if not directory.is_dir():
        return CustomAgentDiscovery(directory=directory)

    agents: list[CustomAgentSpec] = []
    issues: list[CustomAgentIssue] = []
    seen: dict[str, Path] = {}
    for file_path in sorted(directory.glob("*.md")):
        if not file_path.is_file():
            continue
        try:
            text = file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            issues.append(
                CustomAgentIssue(
                    path=file_path,
                    field="file",
                    message=f"could not be read: {error}",
                ),
            )
            continue
        try:
            agent = parse_custom_agent(path=file_path, text=text)
        except CustomAgentConfigError as error:
            issues.append(
                CustomAgentIssue(
                    path=file_path,
                    field=error.field,
                    message=str(error),
                ),
            )
            continue
        previous = seen.get(agent.name)
        if previous is not None:
            issues.append(
                CustomAgentIssue(
                    path=file_path,
                    field="name",
                    message=(
                        f"duplicate agent name {agent.name!r} already declared "
                        f"in {previous.name}"
                    ),
                ),
            )
            continue
        seen[agent.name] = file_path
        agents.append(agent)

    return CustomAgentDiscovery(
        directory=directory,
        agents=tuple(agents),
        issues=tuple(issues),
    )


def files_for_agent(
    *,
    agent: CustomAgentSpec,
    changed_paths: tuple[str, ...],
) -> tuple[str, ...]:
    """Return the changed files an agent is scoped to.

    Args:
        agent: The agent whose globs are applied.
        changed_paths: Repository-relative changed file paths.

    Returns:
        Matching paths in input order.
    """
    matched: list[str] = []
    for raw_path in changed_paths:
        path = normalize_path(path=raw_path)
        if not path_matches_any_glob(path=path, patterns=agent.include):
            continue
        if agent.exclude and path_matches_any_glob(path=path, patterns=agent.exclude):
            continue
        matched.append(raw_path)
    return tuple(matched)


def select_custom_agents(
    *,
    agents: tuple[CustomAgentSpec, ...],
    changed_paths: tuple[str, ...],
) -> CustomAgentSelection:
    """Partition agents into those that run for a diff and those skipped.

    Args:
        agents: Validated agents from discovery.
        changed_paths: Repository-relative changed file paths in the diff.

    Returns:
        Selection carrying scoped agents and skipped agents with reasons.
    """
    selected: list[SelectedCustomAgent] = []
    skipped: list[SkippedCustomAgent] = []
    for agent in agents:
        if not agent.enabled:
            skipped.append(
                SkippedCustomAgent(
                    agent=agent,
                    reason=CustomAgentSkipReason.DISABLED,
                ),
            )
            continue
        files = files_for_agent(agent=agent, changed_paths=changed_paths)
        if not files:
            skipped.append(
                SkippedCustomAgent(
                    agent=agent,
                    reason=CustomAgentSkipReason.NO_MATCHING_FILES,
                ),
            )
            continue
        selected.append(SelectedCustomAgent(agent=agent, files=files))
    return CustomAgentSelection(selected=tuple(selected), skipped=tuple(skipped))


def format_custom_agent_listing(*, discovery: CustomAgentDiscovery) -> str:
    """Render discovered agents for ``lintro review --list-agents``.

    Args:
        discovery: Discovery result to render.

    Returns:
        Plain-text listing of agents and any structured configuration errors.
    """
    lines: list[str] = [f"Custom review agents: {discovery.directory}"]
    if not discovery.agents and not discovery.issues:
        lines.append("")
        lines.append("  (none found)")
        return "\n".join(lines)

    for agent in discovery.agents:
        state = "enabled" if agent.enabled else "disabled"
        lines.append("")
        lines.append(f"  {agent.name} ({state})")
        if agent.description:
            lines.append(f"    description: {agent.description}")
        lines.append(f"    include: {', '.join(agent.include)}")
        if agent.exclude:
            lines.append(f"    exclude: {', '.join(agent.exclude)}")
        lines.append(f"    severity: {agent.severity.value}")
        lines.append(f"    strictness: {agent.strictness.value}")
        lines.append(f"    model: {agent.model or '(default)'}")
        lines.append(f"    file: {agent.path.name}")

    if discovery.issues:
        lines.append("")
        lines.append(f"  Invalid agent files (skipped): {len(discovery.issues)}")
        for issue in discovery.issues:
            lines.append(f"    {issue.format()}")

    return "\n".join(lines)
