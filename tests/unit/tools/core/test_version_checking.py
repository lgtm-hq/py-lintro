"""Unit tests for version_checking module."""

from __future__ import annotations

from pathlib import Path
from typing import NoReturn

import pytest
import yaml
from assertpy import assert_that
from loguru import logger
from pydantic import ValidationError

from lintro.config import config_loader
from lintro.config.config_loader import clear_config_cache
from lintro.exceptions.errors import ConfigurationError
from lintro.tools.core import version_checking
from lintro.tools.core.version_checking import (
    VERSION_PROBE_RETRY_CAP,
    _get_version_timeout,
    get_install_hints,
    get_minimum_versions,
    get_version_check_timeout,
    version_probe_retry_timeout,
)
from tests.module_helpers import load_fresh_module

#: Text the config-error fallback warning opens with, before the detail.
_CONFIG_WARNING = (
    "Could not read execution.version_check_timeout; using the default "
    "version probe timeout: "
)

# Tests for _get_version_timeout


def test_get_version_timeout_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Return default timeout when env var not set.

    Args:
        monkeypatch: Pytest fixture for patching modules and attributes.
    """
    monkeypatch.delenv("LINTRO_VERSION_TIMEOUT", raising=False)
    result = _get_version_timeout()
    assert_that(result).is_equal_to(30)


def test_get_version_timeout_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    """Return parsed timeout from env var.

    Args:
        monkeypatch: Pytest fixture for patching modules and attributes.
    """
    monkeypatch.setenv("LINTRO_VERSION_TIMEOUT", "60")
    result = _get_version_timeout()
    assert_that(result).is_equal_to(60)


@pytest.mark.parametrize(
    ("env_value", "expected"),
    [
        ("invalid", 30),
        ("-5", 30),
        ("0", 30),
    ],
    ids=["non_numeric", "negative", "zero"],
)
def test_get_version_timeout_invalid(
    env_value: str,
    expected: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return default on invalid timeout values.

    Args:
        env_value: The environment variable value to test.
        expected: The expected timeout value.
        monkeypatch: Pytest monkeypatch fixture for environment manipulation.
    """
    monkeypatch.setenv("LINTRO_VERSION_TIMEOUT", env_value)
    result = _get_version_timeout()
    assert_that(result).is_equal_to(expected)


# Tests for get_version_check_timeout (#3083)


def _use_project_config(
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    file_name: str,
    content: str,
) -> None:
    """Make *project* the working directory with one config file in it.

    Args:
        project: Directory to run from.
        monkeypatch: Pytest fixture for patching modules and attributes.
        file_name: Name of the config file to write.
        content: Config file content.
    """
    (project / file_name).write_text(content, encoding="utf-8")
    monkeypatch.chdir(project)
    clear_config_cache()


