"""Frozen execution and fix-accounting states for the staged result migration.

Execution and accounting are independent: a timed-out mutation can retain
findings and even gain measured counts from a later verify pass. Neither a
successful command nor an empty issue list proves that files were inspected.
Issue objects, output and metadata remain on the frozen ToolResult envelope.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypeAlias

if TYPE_CHECKING:
    from lintro.models.core.tool_result import ToolResult


def _validate_counts(*counts: object, allow_unknown: bool = True) -> None:
    """Reject invalid supplied counts without manufacturing missing ones.

    Args:
        *counts: Measurements, or None when no measurement is available.
        allow_unknown: Whether missing measurements are permitted in this state.

    Raises:
        ValueError: If a supplied count is not a non-negative integer.
    """
    for value in counts:
        if value is None and allow_unknown:
            continue
        if type(value) is not int or value < 0:
            raise ValueError("supplied issue counts must be non-negative integers")


def _validate_boolean(value: object, *, allow_unknown: bool = False) -> None:
    """Validate evidence supplied through an untyped construction boundary.

    Args:
        value: A boolean, or None for unknown inspection evidence.
        allow_unknown: Whether evidence may be absent.

    Raises:
        ValueError: If supplied evidence is not a boolean.
    """
    if value is None and allow_unknown:
        return
    if type(value) is not bool:
        raise ValueError("execution evidence must be boolean")


def _validate_reason(value: object) -> None:
    """Require a non-empty textual reason without normalizing its contents.

    Args:
        value: Reason supplied by a caller, possibly without type checking.

    Raises:
        ValueError: If the reason is missing or blank.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError("a non-empty reason is required")


@dataclass(frozen=True)
class Checked:
    """An ordinary command verdict, including failures without findings.

    Inspection evidence is explicit; success alone does not establish coverage.
    """

    success: bool
    inspected_files: bool | None = None

    def __post_init__(self) -> None:
        """Validate verdict and optional inspection evidence."""
        _validate_boolean(self.success)
        _validate_boolean(self.inspected_files, allow_unknown=True)


@dataclass(frozen=True)
class Skipped:
    """A tool did not run, for a required non-empty reason."""

    reason: str

    def __post_init__(self) -> None:
        """Require a reason for skipping."""
        _validate_reason(self.reason)


@dataclass(frozen=True)
class Unavailable:
    """A capability probe could not establish a runnable tool.

    Strict policy fails the run without turning unavailable tools into findings.
    """

    reason: str
    strict: bool = False

    def __post_init__(self) -> None:
        """Validate the probe evidence and missing-tool policy."""
        _validate_reason(self.reason)
        _validate_boolean(self.strict)


@dataclass(frozen=True)
class NoFiles:
    """File discovery found nothing; no files received a clean verdict."""


@dataclass(frozen=True)
class TimedOut:
    """Execution failed on its deadline, retaining any available output."""

    output: str | None

    def __post_init__(self) -> None:
        """Validate optional captured output.

        Raises:
            ValueError: If captured output is neither text nor None.
        """
        if self.output is not None and not isinstance(self.output, str):
            raise ValueError("timeout output must be text or None")


ExecutionOutcome: TypeAlias = Checked | Skipped | Unavailable | NoFiles | TimedOut


@dataclass(frozen=True)
class FixMeasured:
    """Complete reported before/after counters, independent of execution evidence.

    Even explicit zero counters on a no-files result do not prove inspection.
    """

    initial: int
    fixed: int
    remaining: int

    def __post_init__(self) -> None:
        """Require all measurements and consistent accounting.

        Raises:
            ValueError: If a measurement is absent, invalid or inconsistent.
        """
        _validate_counts(self.initial, self.fixed, self.remaining, allow_unknown=False)
        if self.initial != self.fixed + self.remaining:
            raise ValueError(
                "measured fix accounting requires initial = fixed + remaining",
            )


@dataclass(frozen=True)
class FixUnmeasured:
    """Fix accounting could not be completed, with no invented after-counts."""

    reason: str
    initial: int | None = None

    @property
    def fixed(self) -> None:
        """Return the absent fix measurement.

        Returns:
            None, never a manufactured zero.
        """
        return None

    @property
    def remaining(self) -> None:
        """Return the absent residual measurement.

        Returns:
            None, never a manufactured zero.
        """
        return None

    def __post_init__(self) -> None:
        """Require failure evidence and validate the optional before-count."""
        _validate_reason(self.reason)
        _validate_counts(self.initial)


@dataclass(frozen=True)
class FixPartial:
    """Counters without complete fix accounting, including all unknown.

    This compatibility state preserves each supplied count exactly. It must
    never be treated as a complete measurement or filled from issue-list lengths.
    Checks and producers with partial accounting use this state. Missing counters
    are not necessarily a failed verification and cannot acquire a
    residual_unknown reason here.
    """

    initial: int | None = None
    fixed: int | None = None
    remaining: int | None = None

    def __post_init__(self) -> None:
        """Validate supplied counters while keeping incomplete ones unknown.

        Raises:
            ValueError: If counters are invalid or already form a complete set.
        """
        counts = (self.initial, self.fixed, self.remaining)
        _validate_counts(*counts)
        if all(value is not None for value in counts):
            raise ValueError("complete counters must use FixMeasured")


FixAccounting: TypeAlias = FixMeasured | FixUnmeasured | FixPartial


