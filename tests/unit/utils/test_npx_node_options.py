"""Tests for keeping Node preloads away from the npx fallback (#3460)."""

from __future__ import annotations

import os
import sys
import threading
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from assertpy import assert_that
from loguru import logger

from lintro.models.core.subprocess_result import SubprocessResult
from lintro.plugins.subprocess_executor import (
    run_subprocess,
    run_subprocess_streaming,
)
from lintro.tools.core.node_fallback import (
    npx_runner_options,
    registry_fallback_spec,
)
from lintro.tools.core.version_parsing import (
    check_tool_version,
    get_minimum_versions,
)
from lintro.utils import npx_call
from lintro.utils.npx_call import filter_node_options, npx_child_env

_SPEC = "prettier@3.9.4"
_PRELOAD = "--require=/tmp/preload.cjs"

posix_only = pytest.mark.skipif(
    sys.platform == "win32",
    reason="the fake runners are POSIX shell scripts",
)


def _npx() -> list[str]:
    """Return the npx fallback lintro's Node builder emits.

    Returns:
        The command without tool arguments.
    """
    return ["npx", "--yes", *npx_runner_options(_SPEC), "--package", _SPEC, "prettier"]


def test_builder_command_empties_npm_node_options_before_the_package() -> None:
    """``--node-options=`` sits among the runner options and the spec survives."""
    command = _npx()

    assert_that(command[: command.index("--package")]).contains("--node-options=")
    assert_that(registry_fallback_spec(command)).is_equal_to(_SPEC)


@pytest.mark.parametrize(
    ("inherited", "kept"),
    [
        pytest.param(
            "--max-old-space-size=4096",
            "--max-old-space-size=4096",
            id="mem",
        ),
        pytest.param(
            f"--max_old_space_size=2048 {_PRELOAD} --no-warnings",
            "--max_old_space_size=2048 --no-warnings",
            id="mixed",
        ),
        pytest.param("-r /tmp/p.cjs", "", id="short-require"),
        pytest.param("--import=data:text/javascript,x", "", id="import"),
        pytest.param("--loader ./l.mjs --experimental-loader=./l.mjs", "", id="loader"),
        pytest.param("--env-file=.env --inspect=0.0.0.0:9229", "", id="env-inspect"),
        pytest.param("--experimental-vm-modules", "", id="unknown-flag"),
        pytest.param('--max-old-space-size=1 "--require=x"', "", id="quoted"),
        pytest.param("--no-warnings\t--require=x", "", id="tab-joined"),
        pytest.param("--dns-result-order=ipv4first  --use-openssl-ca", None, id="ws"),
        pytest.param("", "", id="empty"),
    ],
)
def test_node_options_keep_only_flags_that_load_no_code(
    inherited: str,
    kept: str | None,
) -> None:
    """Allowlisted flags survive; every code-loading or unknown entry is dropped.

    Args:
        inherited: ``NODE_OPTIONS`` as the user set it.
        kept: Expected result, or None for the allowlisted entries single-spaced.
    """
    expected = " ".join(inherited.split()) if kept is None else kept

    assert_that(filter_node_options(inherited)).is_equal_to(expected)


def test_npx_child_env_scrubs_npm_and_node_preloads() -> None:
    """Every casing of npm's variable goes; NODE_OPTIONS keeps its safe part."""
    env = {
        "PATH": "/usr/bin",
        "NODE_OPTIONS": f"--max-old-space-size=4096 {_PRELOAD}",
        "node_options": _PRELOAD,
        "npm_config_node_options": _PRELOAD,
        "NPM_CONFIG_NODE_OPTIONS": _PRELOAD,
        "Npm_Config_Node_Options": _PRELOAD,
        "npm_config_node-options": _PRELOAD,
        "npm_config_registry": "http://127.0.0.1/",
    }

    child = npx_child_env([*_npx(), "--check", "a.css"], env)

    assert_that(child).is_equal_to(
        {
            "PATH": "/usr/bin",
            "NODE_OPTIONS": "--max-old-space-size=4096",
            "npm_config_registry": "http://127.0.0.1/",
        },
    )
    assert_that(env).contains_key("npm_config_node_options")


def test_npx_child_env_removes_node_options_when_nothing_is_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An inherited preload-only NODE_OPTIONS is removed from the copied environ.

    Args:
        monkeypatch: Scoped environment override.
    """
    monkeypatch.setenv("NODE_OPTIONS", _PRELOAD)
    monkeypatch.setenv("LINTRO_TEST_MARKER", "kept")

    child = npx_child_env(_npx(), None)

    assert_that(child).is_not_none()
    assert child is not None
    assert_that(child).does_not_contain_key("NODE_OPTIONS")
    assert_that(child).contains_entry({"LINTRO_TEST_MARKER": "kept"})
    assert_that(os.environ["NODE_OPTIONS"]).is_equal_to(_PRELOAD)


@pytest.mark.parametrize(
    "command",
    [
        pytest.param(["bunx", "--package", _SPEC, "prettier"], id="bunx"),
        pytest.param(["/proj/node_modules/.bin/prettier", "--check"], id="local"),
        pytest.param(["prettier", "--check"], id="path"),
        pytest.param(["npx", "prettier", "--package", _SPEC], id="npx-positional"),
    ],
)
def test_other_commands_keep_their_environment(command: list[str]) -> None:
    """bunx, project-local and PATH binaries see NODE_OPTIONS unchanged.

    Args:
        command: Command that is not lintro's npx fallback.
    """
    env = {"NODE_OPTIONS": _PRELOAD, "npm_config_node_options": _PRELOAD}

    assert_that(npx_child_env(command, env)).is_equal_to(env)
    assert_that(npx_child_env(command, None)).is_none()


_PRINT_NODE_ENV = (
    'printf "%s|%s\\n" "${NODE_OPTIONS-unset}" "${npm_config_node_options-unset}"\n'
)


def _runner(directory: Path, name: str) -> Path:
    """Write a fake package runner that prints the Node-related environment.

    Args:
        directory: Where to create it.
        name: Runner name (``npx`` or ``bunx``).

    Returns:
        Path to the runner.
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(f"#!/bin/sh\n{_PRINT_NODE_ENV}", encoding="utf-8")
    path.chmod(0o755)
    return path


