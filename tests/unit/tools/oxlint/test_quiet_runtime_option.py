"""Oxlint honours a ``quiet`` passed as a runtime option (#2400).

Before the fix the command read ``quiet`` from the stored options only, so a
per-run ``{"quiet": True}`` was dropped and warnings were still reported.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from assertpy import assert_that

from lintro.plugins.subprocess_executor import SubprocessResult
from lintro.tools.oxlint.definition import OxlintPlugin

_WARNING_REPORT = json.dumps(
    {
        "diagnostics": [
            {
                "message": "Variable 'unused' is declared but never used.",
                "code": "eslint(no-unused-vars)",
                "severity": "warning",
                "filename": "test.js",
                "labels": [{"span": {"line": 1, "column": 5}}],
            },
        ],
    },
)


def _fake_oxlint(cmd: list[str], **_kwargs: object) -> SubprocessResult:
    """Behave like oxlint: ``--quiet`` drops warning-severity diagnostics.

    Args:
        cmd: The command lintro built.
        **_kwargs: Subprocess keyword arguments (ignored).

    Returns:
        A clean report under ``--quiet``, otherwise one warning.
    """
    report = '{"diagnostics": []}' if "--quiet" in cmd else _WARNING_REPORT
    return SubprocessResult(0, report, "", report)


def test_runtime_quiet_suppresses_warnings(
    oxlint_plugin: OxlintPlugin,
    tmp_path: Path,
) -> None:
    """A runtime ``quiet`` option reaches the oxlint command line.

    Args:
        oxlint_plugin: The OxlintPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    test_file = tmp_path / "test.js"
    test_file.write_text("var unused = 1;\n")
    ctx = MagicMock()
    ctx.timeout = 30
    ctx.cwd = str(tmp_path)
    ctx.rel_files = ["test.js"]
    ctx.files = [str(test_file)]

    with (
        patch.object(oxlint_plugin, "prepare", return_value=ctx),
        patch.object(oxlint_plugin, "_get_executable_command", return_value=["oxlint"]),
        patch.object(oxlint_plugin, "_build_config_args", return_value=[]),
        patch.object(
            oxlint_plugin,
            "_run_subprocess_result",
            side_effect=_fake_oxlint,
        ),
    ):
        result = oxlint_plugin.check([str(test_file)], {"quiet": True})

    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.success).is_true()
