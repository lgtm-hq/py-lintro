"""Python formatting ownership follows executable capability evidence."""

from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.enums.action import Action
from lintro.models.core.tool_snapshot import ToolSnapshot
from lintro.tools.ruff.definition import RuffPlugin
from lintro.utils.execution.tool_configuration import configure_tool_for_execution
from lintro.utils.python_formatting import python_formatting_notice
from lintro.utils.unified_config import UnifiedConfigManager


@pytest.mark.parametrize("action", [Action.CHECK, Action.FIX])
@pytest.mark.parametrize(
    ("available", "binary_path", "version_passed", "override", "enabled"),
    [
        (False, "", False, None, True),
        (True, "/tools/black", False, None, True),
        (True, "", True, None, True),
        (True, "/tools/black", True, None, False),
        (True, "/tools/black", True, True, True),
        (False, "", False, False, False),
    ],
    ids=["missing", "too-old", "unresolved", "runnable", "opt-in", "opt-out"],
)
def test_black_capability_controls_ruff_formatting(
    action: Action,
    available: bool,
    binary_path: str,
    version_passed: bool,
    override: bool | None,
    enabled: bool,
) -> None:
    """Only runnable Black demotes Ruff, preserving explicit user choices.

    Args:
        action: Check or format action.
        available: Whether Black's probe succeeded.
        binary_path: Resolved Black executable.
        version_passed: Whether Black meets its minimum version.
        override: Explicit Ruff option, if supplied.
        enabled: Expected effective Ruff formatting state.
    """
    option = "format" if action == Action.FIX else "format_check"
    options: dict[str, object] = {} if override is None else {option: override}
    with patch(
        "lintro.utils.tool_snapshots.get_tool_snapshot",
        return_value=ToolSnapshot(
            name="black",
            available=available,
            binary_path=binary_path,
            version_check_passed=version_passed,
        ),
    ):
        tool = configure_tool_for_execution(
            tool=RuffPlugin(),
            tool_name="ruff",
            config_manager=UnifiedConfigManager(),
            tool_option_dict={"ruff": options},
            exclude=None,
            include_venv=False,
            incremental=False,
            action=action,
            selected_tools={"ruff", "black"},
        )
    assert_that(tool.options.get(option, True)).is_equal_to(enabled)


@pytest.mark.parametrize(
    ("selected", "ruff_options", "expected"),
    [
        ({"mypy"}, {}, None),
        ({"black", "ruff"}, {}, "no runnable owner"),
        ({"ruff"}, {"format_check": False}, None),
    ],
)
def test_notice_does_not_invent_an_owner(
    selected: set[str],
    ruff_options: dict[str, object],
    expected: str | None,
) -> None:
    """Absent or disabled formatters cannot be reported as the owner.

    Args:
        selected: Selected tools.
        ruff_options: Explicit Ruff configuration.
        expected: Expected notice fragment, or no notice.
    """
    with patch(
        "lintro.utils.tool_snapshots.get_tool_snapshot",
        return_value=ToolSnapshot(name="unavailable"),
    ):
        notice = python_formatting_notice(
            selected,
            action=Action.CHECK,
            ruff_options=ruff_options,
        )
    if expected is None:
        assert_that(notice).is_none()
    else:
        assert_that(notice).contains(expected)


def test_notice_preserves_a_probe_failure_diagnostic() -> None:
    """Reporting must not crash again after execution shaped a probe failure."""
    with patch(
        "lintro.utils.tool_snapshots.get_tool_snapshot",
        side_effect=RuntimeError("internal probe crash"),
    ):
        notice = python_formatting_notice(
            {"ruff", "black"},
            action=Action.CHECK,
            ruff_options={},
        )
    assert_that(notice).contains("could not be confirmed", "capability probe failed")
