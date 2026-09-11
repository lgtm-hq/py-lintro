"""Tests for PytestPlugin check method."""

from __future__ import annotations

import subprocess  # nosec B404 - only referenced to build a TimeoutExpired object
from collections.abc import Callable, Generator
from contextlib import contextmanager
from typing import TYPE_CHECKING
from unittest.mock import patch

from assertpy import assert_that

from lintro.enums.pytest_enums import PytestSpecialMode
from lintro.parsers.pytest.pytest_issue import PytestIssue

if TYPE_CHECKING:
    from lintro.tools.pytest.definition import PytestPlugin


# =============================================================================
# Tests for PytestPlugin check method with mocked subprocess
# =============================================================================


def test_check_success_with_mocked_subprocess(
    sample_pytest_plugin: PytestPlugin,
) -> None:
    """Check succeeds with mocked subprocess returning success.

    Args:
        sample_pytest_plugin: The PytestPlugin instance to test.
    """
    with (
        patch.object(
            sample_pytest_plugin,
            "_verify_tool_version",
            return_value=None,
        ),
        patch.object(
            sample_pytest_plugin,
            "_run_subprocess",
            return_value=(True, "10 passed in 0.12s"),
        ),
        patch.object(sample_pytest_plugin, "_parse_output", return_value=[]),
        patch.object(
            sample_pytest_plugin.executor,
            "prepare_test_execution",
            return_value=10,
        ),
        patch.object(
            sample_pytest_plugin.executor,
            "execute_tests",
        ) as mock_execute,
    ):
        mock_execute.return_value = (True, "10 passed in 0.12s", 0)

        result = sample_pytest_plugin.check(["tests"], {})

        assert_that(result.success).is_true()
        assert_that(result.name).is_equal_to("pytest")


def test_check_failure_with_mocked_subprocess(
    sample_pytest_plugin: PytestPlugin,
    sample_pytest_issues: list[PytestIssue],
) -> None:
    """Check fails with mocked subprocess returning failure.

    Args:
        sample_pytest_plugin: The PytestPlugin instance to test.
        sample_pytest_issues: List of sample PytestIssue objects.
    """
    failed_issues = [
        i for i in sample_pytest_issues if i.test_status in ("FAILED", "ERROR")
    ]

    with (
        patch.object(
            sample_pytest_plugin,
            "_verify_tool_version",
            return_value=None,
        ),
        patch.object(
            sample_pytest_plugin,
            "_run_subprocess",
            return_value=(False, "2 failed, 8 passed in 0.15s"),
        ),
        patch.object(
            sample_pytest_plugin,
            "_parse_output",
            return_value=failed_issues,
        ),
        patch.object(
            sample_pytest_plugin.executor,
            "prepare_test_execution",
            return_value=10,
        ),
        patch.object(
            sample_pytest_plugin.executor,
            "execute_tests",
        ) as mock_execute,
    ):
        mock_execute.return_value = (False, "2 failed, 8 passed in 0.15s", 1)

        result = sample_pytest_plugin.check(["tests"], {})

        assert_that(result.success).is_false()
        assert_that(result.issues_count).is_greater_than(0)


def test_check_handles_executor_not_initialized(
    sample_pytest_plugin: PytestPlugin,
) -> None:
    """Check handles case when executor is None.

    Args:
        sample_pytest_plugin: The PytestPlugin instance to test.
    """
    sample_pytest_plugin.executor = None

    with patch.object(
        sample_pytest_plugin,
        "_verify_tool_version",
        return_value=None,
    ):
        result = sample_pytest_plugin.check(["tests"], {})

        assert_that(result.success).is_false()
        assert_that(result.output).contains("not initialized")


