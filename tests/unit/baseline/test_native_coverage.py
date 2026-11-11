"""Native exclusions and ambiguous Ruff output cannot erase acknowledged debt."""

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli import cli
from lintro.config.config_loader import clear_config_cache
from lintro.models.core.subprocess_result import SubprocessResult
from lintro.plugins import ToolRegistry
from lintro.plugins.base import BaseToolPlugin
from lintro.utils.config import clear_pyproject_cache, load_lintro_tool_config
from lintro.utils.fingerprint_baseline import snapshot
from lintro.utils.fingerprint_baseline_file import change_baseline, read_baseline
from tests.unit.tools.subprocess_helpers import subprocess_result


@pytest.mark.parametrize("mode", ["update", "prune"])
@pytest.mark.parametrize(
    ("tool", "filename", "source", "initial_config", "native_exclusion"),
    [
        pytest.param(
            "pydoclint",
            "example.py",
            'def f(value: str) -> bool:\n    """Return a value.\n\n'
            '    Returns:\n        bool: Always true.\n    """\n    return True\n',
            {"pyproject.toml": '[tool.pydoclint]\nstyle = "google"\n'},
            {
                "pyproject.toml": '[tool.pydoclint]\nstyle = "google"\n'
                'exclude = "example[.]py$"\n',
            },
        ),
        pytest.param(
            "sqlfluff",
            "q.sql",
            "select 1",
            {".sqlfluff": "[sqlfluff]\ndialect = ansi\n"},
            {".sqlfluffignore": "q.sql\n"},
        ),
        pytest.param(
            "dotenv_linter",
            ".env",
            "lowercase=value\n",
            {},
            {
                "pyproject.toml": '[tool.lintro.dotenv_linter]\nexclude = ["{file}"]\n',
            },
        ),
    ],
)
def test_native_per_file_exclusion_retains_acknowledgements(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    tool: str,
    filename: str,
    source: str,
    initial_config: dict[str, str],
    native_exclusion: dict[str, str],
) -> None:
    """Canned clean native skips cannot authorize erasing acknowledged debt."""

    def execute(
        self: BaseToolPlugin,
        cmd: list[str],
        *args: Any,
        **kwargs: Any,
    ) -> SubprocessResult:
        """Model each native report without invoking an installed tool."""
        configured = (workspace / next(iter(native_exclusion))).exists()
        if tool == "pydoclint":
            configured = (
                configured and "exclude" in (workspace / "pyproject.toml").read_text()
            )
        elif tool == "dotenv_linter":
            configured = (
                "--exclude" in cmd
                and str(workspace / filename) in cmd[cmd.index("--exclude") + 1 :]
            )
        if configured:
            return subprocess_result(True, "[]" if tool == "sqlfluff" else "")
        payloads = {
            "pydoclint": f"{workspace / filename}\n    1: DOC101: Missing argument\n",
            "sqlfluff": json.dumps(
                [
                    {
                        "filepath": str(workspace / filename),
                        "violations": [
                            {
                                "start_line_no": 1,
                                "code": "CP01",
                                "description": "Uppercase keyword",
                            },
                        ],
                    },
                ],
            ),
            "dotenv_linter": f"{workspace / filename}:1 LowercaseKey: Uppercase key\n",
        }
        return subprocess_result(False, payloads[tool])

    monkeypatch.setattr(BaseToolPlugin, "_run_subprocess_result", execute)
    monkeypatch.setattr(
        "lintro.plugins.execution_preparation.verify_tool_version",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        BaseToolPlugin,
        "_get_executable_command",
        lambda self, tool_name: [tool_name],
    )
    file = workspace / filename
    file.write_text(source, encoding="utf-8")
    for name, content in initial_config.items():
        (workspace / name).write_text(content, encoding="utf-8")
    clear_config_cache()
    clear_pyproject_cache()
    runner = CliRunner()
    created = runner.invoke(cli, ["baseline", "create", filename, "--tools", tool])
    assert_that(created.exit_code).described_as(created.output).is_equal_to(1)
    assert_that(created.output).contains("does not certify complete findings")
    baseline = workspace / ".lintro-baseline.json"
    assert_that(baseline.exists()).is_false()
    # Import debt from a previous producer; current sequence-only parsers cannot
    # certify a failed report, and clean native exclusions cannot prune this debt.
    result = ToolRegistry.get(tool).check([str(file)], {})
    imported = snapshot([replace(result, findings_complete=True)], root=workspace)
    change_baseline(baseline, lambda _: imported, create=True)
    assert_that(read_baseline(baseline).entries).is_not_empty()
    before = baseline.read_bytes()

    for name, content in native_exclusion.items():
        (workspace / name).write_text(
            content.replace("{file}", str(file)),
            encoding="utf-8",
        )
    clear_config_cache()
    clear_pyproject_cache()
    maintained = runner.invoke(cli, ["baseline", mode, filename, "--tools", tool])

    assert_that(maintained.exit_code).described_as(maintained.output).is_zero()
    assert_that(baseline.read_bytes()).is_equal_to(before)
    plugin = ToolRegistry.get(tool).copy_for_execution()
    plugin.set_options(**load_lintro_tool_config(tool))
    result = plugin.check([str(file)], {})
    assert_that(result.success).is_true()
    assert_that(result.findings_complete).is_false()
    assert_that(result.issues_count).is_zero()
    assert_that(result.inspected_paths).is_none()
