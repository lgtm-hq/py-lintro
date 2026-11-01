"""Native exclusions and ambiguous Ruff output cannot erase acknowledged debt."""

import json
import subprocess  # nosec B404 - real native binaries under test, shell=False
from dataclasses import replace
from pathlib import Path

import pytest
from click.testing import CliRunner

from lintro.cli import cli
from lintro.config.config_loader import clear_config_cache
from lintro.plugins import ToolRegistry
from lintro.tools.ruff.definition import RuffPlugin
from lintro.utils.config import clear_pyproject_cache
from lintro.utils.fingerprint_baseline import snapshot
from lintro.utils.fingerprint_baseline_file import change_baseline, read_baseline
from tests.integration._tools import require_tool


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
            marks=require_tool("pydoclint"),
        ),
        pytest.param(
            "sqlfluff",
            "q.sql",
            "select 1",
            {".sqlfluff": "[sqlfluff]\ndialect = ansi\n"},
            {".sqlfluffignore": "q.sql\n"},
            marks=require_tool("sqlfluff"),
        ),
        pytest.param(
            "dotenv_linter",
            ".env",
            "lowercase=value\n",
            {},
            {
                "pyproject.toml": '[tool.lintro.dotenv_linter]\nexclude = ["{file}"]\n',
            },
            marks=require_tool("dotenv-linter"),
        ),
    ],
)
def test_native_per_file_exclusion_retains_acknowledgements(
    workspace: Path,
    mode: str,
    tool: str,
    filename: str,
    source: str,
    initial_config: dict[str, str],
    native_exclusion: dict[str, str],
) -> None:
    """Real clean native skips supply no exact coverage for update or prune."""
    file = workspace / filename
    file.write_text(source, encoding="utf-8")
    for name, content in initial_config.items():
        (workspace / name).write_text(content, encoding="utf-8")
    clear_config_cache()
    clear_pyproject_cache()
    runner = CliRunner()
    created = runner.invoke(cli, ["baseline", "create", filename, "--tools", tool])
    assert created.exit_code == 1, created.output
    assert "does not certify complete findings" in created.output
    baseline = workspace / ".lintro-baseline.json"
    assert not baseline.exists()
    # Import debt from a previous producer; current sequence-only parsers cannot
    # certify a failed report, and clean native exclusions cannot prune this debt.
    result = ToolRegistry.get(tool).check([str(file)], {})
    imported = snapshot([replace(result, findings_complete=True)], root=workspace)
    change_baseline(baseline, lambda _: imported, create=True)
    assert read_baseline(baseline).entries
    before = baseline.read_bytes()

    for name, content in native_exclusion.items():
        (workspace / name).write_text(
            content.replace("{file}", str(file)),
            encoding="utf-8",
        )
    clear_config_cache()
    clear_pyproject_cache()
    maintained = runner.invoke(cli, ["baseline", mode, filename, "--tools", tool])

    assert maintained.exit_code == 0, maintained.output
    assert baseline.read_bytes() == before
    plugin = ToolRegistry.get(tool)
    result = plugin.check([str(file)], {})
    assert result.success and not result.findings_complete
    assert result.issues_count == 0
    assert result.inspected_paths is None


@require_tool("ruff")
@pytest.mark.parametrize("mode", ["update", "prune"])
@pytest.mark.parametrize("separator", ["\n", "\r"])
def test_native_ruff_multiline_discovery_cannot_prune_excluded_victim(
    workspace: Path,
    mode: str,
    separator: str,
) -> None:
    """Validate the corrected Opus repro against Ruff's actual unescaped output."""
    victim = workspace / "victim.py"
    victim.write_text("import os\n", encoding="utf-8")
    runner = CliRunner()
    created = runner.invoke(cli, ["baseline", "create", victim.name, "--tools", "ruff"])
    assert created.exit_code == 0, created.output
    baseline = workspace / ".lintro-baseline.json"
    assert any(entry.code == "F401" for entry in read_baseline(baseline).entries)
    before = baseline.read_bytes()
    # A basename exclusion would skip both files. Match only the absolute victim.
    (workspace / "pyproject.toml").write_text(
        "[tool.ruff]\nforce-exclude = true\nexclude = "
        + json.dumps([str(victim)])
        + "\n",
        encoding="utf-8",
    )
    prefix = workspace / "x"
    prefix.touch()
    crafted = Path(str(prefix) + separator + str(victim))
    crafted.parent.mkdir(parents=True)
    crafted.write_text("", encoding="utf-8")
    arguments = [str(crafted), str(victim)]
    native = subprocess.run(  # nosec B603 B607 - installed Ruff and controlled fixture paths, shell=False
        ["ruff", "check", "--show-files", *arguments],
        capture_output=True,
        text=True,
        check=False,
        cwd=workspace,
        timeout=30,
    )
    assert native.returncode == 0, native.stderr
    assert native.stdout.splitlines() == [str(prefix), str(victim)]
    plugin = RuffPlugin()
    plugin.set_options(format_check=False, _baseline_coverage=True)
    result = plugin.check(arguments, {})
    assert result.success and result.findings_complete
    assert result.inspected_paths is None

    maintained = runner.invoke(cli, ["baseline", mode, *arguments, "--tools", "ruff"])

    assert maintained.exit_code == 0, maintained.output
    assert baseline.read_bytes() == before