@posix_only
@pytest.mark.parametrize("executor", [run_subprocess, run_subprocess_streaming])
def test_executors_spawn_npx_without_preloads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    executor: Callable[..., SubprocessResult],
) -> None:
    """Both executors hand npx a scrubbed environment, inherited or passed.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Scoped environment override.
        executor: lintro subprocess entry point.
    """
    npx = _runner(tmp_path, "npx")
    monkeypatch.setenv("NODE_OPTIONS", f"{_PRELOAD} --max-old-space-size=512")

    inherited = executor([str(npx), "--package", _SPEC, "prettier"], timeout=30)
    passed = executor(
        [str(npx), "--package", _SPEC, "prettier"],
        timeout=30,
        env={"npm_config_node_options": _PRELOAD},
    )

    assert_that(inherited.stdout.strip()).is_equal_to("--max-old-space-size=512|unset")
    assert_that(passed.stdout.strip()).is_equal_to("--max-old-space-size=512|unset")


@posix_only
def test_executor_leaves_bunx_environment_alone(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Under bunx the inherited NODE_OPTIONS and npm variable pass through.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Scoped environment override.
    """
    bunx = _runner(tmp_path, "bunx")
    monkeypatch.setenv("NODE_OPTIONS", _PRELOAD)
    monkeypatch.setenv("npm_config_node_options", _PRELOAD)

    result = run_subprocess([str(bunx), "--package", _SPEC, "prettier"], timeout=30)

    assert_that(result.stdout.strip()).is_equal_to(f"{_PRELOAD}|{_PRELOAD}")


@pytest.fixture
def drop_warnings(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Collect WARNING messages with the once-per-process drop warning re-armed.

    Args:
        monkeypatch: Scoped attribute override.

    Yields:
        list[str]: Messages logged at WARNING or above.
    """
    monkeypatch.setattr(npx_call, "_DROP_WARNED", threading.Event())
    messages: list[str] = []
    sink = logger.add(
        lambda message: messages.append(str(message).strip()),
        level="WARNING",
        format="{message}",
    )
    try:
        yield messages
    finally:
        logger.remove(sink)


def test_dropped_entries_are_named_in_one_warning(drop_warnings: list[str]) -> None:
    """The first drop is warned about with its entries and the docs; later ones not.

    Args:
        drop_warnings: Captured WARNING-level messages.
    """
    env = {"NODE_OPTIONS": f"--max-old-space-size=4096 {_PRELOAD} --import=x.mjs"}

    for _ in range(3):
        npx_child_env(_npx(), env)

    assert_that(drop_warnings).is_length(1)
    assert_that(drop_warnings[0]).contains(
        repr(_PRELOAD),
        "'--import=x.mjs'",
        "docs/configuration.md#nodejs-tool-resolution",
    )
    assert_that(drop_warnings[0]).does_not_contain("--max-old-space-size")


def test_extra_spaces_alone_are_not_a_drop(drop_warnings: list[str]) -> None:
    """Collapsing extra spaces between kept flags warns about nothing.

    Args:
        drop_warnings: Captured WARNING-level messages.
    """
    child = npx_child_env(
        _npx(),
        {"NODE_OPTIONS": "  --no-warnings   --max-old-space-size=512 "},
    )

    assert_that(child).is_equal_to(
        {"NODE_OPTIONS": "--no-warnings --max-old-space-size=512"},
    )
    assert_that(drop_warnings).is_empty()


@posix_only
def test_version_probe_cannot_be_faked_by_a_preload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The npx version probe gets the same preload-free environment as the run.

    The fake npx answers with a fake version whenever ``NODE_OPTIONS`` still
    carries ``--require``, and with the pin only when exactly the safe flag
    survived.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Scoped environment override.
    """
    pin = get_minimum_versions()["prettier"]
    npx = tmp_path / "npx"
    npx.write_text(
        "#!/bin/sh\n"
        'case "$NODE_OPTIONS" in\n'
        "  *--require*) echo 999.0.0 ;;\n"
        f"  --stack-trace-limit=7) echo {pin} ;;\n"
        "  *) echo 0.0.1 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    npx.chmod(0o755)
    monkeypatch.setenv("NODE_OPTIONS", f"{_PRELOAD} --stack-trace-limit=7")
    command = [
        str(npx),
        "--yes",
        *npx_runner_options(_SPEC),
        "--package",
        _SPEC,
        "prettier",
    ]

    info = check_tool_version("prettier", command)

    assert_that(info.current_version).is_equal_to(pin)
