"""Enumerations for AI diff review."""

from __future__ import annotations

from lintro.ai.review.enums.agent_prompt_scope_kind import AgentPromptScopeKind
from lintro.ai.review.enums.changed_file_status import ChangedFileStatus
from lintro.ai.review.enums.coverage_degradation_reason import (
    CoverageDegradationReason,
)
from lintro.ai.review.enums.cross_chunk_contradiction import (
    CrossChunkContradiction,
)
from lintro.ai.review.enums.evidence_style import EvidenceStyle
from lintro.ai.review.enums.file_domain import FileDomain
from lintro.ai.review.enums.file_skip_reason import FileSkipReason
from lintro.ai.review.enums.finding_kind import FindingKind
from lintro.ai.review.enums.finding_match_outcome import FindingMatchOutcome
from lintro.ai.review.enums.finding_origin import FindingOrigin
from lintro.ai.review.enums.finding_status import FindingStatus
from lintro.ai.review.enums.lifecycle_stage import LifecycleStage
from lintro.ai.review.enums.review_category import ReviewCategory
from lintro.ai.review.enums.review_context_error_code import ReviewContextErrorCode
from lintro.ai.review.enums.review_verdict import ReviewVerdict
from lintro.ai.review.enums.suggestion_drop_reason import SuggestionDropReason

__all__ = [
    "AgentPromptScopeKind",
    "ChangedFileStatus",
    "CoverageDegradationReason",
    "CrossChunkContradiction",
    "EvidenceStyle",
    "FileDomain",
    "FileSkipReason",
    "FindingKind",
    "FindingMatchOutcome",
    "FindingOrigin",
    "FindingStatus",
    "LifecycleStage",
    "ReviewCategory",
    "ReviewContextErrorCode",
    "ReviewVerdict",
    "SuggestionDropReason",
]
