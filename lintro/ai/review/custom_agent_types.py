"""Types for user-defined review agents (issue #1245).

Split out of :mod:`lintro.ai.review.custom_agents` (#2301): the schema types,
the configuration error, and the workspace directory they are read from live
here, the front-matter parser lives in
:mod:`lintro.ai.review.custom_agent_parsing`, and discovery and selection stay
in :mod:`lintro.ai.review.custom_agents`, which re-exports every public name.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, auto
from pathlib import Path

from lintro.ai.review.enums.review_strictness import ReviewStrictness
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
]

CUSTOM_AGENT_DIR_PARTS: tuple[str, ...] = (".lintro", "review-agents")
"""Workspace-relative directory holding custom review agent markdown files."""


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
