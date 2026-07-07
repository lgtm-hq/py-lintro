"""CLI command for dependency version policy validation."""

from __future__ import annotations

import json as json_lib
from pathlib import Path

import click
from loguru import logger
from rich.console import Console
from rich.table import Table

from lintro.config.config_loader import get_config
from lintro.config.deps_config import DepsConfig, DepsPolicy
from lintro.deps.models import Dependency, DepsCheckResult, VersionViolation
from lintro.deps.parsers import SUPPORTED_FILENAMES, parse_file
from lintro.deps.policy_engine import PolicyEngine

__all__ = ["deps_command"]

# Directories skipped during automatic manifest discovery.
_SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "target",
        "dist",
        "build",
        "__pycache__",
        ".tox",
        ".mypy_cache",
    },
)


@click.command("deps")
@click.option(
    "--file",
    "-f",
    "files",
    multiple=True,
    help="Specific dependency file(s) to check. Repeatable.",
)
@click.option(
    "--policy",
    type=click.Choice([p.value for p in DepsPolicy if p is not DepsPolicy.CUSTOM]),
    default=None,
    help="Override the configured policy preset.",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["grid", "json"]),
    default="grid",
    help="Output format.",
)
def deps_command(
    files: tuple[str, ...],
    policy: str | None,
    output_format: str,
) -> None:
    """Validate dependency version specifications against a policy.

    Parses dependency manifests (``pyproject.toml``, ``requirements*.txt``,
    ``package.json``, ``Cargo.toml``), classifies each version specification,
    and reports specs that violate the active policy. Exits non-zero when any
    violation is found.

    Args:
        files: Explicit manifest paths. When empty, manifests are discovered.
        policy: Optional policy preset overriding configuration.
        output_format: Output format (``grid`` or ``json``).

    Raises:
        SystemExit: Process exit; non-zero when any violation is found.
    """
    config = _resolve_config(policy)
    engine = PolicyEngine(config)

    targets = _resolve_targets(files)
    if not targets:
        message = "No dependency manifests found."
        if output_format == "json":
            click.echo(json_lib.dumps({"error": message}, indent=2))
        else:
            Console().print(f"[yellow]{message}[/yellow]")
        raise SystemExit(0)

    result = _run_checks(targets, engine)

    if output_format == "json":
        _render_json(result)
    else:
        _render_grid(result)

    raise SystemExit(1 if result.violations else 0)


def _resolve_config(policy: str | None) -> DepsConfig:
    """Resolve the deps config, applying an optional CLI policy override.

    Args:
        policy: Policy preset name from the CLI, or ``None``.

    Returns:
        DepsConfig: The effective configuration.
    """
    try:
        config = get_config().deps
    except Exception as exc:  # pragma: no cover - defensive config fallback
        logger.debug(f"Falling back to default deps config: {exc}")
        config = DepsConfig()

    if policy is not None:
        config = config.model_copy(update={"policy": DepsPolicy(policy)})
    return config


def _resolve_targets(files: tuple[str, ...]) -> list[Path]:
    """Resolve manifest paths from CLI args or auto-discovery.

    Args:
        files: Explicit manifest paths (may be empty).

    Returns:
        list[Path]: Existing manifest paths to check.
    """
    if files:
        return [Path(f) for f in files if Path(f).exists()]
    return _discover_manifests(Path.cwd())


def _discover_manifests(root: Path) -> list[Path]:
    """Discover supported manifests under ``root``.

    Args:
        root: Directory to search recursively.

    Returns:
        list[Path]: Discovered manifest paths, sorted for stable output.
    """
    found: list[Path] = []
    for path in root.rglob("*"):
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        if not path.is_file():
            continue
        name = path.name.lower()
        if name in {n.lower() for n in SUPPORTED_FILENAMES} or (
            name.startswith("requirements") and name.endswith(".txt")
        ):
            found.append(path)
    return sorted(found)


def _run_checks(targets: list[Path], engine: PolicyEngine) -> DepsCheckResult:
    """Parse and validate all target manifests.

    Args:
        targets: Manifest paths to check.
        engine: Configured policy engine.

    Returns:
        DepsCheckResult: Aggregated dependencies and violations.
    """
    dependencies: list[Dependency] = []
    for target in targets:
        try:
            dependencies.extend(parse_file(target))
        except (ValueError, OSError) as exc:
            logger.warning(f"Skipping {target}: {exc}")
    violations = engine.validate(dependencies)
    return DepsCheckResult(dependencies=dependencies, violations=violations)


def _render_grid(result: DepsCheckResult) -> None:
    """Render results as a grouped Rich table.

    Args:
        result: The check result to render.
    """
    console = Console()
    violations_by_dep: dict[int, VersionViolation] = {
        id(v.dependency): v for v in result.violations
    }

    by_file: dict[str, list[Dependency]] = {}
    for dep in result.dependencies:
        by_file.setdefault(dep.file, []).append(dep)

    for file, deps in by_file.items():
        table = Table(title=f"Dependency Version Check — {file}")
        table.add_column("Package", style="cyan", no_wrap=True)
        table.add_column("Version Spec", style="yellow")
        table.add_column("Type", style="magenta")
        table.add_column("Status", justify="center")
        table.add_column("Issue", style="red")

        for dep in deps:
            violation = violations_by_dep.get(id(dep))
            if violation is None:
                status = "[green]✓[/green]"
                issue = ""
            else:
                status = "[red]✗[/red]"
                issue = violation.message
            table.add_row(
                dep.name,
                dep.version_spec or "*",
                str(dep.spec_type),
                status,
                issue,
            )
        console.print(table)

    _render_summary(console, result)


def _render_summary(console: Console, result: DepsCheckResult) -> None:
    """Print a summary line for the check.

    Args:
        console: Rich console to write to.
        result: The check result to summarize.
    """
    count = len(result.violations)
    if count == 0:
        console.print(
            f"\n[green]✅ All {len(result.dependencies)} dependencies "
            f"satisfy the policy.[/green]",
        )
        return

    console.print(f"\n[red]Summary: {count} issue(s) found[/red]")
    by_rule: dict[str, int] = {}
    for violation in result.violations:
        by_rule[violation.message] = by_rule.get(violation.message, 0) + 1
    for message, n in sorted(by_rule.items()):
        console.print(f"  [dim]- {n} dependency(ies): {message}[/dim]")


def _render_json(result: DepsCheckResult) -> None:
    """Render results as JSON.

    Args:
        result: The check result to render.
    """
    payload = {
        "passed": result.passed,
        "total": len(result.dependencies),
        "violation_count": len(result.violations),
        "violations": [
            {
                "package": v.dependency.name,
                "file": v.dependency.file,
                "version_spec": v.dependency.version_spec,
                "spec_type": str(v.dependency.spec_type),
                "rule": v.rule,
                "message": v.message,
            }
            for v in result.violations
        ],
    }
    click.echo(json_lib.dumps(payload, indent=2))