@dataclass(frozen=True)
class ToolOutcome:
    """Execution state together with independently recorded fix accounting.

    ``issues_count`` keeps its legacy meaning: findings carried by the result,
    which may be pre-fix or partial findings rather than a measured residual.
    """

    execution: ExecutionOutcome
    accounting: FixAccounting = field(default_factory=FixPartial)
    issues_count: int = 0

    def __post_init__(self) -> None:
        """Reject contradictory explicit states at their construction boundary.

        Raises:
            TypeError: If either state is not a supported frozen variant.
            ValueError: If counts or the combined execution/accounting are invalid.
        """
        if not isinstance(
            self.execution,
            (Checked, Skipped, Unavailable, NoFiles, TimedOut),
        ):
            raise TypeError("execution must be an execution outcome variant")
        if not isinstance(self.accounting, (FixMeasured, FixUnmeasured, FixPartial)):
            raise TypeError("accounting must be a fix accounting variant")
        _validate_counts(self.issues_count, allow_unknown=False)
        if isinstance(self.accounting, FixUnmeasured) and self.success:
            raise ValueError("unmeasured fix accounting cannot report success")
        if isinstance(self.execution, (Skipped, Unavailable, NoFiles)):
            counts = self.accounting
            if self.issues_count or any(
                value not in (None, 0)
                for value in (counts.initial, counts.fixed, counts.remaining)
            ):
                raise ValueError("inactive outcomes cannot carry findings")

    @property
    def success(self) -> bool:
        """Return the execution verdict without inferring it from counts.

        Returns:
            Whether the execution succeeded or was a successful non-event.
        """
        if isinstance(self.execution, Checked):
            return self.execution.success
        if isinstance(self.execution, Unavailable):
            return not self.execution.strict
        return isinstance(self.execution, (Skipped, NoFiles))

    def to_legacy_fields(self) -> dict[str, Any]:
        """Derive the mutable fields used by existing producers and consumers.

        Returns:
            State fields suitable for dataclasses.replace on a ToolResult.
            Non-state payloads remain on that result; timeout output is explicit.
        """
        execution = self.execution
        accounting = self.accounting
        unknown = isinstance(accounting, FixUnmeasured)
        fields: dict[str, Any] = {
            "success": self.success,
            "issues_count": self.issues_count,
            "skipped": isinstance(execution, Skipped),
            "skip_reason": execution.reason if isinstance(execution, Skipped) else None,
            "unavailable_reason": (
                execution.reason if isinstance(execution, Unavailable) else None
            ),
            "no_files": isinstance(execution, NoFiles),
            "timed_out": isinstance(execution, TimedOut),
            "inspected_files": (
                execution.inspected_files if isinstance(execution, Checked) else False
            ),
            "initial_issues_count": accounting.initial,
            "fixed_issues_count": accounting.fixed,
            "remaining_issues_count": accounting.remaining,
            "residual_unknown": unknown,
            "residual_unknown_reason": (
                accounting.reason if isinstance(accounting, FixUnmeasured) else None
            ),
        }
        if isinstance(execution, TimedOut):
            fields["output"] = execution.output
        return fields


def _execution_from_result(result: ToolResult) -> ExecutionOutcome:
    """Read execution evidence without silently normalizing contradictory flags.

    Args:
        result: The result to validate, including untyped legacy plugin values.

    Returns:
        The corresponding execution variant.

    Raises:
        ValueError: If the legacy execution fields contradict each other.
    """
    inactive = (result.skipped, result.no_files, result.timed_out)
    for flag in (result.success, *inactive):
        _validate_boolean(flag)
    if sum(inactive) > 1:
        raise ValueError("skipped, no_files and timed_out are mutually exclusive")
    if not result.skipped and result.skip_reason is not None:
        raise ValueError("skip_reason requires a skipped outcome")
    if result.unavailable_reason is not None:
        if any(inactive) or result.inspected_files is not False:
            raise ValueError(
                "unavailable execution cannot claim another outcome or inspection",
            )
        return Unavailable(reason=result.unavailable_reason, strict=not result.success)
    if any(inactive):
        if result.success != (not result.timed_out):
            raise ValueError("execution flags contradict success")
        if result.inspected_files is not False:
            raise ValueError("inactive execution cannot claim file inspection")
    if result.skipped:
        if result.skip_reason is None:
            raise ValueError("a non-empty skip reason is required")
        return Skipped(reason=result.skip_reason)
    if result.no_files:
        return NoFiles()
    if result.timed_out:
        return TimedOut(output=result.output)
    return Checked(success=result.success, inspected_files=result.inspected_files)


def outcome_from_result(result: ToolResult) -> ToolOutcome:
    """Validate and adapt the current fields, never a cached construction state.

    Args:
        result: The result at the boundary, including untyped legacy plugin values.

    Returns:
        A frozen value describing the current execution and accounting.

    Raises:
        ValueError: If fields describe contradictory state or invalid counts.
    """
    _validate_boolean(result.residual_unknown)
    initial = result.initial_issues_count
    fixed = result.fixed_issues_count
    remaining = result.remaining_issues_count
    accounting: FixAccounting
    if result.residual_unknown:
        if fixed is not None or remaining is not None:
            raise ValueError(
                "unmeasured fix accounting cannot carry measured after-counts",
            )
        if result.residual_unknown_reason is None:
            raise ValueError("a non-empty unmeasured fix reason is required")
        accounting = FixUnmeasured(
            reason=result.residual_unknown_reason,
            initial=initial,
        )
    elif result.residual_unknown_reason is not None:
        raise ValueError("residual_unknown_reason requires unmeasured fix accounting")
    elif initial is not None and fixed is not None and remaining is not None:
        accounting = FixMeasured(initial, fixed, remaining)
    else:
        accounting = FixPartial(initial, fixed, remaining)
    return ToolOutcome(
        execution=_execution_from_result(result),
        accounting=accounting,
        issues_count=result.issues_count,
    )
