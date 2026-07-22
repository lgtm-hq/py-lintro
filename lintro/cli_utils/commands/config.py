"""Config command group for inspecting and validating Lintro configuration.

Provides:

- ``lintro config`` / ``lintro config show``: display effective configuration.
- ``lintro config validate``: validate a config file against the schema.
- ``lintro config init``: scaffold a starter config (delegates to ``init``).
"""

from dataclasses import asdict
from pathlib import Path
from typing import Any, cast

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from lintro.cli_utils.commands.init import init_command
from lintro.config import LintroConfig, get_config
from lintro.config.config_validator import ValidationResult, validate_config_file
from lintro.utils.unified_config import (
    _load_native_tool_config,
    get_ordered_tools,
    get_tool_priority,
    is_tool_injectable,
    validate_config_consistency,
)


def _get_all_tool_names() -> list[str]:
    """Get list of all registered tool names.

    Dynamically retrieves tool names from the plugin registry to ensure
    all tools are included without manual maintenance.

    Returns:
        list[str]: Sorted list of tool names.
    """
    from lintro.plugins.registry import ToolRegistry

    return ToolRegistry.get_names()


@click.group(
    invoke_without_command=True,
    no_args_is_help=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    help="Show detailed configuration including native tool configs.",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Output configuration as JSON.",
)
@click.option(
    "--export",
    "export_path",
    type=click.Path(),
    help="Export effective configuration as a .lintro-config.yaml file.",
)
@click.pass_context
def config_command(
    ctx: click.Context,
    verbose: bool,
    json_output: bool,
    export_path: str | None,
) -> None:
    r"""Inspect and validate Lintro configuration.

    Without a subcommand, displays the effective configuration (same as
    ``config show``). Subcommands provide validation and scaffolding:

    \b
    - config show      Display the effective configuration.
    - config validate  Validate a config file against the schema.
    - config init      Scaffold a starter .lintro-config.yaml.

    Args:
        ctx: Click context used to detect subcommand dispatch.
        verbose: Show detailed configuration including native tool configs.
        json_output: Output configuration as JSON.
        export_path: Path to export effective configuration as YAML file.
    """
    if ctx.invoked_subcommand is not None:
        return

    _run_config_report(
        verbose=verbose,
        json_output=json_output,
        export_path=export_path,
    )


def _run_config_report(
    verbose: bool,
    json_output: bool,
    export_path: str | None,
) -> None:
    """Display Lintro configuration status.

    Shows the unified configuration for all tools including:
    - Config source (.lintro-config.yaml or pyproject.toml)
    - Global settings (line_length, tool ordering strategy)
    - Tool execution order based on configured strategy
    - Per-tool effective configuration
    - Configuration warnings and inconsistencies

    Args:
        verbose: Show detailed configuration including native tool configs.
        json_output: Output configuration as JSON.
        export_path: Path to export effective configuration as YAML file.
    """
    console = Console()
    config = get_config(reload=True)

    if export_path:
        _export_yaml(config=config, export_path=export_path, console=console)
        return

    if json_output:
        _output_json(config=config, verbose=verbose)
        return

    _output_rich(
        console=console,
        config=config,
        verbose=verbose,
    )


@config_command.command("show")
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    help="Show detailed configuration including native tool configs.",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Output configuration as JSON.",
)
@click.option(
    "--export",
    "export_path",
    type=click.Path(),
    help="Export effective configuration as a .lintro-config.yaml file.",
)
def config_show_command(
    verbose: bool,
    json_output: bool,
    export_path: str | None,
) -> None:
    """Display the effective Lintro configuration.

    Args:
        verbose: Show detailed configuration including native tool configs.
        json_output: Output configuration as JSON.
        export_path: Path to export effective configuration as YAML file.
    """
    _run_config_report(
        verbose=verbose,
        json_output=json_output,
        export_path=export_path,
    )


@config_command.command("validate")
@click.option(
    "--path",
    "config_path",
    type=click.Path(),
    default=None,
    help="Path to the config file to validate (default: auto-detect).",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Output validation results as JSON.",
)
def config_validate_command(
    config_path: str | None,
    json_output: bool,
) -> None:
    """Validate a Lintro configuration file against the schema.

    Reports unknown tools (with typo suggestions), deprecated or unknown
    options, and hard errors such as invalid value types. Exits non-zero when
    the configuration is invalid.

    Args:
        config_path: Explicit config file to validate; auto-detected if None.
        json_output: Emit machine-readable JSON instead of Rich output.

    Raises:
        SystemExit: With code 1 when the configuration is invalid.
    """
    result = validate_config_file(config_path)

    if json_output:
        _output_validation_json(result)
    else:
        _output_validation_rich(result, console=Console())

    if not result.is_valid:
        raise SystemExit(1)


def _output_validation_json(result: ValidationResult) -> None:
    """Print validation results as JSON.

    Args:
        result: The validation result to serialize.
    """
    import json

    payload = {
        "config_path": str(result.config_path) if result.config_path else None,
        "valid": result.is_valid,
        "errors": [
            {
                "message": msg.message,
                "location": msg.location,
                "suggestion": msg.suggestion,
            }
            for msg in result.errors
        ],
        "warnings": [
            {
                "message": msg.message,
                "location": msg.location,
                "suggestion": msg.suggestion,
            }
            for msg in result.warnings
        ],
    }
    print(json.dumps(payload, indent=2))


