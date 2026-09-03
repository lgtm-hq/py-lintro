"""Cross-provider review agreement matrix harness (issue #2147).

This package is an offline eval harness. It lives in ``evals/`` and is
deliberately excluded from the shipped ``lintro`` distribution: it drives the
installed CLI as a subprocess and reuses ``lintro.ai.review`` only as a
read-only library for finding identity and verdict derivation.
"""

from __future__ import annotations

__all__ = ["__doc__"]
