"""Duplicate-code ratchet gate for Lintro's own tool definitions.

Issue #2293. ``pylint``'s ``duplicate-code`` checker (``R0801``) is enabled on
the per-tool packages under ``lintro/tools`` so the copy-pasted tool-definition
template cannot grow back after #2311 factored it out. pylint has no
per-finding baseline the way
ruff has per-file ignores, so the ratchet is a *count*: the blocks that existed
when the gate landed are recorded as ``duplicate_code_baseline`` under
``[tool.lintro.pylint]`` in ``pyproject.toml``.

Behaviour:
    * Every ``R0801`` finding is removed from the pylint result and replaced by
      this gate's verdict, so the baselined blocks are accounted for in exactly
      one place instead of failing the run twice over.
    * The run fails only when the count is **higher** than the baseline.
    * The baseline may only shrink. Lower it in the pull request that removes
      duplication; never raise it.

The gate is wired into the post-check phase (``lintro/utils/post_checks.py``),
which runs after the primary tools and therefore sees the pylint result the
run already produced — pylint is never invoked a second time.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lintro.models.core.tool_result import ToolResult

#: pylint message id for the duplicate-code checker.
DUPLICATE_CODE_MESSAGE_ID: str = "R0801"

#: Result name used for the gate's own verdict in the run summary.
DUPLICATE_CODE_GATE_NAME: str = "duplicate-code"

#: Tool whose results carry the ``R0801`` findings.
PYLINT_TOOL_NAME: str = "pylint"

#: Key holding the ratchet baseline in ``[tool.lintro.pylint]``.
DUPLICATE_CODE_BASELINE_KEY: str = "duplicate_code_baseline"

#: ``ToolResult.metadata`` marker the pylint plugin sets on a result built from
#: a real pylint report. A run that analysed nothing — no Python files, or none
#: under the configured ``include`` scope — carries no marker and yields no
#: verdict, so "pylint had nothing to look at" is never read as "no clones".
PYLINT_ANALYSED_METADATA_KEY: str = "pylint_analysed"


@dataclass(frozen=True)
class DuplicateCodeVerdict:
    """Outcome of comparing a run's duplicate-code count against the baseline.

    Attributes:
        count: Number of ``R0801`` findings the run reported.
        baseline: Configured ratchet baseline.
        exceeded: Whether ``count`` is strictly above ``baseline``.
    """

    count: int
    baseline: int
    exceeded: bool

    @property
    def message(self) -> str:
        """Render the human-readable verdict line.

        Returns:
            str: The failure message when the baseline is exceeded, otherwise
            a note recording the count against the baseline.
        """
        if self.exceeded:
            return (
                f"duplicate-code count {self.count} exceeds baseline "
                f"{self.baseline}; baseline may only shrink"
            )
        return f"duplicate-code count {self.count} is within baseline {self.baseline}"


def resolve_duplicate_code_baseline(
    *,
    config: Mapping[str, object],
) -> int | None:
    """Resolve the ratchet baseline from ``[tool.lintro.pylint]``.

    A missing, non-integral or negative value disables the gate rather than
    raising: a mistyped baseline must not be silently read as ``0`` and fail
    every run, nor crash the post-check phase.

    Args:
        config: Raw ``[tool.lintro.pylint]`` configuration mapping.

    Returns:
        int | None: The configured baseline, or None when the gate is not
        configured or the value is unusable.
    """
    raw = config.get(DUPLICATE_CODE_BASELINE_KEY)
    if raw is None or isinstance(raw, bool) or not isinstance(raw, (int, str)):
        return None
    try:
        baseline = int(raw)
    except (TypeError, ValueError):
        return None
    return baseline if baseline >= 0 else None


def is_duplicate_code_issue(issue: object) -> bool:
    """Report whether a parsed issue is a duplicate-code finding.

    Args:
        issue: A parsed issue object from a tool result.

    Returns:
        bool: True when the issue carries pylint's ``R0801`` message id.
    """
    return str(getattr(issue, "code", "")).upper() == DUPLICATE_CODE_MESSAGE_ID


def _was_analysed(*, result: ToolResult) -> bool:
    """Report whether a pylint result came from a real pylint report.

    Args:
        result: A pylint tool result from the run.

    Returns:
        bool: True when pylint actually analysed files and reported back.
    """
    if getattr(result, "skipped", False):
        return False
    metadata = result.metadata or {}
    return bool(metadata.get(PYLINT_ANALYSED_METADATA_KEY, False))


def _strip_duplicate_code_issues(*, result: ToolResult) -> int:
    """Remove duplicate-code findings from a tool result, in place.

    The gate owns the accounting for ``R0801``, so the findings are taken out
    of the tool result's counts before the run's exit code is derived. A result
    that carries nothing else becomes a pass.

    Args:
        result: Tool result to strip. Mutated in place.

    Returns:
        int: Number of duplicate-code findings removed.
    """
    issues = list(result.issues or ())
    duplicates = [issue for issue in issues if is_duplicate_code_issue(issue)]
    if not duplicates:
        return 0

    remaining = [issue for issue in issues if not is_duplicate_code_issue(issue)]
    result.issues = remaining or None
    result.issues_count = max(result.issues_count - len(duplicates), len(remaining))
    if result.issues_count == 0:
        result.success = True
    return len(duplicates)


def apply_duplicate_code_baseline(
    *,
    results: Iterable[ToolResult],
    baseline: int,
) -> DuplicateCodeVerdict | None:
    """Strip duplicate-code findings from pylint results and judge the ratchet.

    Args:
        results: Tool results collected during the run.
        baseline: Configured ratchet baseline.

    Returns:
        DuplicateCodeVerdict | None: The verdict, or None when pylint did not
        analyse anything (there is nothing to judge, and no verdict to report).
    """
    pylint_results = [
        result
        for result in results
        if str(result.name).lower() == PYLINT_TOOL_NAME and _was_analysed(result=result)
    ]
    if not pylint_results:
        return None

    count = sum(
        _strip_duplicate_code_issues(result=result) for result in pylint_results
    )
    return DuplicateCodeVerdict(
        count=count,
        baseline=baseline,
        exceeded=count > baseline,
    )
