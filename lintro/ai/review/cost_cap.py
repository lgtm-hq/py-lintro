"""ConfigSource × cost_basis enforcement for ``max_cost_usd`` (#2154).

Flag and env overlays are operator intent for this run and enforce on
every cost basis, including subscription CLI. Committed YAML is repo
policy and is transport-unaware: it enforces when real money is at
stake (``billed`` or ``estimated``) and is display-only on
``unpriceable``. Unset stays uncapped. Shadow pricing (``~$``) is
unchanged — measurement is not enforcement.
"""

from __future__ import annotations

from lintro.ai.enums.config_source import ConfigSource
from lintro.ai.enums.cost_basis import CostBasis

__all__ = ["cap_is_enforced"]


def cap_is_enforced(*, source: ConfigSource, basis: CostBasis) -> bool:
    """Return whether a resolved dollar cap must stop the run.

    Args:
        source: Where ``max_cost_usd`` was set.
        basis: How the run's spend is measured.

    Returns:
        True when the orchestrator must treat the cap as a hard stop.
    """
    if source in (ConfigSource.FLAG, ConfigSource.ENV):
        return True
    if source is ConfigSource.CONFIG:
        return basis in (CostBasis.BILLED, CostBasis.ESTIMATED)
    return False