@pytest.mark.parametrize(
    ("env_value", "config_timeout", "expected"),
    [
        ("45", 120, 45),
        (None, 120, 120),
        (None, None, 30),
        ("not-a-number", 120, 120),
    ],
    ids=["env_over_config", "config_over_default", "default", "invalid_env"],
)
def test_get_version_check_timeout_precedence(
    env_value: str | None,
    config_timeout: int | None,
    expected: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolve env var > execution.version_check_timeout > default 30.

    Args:
        env_value: ``LINTRO_VERSION_TIMEOUT`` value, or None to unset it.
        config_timeout: Configured ``version_check_timeout``, or None to omit it.
        expected: Effective timeout.
        tmp_path: Temporary project directory.
        monkeypatch: Pytest fixture for patching modules and attributes.
    """
    if env_value is None:
        monkeypatch.delenv("LINTRO_VERSION_TIMEOUT", raising=False)
    else:
        monkeypatch.setenv("LINTRO_VERSION_TIMEOUT", env_value)
    config_line = (
        "" if config_timeout is None else f"  version_check_timeout: {config_timeout}\n"
    )
    _use_project_config(
        tmp_path,
        monkeypatch,
        file_name=".lintro-config.yaml",
        content=f"execution:\n  fail_fast: false\n{config_line}",
    )

    assert_that(get_version_check_timeout()).is_equal_to(expected)


def test_get_version_check_timeout_reads_pyproject_execution_table(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``[tool.lintro.execution] version_check_timeout`` sets the budget.

    Args:
        tmp_path: Temporary project directory.
        monkeypatch: Pytest fixture for patching modules and attributes.
    """
    monkeypatch.delenv("LINTRO_VERSION_TIMEOUT", raising=False)
    _use_project_config(
        tmp_path,
        monkeypatch,
        file_name="pyproject.toml",
        content="[tool.lintro.execution]\nversion_check_timeout = 75\n",
    )

    assert_that(get_version_check_timeout()).is_equal_to(75)


@pytest.mark.parametrize(
    ("env_value", "expected_warning"),
    [
        ("not-a-number", "Invalid LINTRO_VERSION_TIMEOUT 'not-a-number'"),
        ("0", "LINTRO_VERSION_TIMEOUT must be >= 1"),
    ],
    ids=["non_numeric", "below_one"],
)
def test_invalid_env_version_timeout_warns_once_per_process(
    env_value: str,
    expected_warning: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    logged_warnings: list[str],
) -> None:
    """An invalid env override warns once, not once per version probe.

    Args:
        env_value: Invalid ``LINTRO_VERSION_TIMEOUT`` value.
        expected_warning: Text the single warning must carry.
        tmp_path: Temporary project directory.
        monkeypatch: Pytest fixture for patching modules and attributes.
        logged_warnings: Captured WARNING-level log messages.
    """
    monkeypatch.setattr(version_checking, "_logged_warnings", set())
    monkeypatch.setenv("LINTRO_VERSION_TIMEOUT", env_value)
    _use_project_config(
        tmp_path,
        monkeypatch,
        file_name=".lintro-config.yaml",
        content="execution:\n  version_check_timeout: 45\n",
    )

    timeouts = [get_version_check_timeout() for _ in range(3)]

    assert_that(timeouts).is_equal_to([45, 45, 45])
    assert_that(logged_warnings).is_length(1)
    assert_that(logged_warnings[0]).contains(expected_warning)


def test_malformed_config_falls_back_to_default_with_one_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    logged_warnings: list[str],
) -> None:
    """Malformed YAML yields the 30s default and warns once per process.

    Args:
        tmp_path: Temporary project directory.
        monkeypatch: Pytest fixture for patching modules and attributes.
        logged_warnings: Captured WARNING-level log messages.
    """
    monkeypatch.setattr(version_checking, "_logged_warnings", set())
    monkeypatch.delenv("LINTRO_VERSION_TIMEOUT", raising=False)
    _use_project_config(
        tmp_path,
        monkeypatch,
        file_name=".lintro-config.yaml",
        content="execution: [version_check_timeout: 45\n",
    )

    timeouts = [get_version_check_timeout() for _ in range(3)]

    assert_that(timeouts).is_equal_to([30, 30, 30])
    # PyYAML's message opens with the context ("while parsing a flow
    # sequence"); the warning carries the problem and its 1-based position.
    config = Path.cwd() / ".lintro-config.yaml"
    assert_that(logged_warnings).is_equal_to(
        [
            f"{_CONFIG_WARNING}expected ',' or ']', but got '<stream end>' "
            f"({config}, line 2, column 1)",
        ],
    )


@pytest.mark.parametrize(
    ("content", "field"),
    [
        (
            "execution:\n  version_check_timeout: soon\n",
            "execution.version_check_timeout",
        ),
        ("enforce:\n  line_length: soon\n", "enforce.line_length"),
    ],
    ids=["execution", "enforce"],
)
def test_invalid_config_value_warns_with_the_field_and_its_error(
    content: str,
    field: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    logged_warnings: list[str],
) -> None:
    """A rejected field names its section, field and pydantic's message.

    The first line of a ``ValidationError`` is ``1 validation error for
    ExecutionConfig``, which says nothing about what is wrong, and its
    location is relative to the section model, so the section is added back.

    Args:
        content: ``.lintro-config.yaml`` content with one invalid value.
        field: Dotted path of the invalid value, section first.
        tmp_path: Temporary project directory.
        monkeypatch: Pytest fixture for patching modules and attributes.
        logged_warnings: Captured WARNING-level log messages.
    """
    monkeypatch.setattr(version_checking, "_logged_warnings", set())
    monkeypatch.delenv("LINTRO_VERSION_TIMEOUT", raising=False)
    _use_project_config(
        tmp_path,
        monkeypatch,
        file_name=".lintro-config.yaml",
        content=content,
    )

    timeouts = [get_version_check_timeout() for _ in range(2)]

    assert_that(timeouts).is_equal_to([30, 30])
    assert_that(logged_warnings).is_equal_to(
        [
            f"{_CONFIG_WARNING}{field}: Input should be a valid "
            "integer, unable to parse string as an integer",
        ],
    )


