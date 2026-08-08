"""AI-specific enumerations for transports and sanitization modes.

``ConfidenceLevel`` and ``RiskLevel`` now live in :mod:`lintro.enums` because
they are consumed by the core SARIF renderer. They are re-exported here for
backwards compatibility with existing ``lintro.ai.enums`` importers.
"""

from lintro.ai.enums.ai_transport import AITransport
from lintro.ai.enums.cli_bare_mode import CliBareMode
from lintro.ai.enums.cost_basis import CostBasis
from lintro.ai.enums.sanitize_mode import SanitizeMode
from lintro.enums.confidence_level import ConfidenceLevel
from lintro.enums.risk_level import RiskLevel

__all__ = [
    "AITransport",
    "CliBareMode",
    "ConfidenceLevel",
    "CostBasis",
    "RiskLevel",
    "SanitizeMode",
]