def test_check_handles_result_processor_not_initialized(
    sample_pytest_plugin: PytestPlugin,
) -> None:
    """Check handles case when result_processor is None.

    Args:
        sample_pytest_plugin: The PytestPlugin instance to test.
    """
    sample_pytest_plugin.result_processor = None

    with (
        patch.object(
            sample_pytest_plugin,
            "_verify_tool_version",
            return_value=None,
        ),
        patch.object(
            sample_pytest_plugin.executor,
            "prepare_test_execution",
            return_value=10,
        ),
        patch.object(
            sample_pytest_plugin.executor,
            "execute_tests",
            return_value=(True, "10 passed", 0),
        ),
        patch.object(sample_pytest_plugin, "_parse_output", return_value=[]),
    ):
        result = sample_pytest_plugin.check(["tests"], {})

        assert_that(result.success).is_false()
        assert_that(result.output).contains("not initialized")


# =============================================================================
# Tests for pytest test collection mode
# =============================================================================


def test_collect_only_mode_enabled(
    sample_pytest_plugin: PytestPlugin,
) -> None:
    """Collect only mode is enabled correctly.

    Args:
        sample_pytest_plugin: The PytestPlugin instance to test.
    """
    sample_pytest_plugin.set_options(collect_only=True)
    assert_that(sample_pytest_plugin.pytest_config.collect_only).is_true()
    assert_that(sample_pytest_plugin.pytest_config.is_special_mode()).is_true()


def test_collect_only_returns_special_mode(
    sample_pytest_plugin: PytestPlugin,
) -> None:
    """Collect only returns correct special mode name.

    Args:
        sample_pytest_plugin: The PytestPlugin instance to test.
    """
    sample_pytest_plugin.set_options(collect_only=True)
    mode = sample_pytest_plugin.pytest_config.get_special_mode()
    assert_that(mode).is_equal_to(PytestSpecialMode.COLLECT_ONLY.value)


# =============================================================================
# Tests for per-invocation option overrides (#2393)
# =============================================================================


def test_check_options_override_reaches_the_pytest_argv(
    sample_pytest_plugin: PytestPlugin,
) -> None:
    """A per-invocation option override reaches the built pytest argv.

    Args:
        sample_pytest_plugin: The PytestPlugin instance to test.
    """
    executed: list[list[str]] = []

    def record_command(
        cmd: list[str],
        timeout: int | float | None = None,
    ) -> tuple[bool, str, int]:
        """Record the argv pytest would be launched with.

        Args:
            cmd: Command line built for the pytest subprocess.
            timeout: Seconds allowed before the subprocess is killed.

        Returns:
            tuple[bool, str, int]: A successful, empty execution result.
        """
        executed.append(cmd)
        return True, "10 passed", 0

    with (
        patch.object(
            sample_pytest_plugin,
            "_verify_tool_version",
            return_value=None,
        ),
        patch.object(
            sample_pytest_plugin,
            "_get_executable_command",
            return_value=["pytest"],
        ),
        patch.object(
            sample_pytest_plugin.executor,
            "prepare_test_execution",
            return_value=10,
        ),
        patch.object(sample_pytest_plugin, "_parse_output", return_value=[]),
        patch.object(
            sample_pytest_plugin.executor,
            "execute_tests",
            new=record_command,
        ),
    ):
        sample_pytest_plugin.check(["tests"], {"maxfail": 3})

    cmd = executed[0]
    assert_that(cmd).contains("--maxfail")
    assert_that(cmd[cmd.index("--maxfail") + 1]).is_equal_to("3")


def test_check_options_override_does_not_mutate_persisted_options(
    sample_pytest_plugin: PytestPlugin,
) -> None:
    """A per-invocation override leaves the plugin's persisted options alone.

    Args:
        sample_pytest_plugin: The PytestPlugin instance to test.
    """
    options_before = dict(sample_pytest_plugin.options)

    with (
        patch.object(
            sample_pytest_plugin,
            "_verify_tool_version",
            return_value=None,
        ),
        patch.object(
            sample_pytest_plugin,
            "_get_executable_command",
            return_value=["pytest"],
        ),
        patch.object(
            sample_pytest_plugin.executor,
            "prepare_test_execution",
            return_value=10,
        ),
        patch.object(sample_pytest_plugin, "_parse_output", return_value=[]),
        patch.object(
            sample_pytest_plugin.executor,
            "execute_tests",
            return_value=(True, "10 passed", 0),
        ),
    ):
        sample_pytest_plugin.check(["tests"], {"maxfail": 3})

    assert_that(sample_pytest_plugin.options).is_equal_to(options_before)
    assert_that(sample_pytest_plugin.options.get("maxfail")).is_none()


