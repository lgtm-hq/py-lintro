"""Python formatting ownership follows executable capability evidence."""

from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.enums.action import Action
from lintro.models.core.tool_snapshot import ToolSnapshot
from lintro.tools.ruff.definition import RuffPlugin
from lintro.utils.execution.tool_configuration import configure_tool_for_execution
from lintro.utils.python_formatting import resolve_python_formatting
from lintro.utils.unified_config import UnifiedConfigManager


@pytest.mark.parametrize("action", [Action.CHECK, Action.FIX])
@pytest.mark.parametrize(
    ("evidence", "override", "enabled"),
    [
        (ToolSnapshot(name="black"), None, True),
        (ToolSnapshot(name="black", available=True), None, True),
        (
            ToolSnapshot(name="black", available=True, version_check_passed=True),
            None,
            False,
        ),
        (
            ToolSnapshot(
                name="black",
                available=True,
                binary_path="/tools/black",
                version_check_passed=True,
            ),
            None,
            False,
        ),
        (
            ToolSnapshot(name="black", available=True, version_check_passed=True),
            True,
            True,
        ),
        (ToolSnapshot(name="black"), False, False),
    ],
    ids=["missing", "too-old", "module-invocation", "runnable", "opt-in", "opt-out"],
)
def test_black_capability_controls_ruff_formatting(
    action: Action,
    evidence: ToolSnapshot,
    override: bool | None,
    enabled: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only runnable Black demotes Ruff, preserving explicit user choices.

    Args:
        action: Check or format action.
        evidence: Capability evidence for Black.
        override: Explicit Ruff option, if supplied.
        enabled: Expected effective Ruff formatting state.
        tmp_path: Isolated project directory.
        monkeypatch: Working-directory isolation fixture.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[tool.lintro]\n")
    option = "format" if action == Action.FIX else "format_check"
    options: dict[str, object] = {} if override is None else {option: override}
    with patch(
        "lintro.utils.tool_snapshots.get_tool_snapshot",
        return_value=evidence,
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
        notice = resolve_python_formatting(
            selected,
            action=Action.CHECK,
            ruff_options=ruff_options,
        )
    if expected is None:
        assert_that(notice).is_none()
    else:
        assert_that(notice.text if notice else "").contains(expected)


def test_notice_preserves_a_probe_failure_diagnostic() -> None:
    """Reporting must not crash again after execution shaped a probe failure."""
    with patch(
        "lintro.utils.tool_snapshots.get_tool_snapshot",
        side_effect=RuntimeError("internal probe crash"),
    ):
        notice = resolve_python_formatting(
            {"ruff", "black"},
            action=Action.CHECK,
            ruff_options={},
        )
    assert_that(notice.text if notice else "").contains(
        "could not be confirmed",
        "capability probe failed",
    )


@pytest.mark.parametrize(
    "module_invocation",
    [False, True],
    ids=["allowed-lag", "module"],
)
def test_format_ownership_and_execution_gate_agree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    module_invocation: bool,
) -> None:
    """Module commands and explicitly allowed old versions retain Black ownership.

    Args:
        tmp_path: Isolated project.
        monkeypatch: Environment and probe controls.
        module_invocation: Exercise a module command rather than allowed version lag.
    """
    import sys

    from lintro.plugins.execution_preparation import verify_tool_version
    from lintro.plugins.protocol import ToolDefinition

    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[tool.lintro]\n")
    monkeypatch.setenv("LINTRO_ALLOW_VERSION_LAG", "black")
    evidence = ToolSnapshot(
        name="black",
        available=True,
        version="1.0",
        min_version="2.0",
        version_check_passed=module_invocation,
        command=(sys.executable, "-m", "black") if module_invocation else ("black",),
    )
    with patch("lintro.utils.tool_snapshots.get_tool_snapshot", return_value=evidence):
        gate = verify_tool_version(
            ToolDefinition(name="black", description="formatter"),
        )
        tool = configure_tool_for_execution(
            tool=RuffPlugin(),
            tool_name="ruff",
            config_manager=UnifiedConfigManager(),
            tool_option_dict={},
            exclude=None,
            include_venv=False,
            incremental=False,
            action=Action.CHECK,
            selected_tools={"ruff", "black"},
        )
        notice = resolve_python_formatting(
            {"ruff", "black"},
            action=Action.CHECK,
            ruff_options={},
        )
    assert_that(gate).is_none()
    assert_that(tool.options["format_check"]).is_false()
    assert_that(notice.text if notice else "").contains("black owns Python formatting")
    if not module_invocation:
        assert_that(notice.text if notice else "").contains("version lag allowed")


@pytest.mark.parametrize(
    "failure",
    [RuntimeError("probe crashed"), OSError("probe I/O")],
)
def test_unconfirmed_black_probe_keeps_ruff_formatting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
) -> None:
    """Probe-service failures must not turn Ruff into a failed or disabled formatter.

    Args:
        tmp_path: Isolated project.
        monkeypatch: Working-directory isolation.
        failure: Probe failure that prevents confirmation.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[tool.lintro]\n")
    with patch("lintro.utils.tool_snapshots.get_tool_snapshot", side_effect=failure):
        tool = configure_tool_for_execution(
            tool=RuffPlugin(),
            tool_name="ruff",
            config_manager=UnifiedConfigManager(),
            tool_option_dict={},
            exclude=None,
            include_venv=False,
            incremental=False,
            action=Action.FIX,
            selected_tools={"ruff", "black"},
        )
        notice = resolve_python_formatting(
            {"ruff", "black"},
            action=Action.FIX,
            ruff_options={},
        )
    assert_that(tool.options["format"]).is_true()
    assert_that(notice.text if notice else "").contains("owner could not be confirmed")


@pytest.mark.parametrize(
    ("black", "options", "reason"),
    [
        (
            ToolSnapshot(name="black", probe_error="not installed"),
            {},
            "black: not installed",
        ),
        (
            ToolSnapshot(
                name="black",
                available=True,
                version="1.0",
                min_version="2.0",
            ),
            {},
            "below minimum 2.0, found 1.0",
        ),
        (
            ToolSnapshot(name="black"),
            {"format_check": False},
            "ruff format_check disabled",
        ),
    ],
    ids=["missing", "below-minimum", "disabled"],
)
def test_notice_reports_the_actionable_reason(
    monkeypatch: pytest.MonkeyPatch,
    black: ToolSnapshot,
    options: dict[str, object],
    reason: str,
) -> None:
    """A fallback or absence names the actual problem the user can address.

    Args:
        monkeypatch: Environment isolation fixture.
        black: Capability failure for Black.
        options: Explicit Ruff configuration.
        reason: Required actionable explanation.
    """
    monkeypatch.delenv("LINTRO_ALLOW_VERSION_LAG", raising=False)
    ruff = ToolSnapshot(name="ruff", available=True, version_check_passed=True)
    with patch(
        "lintro.utils.tool_snapshots.get_tool_snapshot",
        side_effect=[black, ruff],
    ):
        notice = resolve_python_formatting(
            {"ruff", "black"},
            action=Action.CHECK,
            ruff_options=options,
        )
    assert_that(notice.text if notice else "").contains(reason).does_not_contain(
        "unavailable or",
    )


def test_notice_does_not_hide_programming_errors() -> None:
    """An invalid snapshot contract is not an expected probe-service failure."""
    with patch(
        "lintro.utils.tool_snapshots.get_tool_snapshot",
        side_effect=ValueError("invalid evidence"),
    ):
        with pytest.raises(ValueError, match="invalid evidence"):
            resolve_python_formatting(
                {"ruff", "black"},
                action=Action.CHECK,
                ruff_options={},
            )