def _output_validation_rich(result: ValidationResult, console: Console) -> None:
    """Render validation results using Rich formatting.

    Args:
        result: The validation result to render.
        console: Rich console for output.
    """
    source = result.config_path or "[dim]none found[/dim]"
    console.print(f"[bold]Validating[/bold] {source}")
    console.print()

    if result.warnings:
        console.print("[bold yellow]Warnings:[/bold yellow]")
        for msg in result.warnings:
            console.print(f"  [yellow]⚠️[/yellow]  {msg.render()}")
        console.print()

    if result.errors:
        console.print("[bold red]Errors:[/bold red]")
        for msg in result.errors:
            console.print(f"  [red]✗[/red] {msg.render()}")
        console.print()

    n_err = len(result.errors)
    n_warn = len(result.warnings)
    if result.is_valid:
        suffix = f" ({n_warn} warning{'s' if n_warn != 1 else ''})" if n_warn else ""
        console.print(f"[green]✅ Configuration is VALID{suffix}[/green]")
    else:
        console.print(
            f"[red]❌ Configuration is INVALID "
            f"({n_err} error{'s' if n_err != 1 else ''}, "
            f"{n_warn} warning{'s' if n_warn != 1 else ''})[/red]",
        )


# Reuse the top-level init scaffolding as `config init`.
config_command.add_command(init_command, name="init")


def _config_to_export_dict(config: LintroConfig) -> dict[str, Any]:
    """Convert LintroConfig to a YAML-friendly dictionary.

    Args:
        config: The LintroConfig instance to convert.

    Returns:
        dict[str, Any]: Dictionary suitable for YAML serialization.
    """
    tools = {name: asdict(cfg) for name, cfg in config.tools.items()}
    return {
        "enforce": asdict(config.enforce),
        "execution": asdict(config.execution),
        "defaults": config.defaults,
        "tools": tools,
    }


def _export_yaml(
    config: LintroConfig,
    export_path: str,
    console: Console,
) -> None:
    """Export the effective configuration as YAML.

    Args:
        config: Loaded configuration.
        export_path: Destination file path.
        console: Rich console for user feedback.

    Raises:
        SystemExit: If PyYAML is not installed.
    """
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - enforced by packaging
        console.print(
            "[red]PyYAML is required to export configuration. "
            "Install it with `pip install pyyaml`.[/red]",
        )
        raise SystemExit(1) from exc

    export_file = Path(export_path)
    data = _config_to_export_dict(config)
    header = "# Generated by `lintro config --export`\n"
    export_file.write_text(
        header + yaml.safe_dump(data, sort_keys=False),
        encoding="utf-8",
    )
    console.print(f"[green]Exported configuration to {export_file}[/green]")


def _output_json(
    config: LintroConfig,
    verbose: bool = False,
) -> None:
    """Output configuration as JSON.

    Args:
        config: LintroConfig instance from get_config()
        verbose: Include native configs in output when True
    """
    import json

    # Get tool order settings
    tool_order = config.execution.tool_order
    if isinstance(tool_order, list):
        order_strategy = "custom"
        custom_order = tool_order
    else:
        order_strategy = tool_order or "priority"
        custom_order = []

    # Get list of all known tools
    tool_names = _get_all_tool_names()
    ordered_tools = get_ordered_tools(
        tool_names=tool_names,
        tool_order=config.execution.tool_order,
    )

    output: dict[str, Any] = {
        "config_source": config.config_path or "defaults",
        "global_settings": {
            "line_length": config.enforce.line_length,
            "target_python": config.enforce.target_python,
            "tool_order": order_strategy,
            "custom_order": custom_order,
        },
        "execution": {
            "enabled_tools": config.execution.enabled_tools or "all",
            "fail_fast": config.execution.fail_fast,
            "parallel": config.execution.parallel,
        },
        "tool_execution_order": [
            {"tool": t, "priority": get_tool_priority(t)} for t in ordered_tools
        ],
        "tool_configs": {},
        "warnings": validate_config_consistency(),
    }

    tool_configs = cast(dict[str, Any], output["tool_configs"])

    for tool_name in tool_names:
        tool_config = config.get_tool_config(tool_name)
        effective_ll = config.get_effective_line_length(tool_name)

        tool_output: dict[str, Any] = {
            "enabled": tool_config.enabled,
            "is_injectable": is_tool_injectable(tool_name),
            "effective_line_length": effective_ll,
            "config_source": tool_config.config_source,
        }
        if verbose:
            native = _load_native_tool_config(tool_name)
            tool_output["native_config"] = native if native else None
            tool_output["defaults"] = config.get_tool_defaults(tool_name) or None

        tool_configs[tool_name] = tool_output

    print(json.dumps(output, indent=2))


