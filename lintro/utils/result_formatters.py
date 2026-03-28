"""Result formatting utilities for Lintro tool output.

Handles formatting and display of individual tool results with rich colors and
status messages.
"""

import json
import re
from collections.abc import Callable

from loguru import logger

from lintro.enums.action import Action, normalize_action
from lintro.enums.tool_name import ToolName

# ANSI color codes for suppression table
_YELLOW = "\033[33m"
_RESET = "\033[0m"
_DIM = "\033[2m"


def _print_suppression_table(
    console_output_func: Callable[..., None],
    ai_metadata: dict[str, object] | None,
) -> None:
    """Print suppression details table if ai_metadata contains suppressions.

    Args:
        console_output_func: Function to output text to console.
        ai_metadata: Tool metadata dict with optional suppressions list.
    """
    if not isinstance(ai_metadata, dict):
        return

    suppressions = ai_metadata.get("suppressions", [])
    if not isinstance(suppressions, list) or not suppressions:
        return

    console_output_func(text="")
    console_output_func(
        text="  🔇 Suppressed Vulnerabilities (via .osv-scanner.toml):",
    )

    # Compute column widths
    id_width = max(
        len(str(s.get("id", ""))) for s in suppressions if isinstance(s, dict)
    )
    id_width = max(id_width, 2) + 2  # padding
    exp_width = 12  # YYYY-MM-DD + padding
    status_width = 11  # "⚠ Expired" + padding

    # Header
    header = (
        f"  {'ID':<{id_width}} {'Expires':<{exp_width}} "
        f"{'Status':<{status_width}} Reason"
    )
    console_output_func(text=f"  {'-' * (len(header) - 2)}")
    console_output_func(text=header)
    console_output_func(text=f"  {'-' * (len(header) - 2)}")

    for s in suppressions:
        if not isinstance(s, dict):
            continue
        sid = str(s.get("id", "?"))
        expires = str(s.get("ignore_until", "?"))
        status = str(s.get("status", "active"))
        reason = str(s.get("reason", ""))

        # Truncate long reasons
        max_reason = 60
        if len(reason) > max_reason:
            reason = reason[: max_reason - 1] + "…"

        if status == "expired":
            status_display = f"{_YELLOW}⚠ Expired{_RESET}"
        elif status == "stale":
            status_display = f"{_YELLOW}⚠ Stale{_RESET}"
        else:
            status_display = f"{_DIM}Active{_RESET}  "

        console_output_func(
            text=f"  {sid:<{id_width}} {expires:<{exp_width}} "
            f"{status_display}  {_DIM}{reason}{_RESET}",
        )

    console_output_func(text=f"  {'-' * (len(header) - 2)}")


