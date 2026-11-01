"""Manage explicit fingerprint acknowledgements without AI or suppression."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import click

from lintro.config.config_loader import get_config
from lintro.exceptions.errors import ConfigurationError
from lintro.utils.fingerprint_baseline import (
    baseline_incomplete_reason,
    maintain_baseline,
    snapshot,
)
from lintro.utils.fingerprint_baseline_file import (
    change_baseline,
    read_baseline,
    require_baseline_root,
    resolve_baseline_path,
)
from lintro.utils.tool_executor import build_run_context, execute_run
from lintro.utils.tool_options import parse_tool_options

if TYPE_CHECKING:
    from lintro.models.core.fingerprint_baseline import FingerprintBaseline
    from lintro.models.core.run_artifact import RunArtifact

BASELINE_SCAN_HELP = (
    "Certified check findings: ruff, black, shellcheck, hadolint, shfmt, "
    "dotenv-linter, pydoclint, sqlfluff. Use --tools to select supported tools. "
    "Default or mixed selections abort if any active result is incomplete or "
    "cannot certify its findings; no failing tool is silently omitted."
)


@click.group(
    "baseline",
    help="Create, update, prune, or inspect a fingerprint baseline. "
    + BASELINE_SCAN_HELP,
)
def baseline_command() -> None:
    """Create, update, prune, or inspect a fingerprint baseline."""


def _scan(
    paths: tuple[str, ...],
    *,
    tools: str | None,
    tool_options: str | None,
    exclude: str | None,
) -> RunArtifact:
    """Run an unsuppressed read-only scan without dependency installation.

    Args:
        paths: Workspace paths to inspect.
        tools: Selected tool names, or configured defaults.
        tool_options: Native tool overrides.
        exclude: Lintro exclusion patterns.

    Returns:
        Raw, complete check results.

    Raises:
        click.ClickException: If the scan cannot safely produce a snapshot.
    """
    ctx = build_run_context(action="check", output_format="json", no_art=True)
    config = ctx.lintro_config.model_copy(deep=True)
    config.execution.auto_install_deps = False
    for tool_config in config.tools.values():
        tool_config.auto_install = False
    ctx = replace(ctx, lintro_config=config)
    try:
        artifact = execute_run(
            ctx=ctx,
            paths=list(paths) or ["."],
            tools=tools,
            tool_options=",".join(
                filter(
                    None,
                    (
                        tool_options,
                        "ruff:_baseline_coverage=true",
                        "black:_baseline_coverage=true",
                    ),
                ),
            ),
            exclude=exclude,
            include_venv=False,
            group_by="file",
            output_format="json",
            verbose=False,
            yes=True,
        )
    finally:
        ctx.output_manager.mark_run_complete()
    active = [r for r in artifact.tool_results if not r.skipped and not r.no_files]
    blockers = [
        f"{result.name}: {reason}"
        for result in active
        if (reason := baseline_incomplete_reason(result)) is not None
    ]
    if artifact.early_exit:
        blockers.append("scan ended before tool execution")
    if not active:
        blockers.append("no active tool confirmed file inspection")
    if blockers:
        raise click.ClickException(
            "Baseline unchanged: "
            + "; ".join(blockers)
            + ". Use --tools to select supported tools; see 'lintro baseline --help'.",
        )
    # Exact file discovery does not prove that user overrides ran every rule.
    # Only original user options count; our internal coverage flags do not.
    overridden_tools = parse_tool_options(tool_options)
    for result in artifact.tool_results:
        if result.name.lower() in overridden_tools:
            result.inspected_paths = None
    return artifact


def _manage(
    mode: str,
    *,
    paths: tuple[str, ...],
    baseline: str | None,
    tools: str | None,
    tool_options: str | None,
    exclude: str | None,
) -> None:
    """Scan once and commit a conservative baseline transaction.

    Args:
        mode: Create, update, or prune.
        paths: Paths to inspect.
        baseline: Explicit baseline path.
        tools: Selected tool names.
        tool_options: Native tool overrides.
        exclude: Exclusion patterns.

    Raises:
        click.ClickException: If validation or persistence fails.
        ValueError: If creation would overwrite an existing file (wrapped below).
    """
    try:
        config = get_config()
        path = resolve_baseline_path(baseline, config, default=True)
        if path is None:
            raise ValueError("baseline path could not be resolved")
        root = require_baseline_root(path, config)
        if mode == "create":
            if path.exists() or path.is_symlink():
                raise ValueError(f"baseline already exists: {path}")
        else:
            read_baseline(path)  # Fail before running tools; reread under lock.
        artifact = _scan(
            paths,
            tools=tools,
            tool_options=tool_options,
            exclude=exclude,
        )
        current = snapshot(artifact.tool_results, root=root)

        def transform(previous: FingerprintBaseline | None) -> FingerprintBaseline:
            """Merge against the latest file while holding the writer lock."""
            if previous is None:
                return current
            return maintain_baseline(
                previous,
                current,
                results=artifact.tool_results,
                root=root,
                prune=mode == "prune",
            )

        updated = change_baseline(path, transform, create=mode == "create")
    except (OSError, ValueError, ConfigurationError) as exc:
        raise click.ClickException(str(exc)) from exc
    count = sum(entry.count for entry in updated.entries)
    click.echo(f"Baseline {mode}: {count} acknowledged findings in {path}")


def _register_management(mode: str, help_text: str) -> None:
    """Register a management scan with shared, typed options.

    Args:
        mode: Command name and transaction mode.
        help_text: CLI help for the operation.
    """

    @baseline_command.command(mode, help=help_text + " " + BASELINE_SCAN_HELP)
    @click.argument("paths", nargs=-1, type=click.Path(exists=True))
    @click.option("--baseline", type=click.Path(dir_okay=False), help="Baseline file.")
    @click.option("--tools", help="Comma-separated tools (or configured defaults).")
    @click.option("--tool-options", help="Tool-specific option overrides.")
    @click.option("--exclude", help="Comma-separated exclusion patterns.")
    def command(
        paths: tuple[str, ...],
        baseline: str | None,
        tools: str | None,
        tool_options: str | None,
        exclude: str | None,
    ) -> None:
        """Run the selected baseline transaction."""
        _manage(
            mode,
            paths=paths,
            baseline=baseline,
            tools=tools,
            tool_options=tool_options,
            exclude=exclude,
        )


_register_management(
    "create",
    "Snapshot findings without overwriting an existing file.",
)
_register_management(
    "update",
    "Acknowledge current findings; remove entries proven fixed.",
)
_register_management("prune", "Remove proven fixed entries; add no findings.")


@baseline_command.command("show")
@click.option("--baseline", type=click.Path(dir_okay=False), help="Baseline file.")
def show(baseline: str | None) -> None:
    """Display per-tool baseline statistics without executing tools.

    \u000c

    Args:
        baseline: Explicit destination overriding project configuration.

    Raises:
        click.ClickException: If the baseline is missing or invalid.
        ValueError: If no destination can be resolved (wrapped below).
    """
    try:
        path = resolve_baseline_path(baseline, get_config(), default=True)
        if path is None:
            raise ValueError("baseline path could not be resolved")
        loaded = read_baseline(path)
    except (OSError, ValueError, ConfigurationError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Baseline: {path} (version {loaded.version}, created {loaded.created})")
    for tool in sorted({entry.tool for entry in loaded.entries}):
        entries = [entry for entry in loaded.entries if entry.tool == tool]
        click.echo(
            f"{tool}: {sum(entry.count for entry in entries)} findings "
            f"in {len({entry.file for entry in entries})} files",
        )
    click.echo(f"Total: {sum(entry.count for entry in loaded.entries)} findings")
