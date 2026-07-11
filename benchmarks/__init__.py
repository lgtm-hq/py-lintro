"""Comparative benchmark harness for lintro.

This package provides a reproducible, locally-runnable harness that measures
lintro's wall-clock performance against other meta-linters (MegaLinter,
pre-commit) and raw sequential native tool invocation.

The harness degrades gracefully: when optional competitor tools are not
installed it still benchmarks lintro and the always-available sequential-native
baseline, and records which competitors were skipped so results stay honest
about coverage.

See ``benchmarks/README.md`` for the full methodology and instructions on
running the complete comparison.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
