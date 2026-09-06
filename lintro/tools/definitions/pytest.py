"""Re-export shim for the pytest tool definition (#2311).

The pytest plugin now lives in its own package, :mod:`lintro.tools.pytest`,
next to the command builder, executor and output processing modules it uses.
Plugin discovery still scans this package, so importing this module registers
the tool. Deleted once discovery moves to the per-tool packages.
"""

from lintro.tools.pytest.definition import (
    PYTEST_DEFAULT_PRIORITY,
    PYTEST_DEFAULT_TIMEOUT,
    PYTEST_FILE_PATTERNS,
    PytestPlugin,
)

__all__ = [
    "PYTEST_DEFAULT_PRIORITY",
    "PYTEST_DEFAULT_TIMEOUT",
    "PYTEST_FILE_PATTERNS",
    "PytestPlugin",
]
