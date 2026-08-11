"""Review run metadata."""

from __future__ import annotations

from dataclasses import dataclass, field

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
        phase_timings (dict[str, float]): Per-phase wall-clock seconds for
            regression visibility. Keys include ``context_collection``,
            ``provider`` (chunk + custom-agent provider calls), and
            ``parse_merge``.
        custom_agents_run (int): Number of user-defined review agents that
            completed a pass in this run (issue #1245).
        custom_agents_skipped (int): Number of discovered agents that did not
            run because they are disabled or matched no changed file.
        reviewed_paths (tuple[str, ...]): Repository-relative paths the review
            actually looked at, in sorted order.
        skipped_files (tuple[SkippedFile, ...]): Changed files excluded from
            the review, each carrying the reason it was excluded (#1910).
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
    phase_timings: dict[str, float] = field(default_factory=dict)
    custom_agents_run: int = 0
    custom_agents_skipped: int = 0
    reviewed_paths: tuple[str, ...] = field(default_factory=tuple)
    skipped_files: tuple[SkippedFile, ...] = field(default_factory=tuple)
