"""Shared process exit codes for the build-time artifact generators.

The contract is pinned by CI consumers and tests: ``0`` when outputs are in
sync (or were written), ``1`` when ``--check`` detects drift, ``2`` when an
input is missing, malformed, or inconsistent.
"""

from __future__ import annotations

EXIT_OK = 0
EXIT_DRIFT = 1
EXIT_INPUT_ERROR = 2
