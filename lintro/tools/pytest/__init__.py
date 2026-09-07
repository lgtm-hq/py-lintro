"""Pytest tool package.

Everything the ``pytest`` tool owns lives here: the plugin and its
:class:`~lintro.plugins.protocol.ToolDefinition` in
:mod:`lintro.tools.pytest.definition`, and the command builder, executor,
output processing and analytics modules it delegates to.
Plugin discovery enters the package through that module (#2311).
"""

from lintro.tools.pytest.definition import PytestPlugin
from lintro.tools.pytest.pytest_command_builder import (
    build_base_command,
    build_check_command,
)
from lintro.tools.pytest.pytest_executor import PytestExecutor

__all__ = [
    "PytestExecutor",
    "PytestPlugin",
    "build_base_command",
    "build_check_command",
]
