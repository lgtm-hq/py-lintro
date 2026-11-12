"""Every ruff subprocess on the fix path runs in the execution cwd (#2349)."""

from __future__ import annotations

from collections.abc import Callable
from typing import cast
from unittest.mock import MagicMock

from assertpy import assert_that

from lintro.tools.ruff.fix import execute_ruff_fix

#: Project root the prepared context anchors ruff to. Deliberately not the
#: process cwd, so a call that drops ``cwd`` records ``None`` instead.
PROJECT_ROOT = "/repo/project-root"

#: One lint finding that only an unsafe fix would remove, so the fix pass
#: leaves it in place and the unsafe-fix probe runs.
UNSAFE_ONLY_FINDING = """[
    {
        "code": "F841",
        "message": "Local variable 'x' is assigned but never used",
        "filename": "pkg/test.py",
        "location": {"row": 1, "column": 1},
        "end_location": {"row": 1, "column": 5},
        "fix": {"applicability": "unsafe"}
    }
]"""


def _stage(cmd: list[str]) -> str:
    """Name the fix-path stage a ruff argv belongs to.

    Args:
        cmd: The ruff argv ``execute_ruff_fix`` asked to run.

    Returns:
        A short stage name.
    """
    if "format" in cmd:
        return "format-check" if "--check" in cmd else "format"
    if "--unsafe-fixes" in cmd:
        return "unsafe-probe"
    return "lint-fix" if "--fix" in cmd else "lint-check"


def test_every_fix_path_ruff_call_runs_in_the_execution_cwd(
    mock_ruff_tool: MagicMock,
    ruff_execution_context: Callable[..., MagicMock],
) -> None:
    """Lint check, format check, fix, unsafe probe and format share ``ctx.cwd``.

    Ruff resolves its default ``src`` -- and the ``Would reformat:`` paths it
    prints -- against its working directory, so a call that falls back to the
    process cwd classifies imports and reports paths differently from the rest.

    Args:
        mock_ruff_tool: Mock RuffTool instance for testing.
        ruff_execution_context: Factory for mock execution contexts.
    """
    mock_ruff_tool.options["format"] = True
    mock_ruff_tool.prepare.return_value = ruff_execution_context(
        files=[f"{PROJECT_ROOT}/pkg/test.py"],
        cwd=PROJECT_ROOT,
    )
    outputs = {
        "lint-check": (False, UNSAFE_ONLY_FINDING),
        "format-check": (False, "Would reformat: pkg/test.py\n"),
        "lint-fix": (False, UNSAFE_ONLY_FINDING),
        "unsafe-probe": (True, "[]"),
        "format": (True, ""),
    }
    calls: list[tuple[str, object]] = []

    def fake_run(**kwargs: object) -> tuple[bool, str]:
        """Record the stage and working directory of one ruff call.

        Args:
            **kwargs: Arguments ruff passed to ``_run_subprocess``.

        Returns:
            The canned result for that stage.
        """
        stage = _stage(list(cast("list[str]", kwargs["cmd"])))
        calls.append((stage, kwargs.get("cwd")))
        return outputs[stage]

    mock_ruff_tool._run_subprocess.side_effect = fake_run

    result = execute_ruff_fix(mock_ruff_tool, [PROJECT_ROOT])

    assert_that(calls).is_equal_to(
        [
            ("lint-check", PROJECT_ROOT),
            ("format-check", PROJECT_ROOT),
            ("lint-fix", PROJECT_ROOT),
            ("unsafe-probe", PROJECT_ROOT),
            ("format", PROJECT_ROOT),
        ],
    )
    # The format-check finding resolves against the same root ruff ran in.
    format_files = [
        issue.file
        for issue in result.initial_issues or []
        if getattr(issue, "code", "") != "F841"
    ]
    assert_that(format_files).is_equal_to([f"{PROJECT_ROOT}/pkg/test.py"])
