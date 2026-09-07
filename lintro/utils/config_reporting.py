"""Configuration reporting utilities.

Generates human-readable reports of tool configurations and validation
warnings.

Execution order is deliberately absent here. Since #1742 the order is derived
from tool claims rather than configured, and it is reported by the commands
that can resolve it — ``lintro config``, ``lintro check --explain-order`` and
``lintro doctor``.
"""

from loguru import logger

from lintro.utils.unified_config import (
    get_effective_line_length,
    validate_config_consistency,
)

# Tools that don't require displaying native config (managed internally)
_TOOLS_WITHOUT_EXTERNAL_CONFIG = {"ruff", "black", "bandit"}


def get_config_report() -> str:
    """Generate a configuration report as a string.

    Returns:
        Formatted configuration report
    """
    # Late import to avoid circular dependency
    from lintro.utils.unified_config import get_tool_config_summary

    summary = get_tool_config_summary()
    central_ll = get_effective_line_length("ruff")

    lines: list[str] = []
    lines.append("=" * 60)
    lines.append("LINTRO CONFIGURATION REPORT")
    lines.append("=" * 60)
    lines.append("")

    # Global settings section
    lines.append("── Global Settings ──")
    lines.append(f"  Central line_length: {central_ll or 'Not configured'}")
    lines.append("  Tool order: derived from tool claims (lintro config)")
    lines.append("")

    # Per-tool configuration section
    lines.append("── Per-Tool Configuration ──")
    for tool_name, info in summary.items():
        injectable = "✅ Syncable" if info.is_injectable else "⚠️ Native only"
        effective = info.effective_config.get("line_length", "default")
        lines.append(f"  {tool_name}:")
        lines.append(f"    Status: {injectable}")
        lines.append(f"    Effective line_length: {effective}")
        if info.lintro_tool_config:
            lines.append(f"    Lintro config: {info.lintro_tool_config}")
        if info.native_config and tool_name not in _TOOLS_WITHOUT_EXTERNAL_CONFIG:
            # Only show native config for tools with external config files
            lines.append(f"    Native config: {info.native_config}")
    lines.append("")

    # Warnings section
    all_warnings = validate_config_consistency()
    if all_warnings:
        lines.append("── Configuration Warnings ──")
        for warning in all_warnings:
            lines.append(f"  {warning}")
        lines.append("")
    else:
        lines.append("── Configuration Warnings ──")
        lines.append("  None - all configs consistent!")
        lines.append("")

    lines.append("=" * 60)
    return "\n".join(lines)


def print_config_report() -> None:
    """Print a report of configuration status for all tools."""
    report = get_config_report()
    in_warnings_section = False
    for line in report.split("\n"):
        # Track when we're in the warnings section
        if line.startswith("── Configuration Warnings ──"):
            in_warnings_section = True
        elif line.startswith("──") and "Warnings" not in line:
            in_warnings_section = False

        # Route actual warnings in the warnings section to warning level
        if in_warnings_section and line.startswith("  ") and line.strip():
            logger.warning(line)
        else:
            logger.info(line)