def _config_load_raises(monkeypatch: pytest.MonkeyPatch, error: Exception) -> None:
    """Make every config load raise *error*, as a broken config file would.

    ``load_config`` is patched, not ``get_config``: the real ``get_config``
    looks ``load_config`` up in ``config_loader``'s globals, so the patch
    holds however ``version_checking`` imports ``get_config``.

    Args:
        monkeypatch: Pytest fixture for patching modules and attributes.
        error: Exception the config load raises.
    """

    def broken_load_config(*_args: object, **_kwargs: object) -> NoReturn:
        raise error

    monkeypatch.setattr(config_loader, "load_config", broken_load_config)
    clear_config_cache()


def _wrapped_validation_error(*, title: str, message: str) -> ConfigurationError:
    """Return a ``ValidationError`` re-raised the way ``load_config`` does.

    Args:
        title: Name of the model the validation error is reported for.
        message: Message of the validator's ``ValueError``.

    Returns:
        ConfigurationError: Carrying the ``ValidationError`` as its cause.
    """
    cause = ValidationError.from_exception_data(
        title,
        [
            {
                "type": "value_error",
                "loc": ("version_check_timeout",),
                "input": "soon",
                "ctx": {"error": ValueError(message)},
            },
        ],
    )
    wrapped = ConfigurationError(str(cause))
    wrapped.__cause__ = cause
    return wrapped


@pytest.mark.parametrize(
    ("error", "expected_detail"),
    [
        (
            yaml.MarkedYAMLError(
                problem="expected a mapping\n  but found a scalar",
                problem_mark=yaml.Mark("cfg.yaml", 0, 2, 4, "", 0),
            ),
            "expected a mapping but found a scalar (cfg.yaml, line 3, column 5)",
        ),
        (
            _wrapped_validation_error(
                title="ExecutionConfig",
                message="must be a number\n\n  of seconds",
            ),
            "execution.version_check_timeout: Value error, must be a number "
            "of seconds",
        ),
        (
            _wrapped_validation_error(
                title="NotAConfigSection",
                message="must be a number\nof seconds",
            ),
            "version_check_timeout: Value error, must be a number of seconds",
        ),
    ],
    ids=["yaml_problem", "section_model", "unknown_model"],
)
def test_multi_line_config_errors_warn_on_one_line(
    error: Exception,
    expected_detail: str,
    monkeypatch: pytest.MonkeyPatch,
    logged_warnings: list[str],
) -> None:
    """A multi-line YAML problem or validator message is logged on one line.

    The section prefix comes only from a model a config section is typed
    with; any other model keeps its own location.

    Args:
        error: Exception the config load raises.
        expected_detail: Detail the single warning carries after
            ``_CONFIG_WARNING``, compared for exact equality.
        monkeypatch: Pytest fixture for patching modules and attributes.
        logged_warnings: Captured WARNING-level log messages.
    """
    monkeypatch.setattr(version_checking, "_logged_warnings", set())
    monkeypatch.delenv("LINTRO_VERSION_TIMEOUT", raising=False)
    _config_load_raises(monkeypatch, error)

    timeouts = [get_version_check_timeout() for _ in range(2)]

    assert_that(timeouts).is_equal_to([30, 30])
    assert_that(logged_warnings).is_equal_to([f"{_CONFIG_WARNING}{expected_detail}"])
    assert_that(logged_warnings[0]).does_not_contain("\n")


