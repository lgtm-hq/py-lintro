"""Per-invocation parser accounting for Oxlint's batch runners."""

import re
from dataclasses import dataclass, field, replace

from lintro.formatters.formatter import merge_detected_and_remaining
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.oxlint.oxlint_issue import OxlintIssue
from lintro.parsers.oxlint.oxlint_parser import parse_oxlint_output_with_stats


def _without_fix_claims(output: str | None) -> str:
    """Keep findings and verbose output, removing unmeasured fix claims.

    Args:
        output: Shared runner's summary and optional raw fix output.

    Returns:
        Summary without numerical fix claims, preserving raw output verbatim.
    """
    output = output or ""
    # #3000 labels the same raw section "Fix output (oxlint JSON)". Only
    # filter the generated summary, never lines inside either raw section.
    raw_heading = re.search(r"^Fix output(?: \(oxlint JSON\))?:\n", output, re.M)
    summary = output[: raw_heading.start()] if raw_heading else output
    summary = "\n".join(
        line
        for line in summary.splitlines()
        if not line.startswith("Fixed ")
        and line != "All issues were successfully auto-fixed"
    )
    summary = summary.strip()
    if raw_heading:
        verbose = output[raw_heading.start() :]
        return f"{summary}\n{verbose}" if summary else verbose
    return summary


@dataclass
class OxlintOutputParser:
    """Collect report failures without changing the shared batch parser contract.

    Attributes:
        failure_counts: Number of rejected reports or records per parsed stage.
    """

    failure_counts: list[int] = field(default_factory=list)

    def parse(self, output: str) -> list[OxlintIssue]:
        """Parse one check or verification report and retain its failure count.

        Args:
            output: Raw output from the batch runner.

        Returns:
            Valid findings, including those in a partially invalid report.
        """
        issues, failures = parse_oxlint_output_with_stats(output)
        self.failure_counts.append(failures)
        return issues

    def finish(self, result: ToolResult) -> ToolResult:
        """Surface rejected records and prevent incomplete reports implying success.

        Any incomplete report makes derived fix accounting unknown,
        including on timeout. Usable verification and initial findings remain
        visible without duplicates. The shared runner classifies failed exits;
        bare non-JSON prose and whitespace count no parser failures.

        Args:
            result: Check, fix or timeout result from the shared runner.

        Returns:
            Result with explicit parser accounting and reliable fix counts.
        """
        failures = sum(self.failure_counts)
        if not failures:
            return replace(result, parse_failures_count=failures)

        message = f"Failed to parse {failures} Oxlint report(s) or diagnostic(s)."
        if result.initial_issues_count is not None:
            # Partial reports cannot measure fixes. Retain usable findings and
            # the actual baseline only when the initial report was complete.
            output = _without_fix_claims(result.output)
            issues = merge_detected_and_remaining(
                [],
                [*result.issues, *result.initial_issues],
            )
            return replace(
                result,
                parse_failures_count=failures,
                success=False,
                output=f"{message}\n{output}" if output else message,
                fixed_issues_count=None,
                remaining_issues_count=None,
                residual_unknown=True,
                residual_unknown_reason="; ".join(
                    dict.fromkeys(
                        reason
                        for reason in (result.residual_unknown_reason, message)
                        if reason
                    ),
                ),
                initial_issues_count=(
                    None if self.failure_counts[0] else len(result.initial_issues)
                ),
                issues=issues,
                issues_count=len(issues),
            )
        return replace(
            result,
            parse_failures_count=failures,
            success=False,
            output=f"{message}\n{result.output}" if result.output else message,
        )
