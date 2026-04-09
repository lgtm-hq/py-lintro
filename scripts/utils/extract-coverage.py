#!/usr/bin/env python3
"""Extract coverage percentage from coverage.xml file.

This module provides functionality to parse coverage.xml files and extract
the coverage percentage for CI/CD pipelines. XML parsing uses ``defusedxml``
when available for hardened security; if unavailable, it falls back to the
standard library with explicit justification.
"""

import os
import sys
import types
import xml.etree.ElementTree as StdlibET  # nosec B405 - fallback; defusedxml preferred

ET: types.ModuleType
try:
    # Prefer hardened XML parsing
    from defusedxml import ElementTree as ET  # noqa: N817

    _DEFUSED = True
except Exception:  # pragma: no cover - fallback path
    # Fallback to stdlib. This script parses a local CI-generated coverage.xml,
    # not untrusted input. We explicitly justify the fallback usage.
    ET = StdlibET

    _DEFUSED = False


def extract_coverage_percentage() -> None:
    """Extract coverage percentage from coverage.xml file.

    Parses the coverage.xml file in the current directory and outputs
    the coverage percentage in the format 'percentage=X.X' for use
    in CI/CD environments.

    The function looks for coverage data at the root level first, then
    falls back to checking packages if no root data is found.
    """
    coverage_file = "coverage.xml"

    debug: bool = os.getenv("COVERAGE_DEBUG", "0") == "1"

    def dbg(msg: str) -> None:
        if debug:
            print(msg)

    # Debug: Check what files are available (only when enabled)
    dbg(f"Current directory: {os.getcwd()}")
    if debug:
        print("Files in current directory:")
        for file in os.listdir("."):
            if os.path.isfile(file):
                size = os.path.getsize(file)
                print(f"  {file} ({size} bytes)")
        print()

    if not os.path.exists(coverage_file):
        print(f"Coverage XML file '{coverage_file}' not found")
        print("percentage=0.0")
        return

    try:
        dbg("Coverage XML file found, extracting percentage...")
        dbg(f"Coverage XML file size: {os.path.getsize(coverage_file)} bytes")

        # Read and parse the XML file
        tree = ET.parse(coverage_file)  # nosec B314 - see module docstring
        root = tree.getroot()
        if root is None:
            print("Error: Could not get root element from coverage.xml")
            print("percentage=0.0")
            return

        dbg(f"XML root tag: {root.tag}")
        dbg(f"XML root attributes: {root.attrib}")

        # Look for coverage data at root level first (most common)
        root_line_rate = root.get("line-rate") or "0"
        dbg(f"Root line-rate={root_line_rate}")
        if root_line_rate != "0":
            percentage = float(root_line_rate) * 100
            print(f"percentage={percentage:.1f}")
            return

        # Fallback: check packages
        packages = root.findall(".//package")
        dbg(f"Found {len(packages)} packages")

        for i, package in enumerate(packages):
            line_rate = package.get("line-rate", "0")
            dbg(f"Package {i}: line-rate={line_rate}")
            if line_rate != "0":
                percentage = float(line_rate) * 100
                print(f"percentage={percentage:.1f}")
                return

        # If still no coverage found, use root as fallback
        dbg("No coverage found in packages, using root as fallback")
        percentage = float(root_line_rate) * 100
        print(f"percentage={percentage:.1f}")

    except Exception as e:
        print(f"Error parsing coverage.xml: {e}", file=sys.stderr)
        print("percentage=0.0")


if __name__ == "__main__":
    extract_coverage_percentage()
