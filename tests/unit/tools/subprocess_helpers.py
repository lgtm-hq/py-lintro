"""Native subprocess fixtures for tests that only need lint exit statuses."""

from lintro.models.core.subprocess_result import SubprocessResult


def subprocess_result(success: bool, output: str) -> SubprocessResult:
    """Build a native clean or findings result from a test's canned verdict.

    Args:
        success: Whether the invocation exited zero.
        output: Native output to parse and display.

    Returns:
        A native result with exit status zero or one and no stderr.
    """
    return SubprocessResult(0 if success else 1, output, "", output)
