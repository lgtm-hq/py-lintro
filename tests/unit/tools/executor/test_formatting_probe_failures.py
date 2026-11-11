"""Ownership probe failures remain visible through the real execution pipeline."""

from __future__ import annotations

from pathlib import Path
from typing import NoReturn

import pytest
from assertpy import assert_that

from lintro.config.config_loader import clear_config_cache
from lintro.models.core.tool_result import ToolResult
from lintro.models.core.tool_snapshot import ToolSnapshot
from lintro.plugins.base import BaseToolPlugin
from lintro.tools.black.definition import BlackPlugin
from lintro.tools.mypy.definition import MypyPlugin
from lintro.tools.ruff.definition import RuffPlugin
from lintro.utils import tool_snapshots
from lintro.utils.tool_executor import build_run_context, execute_run


@pytest.mark.parametrize("parallel", [False, True], ids=["sequential", "parallel"])
@pytest.mark.parametrize("strict", [False, True], ids=["permissive", "strict"])
@pytest.mark.parametrize(
    "failure",
    [RuntimeError, OSError, AttributeError, ValueError],
    ids=["runtime", "io", "attribute", "value"],
)
def test_ownership_probe_error_is_a_failing_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    parallel: bool,
    strict: bool,
    failure: type[Exception],
) -> None:
    """A real ownership resolver cannot hide an error or discard surviving tools.

    Args:
        tmp_path: Isolated project and output directory.
        monkeypatch: Native check and capability probe controls.
        parallel: Select the parallel dispatcher with two surviving tools.
        strict: Missing-tool policy must not affect internal failures.
        failure: Probe exception type, including non-I/O exceptions.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.py").write_text('x = {"a": 1}\n')
    (tmp_path / ".lintro-config.yaml").write_text(
        "execution:\n  enabled_tools: [ruff, black, mypy]\n"
        f"  parallel: {str(parallel).lower()}\n"
        f"  strict_missing_tools: {str(strict).lower()}\n",
    )
    clear_config_cache()

    def probe(name: str, **kwargs: object) -> ToolSnapshot:
        """Fail the up-front Black probe while leaving other tools healthy.

        Args:
            name: Tool under inspection.
            **kwargs: Ignored probe options.

        Returns:
            Healthy evidence for surviving tools.

        Raises:
            failure: Exception class supplied by the test case for Black's probe.
        """
        if name == "black":
            raise failure("broken ownership probe")
        return ToolSnapshot(name=name, available=True, version_check_passed=True)

    def check(
        self: BaseToolPlugin,
        paths: list[str],
        options: dict[str, object],
    ) -> ToolResult:
        """Return a measured clean check from each surviving tool.

        Args:
            self: Plugin being checked.
            paths: Selected files.
            options: Runtime options.

        Returns:
            Successful native result independent of installed binaries.
        """
        return ToolResult(name=self.name, success=True, inspected_files=True)

    def unexpected_black(*args: object, **kwargs: object) -> NoReturn:
        """Reject execution after Black's probe has failed.

        Args:
            *args: Ignored check arguments.
            **kwargs: Ignored check options.

        Raises:
            AssertionError: A failed probe must prevent Black from running.
        """
        raise AssertionError("Black must not run after probe failure")

    monkeypatch.setattr(tool_snapshots, "get_tool_snapshot", probe)
    monkeypatch.setattr(RuffPlugin, "check", check)
    monkeypatch.setattr(MypyPlugin, "check", check)
    monkeypatch.setattr(BlackPlugin, "check", unexpected_black)
    ctx = build_run_context(action="check", output_format="json", no_art=True)
    artifact = execute_run(
        ctx=ctx,
        paths=[str(tmp_path)],
        tools="ruff,black,mypy",
        tool_options=None,
        exclude=None,
        include_venv=False,
        group_by="auto",
        output_format="json",
        verbose=False,
        yes=True,
    )
    results = {result.name: result for result in artifact.tool_results}
    assert_that(artifact.exit_code).is_equal_to(1)
    assert_that(results).contains_key("ruff", "black", "mypy")
    black = results["black"]
    assert_that(black.success).is_false()
    assert_that(black.execution_failed).is_true()
    assert_that(black.skipped).is_false()
    assert_that(black.unavailable).is_false()
    assert_that(black.output).contains(failure.__name__, "broken ownership probe")
    assert_that(results["ruff"].success).is_true()
    assert_that(results["mypy"].success).is_true()
    assert_that(
        (
            artifact.python_formatting.ruff_format_check
            if artifact.python_formatting
            else False
        ),
    ).is_true()
