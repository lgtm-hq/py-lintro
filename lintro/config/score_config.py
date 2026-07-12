"""Health score configuration model.

Defines the tunable inputs to the 0-100 health score: the per-severity
weights and the smoothing scale. See :mod:`lintro.utils.health_score` for the
formula that consumes these values.
"""

from pydantic import BaseModel, ConfigDict, Field


class ScoreConfig(BaseModel):
    """Tunable weights and scale for the health score formula.

    The health score aggregates issue counts into a single 0-100 metric using
    a severity-weighted, smoothly saturating penalty. These fields control how
    harshly each severity is penalised and how quickly the score drops.

    Attributes:
        model_config: Pydantic model configuration.
        error_weight: Penalty weight applied to each ERROR-severity issue.
        warning_weight: Penalty weight applied to each WARNING-severity issue.
        info_weight: Penalty weight applied to each INFO-severity issue.
        scale: Smoothing constant. Larger values make the score decay more
            slowly; the score reaches 50 when the total weighted penalty
            equals ``scale``. Must be greater than zero.
    """

    model_config = ConfigDict(frozen=False, extra="forbid")

    error_weight: float = Field(default=10.0, ge=0.0)
    warning_weight: float = Field(default=3.0, ge=0.0)
    info_weight: float = Field(default=1.0, ge=0.0)
    scale: float = Field(default=100.0, gt=0.0)