def _output_rich(
    console: Console,
    config: LintroConfig,
    verbose: bool,
) -> None:
    """Output configuration using Rich formatting.

    Args:
        console: Rich Console instance
        config: LintroConfig instance from get_config()
        verbose: Whether to show verbose output
    """
    # Header panel
    console.print(
        Panel.fit(
            "[bold cyan]Lintro Configuration Report[/bold cyan]",
            border_style="cyan",
        ),
    )
    console.print()

    # Config Source Section
    config_source = config.config_path or "[dim]No config file (using defaults)[/dim]"
    console.print(f"[bold]Config Source:[/bold] {config_source}")
    console.print()

    # Global Settings Section
    global_table = Table(
        title="Enforce Settings",
        show_header=False,
        box=None,
    )
    global_table.add_column("Setting", style="cyan", width=25)
    global_table.add_column("Value", style="yellow")

    line_length = config.enforce.line_length
    global_table.add_row(
        "line_length",
        str(line_length) if line_length else "[dim]Not configured[/dim]",
    )

    target_python = config.enforce.target_python
    global_table.add_row(
        "target_python",
        target_python if target_python else "[dim]Not configured[/dim]",
    )

    console.print(global_table)
    console.print()

    # Execution Settings Section
    exec_table = Table(
        title="Execution Settings",
        show_header=False,
        box=None,
    )
    exec_table.add_column("Setting", style="cyan", width=25)
    exec_table.add_column("Value", style="yellow")

    tool_order = config.execution.tool_order
    if isinstance(tool_order, list):
        order_strategy = "custom"
        exec_table.add_row("tool_order", order_strategy)
        exec_table.add_row("custom_order", ", ".join(tool_order))
    else:
        exec_table.add_row("tool_order", tool_order or "priority")

    enabled_tools = config.execution.enabled_tools
    exec_table.add_row(
        "enabled_tools",
        ", ".join(enabled_tools) if enabled_tools else "[dim]all[/dim]",
    )
    exec_table.add_row("fail_fast", str(config.execution.fail_fast))
    exec_table.add_row("parallel", str(config.execution.parallel))

    console.print(exec_table)
    console.print()

    # Tool Execution Order Section
    tool_names = _get_all_tool_names()
    ordered_tools = get_ordered_tools(
        tool_names=tool_names,
        tool_order=config.execution.tool_order,
    )

    order_table = Table(title="Tool Execution Order")
    order_table.add_column("#", style="dim", justify="right", width=3)
    order_table.add_column("Tool", style="cyan")
    order_table.add_column("Priority", justify="center", style="yellow")
    order_table.add_column("Type", style="green")
    order_table.add_column("Enabled", justify="center")

    for idx, tool_name in enumerate(ordered_tools, 1):
        priority = get_tool_priority(tool_name)
        injectable = is_tool_injectable(tool_name)
        tool_type = "Syncable" if injectable else "Native only"
        enabled = config.is_tool_enabled(tool_name)
        enabled_display = "[green]✓[/green]" if enabled else "[red]✗[/red]"

        order_table.add_row(
            str(idx),
            tool_name,
            str(priority),
            tool_type,
            enabled_display,
        )

    console.print(order_table)
    console.print()

    # Per-Tool Configuration Section
    config_table = Table(title="Per-Tool Configuration")
    config_table.add_column("Tool", style="cyan")
    config_table.add_column("Sync Status", justify="center")
    config_table.add_column("Line Length", justify="center", style="yellow")
    config_table.add_column("Config Source", style="dim")

    if verbose:
        config_table.add_column("Native Config", style="dim")

    for tool_name in tool_names:
        tool_config = config.get_tool_config(tool_name)
        injectable = is_tool_injectable(tool_name)
        status = (
            "[green]✓ Syncable[/green]"
            if injectable
            else "[yellow]⚠ Native only[/yellow]"
        )
        effective_ll = config.get_effective_line_length(tool_name)
        ll_display = str(effective_ll) if effective_ll else "[dim]default[/dim]"

        cfg_source = tool_config.config_source or "[dim]auto[/dim]"

        row = [tool_name, status, ll_display, cfg_source]

        if verbose:
            native = _load_native_tool_config(tool_name)
            native_cfg = str(native) if native else "[dim]None[/dim]"
            row.append(native_cfg)

        config_table.add_row(*row)

    console.print(config_table)
    console.print()

    # Warnings Section
    warnings = validate_config_consistency()
    if warnings:
        console.print("[bold red]Configuration Warnings[/bold red]")
        for warning in warnings:
            console.print(f"  [yellow]⚠️[/yellow]  {warning}")
        console.print()
        console.print(
            "[dim]Tools marked 'Native only' cannot be configured by Lintro. "
            "Update their config files manually for consistency.[/dim]",
        )
    else:
        console.print(
            "[green]✅ All configurations are consistent![/green]",
        )

    console.print()

    # Help text
    console.print(
        "[dim]Configure Lintro in .lintro-config.yaml:[/dim]",
    )
    console.print(
        "[dim]  Run 'lintro init' to create a config file[/dim]",
    )
    console.print(
        '[dim]  tool_order: "priority" | "alphabetical" | ["tool1", "tool2"][/dim]',
    )
