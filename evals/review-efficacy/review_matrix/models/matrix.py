"""Models describing the committed eval matrix specification."""

from __future__ import annotations

from dataclasses import dataclass, field

from lintro.ai.config_overrides import (
    ENV_ENABLED,
    ENV_MAX_COST_USD,
    ENV_MODEL,
    ENV_PROVIDER,
    ENV_REVIEW,
    ENV_TRANSPORT,
)

__all__ = ["MatrixConfig", "MatrixSpec"]


@dataclass(frozen=True, slots=True)
class MatrixConfig:
    """One (provider, model, transport) cell of the eval matrix.

    Attributes:
        config_id: Stable identifier used in run directories and reports.
        provider: Value for ``LINTRO_AI_PROVIDER``.
        model: Value for ``LINTRO_AI_MODEL``.
        transport: Value for ``LINTRO_AI_TRANSPORT`` (``api`` or ``cli``).
        max_cost_usd: Per-run ceiling handed to ``LINTRO_AI_MAX_COST_USD``.
            Every invocation of this config is capped at this value.
        projected_cost_usd: Expected spend of a single run, used only by the
            pre-flight spend estimate. Defaults to ``max_cost_usd`` when the
            matrix file omits it, so the estimate is never optimistic by
            accident.
    """

    config_id: str
    provider: str
    model: str
    transport: str
    max_cost_usd: float
    projected_cost_usd: float

    @property
    def env_overrides(self) -> dict[str, str]:
        """Return the ``LINTRO_AI_*`` overlay that pins this config.

        Configuration is driven exclusively through the documented env
        overrides (:mod:`lintro.ai.config_overrides`), so the harness never
        needs provider wiring of its own.

        The master switches are part of the overlay because a cell must be
        self-contained: this repository commits ``ai.enabled: false``, and
        :attr:`lintro.ai.config.AIConfig.review_enabled` requires both
        ``enabled`` and ``review``, so a run driven only by the provider
        triplet would exit at the review gate. The invoker strips every
        ambient ``LINTRO_AI_*`` variable before applying this overlay, so
        these two are the only thing that can turn the review on.

        Returns:
            Mapping of environment variable name to value.
        """
        return {
            ENV_ENABLED: "1",
            ENV_REVIEW: "1",
            ENV_PROVIDER: self.provider,
            ENV_MODEL: self.model,
            ENV_TRANSPORT: self.transport,
            ENV_MAX_COST_USD: f"{self.max_cost_usd:g}",
        }


@dataclass(frozen=True, slots=True)
class MatrixSpec:
    """A whole committed matrix file.

    Attributes:
        version: Schema version of the matrix file.
        repeats: Number of repeated runs per (config, corpus item). Two is the
            minimum that measures anything; the noise floor is only meaningful
            at three or more.
        depth: ``lintro review --depth`` value shared by every config, so
            depth never confounds a cross-config comparison.
        timeout_seconds: ``lintro review --timeout`` value shared by every
            config.
        configs: The matrix cells, in file order.
    """

    version: int
    repeats: int
    depth: int
    timeout_seconds: float
    configs: tuple[MatrixConfig, ...] = field(default_factory=tuple)
