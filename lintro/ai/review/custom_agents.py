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

from pathlib import Path

from lintro.ai.review.custom_agent_parsing import (
    parse_custom_agent,
    split_front_matter,
)
from lintro.ai.review.custom_agent_types import (
    CUSTOM_AGENT_DIR_PARTS,
    CustomAgentConfigError,
    CustomAgentDiscovery,
    CustomAgentIssue,
    CustomAgentSelection,
    CustomAgentSkipReason,
    CustomAgentSpec,
    SelectedCustomAgent,
    SkippedCustomAgent,
    custom_agent_directory,
)
from lintro.ai.review.enums.custom_agent_mode import CustomAgentMode
from lintro.ai.review.glob_utils import normalize_path, path_matches_any_glob

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


def format_custom_agent_listing(
    *,
    discovery: CustomAgentDiscovery,
    mode: CustomAgentMode | None = None,
) -> str:
    """Render discovered agents for ``lintro review --list-agents``.

    Args:
        discovery: Discovery result to render.
        mode: Effective ``review.custom_agents`` mode. When provided, a
            header line reports it and warns when it is ``disabled`` --
            listed agents never run in that configuration.

    Returns:
        Plain-text listing of agents and any structured configuration errors.
    """
    lines: list[str] = [f"Custom review agents: {discovery.directory}"]
    if mode is not None:
        lines.append(f"Effective mode: {mode.value}")
        if mode is CustomAgentMode.DISABLED:
            lines.append(
                "  warning: review.custom_agents is disabled -- none of the "
                "agents below will run during a real review.",
            )
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
