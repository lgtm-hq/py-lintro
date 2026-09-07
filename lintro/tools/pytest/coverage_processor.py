"""Coverage report processing for pytest.

This module provides functions for parsing and extracting coverage reports.
"""

from __future__ import annotations

import re
from typing import Any


def parse_coverage_summary(raw_output: str) -> dict[str, Any] | None:
    """Parse coverage summary statistics from raw pytest output.

    Extracts the TOTAL line from coverage output to get summary stats.

    Args:
        raw_output: Raw output from pytest containing coverage report.

    Returns:
        dict | None: Coverage summary with keys:
            - total_stmts: Total number of statements
            - missing_stmts: Number of missing statements
            - covered_stmts: Number of covered statements
            - coverage_pct: Coverage percentage
            - files_count: Number of files in coverage report
        Returns None if no coverage data found.
    """
    if not raw_output:
        return None

    lines = raw_output.split("\n")

    # Find the TOTAL line which contains summary stats
    # Format: "TOTAL                    20731  12738    39%"
    total_line = None
    files_count = 0
    in_coverage_section = False

    for line in lines:
        stripped = line.strip()
        # Detect start of coverage table (Name header line)
        if stripped.startswith("Name") and "Stmts" in stripped:
            in_coverage_section = True
            continue

        # Count files in coverage report (lines with coverage data)
        if in_coverage_section and stripped and not stripped.startswith("-"):
            if stripped.startswith("TOTAL"):
                total_line = stripped
                break
            # Count file lines (have .py extension or look like module paths)
            if "%" in stripped and not stripped.startswith("TOTAL"):
                files_count += 1

    if not total_line:
        return None

    # Parse TOTAL line: "TOTAL  20731  12738  39%" or "TOTAL  20731  12738  39.5%"
    # Split by whitespace and extract values (support decimal percentages)
    match = re.search(r"TOTAL\s+(\d+)\s+(\d+)\s+(\d+(?:\.\d+)?)%", total_line)
    if not match:
        return None

    total_stmts = int(match.group(1))
    missing_stmts = int(match.group(2))
    coverage_pct = float(match.group(3))

    return {
        "total_stmts": total_stmts,
        "missing_stmts": missing_stmts,
        "covered_stmts": total_stmts - missing_stmts,
        "coverage_pct": coverage_pct,
        "files_count": files_count,
    }


def extract_coverage_report(raw_output: str) -> str | None:
    """Extract coverage report section from raw pytest output.

    Args:
        raw_output: Raw output from pytest.

    Returns:
        str | None: Coverage report section if found, None otherwise.
    """
    if not raw_output:
        return None

    # Look for coverage report markers
    # pytest-cov outputs coverage in a section starting with a header line
    coverage_markers = [
        "---------- coverage:",
        "----------- coverage:",
        "coverage:",
        "Name                ",  # Start of coverage table
        "TOTAL ",  # Coverage summary line
    ]

    lines = raw_output.split("\n")
    coverage_start = None
    coverage_end = None

    for i, line in enumerate(lines):
        # Find the start of coverage section
        if coverage_start is None:
            for marker in coverage_markers:
                if marker in line:
                    # Go back to find the header line with dashes
                    start = i
                    for j in range(max(0, i - 3), i + 1):
                        if "coverage" in lines[j].lower() or lines[j].startswith("---"):
                            start = j
                            break
                    coverage_start = start
                    break
        elif coverage_start is not None:
            # Find the end of coverage section (empty line or new section)
            if line.strip() == "" and i > coverage_start + 2:
                # Check if next non-empty line is not part of coverage
                for j in range(i + 1, min(i + 3, len(lines))):
                    if lines[j].strip():
                        if not any(
                            m in lines[j] for m in ["Name", "TOTAL", "---", "Missing"]
                        ):
                            coverage_end = i
                            break
                        break
                if coverage_end:
                    break
            elif line.startswith("===") and coverage_start is not None:
                coverage_end = i
                break

    if coverage_start is not None:
        if coverage_end is None:
            coverage_end = len(lines)
        coverage_section = "\n".join(lines[coverage_start:coverage_end]).strip()
        if coverage_section:
            return coverage_section

    return None