def test_build_check_command_defaults_to_the_persisted_plugin_options(
    sample_pytest_plugin: PytestPlugin,
) -> None:
    """Omitting the options argument keeps reading the plugin's own options.

    Args:
        sample_pytest_plugin: The PytestPlugin instance to test.
    """
    from lintro.tools.pytest.pytest_command_builder import build_check_command

    sample_pytest_plugin.options["maxfail"] = 7

    with patch.object(
        sample_pytest_plugin,
        "_get_executable_command",
        return_value=["pytest"],
    ):
        cmd, _ = build_check_command(sample_pytest_plugin, ["tests"])

    assert_that(cmd).contains("--maxfail")
    assert_that(cmd[cmd.index("--maxfail") + 1]).is_equal_to("7")


def test_check_enforces_the_overridden_timeout_on_the_subprocess(
    sample_pytest_plugin: PytestPlugin,
) -> None:
    """The subprocess is killed at the per-invocation timeout override.

    Args:
        sample_pytest_plugin: The PytestPlugin instance to test.
    """
    enforced: list[float | int | None] = []

    def record_timeout(
        cmd: list[str],
        timeout: int | float | None = None,
    ) -> tuple[bool, str]:
        """Record the timeout the subprocess would be run under.

        Args:
            cmd: Command line built for the pytest subprocess.
            timeout: Seconds allowed before the subprocess is killed.

        Returns:
            tuple[bool, str]: A successful, empty subprocess result.
        """
        enforced.append(timeout)
        return True, "10 passed"

    with (
        patch.object(
            sample_pytest_plugin,
            "_verify_tool_version",
            return_value=None,
        ),
        patch.object(
            sample_pytest_plugin,
            "_get_executable_command",
            return_value=["pytest"],
        ),
        patch.object(
            sample_pytest_plugin.executor,
            "prepare_test_execution",
            return_value=10,
        ),
        patch.object(sample_pytest_plugin, "_parse_output", return_value=[]),
        patch.object(
            sample_pytest_plugin,
            "_run_subprocess",
            new=record_timeout,
        ),
    ):
        sample_pytest_plugin.check(["tests"], {"timeout": 600})

    assert_that(enforced).is_equal_to([600])


def test_check_timeout_message_uses_the_overridden_timeout(
    sample_pytest_plugin: PytestPlugin,
) -> None:
    """The timeout error reports the per-invocation timeout override.

    Args:
        sample_pytest_plugin: The PytestPlugin instance to test.
    """
    with (
        patch.object(
            sample_pytest_plugin,
            "_verify_tool_version",
            return_value=None,
        ),
        patch.object(
            sample_pytest_plugin,
            "_get_executable_command",
            return_value=["pytest"],
        ),
        patch.object(
            sample_pytest_plugin.executor,
            "prepare_test_execution",
            return_value=10,
        ),
        patch.object(
            sample_pytest_plugin.executor,
            "execute_tests",
            side_effect=subprocess.TimeoutExpired(cmd="pytest", timeout=600),
        ),
    ):
        result = sample_pytest_plugin.check(["tests"], {"timeout": 600})

    assert_that(result.timed_out).is_true()
    assert_that(result.output).contains("600")


