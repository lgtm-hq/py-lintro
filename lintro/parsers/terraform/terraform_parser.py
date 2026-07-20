"""Parser for Terraform output.

Terraform emits two distinct output formats that Lintro consumes:

1. ``terraform fmt -check`` prints, on stdout, one relative path per line for
   every file that is not correctly formatted.
2. ``terraform validate -json`` prints a JSON document whose ``diagnostics``
   array describes each validation error or warning (severity, summary,
   detail, and an optional source range).

This module maps both formats onto :class:`TerraformIssue` objects.
"""

from __future__ import annotations

import json
import os

from lintro.parsers.base_parser import strip_ansi_codes
from lintro.parsers.terraform.terraform_issue import TerraformIssue

# File extensions that ``terraform fmt`` is able to process. Lines of fmt
# output ending in one of these are treated as offending file paths.
_FMT_SUFFIXES: tuple[str, ...] = (".tf", ".tfvars", ".tftest.hcl")


def parse_terraform_fmt_output(
    output: str | None,
    base_dir: str = "",
) -> list[TerraformIssue]:
    """Parse ``terraform fmt -check`` output into issues.

    Each non-empty stdout line that names a Terraform file is reported as a
    formatting issue. Non-path noise (blank lines, error banners) is ignored.

    Args:
        output: The raw stdout from ``terraform fmt -check``, or None.
        base_dir: Optional directory prefix to prepend to each reported path,
            used when fmt is run with a directory target rather than explicit
            file arguments.

    Returns:
        List of TerraformIssue objects, one per unformatted file.
    """
    issues: list[TerraformIssue] = []

    if not output or not output.strip():
        return issues

    output = strip_ansi_codes(output)

    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or not line.endswith(_FMT_SUFFIXES):
            continue
        file_path = os.path.join(base_dir, line) if base_dir else line
        issues.append(
            TerraformIssue(
                file=file_path,
                line=0,
                column=0,
                level="error",
                code="fmt",
                message="File is not correctly formatted (run terraform fmt)",
            ),
        )

    return issues


def parse_terraform_validate_output(
    output: str | None,
    module_dir: str = "",
) -> list[TerraformIssue]:
    """Parse ``terraform validate -json`` output into issues.

    Args:
        output: The raw stdout JSON from ``terraform validate -json``, or None.
        module_dir: Directory of the module being validated, used to make the
            diagnostic ``range.filename`` relative to the working directory
            rather than the module root.

    Returns:
        List of TerraformIssue objects, one per diagnostic.
    """
    issues: list[TerraformIssue] = []

    if not output or not output.strip():
        return issues

    try:
        data = json.loads(output)
    except (json.JSONDecodeError, ValueError):
        return issues

    diagnostics = data.get("diagnostics") if isinstance(data, dict) else None
    if not isinstance(diagnostics, list):
        return issues

    normalized_dir = "" if module_dir in ("", ".") else module_dir

    for diagnostic in diagnostics:
        if not isinstance(diagnostic, dict):
            continue

        severity = str(diagnostic.get("severity") or "error")
        summary = str(diagnostic.get("summary") or "").strip()
        detail = str(diagnostic.get("detail") or "").strip()
        message = f"{summary}: {detail}" if summary and detail else (summary or detail)

        diag_range = diagnostic.get("range")
        filename = ""
        line = 0
        column = 0
        if isinstance(diag_range, dict):
            filename = str(diag_range.get("filename") or "")
            start = diag_range.get("start")
            if isinstance(start, dict):
                line = int(start.get("line") or 0)
                column = int(start.get("column") or 0)

        if filename:
            file_path = (
                os.path.join(normalized_dir, filename) if normalized_dir else filename
            )
        else:
            file_path = normalized_dir

        issues.append(
            TerraformIssue(
                file=file_path,
                line=line,
                column=column,
                level=severity,
                code="validate",
                message=message,
            ),
        )

    return issues


def parse_terraform_output(output: str | None) -> list[TerraformIssue]:
    """Parse Terraform output into issues, auto-detecting the format.

    JSON payloads are treated as ``terraform validate`` output; anything else
    is treated as ``terraform fmt`` output. Plugins call the format-specific
    parsers directly; this canonical entry point exists for the standard
    ``parse_<tool>_output`` contract and for callers that do not know the
    origin of the output.

    Args:
        output: The raw output from Terraform, or None.

    Returns:
        List of TerraformIssue objects parsed from the output.
    """
    if not output or not output.strip():
        return []
    if output.lstrip().startswith("{"):
        return parse_terraform_validate_output(output)
    return parse_terraform_fmt_output(output)