def test_section_model_errors_are_titled_with_their_class_name() -> None:
    """Every section model's ``ValidationError`` title is its class name.

    ``_config_section_of`` matches a section by the error's title, which is
    the class name only while the model's ``model_config`` sets no ``title``.
    The section set is pinned exactly, so a section that drops out of the
    model filter (say, retyped ``ExecutionConfig | None``) fails here instead
    of leaving both this guard and ``_config_section_of`` silently skipping it.
    A new ``LintroConfig`` section model must be added to the pinned list on
    purpose: this test fails until it is, so the new model's title is checked
    deliberately rather than assumed.
    """
    expected = {
        "execution": "ExecutionConfig",
        "enforce": "EnforceConfig",
        "review": "ReviewConfig",
        "output": "OutputConfig",
        "watch": "WatchConfig",
        "deps": "DepsConfig",
    }
    section_models = version_checking._section_models()

    assert_that(
        {name: model.__name__ for name, model in section_models.items()},
    ).is_equal_to(expected)
    assert_that(
        {
            name: version_checking._config_section_of(model_name)
            for name, model_name in expected.items()
        },
    ).is_equal_to({name: name for name in expected})

    titles: dict[str, str] = {}
    for model in section_models.values():
        with pytest.raises(ValidationError) as caught:
            model.model_validate("not a mapping")
        titles[model.__name__] = caught.value.title

    assert_that(titles).is_equal_to({name: name for name in titles})


@pytest.mark.parametrize(
    ("error", "expected_detail"),
    [
        (
            OSError("\n\n  permission denied: .lintro-config.yaml  \nsecond line"),
            "permission denied: .lintro-config.yaml",
        ),
        (ValueError("x" * 500), "x" * 197 + "..."),
        (TypeError(""), "TypeError"),
        (TypeError("\n \n"), "TypeError"),
    ],
    ids=["leading_blank_lines", "too_long", "empty", "blank_lines_only"],
)
def test_other_config_errors_warn_with_their_first_non_empty_line(
    error: Exception,
    expected_detail: str,
    monkeypatch: pytest.MonkeyPatch,
    logged_warnings: list[str],
) -> None:
    """Any other error quotes its first non-empty line, at most 200 characters.

    Args:
        error: Exception the config loader raises.
        expected_detail: Detail the single warning carries after
            ``_CONFIG_WARNING``, compared for exact equality.
        monkeypatch: Pytest fixture for patching modules and attributes.
        logged_warnings: Captured WARNING-level log messages.
    """
    monkeypatch.setattr(version_checking, "_logged_warnings", set())
    monkeypatch.delenv("LINTRO_VERSION_TIMEOUT", raising=False)
    _config_load_raises(monkeypatch, error)

    timeouts = [get_version_check_timeout() for _ in range(2)]

    assert_that(timeouts).is_equal_to([30, 30])
    assert_that(logged_warnings).is_equal_to([f"{_CONFIG_WARNING}{expected_detail}"])
    logged_detail = logged_warnings[0].removeprefix(_CONFIG_WARNING)
    assert_that(len(logged_detail)).is_less_than_or_equal_to(200)


def test_a_different_config_error_later_still_warns_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    logged_warnings: list[str],
) -> None:
    """Each distinct config error warns once, so a later new one is not lost.

    Watch mode resolves the timeout again after the config changes; a second,
    different error must surface rather than hide behind the first one's key.

    Args:
        tmp_path: Temporary project directory.
        monkeypatch: Pytest fixture for patching modules and attributes.
        logged_warnings: Captured WARNING-level log messages.
    """
    monkeypatch.setattr(version_checking, "_logged_warnings", set())
    monkeypatch.delenv("LINTRO_VERSION_TIMEOUT", raising=False)
    timeouts: list[int] = []
    for content in (
        "execution: [version_check_timeout: 45\n",
        "execution: {version_check_timeout: 45\n",
        "execution: [version_check_timeout: 45\n",
    ):
        _use_project_config(
            tmp_path,
            monkeypatch,
            file_name=".lintro-config.yaml",
            content=content,
        )
        timeouts.extend(get_version_check_timeout() for _ in range(2))

    assert_that(timeouts).is_equal_to([30] * 6)
    config = Path.cwd() / ".lintro-config.yaml"
    assert_that(
        [line.removeprefix(_CONFIG_WARNING) for line in logged_warnings],
    ).is_equal_to(
        [
            f"expected ',' or ']', but got '<stream end>' "
            f"({config}, line 2, column 1)",
            f"expected ',' or '}}', but got '<stream end>' "
            f"({config}, line 2, column 1)",
        ],
    )


