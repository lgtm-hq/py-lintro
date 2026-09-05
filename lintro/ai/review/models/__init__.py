"""Data models for AI diff review."""

from __future__ import annotations

from lintro.ai.review.models.agent_prompt_scope import AgentPromptScope
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.checklist_answer import ChecklistAnswer
from lintro.ai.review.models.checklist_item import ChecklistItem
from lintro.ai.review.models.chunk_summary import ChunkSummary
from lintro.ai.review.models.chunk_timing import ChunkTiming
from lintro.ai.review.models.chunking_result import ChunkingResult
from lintro.ai.review.models.convergence_decision import ConvergenceDecision
from lintro.ai.review.models.coverage_degradation import CoverageDegradation
from lintro.ai.review.models.file_assessment import FileAssessment
from lintro.ai.review.models.file_classification import FileClassification
from lintro.ai.review.models.finding_match_result import FindingMatchResult
from lintro.ai.review.models.finding_occurrence import FindingOccurrence
from lintro.ai.review.models.finding_record import FindingRecord
from lintro.ai.review.models.phase_span import PhaseSpan
from lintro.ai.review.models.pr_metadata import PRMetadata
from lintro.ai.review.models.review_chunk import ReviewChunk
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.models.review_finding import ReviewFinding, Severity
from lintro.ai.review.models.review_metadata import ReviewMetadata
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.review_summary import ReviewSummary
from lintro.ai.review.models.review_thread import ReviewThread
from lintro.ai.review.models.review_timings import ReviewTimings
from lintro.ai.review.models.run_record import RunRecord
from lintro.ai.review.models.skipped_file import SkippedFile
from lintro.ai.review.models.suggested_change import SuggestedChange
from lintro.ai.review.models.summary_bullet import SummaryBullet
from lintro.ai.review.models.synthesis_outcome import SynthesisOutcome
from lintro.ai.review.models.verdict_reasoning import VerdictReasoning

__all__ = [
    "AgentPromptScope",
    "ChangedFile",
    "ChecklistAnswer",
    "ChecklistItem",
    "ChunkSummary",
    "ChunkTiming",
    "ChunkingResult",
    "ConvergenceDecision",
    "CoverageDegradation",
    "FileAssessment",
    "FileClassification",
    "FindingMatchResult",
    "FindingOccurrence",
    "FindingRecord",
    "PRMetadata",
    "PhaseSpan",
    "ReviewChunk",
    "ReviewContext",
    "ReviewFinding",
    "ReviewMetadata",
    "ReviewResult",
    "ReviewState",
    "ReviewSummary",
    "ReviewThread",
    "ReviewTimings",
    "RunRecord",
    "SkippedFile",
    "Severity",
    "SuggestedChange",
    "SummaryBullet",
    "SynthesisOutcome",
    "VerdictReasoning",
]
