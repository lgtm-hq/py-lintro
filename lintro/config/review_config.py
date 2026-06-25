"""Review configuration models.

Custom checklist items activate on ``domains`` (role labels such as ``api``,
``test``, ``ci``) and/or ``languages`` (``identify`` tags such as ``python``,
``rust``, ``ts``). Example:

.. code-block:: yaml

    review:
      checklist:
        items:
          - question: Does any API handler skip auth?
            domains: [api]
            languages: [python]
            category: security
"""

from __future__ import annotations

from identify.identify import ALL_TAGS
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from lintro.ai.review.constants import CUSTOM_CHECKLIST_ID_START
from lintro.ai.review.enums.checklist_display import ChecklistDisplay
from lintro.ai.review.enums.file_domain import FileDomain
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
        if any(char in value for char in "\n\r"):
            msg = "review.checklist.items question must not contain newline characters"
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
    checklist_display: ChecklistDisplay = Field(
        default=ChecklistDisplay.OFF,
        description=(
            "Structured checklist visibility: off, linked (under findings), "
            "or all (linked plus cleared/orphan appendices)."
        ),
    )
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
