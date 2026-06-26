"""Review configuration models."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from lintro.ai.review.constants import CUSTOM_CHECKLIST_ID_START
from lintro.ai.review.enums.review_category import ReviewCategory
from lintro.ai.review.enums.review_strictness import ReviewStrictness

__all__ = [
    "CUSTOM_CHECKLIST_ID_START",
    "ReviewChecklistConfig",
    "ReviewChecklistItemConfig",
    "ReviewConfig",
    "ReviewSensitivityOverrides",
]


class ReviewChecklistItemConfig(BaseModel):
    """User-defined checklist item from configuration."""

    model_config = ConfigDict(frozen=False, extra="forbid")

    question: str
    triggers: list[str] = Field(default_factory=list)
    category: ReviewCategory

    @field_validator("question")
    @classmethod
    def _validate_question(cls, value: str) -> str:
        if not value.strip():
            msg = "review.checklist.items question must not be empty"
            raise ValueError(msg)
        return value.strip()

    @field_validator("triggers")
    @classmethod
    def _validate_triggers(cls, value: list[str]) -> list[str]:
        triggers = [trigger.strip() for trigger in value if trigger.strip()]
        if not triggers:
            msg = (
                "review.checklist.items triggers must include at least "
                "one glob pattern"
            )
            raise ValueError(msg)
        return triggers


class ReviewChecklistConfig(BaseModel):
    """Checklist configuration section."""

    model_config = ConfigDict(frozen=False, extra="forbid")

    items: list[ReviewChecklistItemConfig] = Field(default_factory=list)


class ReviewSensitivityOverrides(BaseModel):
    """Fine-grained sensitivity overrides for ``lintro review``.

    Each field overrides the active ``strictness`` preset when set.
    ``null`` keeps the preset default.
    """

    model_config = ConfigDict(frozen=False, extra="forbid")

    migration_notes: bool | None = Field(
        default=None,
        description=("Report missing migration notes and old→new command mappings."),
    )
    doc_drift: bool | None = Field(
        default=None,
        description=(
            "Report documentation-only contract drift and undocumented "
            "CI/local parity in docs."
        ),
    )
    test_gaps: bool | None = Field(
        default=None,
        description="Report P3 test-coverage and wiring gaps.",
    )


class ReviewConfig(BaseModel):
    """Configuration for the lintro review command."""

    model_config = ConfigDict(frozen=False, extra="forbid")

    checklist: ReviewChecklistConfig = Field(default_factory=ReviewChecklistConfig)
    strictness: ReviewStrictness = Field(
        default=ReviewStrictness.BALANCED,
        description=("Review sensitivity preset: focused, balanced, or thorough."),
    )
    sensitivity: ReviewSensitivityOverrides = Field(
        default_factory=ReviewSensitivityOverrides,
        description="Optional per-category sensitivity overrides.",
    )
    depth: int = Field(
        default=1,
        ge=1,
        le=3,
        description=(
            "Review depth: 1=checklist only, 2=+generated questions, "
            "3=+adversarial sweep (per chunk)."
        ),
    )
    force_semantic_chunking: bool = Field(
        default=False,
        description=(
            "Split the diff into semantic chunks even when it fits the token "
            "budget (same as ``lintro review --semantic-chunks``). Slower "
            "(one agent call per chunk) but can surface more per-file doc nits."
        ),
    )
