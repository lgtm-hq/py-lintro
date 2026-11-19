"""Per-invocation overrides of loaded configuration values.

A CLI flag that mirrors a config key has to reach code that reads the loaded
configuration directly, such as the version gate reading
``execution.strict_missing_tools`` while it runs inside a worker thread. The
override is applied to the loaded singleton for the length of one run and
restored afterwards, so an in-process caller (tests, ``lintro fmt , chk``
chaining, the MCP server) never inherits a previous command's flag.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from lintro.config.config_loader import get_config


@contextmanager
def strict_missing_tools_override(value: bool | None) -> Iterator[None]:
    """Apply ``--strict-missing-tools`` / ``--no-strict-missing-tools`` to one run.

    Args:
        value: The flag value, or None when neither flag was given, in which
            case configuration decides and nothing is changed.

    Yields:
        None: The run executes inside the override.
    """
    if value is None:
        yield
        return
    execution = get_config().execution
    previous = execution.strict_missing_tools
    execution.strict_missing_tools = value
    try:
        yield
    finally:
        execution.strict_missing_tools = previous
