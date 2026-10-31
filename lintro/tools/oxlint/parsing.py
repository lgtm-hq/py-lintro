"""Per-invocation parser accounting for Oxlint's batch runners."""

from dataclasses import dataclass, field

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
    summary, separator, raw_output = (output or "").partition("Fix output:\n")
    summary = "\n".join(
        line
        for line in summary.splitlines()
        if not line.startswith("Fixed ")
        and line != "All issues were successfully auto-fixed"
    )
    summary = summary.strip()
    if separator:
        verbose = f"{separator}{raw_output}"
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

        Either incomplete fix report makes derived fix accounting unknown,
        including on timeout. Bare non-JSON prose and whitespace still count
        no failures and can pass clean under ISSUES_ONLY until #3000 lands.

        Args:
            result: Check, fix or timeout result from the shared runner.

        Returns:
            Result with explicit parser accounting and reliable fix counts.
        """
        failures = sum(self.failure_counts)
        result.parse_failures_count = failures
        if not failures:
            return result

        result.success = False
        message = f"Failed to parse {failures} Oxlint report(s) or diagnostic(s)."
        if result.initial_issues_count is not None:
            # Neither a partial baseline nor a partial after-state can measure
            # fixes. Keep this conservative state through the executor's fold,
            # even if a later CHECK answers over only the changed files.
            result.output = _without_fix_claims(result.output)
            result.fixed_issues_count = None
            result.remaining_issues_count = None
            result.residual_unknown = True
            result.residual_unknown_reason = message
            if self.failure_counts[0]:
                result.initial_issues_count = None
            # Unknown results carry detected findings, not a measured residual.
            result.issues = list(result.initial_issues or [])
            result.issues_count = len(result.issues)
        result.output = f"{message}\n{result.output}" if result.output else message
        return result
