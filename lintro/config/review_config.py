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

from lintro.enums.checklist_display import ChecklistDisplay
from lintro.enums.custom_agent_mode import CustomAgentMode
from lintro.enums.file_domain import FileDomain
from lintro.enums.review_category import ReviewCategory
from lintro.enums.review_strictness import ReviewStrictness

__all__ = [
    "ReviewChecklistConfig",
    "ReviewChecklistItemConfig",
    "ReviewConfig",
    "ReviewConvergenceConfig",
    "ReviewSensitivityOverrides",
    "ReviewSynthesisConfig",
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


class ReviewSynthesisConfig(BaseModel):
    """Cross-chunk synthesis pass configuration (#2269).

    Each review chunk is reviewed in isolation, so a bug that only exists in
    the combination of two files split across chunks is invisible to every
    chunk. The synthesis pass is one extra provider call, made after the chunk
    findings are merged, that sees the whole changed-file list, a compact
    per-chunk summary, and as much of the whole-PR diff as its token budget
    allows, and is asked for cross-file inconsistencies only.

    Off by default: it adds a call per round, and the cost and wall-clock
    delta is measured through the #2148 phase timings and the #2147 matrix
    before it is switched on.
    """

    model_config = ConfigDict(frozen=False, extra="forbid")

    enabled: bool = Field(
        default=False,
        description=(
            "Run one extra whole-PR pass after the chunk findings are merged, "
            "asked only for inconsistencies between files reviewed in "
            "different chunks. Costs one additional provider call per round "
            "and only runs when the review used more than one chunk."
        ),
    )
    max_findings: int = Field(
        default=5,
        ge=1,
        description=(
            "Maximum findings the synthesis pass may add to a round. The pass "
            "is a targeted cross-file sweep, not a second review, so the cap "
            "is deliberately small."
        ),
    )

    @field_validator("max_findings", mode="before")
    @classmethod
    def _reject_bool_max_findings(cls, value: object) -> object:
        """Reject a boolean where an integer count is required.

        ``bool`` is an ``int`` subclass, so ``max_findings: true`` would
        otherwise validate as ``1`` and silently cap the pass at one finding.

        Args:
            value: Raw ``review.synthesis.max_findings`` value.

        Returns:
            The value unchanged when it is not a boolean.

        Raises:
            ValueError: When the value is a boolean.
        """
        if isinstance(value, bool):
            msg = (
                "review.synthesis.max_findings must be an integer >= 1, "
                f"got {value!r}"
            )
            raise ValueError(msg)
        return value


class ReviewConvergenceConfig(BaseModel):
    """Deterministic re-review stop rule for ``lintro review`` (#2099).

    Each round scores its still-open findings (see
    :mod:`lintro.ai.review.convergence`). Once ``stable_rounds`` consecutive
    rounds have scored below ``threshold``, the next round short-circuits
    before any provider call. The decision is made in code from persisted
    state, never asked of the model.

    Disabled by default: with ``threshold`` unset, every round runs exactly as
    it did before the rule existed.
    """

    model_config = ConfigDict(frozen=False, extra="forbid")

    threshold: float | None = Field(
        default=None,
        ge=0.0,
        description=(
            "Convergence score strictly below which a round counts as quiet. "
            "null (the default) disables the stop rule and reviews every "
            "round. For calibration: one low-confidence P3 scores 1.25, one "
            "high-confidence P1 scores 10.0."
        ),
    )
    stable_rounds: int = Field(
        default=2,
        ge=1,
        description=(
            "How many consecutive rounds must score below the threshold "
            "before the next round is skipped. A partial or coverage-limited "
            "round never counts toward the streak."
        ),
    )

    @field_validator("threshold", "stable_rounds", mode="before")
    @classmethod
    def _reject_booleans(cls, value: object) -> object:
        """Refuse a YAML boolean where a number is expected.

        Pydantic's lax mode would coerce ``true`` to ``1.0``, silently arming
        the stop rule at a threshold of one. A rule that skips reviews must
        never switch itself on by accident.

        Args:
            value: Raw configured value.

        Returns:
            The value unchanged when it is not a boolean.

        Raises:
            ValueError: When the value is a boolean.
        """
        if isinstance(value, bool):
            msg = "must be a number, not a boolean"
            raise ValueError(msg)
        return value


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
    auto_resolve: bool = Field(
        default=True,
        description=(
            "Resolve a PR review thread once its finding no longer reproduces. "
            "The '✔ Addressed' banner is written onto the inline comment either "
            "way; set false to keep resolving threads a manual ceremony. A "
            "partially addressed pattern is never resolved, and a regression "
            "never reopens a resolved thread."
        ),
    )
    synthesis: ReviewSynthesisConfig = Field(
        default_factory=ReviewSynthesisConfig,
        description=(
            "Final cross-chunk synthesis pass (#2269). Off by default pending "
            "the #2147 cost/agreement measurement."
        ),
    )
    convergence: ReviewConvergenceConfig = Field(
        default_factory=ReviewConvergenceConfig,
        description=(
            "Deterministic re-review stop rule; disabled unless "
            "review.convergence.threshold is set."
        ),
    )
    custom_agents: CustomAgentMode = Field(
        default=CustomAgentMode.ENABLED,
        description=(
            "User-defined review agents from .lintro/review-agents/*.md: "
            "true/enabled (default, run alongside the built-in checklist), "
            "false/disabled (skip discovery), or only (run agents instead of "
            "the built-in checklist)."
        ),
    )

    @field_validator("custom_agents", mode="before")
    @classmethod
    def _coerce_custom_agents(cls, value: object) -> object:
        """Accept the ``true`` / ``false`` YAML spellings for the mode enum.

        Args:
            value: Raw ``review.custom_agents`` value from configuration.

        Returns:
            A value the ``CustomAgentMode`` enum can validate.

        Raises:
            ValueError: When the value is not a recognized mode spelling.
        """
        if isinstance(value, bool):
            return CustomAgentMode.ENABLED if value else CustomAgentMode.DISABLED
        if isinstance(value, str):
            normalized = value.strip().lower()
            aliases = {
                "true": CustomAgentMode.ENABLED,
                "yes": CustomAgentMode.ENABLED,
                "on": CustomAgentMode.ENABLED,
                "false": CustomAgentMode.DISABLED,
                "no": CustomAgentMode.DISABLED,
                "off": CustomAgentMode.DISABLED,
            }
            if normalized in aliases:
                return aliases[normalized]
            if normalized in {mode.value for mode in CustomAgentMode}:
                return normalized
            allowed = ", ".join(mode.value for mode in CustomAgentMode)
            msg = (
                f"review.custom_agents must be true, false, or one of: "
                f"{allowed} (got {value!r})"
            )
            raise ValueError(msg)
        return value
