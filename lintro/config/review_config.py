"""Review configuration models."""

from __future__ import annotations

from identify.identify import ALL_TAGS
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from lintro.ai.review.constants import CUSTOM_CHECKLIST_ID_START
from lintro.ai.review.enums.file_domain import FileDomain
from lintro.ai.review.enums.review_category import ReviewCategory

__all__ = [
    "CUSTOM_CHECKLIST_ID_START",
    "ReviewChecklistConfig",
    "ReviewChecklistItemConfig",
    "ReviewConfig",
]


class ReviewChecklistItemConfig(BaseModel):
    """User-defined checklist item from configuration.

    Custom items activate on the same two axes as builtins: ``domains`` are role
    labels from :class:`FileDomain` and ``languages`` are ``identify`` tags. At
    least one axis must be set so the item targets some part of a diff.
    """

    model_config = ConfigDict(frozen=False, extra="forbid")

    question: str
    domains: list[FileDomain] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    category: ReviewCategory

    @field_validator("question")
    @classmethod
    def _validate_question(cls, value: str) -> str:
        if not value.strip():
            msg = "review.checklist.items question must not be empty"
            raise ValueError(msg)
        return value.strip()

    @field_validator("languages")
    @classmethod
    def _validate_languages(cls, value: list[str]) -> list[str]:
        languages = [language.strip() for language in value if language.strip()]
        unknown = [language for language in languages if language not in ALL_TAGS]
        if unknown:
            msg = (
                "review.checklist.items languages must be known identify tags; "
                f"unknown: {', '.join(sorted(unknown))}"
            )
            raise ValueError(msg)
        return languages

    @model_validator(mode="after")
    def _validate_targets(self) -> ReviewChecklistItemConfig:
        if not self.domains and not self.languages:
            msg = (
                "review.checklist.items must set at least one of domains or "
                "languages"
            )
            raise ValueError(msg)
        return self


class ReviewChecklistConfig(BaseModel):
    """Checklist configuration section."""

    model_config = ConfigDict(frozen=False, extra="forbid")

    items: list[ReviewChecklistItemConfig] = Field(default_factory=list)


class ReviewConfig(BaseModel):
    """Configuration for the lintro review command."""

    model_config = ConfigDict(frozen=False, extra="forbid")

    checklist: ReviewChecklistConfig = Field(default_factory=ReviewChecklistConfig)
