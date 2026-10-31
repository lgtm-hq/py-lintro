"""Per-invocation parser accounting for Oxlint's batch runners."""

from dataclasses import dataclass, field

from lintro.models.core.tool_result import ToolResult
from lintro.parsers.oxlint.oxlint_issue import OxlintIssue
from lintro.parsers.oxlint.oxlint_parser import parse_oxlint_output_with_stats


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
        if result.initial_issues_count is not None and not result.timed_out:
            # A difference between incomplete reports cannot measure fixes.
            result.fixed_issues_count = None
            if self.failure_counts[0]:
                result.initial_issues_count = None
            if self.failure_counts[-1]:
                # Verification did not establish a residual. Carry forward the
                # known before-state, following ToolResult's unknown contract.
                result.remaining_issues_count = None
                result.residual_unknown = True
                result.residual_unknown_reason = message
                result.issues = list(result.initial_issues or [])
                result.issues_count = len(result.issues)
            result.output = message
        else:
            result.output = f"{message}\n{result.output}" if result.output else message
        return result
