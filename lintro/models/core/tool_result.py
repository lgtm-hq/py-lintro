"""Models for core tool execution results.

This module defines the canonical result object returned by all tools. It
supports both check and fix flows and includes standardized fields to report
fixed vs remaining counts for fix-capable tools.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from lintro.enums.capability import Cap
from lintro.models.core.tool_outcome import ToolOutcome, outcome_from_result

if TYPE_CHECKING:
    from lintro.parsers.base_issue import BaseIssue


@dataclass(frozen=True)
class ToolResult:
    """Result of running a tool.

    For check operations:
        - ``issues_count`` represents the number of issues found.

    For fix/format operations:
        - ``initial_issues_count`` is the number of issues detected before fixes
        - ``fixed_issues_count`` is the number of issues the tool auto-fixed
        - ``remaining_issues_count`` is the number of issues still remaining,
          or ``None`` when ``residual_unknown`` says it was never measured
        - ``issues_count`` should mirror ``remaining_issues_count`` for
          backward compatibility in format-mode summaries

    When ``residual_unknown`` is set the tool or run-level verify pass (#1743)
    could not establish complete fix accounting, so there is no count to mirror:
    ``fixed_issues_count`` and ``remaining_issues_count`` are both ``None``
    (enforced below) and ``issues_count`` counts the usable findings carried
    forward in ``issues``. These include pre-fix findings and, for incomplete
    reports, successfully parsed post-fix findings without duplicates. It is
    not a measured residual, and consumers must render "unknown" rather than
    presenting it as one.

    The ``issues`` field can contain parsed issue objects (tool-specific) to
    support unified table formatting.

    Convention: for FIX action, remaining issues occupy the tail of the
    ``issues`` list. Tools append all detected issues in order, so the
    last ``remaining_issues_count`` entries are the ones still unfixed.

    State is frozen and validated at construction, including dataclasses.replace.
    Producers use ``from_outcome`` to derive state fields from frozen variants;
    ``with_outcome`` preserves the rest of an existing envelope. ``outcome`` is
    a computed view, not a stored second source of truth. Collection payloads
    remain shallowly shared, as with other frozen dataclasses; replacing state
    never requires changing an existing result. Legacy inactive constructors default
    unknown inspection to False, and skips normalize success to True as before.
    Explicit inspection claims and contradictory outcomes are rejected.
    """

    name: str = field(default="")
    success: bool = field(default=False)
    output: str | None = field(default=None)
    issues_count: int = field(default=0)
    formatted_output: str | None = field(default=None)
    issues: Sequence[BaseIssue] = field(default_factory=tuple)

    # Optional standardized counts for fix-capable tools
    initial_issues_count: int | None = field(default=None)
    fixed_issues_count: int | None = field(default=None)
    remaining_issues_count: int | None = field(default=None)

    # Pre-fix issues detected before applying fixes (for displaying what was fixed)
    initial_issues: Sequence[BaseIssue] = field(default_factory=tuple)

    # Optional pytest-specific summary data for display
    pytest_summary: dict[str, Any] | None = field(default=None)

    # Tool metadata. Most keys are AI-generated (explanations, fix
    # suggestions), but the field is not AI-specific: osv-scanner stores its
    # suppression classifications here with the AI layer fully disabled.
    # Expected keys (all optional):
    #   "fix_suggestions": list[AIFixSuggestionPayload]  (serialized)
    #   "fixed_count": int
    #   "verified_count": int
    #   "unverified_count": int
    #   "telemetry": dict with api_calls, tokens, cost, latency
    #   "suppressions": list[dict]  (osv-scanner, non-AI)
    # AI keys are built incrementally via helpers in lintro.ai.metadata.
    metadata: dict[str, Any] = field(default_factory=dict)

    # Working directory used during tool execution (for resolving relative
    # issue file paths in AI fix generation)
    cwd: str | None = field(default=None)

    # True when the tool returned early because file discovery matched
    # nothing. Deliberately distinct from ``skipped`` (which requires
    # ``success=True`` and hides the tool from reporting): a no-files result is
    # a successful non-event, but it is not a verdict about any file, so the
    # run-level verify pass (#1743) must not read it as "these files are
    # clean".
    no_files: bool = field(default=False)

    # Skip tracking for tools that didn't execute
    skipped: bool = field(default=False)
    skip_reason: str | None = field(default=None)

    # Probe evidence for the frozen Unavailable outcome; success carries policy.
    unavailable_reason: str | None = field(default=None)

    # Execution-timeout tracking. ``True`` when the tool's subprocess exceeded
    # its deadline and was killed. A timeout is an *execution failure*, not a
    # lint finding: it never contributes to ``issues_count``. Consumers use
    # this flag (surfaced as ``timed_out`` in the JSON report) to tell an
    # infrastructure flake apart from a genuine finding without regex-matching
    # the human-readable ``output`` string.
    timed_out: bool = field(default=False)

    # Parser failures (items that could not be parsed from tool output).
    # Omitted from JSON output unless the tool sets this explicitly.
    parse_failures_count: int | None = field(default=None)

    # Wall-clock seconds the tool took, recorded by the executor around the
    # check/fix call (and around synthetic crash/timeout failure results so
    # they still appear in ``--profile``). ``None`` when the result was not
    # produced by a run (skipped tools, post-checks, or a result built
    # directly in a test).
    duration_seconds: float | None = field(default=None)

    # Set when the tool or run-level verify pass (#1743) could not measure
    # complete fix accounting: a report was incomplete, its ``CHECK`` raised,
    # timed out, was skipped, or the tool could not be resolved.
    # This is a third state beside "clean" and "N remaining": the difference
    # between complete before/after measurements is unavailable, so
    # ``fixed_issues_count`` and ``remaining_issues_count`` are cleared to
    # ``None`` and consumers must render "unknown" rather than a number. The
    # run fails either way, which is enforced below: ``success`` must be
    # False whenever this is set.
    residual_unknown: bool = field(default=False)
    residual_unknown_reason: str | None = field(default=None)

    # Which capability produced this result: ``CHECK`` outside a fix run, and
    # the tool's mutating capability (``FIX``/``FORMAT``) inside one. It keeps
    # that value after the verify pass folds its ``CHECK`` residual into the
    # same row (#1743) — display rolls up to the tool, so the row stays the
    # mutation's and the field names what produced it. ``None`` on results not
    # produced by a capability at all (skipped tools, run-level gates, results
    # built directly in a test).
    capability: Cap | None = field(default=None)

    # Whether the producer actually inspected files. None preserves the unknown
    # state for legacy plugins; neither output text nor success proves inspection.
    # False with no_files=True means empty discovery; False without it means a
    # decline (for example missing configuration), skip, or timeout.
    inspected_files: bool | None = field(default=None)

    @classmethod
    def from_outcome(cls, outcome: ToolOutcome, **payload: Any) -> ToolResult:
        """Construct legacy-compatible fields from explicit producer evidence.

        Args:
            outcome: Validated execution and accounting state.
            **payload: Non-state envelope fields, such as name, issues and metadata.
                TimedOut owns its output; a shared check/timeout branch may also
                supply envelope output, but it must match the timeout payload.

        Returns:
            A frozen, validated result with legacy-compatible fields.

        Raises:
            TypeError: If payload supplies state fields or conflicting timeout text.
        """
        state = outcome.to_legacy_fields()
        conflicts = (payload.keys() & state.keys()) - {"output"}
        if conflicts:
            raise TypeError(
                f"outcome owns state fields: {', '.join(sorted(conflicts))}",
            )
        if (
            "output" in state
            and "output" in payload
            and payload["output"] != state["output"]
        ):
            raise TypeError("timeout output must match the outcome")
        return cls(**(payload | state))

    @property
    def outcome(self) -> ToolOutcome:
        """Adapt the frozen fields to a validated outcome.

        Returns:
            The current execution and accounting state, without inferred counts.
        """
        return outcome_from_result(self)

    @property
    def unavailable(self) -> bool:
        """Whether a probe prevented execution, independent of strict policy.

        Returns:
            True when the result carries unavailable evidence.
        """
        return self.unavailable_reason is not None

    def validate_outcome(self) -> None:
        """Validate the outcome, raising ValueError for contradictory evidence."""
        # Plugin boundaries call this unbound so a duck-typed object's own
        # validator cannot bypass the ToolResult type guard.
        if not isinstance(self, ToolResult):
            raise ValueError("plugin check()/fix() must return ToolResult")
        outcome_from_result(self)

    def with_outcome(self, outcome: ToolOutcome) -> ToolResult:
        """Return a result with state derived from an explicit outcome.

        Args:
            outcome: Frozen execution and accounting state to apply.

        Returns:
            A replacement preserving issues, metadata and other envelope fields.
            As with dataclasses.replace, mutable payloads are shared.
        """
        return replace(self, **outcome.to_legacy_fields())

    def __post_init__(self) -> None:
        """Validate collections, issue counts and execution state.

        Raises:
            TypeError: If a collection field is None.
            ValueError: If issue counts are inconsistent or skip state is invalid.
        """
        # Untyped plugins can violate the annotations at the construction boundary.
        collection_fields: tuple[tuple[str, object], ...] = (
            ("issues", self.issues),
            ("initial_issues", self.initial_issues),
            ("metadata", self.metadata),
        )
        for field_name, value in collection_fields:
            if value is None:
                raise TypeError(f"ToolResult.{field_name} must not be None")

        # Preserve API-v2 constructor defaults only at the construction boundary.
        # False is also the historical default for success, so a skip normalizes
        # it to True. Explicit affirmative inspection is never normalized away.
        if (
            self.no_files
            or self.skipped
            or self.timed_out
            or self.unavailable_reason is not None
        ) and (self.inspected_files is None):
            object.__setattr__(self, "inspected_files", False)
        if self.skipped and self.success is False:
            object.__setattr__(self, "success", True)

        if self.skipped and not self.skip_reason:
            raise ValueError(
                "skip_reason is required when skipped=True",
            )

        if self.skip_reason and not self.skipped:
            raise ValueError(
                "skip_reason can only be set when skipped=True",
            )

        if self.residual_unknown and not self.residual_unknown_reason:
            raise ValueError(
                "residual_unknown_reason is required when residual_unknown=True",
            )

        if self.residual_unknown_reason and not self.residual_unknown:
            raise ValueError(
                "residual_unknown_reason can only be set when residual_unknown=True",
            )

        if self.residual_unknown and self.success:
            # The other half of the same contract: an unmeasured residual
            # fails the run. A result that claims success while admitting it
            # never measured anything would exit 0 on a run whose outcome
            # nobody knows.
            raise ValueError(
                "success must be False when residual_unknown=True",
            )

        if self.residual_unknown and (
            self.fixed_issues_count is not None
            or self.remaining_issues_count is not None
        ):
            # "Unknown" beside a hard number is the one rendering this state
            # exists to prevent: the display would print "unknown" in one
            # column and a measurement nobody took in the next. A producer
            # that folds incorrectly fails here rather than downstream.
            raise ValueError(
                "fixed_issues_count and remaining_issues_count must be None "
                "when residual_unknown=True: "
                f"fixed={self.fixed_issues_count}, "
                f"remaining={self.remaining_issues_count}",
            )

        if (
            self.initial_issues_count is not None
            and self.fixed_issues_count is not None
            and self.remaining_issues_count is not None
            and self.initial_issues_count
            != self.fixed_issues_count + self.remaining_issues_count
        ):
            raise ValueError(
                f"Inconsistent issue counts: "
                f"initial={self.initial_issues_count}, "
                f"fixed={self.fixed_issues_count}, "
                f"remaining={self.remaining_issues_count}. "
                f"Expected: initial = fixed + remaining",
            )

        self.validate_outcome()