def _record_subprocess_timeout(
    sink: list[int | float | None],
) -> Callable[..., tuple[bool, str]]:
    """Build a ``_run_subprocess`` stand-in that records its timeout.

    Args:
        sink: List the recorded timeout is appended to.

    Returns:
        Callable[..., tuple[bool, str]]: Replacement for ``_run_subprocess``.
    """

    def run(
        cmd: list[str],
        timeout: int | float | None = None,
        *args: object,
        **kwargs: object,
    ) -> tuple[bool, str]:
        """Record the timeout and report a clean run.

        Args:
            cmd: Command line built for the pytest subprocess.
            timeout: Seconds allowed before the subprocess is killed.
            *args: Ignored positional arguments.
            **kwargs: Ignored keyword arguments.

        Returns:
            tuple[bool, str]: A successful, empty subprocess result.
        """
        sink.append(timeout)
        return True, "10 passed"

    return run


@contextmanager
def _configured_check(plugin: PytestPlugin) -> Generator[None]:
    """Patch away everything ``check`` needs beyond the subprocess itself.

    Args:
        plugin: PytestPlugin instance to patch.

    Yields:
        None: Context in which ``check`` runs without touching the filesystem.
    """
    with (
        patch.object(plugin, "_verify_tool_version", return_value=None),
        patch.object(plugin, "_get_executable_command", return_value=["pytest"]),
        patch.object(plugin.executor, "prepare_test_execution", return_value=10),
        patch.object(plugin, "_parse_output", return_value=[]),
    ):
        yield


def test_timeout_configured_through_set_options_reaches_the_subprocess(
    sample_pytest_plugin: PytestPlugin,
) -> None:
    """A timeout configured the way the CLI configures it is enforced.

    ``BaseToolPlugin.set_options`` stores the value as ``600.0``, so this is
    the wiring a configured ``timeout`` actually travels through.

    Args:
        sample_pytest_plugin: The PytestPlugin instance to test.
    """
    sample_pytest_plugin.set_options(timeout=600)
    enforced: list[int | float | None] = []

    with (
        _configured_check(sample_pytest_plugin),
        patch.object(
            sample_pytest_plugin,
            "_run_subprocess",
            new=_record_subprocess_timeout(enforced),
        ),
    ):
        sample_pytest_plugin.check(["tests"], {})

    assert_that(sample_pytest_plugin.options["timeout"]).is_equal_to(600.0)
    assert_that(enforced).is_equal_to([600])


def test_timeout_configured_through_set_options_names_itself_on_timeout(
    sample_pytest_plugin: PytestPlugin,
) -> None:
    """The timeout message names the configured value, not the default.

    Args:
        sample_pytest_plugin: The PytestPlugin instance to test.
    """
    sample_pytest_plugin.set_options(timeout=600)

    with (
        _configured_check(sample_pytest_plugin),
        patch.object(
            sample_pytest_plugin.executor,
            "execute_tests",
            side_effect=subprocess.TimeoutExpired(cmd="pytest", timeout=600),
        ),
    ):
        result = sample_pytest_plugin.check(["tests"], {})

    assert_that(result.timed_out).is_true()
    assert_that(result.output).contains("600s")
    assert_that(result.output).does_not_contain("300s")


def test_pytest_config_int_timeout_is_enforced_without_float_drift(
    sample_pytest_plugin: PytestPlugin,
) -> None:
    """The int timeout held by ``pytest_config`` survives as an int.

    Args:
        sample_pytest_plugin: The PytestPlugin instance to test.
    """
    sample_pytest_plugin.pytest_config.set_options(timeout=600)
    sample_pytest_plugin.set_options(
        **sample_pytest_plugin.pytest_config.get_options_dict(),
    )
    enforced: list[int | float | None] = []

    with (
        _configured_check(sample_pytest_plugin),
        patch.object(
            sample_pytest_plugin,
            "_run_subprocess",
            new=_record_subprocess_timeout(enforced),
        ),
    ):
        sample_pytest_plugin.check(["tests"], {})

    assert_that(sample_pytest_plugin.pytest_config.timeout).is_equal_to(600)
    assert_that(enforced[0]).is_instance_of(int)
    assert_that(enforced[0]).is_equal_to(600)