def test_import_time_read_leaves_the_invalid_env_warning_for_the_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    logged_warnings: list[str],
) -> None:
    """Importing with a bad env value stays quiet; the first run read warns.

    At import the run's log sinks (``debug.log``) do not exist yet, so a
    warning there, and the warn-once key it used up, would never reach them.

    Args:
        tmp_path: Temporary project directory.
        monkeypatch: Pytest fixture for patching modules and attributes.
        logged_warnings: Captured WARNING-level log messages.
    """
    monkeypatch.setenv("LINTRO_VERSION_TIMEOUT", "not-a-number")

    # The copy is registered under its own name, so the imported module (and
    # every reference to it) keeps its warn-once keys.
    fresh = load_fresh_module(
        monkeypatch,
        path=version_checking.__file__,
        name="_fresh_version_checking",
    )

    assert_that(fresh.VERSION_CHECK_TIMEOUT).is_equal_to(30)
    assert_that(logged_warnings).is_empty()
    assert_that(fresh._logged_warnings).is_empty()

    _use_project_config(
        tmp_path,
        monkeypatch,
        file_name=".lintro-config.yaml",
        content="execution:\n  version_check_timeout: 45\n",
    )
    timeouts = [fresh.get_version_check_timeout() for _ in range(2)]

    assert_that(timeouts).is_equal_to([45, 45])
    assert_that(logged_warnings).is_equal_to(
        ["Invalid LINTRO_VERSION_TIMEOUT 'not-a-number'; ignoring it"],
    )


@pytest.mark.parametrize(
    ("timeout", "retry_timeout"),
    [(1, 2), (30, 60), (60, 120), (61, 120), (120, 120), (300, 300)],
)
def test_version_probe_retry_timeout_doubles_up_to_cap(
    timeout: int,
    retry_timeout: int,
) -> None:
    """The retry budget doubles, is capped at 120, and never shrinks.

    Args:
        timeout: Budget of the attempt that timed out.
        retry_timeout: Expected retry budget.
    """
    assert_that(VERSION_PROBE_RETRY_CAP).is_equal_to(120)
    assert_that(version_probe_retry_timeout(timeout)).is_equal_to(retry_timeout)


# Tests for get_minimum_versions


def test_get_minimum_versions_returns_dict() -> None:
    """Return a dictionary of tool versions."""
    result = get_minimum_versions()
    assert_that(result).is_instance_of(dict)
    assert_that(result).is_not_empty()


def test_get_minimum_versions_contains_expected_tools() -> None:
    """Return versions for expected external tools."""
    result = get_minimum_versions()
    # Check for some expected tools
    expected_tools = ["hadolint", "actionlint"]
    for tool in expected_tools:
        assert_that(tool in result).is_true()


def test_get_minimum_versions_returns_copy() -> None:
    """Return a copy, not the original dict."""
    result1 = get_minimum_versions()
    result2 = get_minimum_versions()
    # Should be equal but not the same object
    assert_that(result1).is_equal_to(result2)
    # Modifying one shouldn't affect the other
    result1["test_tool"] = "1.0.0"
    assert_that("test_tool" in result2).is_false()


# Tests for get_install_hints


def test_get_install_hints_returns_dict() -> None:
    """Return a dictionary of install hints."""
    result = get_install_hints()
    assert_that(result).is_instance_of(dict)
    assert_that(result).is_not_empty()


def test_get_install_hints_pip_for_python_tools() -> None:
    """Python tools have pip/uv install hints."""
    result = get_install_hints()
    # pytest is a Python tool that should have pip/uv hints
    assert_that("pip install" in result.get("pytest", "")).is_true()
    assert_that("uv add" in result.get("pytest", "")).is_true()


def test_get_install_hints_bun_for_node_tools() -> None:
    """Node.js tools have bun install hints."""
    result = get_install_hints()
    assert_that("bun add" in result.get("markdownlint", "")).is_true()


def test_get_install_hints_includes_commitlint_cli_alias() -> None:
    """``@commitlint/cli`` npm alias has the same bun install hint as commitlint."""
    result = get_install_hints()
    assert_that(result).contains_key("@commitlint/cli")
    assert_that(result["@commitlint/cli"]).contains("bun add")
    assert_that(result["@commitlint/cli"]).contains("@commitlint/cli@")
    assert_that(result["@commitlint/cli"]).is_equal_to(result["commitlint"])


