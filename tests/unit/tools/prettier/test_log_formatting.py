"""Prettier log records interpolate their values under loguru (#2400).

loguru formats with ``str.format`` braces, so a ``%s`` placeholder is logged
verbatim and the value passed with it is dropped.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that
from loguru import logger

from lintro.plugins.subprocess_executor import SubprocessResult
from lintro.tools.prettier.definition import PrettierPlugin


@pytest.fixture
def debug_messages() -> Generator[list[str], None, None]:
    """Capture loguru messages logged at DEBUG or above.

    Yields:
        list[str]: The formatted messages.
    """
    messages: list[str] = []
    sink_id = logger.add(
        lambda message: messages.append(str(message).strip()),
        level="DEBUG",
        format="{message}",
    )
    try:
        yield messages
    finally:
        logger.remove(sink_id)


def test_print_width_log_names_the_width(
    prettier_plugin: PrettierPlugin,
    tmp_path: Path,
    debug_messages: list[str],
) -> None:
    """The fallback --print-width log carries the configured line length.

    Args:
        prettier_plugin: The PrettierPlugin instance to test.
        tmp_path: Temporary directory path for test files.
        debug_messages: Captured DEBUG log messages.
    """
    source = tmp_path / "app.css"
    source.write_text("a { color: red; }\n")
    prettier_plugin.set_options(line_length=97)
    clean = SubprocessResult(returncode=0, stdout="", stderr="", output="")

    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch.object(prettier_plugin, "_build_config_args", return_value=[]),
        patch.object(prettier_plugin, "_find_prettier_config", return_value=None),
        patch.object(
            prettier_plugin,
            "_get_executable_command",
            return_value=["prettier"],
        ),
        patch.object(prettier_plugin, "_run_subprocess_result", return_value=clean),
    ):
        prettier_plugin.check([str(source)], {})

    assert_that(debug_messages).contains(
        "[PrettierPlugin] Using --print-width=97 from options",
    )