def print_tool_result(
    console_output_func: Callable[..., None],
    success_func: Callable[..., None],
    tool_name: str,
    output: str,
    issues_count: int,
    raw_output_for_meta: str | None = None,
    action: str | Action = "check",
    success: bool | None = None,
    ai_metadata: dict[str, object] | None = None,
) -> None:
    """Print the result for a tool.

    Args:
        console_output_func: Function to output text to console
        success_func: Function to display success message
        tool_name: str: The name of the tool.
        output: str: The output from the tool.
        issues_count: int: The number of issues found.
        raw_output_for_meta: str | None: Raw tool output used to extract
            fixable/remaining hints when available.
        action: str | Action: The action being performed ("check", "fmt", "test").
        success: bool | None: Whether the tool run succeeded. When False,
            the result is treated as a failure even if no issues were
            counted (e.g., parse or runtime errors).
        ai_metadata: dict[str, object] | None: Tool-specific metadata
            (e.g. suppression classifications for osv-scanner).
    """
    # Normalize action to enum
    action = normalize_action(action)
    # Normalize tool name for consistent comparisons
    tool_name_normalized = tool_name.lower()

    # Add section header for pytest/test results
    if tool_name_normalized == ToolName.PYTEST.value:
        console_output_func(text="")
        console_output_func(text="🧪 Test Results")
        console_output_func(text="-" * 20)  # Simplified border length

        # Extract coverage summary from JSON in output
        coverage_summary = None

        # Display formatted test failures table if present
        # Skip JSON lines and verbose coverage output, extract coverage data from JSON
        if output and output.strip():
            lines = output.split("\n")
            display_lines = []
            json_buffer: list[str] = []
            in_json = False
            in_coverage_section = False

            for line in lines:
                stripped = line.strip()

                # Skip verbose coverage table lines
                # Detect coverage section start
                if (
                    "coverage:" in stripped.lower() and "platform" in stripped.lower()
                ) or (
                    stripped.startswith("Name")
                    and "Stmts" in stripped
                    and "Miss" in stripped
                ):
                    in_coverage_section = True
                    continue

                # Skip lines within coverage section
                if in_coverage_section:
                    # Coverage section ends at empty line or new section marker
                    if stripped == "" or stripped.startswith("==="):
                        in_coverage_section = False
                        # Don't add the empty line/marker that ends coverage
                        if stripped.startswith("==="):
                            display_lines.append(line)
                        continue
                    # Skip coverage data lines (files, TOTAL, dashes, etc.)
                    if (
                        stripped.startswith("-")
                        or stripped.startswith("TOTAL")
                        or "%" in stripped
                        or stripped.startswith("Coverage")
                    ):
                        continue

                # More specific JSON detection: only start JSON collection for:
                # - Lines starting with '{' (JSON object)
                # - Lines starting with '[' followed by JSON-like content
                #   (not pytest progress like [100%])
                is_json_start = stripped.startswith("{") or (
                    stripped.startswith("[")
                    and len(stripped) > 1
                    and stripped[1]
                    in (
                        "{",
                        '"',
                        "'",
                        "[",
                        "-",
                        "0",
                        "1",
                        "2",
                        "3",
                        "4",
                        "5",
                        "6",
                        "7",
                        "8",
                        "9",
                        "t",
                        "f",
                        "n",
                    )
                )
                if is_json_start:
                    # Try to parse single-line JSON first
                    try:
                        parsed_json = json.loads(stripped)
                        # Extract coverage summary if present
                        if isinstance(parsed_json, dict) and "coverage" in parsed_json:
                            coverage_summary = parsed_json["coverage"]
                        # Successfully parsed single-line JSON - skip it
                        continue
                    except json.JSONDecodeError:
                        # Not complete, start collecting multi-line JSON
                        json_buffer = [line]
                        in_json = True
                        continue
                if in_json:
                    json_buffer.append(line)
                    # Try to parse accumulated JSON
                    try:
                        json_str = "\n".join(json_buffer)
                        parsed_json = json.loads(json_str)
                        # Extract coverage summary if present
                        if isinstance(parsed_json, dict) and "coverage" in parsed_json:
                            coverage_summary = parsed_json["coverage"]
                        # Successfully parsed - skip this JSON block
                        json_buffer = []
                        in_json = False
                    except json.JSONDecodeError:
                        # Not complete yet, continue collecting
                        continue
                # Keep everything else including table headers and content
                display_lines.append(line)

            # Flush any remaining incomplete JSON to display_lines
            if json_buffer:
                display_lines.extend(json_buffer)

            if display_lines:
                console_output_func(text="\n".join(display_lines))

        # Display coverage summary as a clean table if present
        if coverage_summary:
            console_output_func(text="")
            console_output_func(text="📊 Coverage Summary")
            console_output_func(text="-" * 20)

            coverage_pct = coverage_summary.get("coverage_pct", 0)
            total_stmts = coverage_summary.get("total_stmts", 0)
            covered_stmts = coverage_summary.get("covered_stmts", 0)
            missing_stmts = coverage_summary.get("missing_stmts", 0)
            files_count = coverage_summary.get("files_count", 0)

            # Choose color/emoji based on coverage percentage
            if coverage_pct >= 80:
                cov_indicator = "🟢"
            elif coverage_pct >= 60:
                cov_indicator = "🟡"
            else:
                cov_indicator = "🔴"

            console_output_func(
                text=f"{cov_indicator} Coverage: {coverage_pct}%",
            )
            console_output_func(
                text=f"   Lines: {covered_stmts:,} / {total_stmts:,} covered",
            )
            console_output_func(
                text=f"   Missing: {missing_stmts:,} lines",
            )
            console_output_func(
                text=f"   Files: {files_count:,}",
            )

        # Don't show summary line here - it will be in the Execution Summary table
        if issues_count == 0 and not output:
            success_func(message="✓ No issues found.")

        return

    if output and output.strip():
        # Display the output (either raw or formatted, depending on what was passed)
        console_output_func(text=output)
        logger.debug(f"Tool {tool_name} output: {len(output)} characters")
    else:
        logger.debug(f"Tool {tool_name} produced no output")

    # Print result status
    if issues_count == 0:
        # For format action, prefer consolidated fixed summary if present
        if action == Action.FIX and output and output.strip():
            # If output contains a consolidated fixed count, surface it
            m_fixed = re.search(r"Fixed (\d+) issue\(s\)", output)
            m_remaining = re.search(
                r"Found (\d+) issue\(s\) that cannot be auto-fixed",
                output,
            )
            fixed_val = int(m_fixed.group(1)) if m_fixed else 0
            remaining_val = int(m_remaining.group(1)) if m_remaining else 0
            if fixed_val > 0 or remaining_val > 0:
                if fixed_val > 0:
                    console_output_func(text=f"✓ {fixed_val} fixed", color="green")
                if remaining_val > 0:
                    console_output_func(
                        text=f"✗ {remaining_val} remaining",
                        color="red",
                    )
                return

        # If the tool reported a failure (e.g., parse error), do not claim pass
        if success is False:
            console_output_func(text="✗ Tool execution failed", color="red")
        # Check if the output indicates no files were processed
        elif output and any(
            (msg in output for msg in ["No files to", "No Python files found to"]),
        ):
            console_output_func(
                text=("⚠️  No files processed (excluded by patterns)"),
            )
        else:
            # For format operations, check if there are remaining issues that
            # couldn't be auto-fixed
            if output and "cannot be auto-fixed" in output.lower():
                # Don't show "No issues found" if there are remaining issues
                pass
            else:
                success_func(message="✓ No issues found.")
    else:
        # For format operations, parse the output to show better messages
        if output and ("Fixed" in output or "issue(s)" in output):
            # This is a format operation - parse for better messaging
            # Parse counts from output string
            fixed_match = re.search(r"Fixed (\d+) issue\(s\)", output)
            fixed_count = int(fixed_match.group(1)) if fixed_match else 0
            remaining_match = re.search(
                r"Found (\d+) issue\(s\) that cannot be auto-fixed",
                output,
            )
            remaining_count = int(remaining_match.group(1)) if remaining_match else 0
            initial_match = re.search(r"Found (\d+) errors?", output)
            initial_count = int(initial_match.group(1)) if initial_match else 0

            if fixed_count > 0 and remaining_count == 0:
                success_func(message=f"✓ {fixed_count} fixed")
            elif fixed_count > 0 and remaining_count > 0:
                console_output_func(
                    text=f"✓ {fixed_count} fixed",
                    color="green",
                )
                console_output_func(
                    text=f"✗ {remaining_count} remaining",
                    color="red",
                )
            elif remaining_count > 0:
                console_output_func(
                    text=f"✗ {remaining_count} remaining",
                    color="red",
                )
            elif initial_count > 0:
                # If we found initial issues but no specific fixed/remaining counts,
                # show the initial count as found
                console_output_func(
                    text=f"✗ Found {initial_count} issues",
                    color="red",
                )
            else:
                # Fallback to original behavior
                error_msg = f"✗ Found {issues_count} issues"
                console_output_func(text=error_msg, color="red")
        else:
            # Show issue count with action-aware phrasing
            if action == Action.FIX:
                error_msg = f"✗ {issues_count} issue(s) cannot be auto-fixed"
            else:
                error_msg = f"✗ Found {issues_count} issues"
            console_output_func(text=error_msg, color="red")

            # Check if there are fixable issues and show warning
            raw_text = (
                raw_output_for_meta if raw_output_for_meta is not None else output
            )
            # Sum all fixable counts if multiple sections are present
            if raw_text and action != Action.FIX:
                # Sum any reported fixable lint issues
                matches = re.findall(r"\[\*\]\s+(\d+)\s+fixable", raw_text)
                fixable_count: int = sum(int(m) for m in matches) if matches else 0
                # Add formatting issues as fixable by fmt when ruff reports them
                if tool_name_normalized == ToolName.RUFF.value and (
                    "Formatting issues:" in raw_text or "Would reformat" in raw_text
                ):
                    # Count files listed in 'Would reformat:' lines
                    reformat_files = re.findall(r"Would reformat:\s+(.+)", raw_text)
                    fixable_count += len(reformat_files)
                    # Or try summary line like: "N files would be reformatted"
                    if fixable_count == 0:
                        m_sum = re.search(
                            r"(\d+)\s+file(?:s)?\s+would\s+be\s+reformatted",
                            raw_text,
                        )
                        if m_sum:
                            fixable_count += int(m_sum.group(1))

                if fixable_count > 0:
                    hint_a: str = "💡 "
                    hint_b: str = (
                        f"{fixable_count} formatting/linting issue(s) "
                        "can be auto-fixed "
                    )
                    hint_c: str = "with `lintro format`"
                    console_output_func(
                        text=hint_a + hint_b + hint_c,
                        color="yellow",
                    )

    # Display suppression table for osv-scanner when suppressions exist
    _print_suppression_table(console_output_func, ai_metadata)

    console_output_func(text="")  # Blank line after each tool
