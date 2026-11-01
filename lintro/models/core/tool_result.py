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


@dataclass
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

    Stages 1 through 3 of #3019 keep these fields writable: verify folding, retries and
    other existing callers still update them in several steps. ``outcome`` is
    a read-only, validated view computed on each access, not a stored second
    source of truth. Read it after an update is complete; contradictory legacy
    state raises instead of returning an obsolete or normalized outcome.
    Producers use ``from_outcome`` to derive state fields from frozen variants;
    ``with_outcome`` preserves the rest of an existing envelope. Migrated
    consumers read the current outcome; freezing this dataclass belongs to stage 4.
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
    # nothing. Deliberately distinct from ``skipped`` (which forces
    # ``success=True`` and hides the tool from reporting): a no-files result is
    # a successful non-event, but it is not a verdict about any file, so the
    # run-level verify pass (#1743) must not read it as "these files are
    # clean".
    no_files: bool = field(default=False)

    # Skip tracking for tools that didn't execute
    skipped: bool = field(default=False)
    skip_reason: str | None = field(default=None)

    # Execution-timeout tracking. ``True`` when the tool's subprocess exceeded
    # its deadline and was killed. A timeout is an *execution failure*, not a
    # lint finding: it never contributes to ``issues_count``. Consumers use
    # this flag (surfaced as ``timed_out`` in the JSON report) to tell an
    # infrastructure flake apart from a genuine finding without regex-matching
    # the human-readable ``output`` string.
    timed_out: bool = field(default=False)

    # A multi-file check may report real findings alongside a launch/parser
    # failure on another file. Acknowledging the findings cannot hide that error.
    execution_failed: bool = field(default=False)

    # Producer-owned proof that every invocation completed with a recognized
    # lint verdict and its report was fully parsed. Only then can findings
    # explain success=False. Legacy producers default to unknown, so parsed
    # findings alone cannot authorize acknowledgements or baseline pruning.
    findings_complete: bool = field(default=False)

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

    # Exact canonical files successfully examined by this producer. None is
    # unknown coverage, not the requested scope. Fingerprint pruning requires
    # both this evidence and a complete result (#438, #2609). A completed
    # inspection may have lint findings; failed execution supplies no coverage.
    inspected_paths: frozenset[str] | None = field(default=None)
    # Codes outside that exact coverage, e.g. a separate formatter check whose
    # native file exclusions cannot be enumerated by the lint invocation.
    uninspected_codes: frozenset[str] = field(default_factory=frozenset)

    # None preserves the original reporting schema when baselining is absent.
    baselined_count: int | None = field(default=None)

    @classmethod
    def from_outcome(cls, outcome: ToolOutcome, **payload: Any) -> ToolResult:
        """Construct legacy-compatible fields from explicit producer evidence.

        Args:
            outcome: Validated execution and accounting state.
            **payload: Non-state envelope fields, such as name, issues and metadata.
                TimedOut owns its output; a shared check/timeout branch may also
                supply envelope output, but it must match the timeout payload.

        Returns:
            A mutable result compatible with consumers awaiting migration.

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
        """Adapt the current mutable fields to a validated frozen outcome.

        Contradictory legacy mutations raise ValueError at the adapter boundary.

        Returns:
            The current execution and accounting state, without inferred counts.
        """
        return outcome_from_result(self)

    def validate_outcome(self) -> None:
        """Reject contradictory mutable state before handing it to consumers.

        The adapter raises ValueError if the current fields are contradictory.
        """
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

        if self.no_files or self.skipped or self.timed_out:
            self.inspected_files = False
        if self.execution_failed:
            self.success = False

        # Skipped tools are not failures — they just didn't run
        if self.skipped:
            self.success = True
            if not self.skip_reason:
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
