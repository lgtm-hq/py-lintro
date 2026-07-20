"""Health score computation (0-100).

This module derives a single, deterministic 0-100 quality metric from a run's
tool results. The score is designed to be trackable over time, CI-gateable via
``--fail-under``, and shareable as a single number.

Formula
-------
Every issue is normalised to one of three severities (ERROR / WARNING / INFO)
via :meth:`lintro.parsers.base_issue.BaseIssue.get_severity` and weighted::

    weighted = (error_weight   * n_errors)
             + (warning_weight * n_warnings)
             + (info_weight    * n_info)

The weighted penalty is then mapped onto 0-100 with a smoothly saturating
function::

    raw_score = 100 * scale / (scale + weighted)
    score     = floor(raw_score)

Properties (all intentional and covered by tests):

* **Zero issues → exactly 100.** ``weighted == 0`` yields ``raw_score == 100``.
* **Any issue → strictly below 100.** ``weighted > 0`` makes ``raw_score < 100``
  and ``floor`` drops it to at most 99, so a clean run is unambiguous.
* **Monotonic.** Adding any issue (or raising an issue's severity) never
  increases the score.
* **Bounded.** The output is always within ``[0, 100]``.
* **Deterministic.** The result depends only on the severity counts, the
  configured weights, and the scale — no ordering, timing, or file-path input.

The score reaches 50 when the total weighted penalty equals ``scale``. With the
default weights that is, e.g., ten ERROR issues or ~33 WARNING issues.

Tiers
-----
* **great** — score >= 75
* **needs-work** — 50 <= score < 75
* **critical** — score < 50
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import auto
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from lintro.enums.severity_level import SeverityLevel
from lintro.enums.uppercase_str_enum import UppercaseStrEnum

if TYPE_CHECKING:
    from lintro.config.score_config import ScoreConfig

# Tier boundaries (inclusive lower bounds), documented in the module docstring.
GREAT_THRESHOLD: int = 75
NEEDS_WORK_THRESHOLD: int = 50

# Default weights and scale, mirrored by ``ScoreConfig`` so the formula behaves
# identically whether or not a config object is supplied.
DEFAULT_ERROR_WEIGHT: float = 10.0
DEFAULT_WARNING_WEIGHT: float = 3.0
DEFAULT_INFO_WEIGHT: float = 1.0
DEFAULT_SCALE: float = 100.0

# Score bounds.
MAX_SCORE: int = 100
MIN_SCORE: int = 0


class ScoreTier(UppercaseStrEnum):
    """Qualitative bucket a numeric health score falls into.

    Values are the human-facing tier labels used in output.
    """

    GREAT = auto()
    NEEDS_WORK = auto()
    CRITICAL = auto()

    @property
    def label(self) -> str:
        """Return the hyphenated, lower-case display label.

        Returns:
            str: Display label such as ``"needs-work"``.
        """
        return self.name.lower().replace("_", "-")


@dataclass(frozen=True)
class SeverityCounts:
    """Tally of issues by normalized severity.

    Attributes:
        errors: Number of ERROR-severity issues.
        warnings: Number of WARNING-severity issues.
        info: Number of INFO-severity issues.
    """

    errors: int = 0
    warnings: int = 0
    info: int = 0

    @property
    def total(self) -> int:
        """Return the total number of issues across all severities.

        Returns:
            int: Sum of errors, warnings, and info issues.
        """
        return self.errors + self.warnings + self.info


@dataclass(frozen=True)
class HealthScore:
    """Computed health score and its supporting breakdown.

    Attributes:
        score: Integer score in the inclusive range ``[0, 100]``.
        tier: Qualitative :class:`ScoreTier` for the score.
        counts: The severity counts the score was derived from.
        weighted_penalty: The severity-weighted penalty fed into the formula.
    """

    score: int
    tier: ScoreTier
    counts: SeverityCounts
    weighted_penalty: float

    def to_dict(self) -> dict[str, Any]:
        """Serialize the score to a JSON-safe dictionary.

        The shape is additive to the existing JSON summary schema and stable
        for downstream consumers (badges, dashboards).

        Returns:
            dict[str, Any]: Keys ``score``, ``tier``, ``severity_counts``
            (with ``error``/``warning``/``info``), and ``weighted_penalty``.
        """
        return {
            "score": self.score,
            "tier": self.tier.label,
            "severity_counts": {
                "error": self.counts.errors,
                "warning": self.counts.warnings,
                "info": self.counts.info,
            },
            "weighted_penalty": round(self.weighted_penalty, 4),
        }


def count_severities(tool_results: Sequence[object]) -> SeverityCounts:
    """Tally issue severities across all tool results.

    Iterates every issue on every result, normalising each to ERROR / WARNING /
    INFO via its ``get_severity()`` method. Results and issues without severity
    support are skipped gracefully.

    Args:
        tool_results: Sequence of ToolResult-like objects.

    Returns:
        SeverityCounts: Aggregated per-severity counts.
    """
    errors = 0
    warnings = 0
    info = 0
    for result in tool_results:
        issues = getattr(result, "issues", None)
        if not issues:
            continue
        for issue in issues:
            get_sev = getattr(issue, "get_severity", None)
            if not callable(get_sev):
                continue
            level = get_sev()
            if level == SeverityLevel.ERROR:
                errors += 1
            elif level == SeverityLevel.WARNING:
                warnings += 1
            elif level == SeverityLevel.INFO:
                info += 1
    return SeverityCounts(errors=errors, warnings=warnings, info=info)


def tier_for_score(score: int) -> ScoreTier:
    """Return the qualitative tier for a numeric score.

    Args:
        score: Integer health score in ``[0, 100]``.

    Returns:
        ScoreTier: ``GREAT`` (>=75), ``NEEDS_WORK`` (50-74), or ``CRITICAL``
        (<50).
    """
    if score >= GREAT_THRESHOLD:
        return ScoreTier.GREAT
    if score >= NEEDS_WORK_THRESHOLD:
        return ScoreTier.NEEDS_WORK
    return ScoreTier.CRITICAL


def compute_health_score(
    counts: SeverityCounts,
    *,
    error_weight: float = DEFAULT_ERROR_WEIGHT,
    warning_weight: float = DEFAULT_WARNING_WEIGHT,
    info_weight: float = DEFAULT_INFO_WEIGHT,
    scale: float = DEFAULT_SCALE,
) -> HealthScore:
    """Compute the 0-100 health score from severity counts.

    See the module docstring for the full formula and its properties.

    Args:
        counts: Per-severity issue counts.
        error_weight: Penalty weight per ERROR issue.
        warning_weight: Penalty weight per WARNING issue.
        info_weight: Penalty weight per INFO issue.
        scale: Smoothing constant (must be greater than zero).

    Returns:
        HealthScore: The score, tier, counts, and weighted penalty.

    Raises:
        ValueError: If ``scale`` is not greater than zero.
    """
    if scale <= 0:
        raise ValueError(f"scale must be greater than zero, got {scale}")

    weighted = (
        error_weight * counts.errors
        + warning_weight * counts.warnings
        + info_weight * counts.info
    )
    raw_score = MAX_SCORE * scale / (scale + weighted)
    score = int(math.floor(raw_score))
    # Guard against float edge cases; the formula already keeps this in range.
    score = max(MIN_SCORE, min(MAX_SCORE, score))
    return HealthScore(
        score=score,
        tier=tier_for_score(score),
        counts=counts,
        weighted_penalty=weighted,
    )


def compute_health_score_from_config(
    counts: SeverityCounts,
    config: ScoreConfig | None,
) -> HealthScore:
    """Compute the health score using a :class:`ScoreConfig` (or defaults).

    Args:
        counts: Per-severity issue counts.
        config: Score configuration; ``None`` uses the built-in defaults.

    Returns:
        HealthScore: The computed score.
    """
    if config is None:
        return compute_health_score(counts)
    return compute_health_score(
        counts,
        error_weight=config.error_weight,
        warning_weight=config.warning_weight,
        info_weight=config.info_weight,
        scale=config.scale,
    )


def health_score_for_results(
    tool_results: Sequence[object],
    config: ScoreConfig | None = None,
) -> HealthScore:
    """Compute the health score directly from tool results.

    Convenience wrapper that tallies severities and applies the configured
    weights in one call.

    Args:
        tool_results: Sequence of ToolResult-like objects.
        config: Optional score configuration; ``None`` uses defaults.

    Returns:
        HealthScore: The computed score.
    """
    counts = count_severities(tool_results)
    return compute_health_score_from_config(counts, config)


def shields_color_for_tier(tier: ScoreTier) -> str:
    """Return the shields.io color name for a qualitative score tier.

    Mapping follows the README badge convention: bright green for healthy
    projects, yellow for middling scores, and red for critical ones.

    Args:
        tier: Qualitative :class:`ScoreTier` for the numeric score.

    Returns:
        str: shields.io color token (``brightgreen``, ``yellow``, or ``red``).
    """
    if tier is ScoreTier.GREAT:
        return "brightgreen"
    if tier is ScoreTier.NEEDS_WORK:
        return "yellow"
    return "red"


def build_shields_badge_url(
    score: int,
    *,
    style: str | None = None,
) -> str:
    """Build a shields.io static badge URL for a health score.

    Args:
        score: Integer health score in ``[0, 100]`` (clamped if out of range).
        style: Optional shields.io style (e.g. ``flat``); omitted when ``None``.

    Returns:
        str: Absolute shields.io badge URL.
    """
    clamped = max(MIN_SCORE, min(MAX_SCORE, score))
    message = quote(f"{clamped}/100", safe="")
    color = shields_color_for_tier(tier_for_score(clamped))
    url = f"https://img.shields.io/badge/lintro-{message}-{color}"
    if style:
        url = f"{url}?style={quote(style, safe='')}"
    return url


def build_shields_badge_markdown(
    score: int,
    *,
    style: str | None = None,
    alt_text: str = "Lintro Score",
) -> str:
    """Build a markdown image snippet for a health-score shields.io badge.

    Args:
        score: Integer health score in ``[0, 100]``.
        style: Optional shields.io style forwarded to the URL builder.
        alt_text: Alt text for the markdown image.

    Returns:
        str: Markdown such as
        ``![Lintro Score](https://img.shields.io/badge/lintro-84%2F100-brightgreen)``.
    """
    url = build_shields_badge_url(score, style=style)
    return f"![{alt_text}]({url})"
