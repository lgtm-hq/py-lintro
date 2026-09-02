"""Review run metadata."""

from __future__ import annotations

from dataclasses import dataclass, field

from lintro.ai.review.enums.coverage_degradation_reason import (
    CoverageDegradationReason,
)
from lintro.ai.review.models.coverage_degradation import CoverageDegradation
from lintro.ai.review.models.review_timings import ReviewTimings
from lintro.ai.review.models.skipped_file import SkippedFile


@dataclass(frozen=True, slots=True)
class ReviewMetadata:
    """Metadata describing an AI review run.

    Attributes:
        model (str): Model identifier used for the review.
        provider (str): Provider name (anthropic, openai, etc.).
        context_window (int): Model context window in tokens.
        depth (int): Review depth level (1-3).
        chunks_total (int): Total semantic chunks processed.
        chunks_current (int): Chunks included in this result view.
        files_reviewed (int): Number of changed files included in review.
        files_total (int): Total changed files in the diff.
        checklist_items (int): Number of checklist items in the prompt.
        token_usage (dict[str, int]): Aggregated token usage counters.
        cost_estimate_usd (float): Estimated cost in USD.
        base_ref (str): Base git ref for the diff.
        head_ref (str): Head git ref for the diff.
        timestamp (str): ISO 8601 UTC timestamp of the review run.
        strictness (str): Sensitivity preset (focused, balanced, thorough).
        token_usage_estimated (bool): True when token counts were estimated
            locally (e.g. CLI transport) rather than reported by the provider.
            Drives ``~`` / exact labeling in the posted PR comment.
        partial (bool): True when the review stopped before every chunk was
            reviewed (e.g. cost cap reached mid-run).
        chunks_reviewed (int): Number of chunks actually reviewed. Equals
            ``chunks_total`` for a complete review; smaller when ``partial``.
        stopped_reason (str): Human-readable reason a partial review stopped
            (e.g. "cost cap"). Empty for a complete review.
        duration_seconds (float): Wall-clock duration of the review run.
        transport (str): Transport used for the review (``api`` or ``cli``).
            Empty for legacy records that predate transport stamping.
        auth_mode (str): How the provider call authenticated —
            ``api_key`` or ``subscription`` (see
            ``lintro.ai.transport.AuthMode``). Empty when unknown.
        cost_basis (str): Provenance of ``cost_estimate_usd`` — ``billed``,
            ``estimated``, or ``unpriceable`` (#1923). Empty when unknown.
        provider_source (str): Provenance of ``provider`` — ``flag``,
            ``env``, ``config``, or ``default`` (#1970). Empty on legacy
            records.
        model_source (str): Provenance of ``model`` (#1970). Empty on
            legacy records.
        transport_source (str): Provenance of ``transport`` (#1970). Empty
            on legacy records.
        max_cost_usd (float | None): Effective ``ai.max_cost_usd`` ceiling
            for this run. ``None`` means uncapped (#2024). Unset on
            legacy records (same default, so they must not be labeled
            uncapped without ``max_cost_usd_source``).
        max_cost_usd_source (str): Provenance of ``max_cost_usd`` (#2024).
            Empty on legacy records.
        phase_timings (dict[str, float]): Per-phase wall-clock seconds for
            regression visibility. Keys include ``context_collection``,
            ``provider`` (chunk + custom-agent provider calls), and
            ``parse_merge``. Kept as a flat mapping for backward
            compatibility; ``timings`` carries the full breakdown.
        timings (ReviewTimings | None): Full per-phase timing breakdown
            (ordered spans plus per-chunk queued/in-flight detail, #2148).
            ``None`` on legacy records and merge-only placeholders.
        custom_agents_run (int): Number of user-defined review agents that
            completed a pass in this run (issue #1245).
        custom_agents_skipped (int): Number of discovered agents that did not
            run because they are disabled or matched no changed file.
        reviewed_paths (tuple[str, ...]): Repository-relative paths the review
            actually looked at, in sorted order.
        skipped_files (tuple[SkippedFile, ...]): Changed files excluded from
            the review, each carrying the reason it was excluded (#1910).
        coverage_degradations (tuple[CoverageDegradation, ...]): Chunk-level
            limits that may have suppressed findings (#2003), one entry per
            limit event — a CLI per-call findings cap, or an
            output-exhaustion retry at a tighter cap — so one chunk can
            contribute both. Every chunk was still reviewed, so this is a
            *depth* limit and deliberately distinct from ``partial``, which
            means chunks went unreviewed. Empty for a fully uncapped run.
    """

    model: str
    provider: str
    context_window: int
    depth: int
    chunks_total: int
    chunks_current: int
    files_reviewed: int
    files_total: int
    checklist_items: int
    token_usage: dict[str, int] = field(default_factory=dict)
    cost_estimate_usd: float = 0.0
    base_ref: str = ""
    head_ref: str = ""
    timestamp: str = ""
    strictness: str = "balanced"
    token_usage_estimated: bool = False
    partial: bool = False
    chunks_reviewed: int = 0
    stopped_reason: str = ""
    duration_seconds: float = 0.0
    transport: str = ""
    auth_mode: str = ""
    cost_basis: str = ""
    provider_source: str = ""
    model_source: str = ""
    transport_source: str = ""
    max_cost_usd: float | None = None
    max_cost_usd_source: str = ""
    phase_timings: dict[str, float] = field(default_factory=dict)
    timings: ReviewTimings | None = None
    custom_agents_run: int = 0
    custom_agents_skipped: int = 0
    reviewed_paths: tuple[str, ...] = field(default_factory=tuple)
    skipped_files: tuple[SkippedFile, ...] = field(default_factory=tuple)
    coverage_degradations: tuple[CoverageDegradation, ...] = field(
        default_factory=tuple,
    )

    @property
    def coverage_complete(self) -> bool:
        """Return whether the run asked the model for an unlimited finding set.

        Returns:
            True when no chunk ran under a findings cap or a tightened
            output-exhaustion retry. ``partial`` is a separate axis: a run can
            be complete in coverage depth and still have stopped early.
        """
        return not self.coverage_degradations

    @property
    def findings_cap_applied(self) -> int | None:
        """Return the tightest findings ceiling any chunk ran under.

        Returns:
            The smallest recorded cap, or ``None`` when no cap was applied.
        """
        caps = [item.findings_cap for item in self.coverage_degradations]
        return min(caps) if caps else None

    @property
    def output_exhaustion_retried(self) -> bool:
        """Return whether any chunk was retried after output exhaustion.

        Returns:
            True when at least one chunk hit the provider output-token
            ceiling and was re-run under a tighter findings cap.
        """
        return any(
            item.reason is CoverageDegradationReason.OUTPUT_EXHAUSTION_RETRIED
            for item in self.coverage_degradations
        )