def test_get_install_hints_includes_spectral_cli_alias() -> None:
    """``@stoplight/spectral-cli`` npm alias has the same bun install hint.

    The hint must be a plain argv-safe string: no nested quotes or backticks
    around the scoped package name.
    """
    result = get_install_hints()
    hint = result["spectral"]
    assert_that(result).contains_key("@stoplight/spectral-cli")
    assert_that(hint).contains("bun add")
    assert_that(hint).contains("@stoplight/spectral-cli@")
    assert_that(hint).does_not_contain("`")
    assert_that(result["@stoplight/spectral-cli"]).is_equal_to(hint)


def test_get_install_hints_uses_commitlint_companion_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Commitlint hint uses each npm package's resolved version."""
    monkeypatch.setattr(
        version_checking,
        "get_minimum_versions",
        lambda: {"commitlint": "21.2.1", "@commitlint/cli": "21.2.1"},
    )
    monkeypatch.setattr(
        version_checking,
        "get_tool_version",
        lambda package: (
            "21.2.0" if package == "@commitlint/config-conventional" else None
        ),
    )

    result = get_install_hints()

    assert_that(result["commitlint"]).contains("@commitlint/cli@21.2.1")
    assert_that(result["commitlint"]).contains(
        "@commitlint/config-conventional@21.2.0",
    )
    assert_that(result["@commitlint/cli"]).is_equal_to(result["commitlint"])


def test_get_install_hints_falls_back_when_companion_version_unresolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Commitlint hints survive an unresolvable companion version (#1663).

    Args:
        monkeypatch: Pytest fixture for patching modules and attributes.
    """
    monkeypatch.setattr(
        version_checking,
        "get_minimum_versions",
        lambda: {"commitlint": "21.2.1", "@commitlint/cli": "21.2.1"},
    )
    monkeypatch.setattr(version_checking, "get_tool_version", lambda _package: None)

    result = get_install_hints()

    assert_that(result).contains_key("commitlint")
    assert_that(result).contains_key("@commitlint/cli")
    for key in ("commitlint", "@commitlint/cli"):
        assert_that(result[key]).does_not_contain("{")
        assert_that(result[key]).contains("@commitlint/cli@21.2.1")
        assert_that(result[key]).contains("@commitlint/config-conventional@21.2.1")


def test_get_install_hints_missing_template_logs_debug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing hint templates log at debug, not warning (#1593 / #1425).

    Args:
        monkeypatch: Pytest fixture for patching modules and attributes.
    """
    monkeypatch.setattr(
        version_checking,
        "get_minimum_versions",
        lambda: {"hadolint": "2.12.0", "totally_missing_tool": "1.0.0"},
    )
    version_checking._logged_warnings.clear()
    records: list[tuple[str, str]] = []

    def sink(message: object) -> None:
        """Collect the level and text of each loguru record.

        Args:
            message: Loguru message object carrying the record.
        """
        record = message.record  # type: ignore[attr-defined]
        records.append((record["level"].name, record["message"]))

    sink_id = logger.add(sink, level="DEBUG", format="{message}")
    try:
        hints = get_install_hints()
    finally:
        logger.remove(sink_id)

    debug_messages = [text for level, text in records if level == "DEBUG"]
    assert_that([text for level, text in records if level == "WARNING"]).is_empty()
    assert_that("\n".join(debug_messages)).contains("Missing install hints")
    assert_that("\n".join(debug_messages)).contains("totally_missing_tool")
    assert_that(hints).does_not_contain_key("totally_missing_tool")


def test_get_install_hints_external_tools() -> None:
    """External tools have appropriate install hints."""
    result = get_install_hints()
    assert_that("github" in result.get("hadolint", "").lower()).is_true()
    assert_that("rustup" in result.get("clippy", "")).is_true()


def test_get_install_hints_semgrep_is_isolated() -> None:
    """Doctor must not suggest installing semgrep via pip or lintro extras."""
    result = get_install_hints()
    hint = result.get("semgrep", "")
    assert_that(hint).contains("install-semgrep.sh")
    assert_that(hint).does_not_contain("pip install")
    assert_that(hint).does_not_contain("lintro[tools]")
