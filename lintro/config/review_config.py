"""Review configuration models."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from lintro.ai.review.constants import CUSTOM_CHECKLIST_ID_START
from lintro.ai.review.enums.review_category import ReviewCategory

__all__ = [
    "CUSTOM_CHECKLIST_ID_START",
    "ReviewChecklistConfig",
    "ReviewChecklistItemConfig",
    "ReviewConfig",
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


class ReviewConfig(BaseModel):
    """Configuration for the lintro review command."""

    model_config = ConfigDict(frozen=False, extra="forbid")

    checklist: ReviewChecklistConfig = Field(default_factory=ReviewChecklistConfig)
