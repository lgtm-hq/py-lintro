"""Version-one acknowledgements cannot alias files across invocation roots."""

import os
from pathlib import Path

import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro import api
from lintro.cli import cli
from lintro.config.config_loader import clear_config_cache, get_config
from lintro.utils.fingerprint_baseline_file import read_baseline


@pytest.fixture(params=["yaml", "toml", "explicit", "no_config", "absolute", "symlink"])
def rooted_baseline(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> tuple[Path, list[str]]:
    """Acknowledge root a.py, then introduce the identical diagnostic in pkg/a.py."""
    source = request.param
    config = workspace / ".lintro-config.yaml"
    path = workspace / ".lintro-baseline.json"
    if source in {"no_config", "absolute", "symlink", "toml"}:
        config.unlink()
    if source == "toml":
        (workspace / "pyproject.toml").write_text(
            '[tool.lintro]\nbaseline = ".lintro-baseline.json"\n',
            encoding="utf-8",
        )
    elif source in {"yaml", "explicit"}:
        config.write_text(
            config.read_text() + "baseline: .lintro-baseline.json\n",
            encoding="utf-8",
        )
    if source == "explicit":
        path = workspace / "snapshots" / "debt.json"
        path.parent.mkdir()
    flags = [] if source in {"yaml", "toml"} else ["--baseline", str(path)]
    clear_config_cache()
    if source in {"no_config", "absolute", "symlink"}:
        assert_that(get_config().config_path).is_none()
    (workspace / "a.py").write_text("import os\n", encoding="utf-8")
    created = CliRunner().invoke(
        cli,
        ["baseline", "create", "a.py", "--tools", "ruff", *flags],
    )
    assert_that(created.exit_code).described_as(created.output).is_zero()
    assert_that([entry.file for entry in read_baseline(path).entries]).is_equal_to(
        ["a.py"],
    )
    nested = workspace / "pkg"
    nested.mkdir()
    (nested / "a.py").write_text("import os\n", encoding="utf-8")
    if source == "symlink":
        link = nested / "linked-baseline.json"
        link.symlink_to(path)
        flags = ["--baseline", str(link)]
    monkeypatch.chdir(nested)
    clear_config_cache()
    if flags and source not in {"absolute", "symlink"}:
        flags = ["--baseline", os.path.relpath(path, nested)]
    return path, flags


@pytest.mark.parametrize("command", ["chk", "fmt"])
def test_subdirectory_check_cannot_hide_a_new_file(
    rooted_baseline: tuple[Path, list[str]],
    workspace: Path,
    command: str,
) -> None:
    """Upward config discovery and explicit paths both fail before suppression."""
    path, flags = rooted_baseline
    original = path.read_bytes()
    checked = CliRunner().invoke(
        cli,
        [command, "a.py", "--tools", "ruff", "--yes", *flags],
    )
    assert_that(checked.exit_code).is_equal_to(1)
    assert_that(checked.output).contains(
        "require running from the project root",
        str(workspace),
    )
    assert_that(path.read_bytes()).is_equal_to(original)
    assert_that((workspace / "pkg" / "a.py").read_text()).is_equal_to("import os\n")

    # Opting out still checks relative arguments in the invocation directory.
    raw = api.check_run(
        paths=["a.py"],
        tools="ruff",
        baseline=False,
        output_format="json",
        yes=True,
    )
    assert_that(raw.exit_code).is_equal_to(1)
    assert_that(raw.tool_results[0].issues[0].message).contains("os")
    assert_that(raw.tool_results[0].baselined_count).is_none()
    assert_that(raw.workspace_root).is_equal_to(workspace / "pkg")


@pytest.mark.parametrize("mode", ["create", "update", "prune"])
def test_subdirectory_management_cannot_reinterpret_acknowledgements(
    rooted_baseline: tuple[Path, list[str]],
    workspace: Path,
    mode: str,
) -> None:
    """Reject both new snapshots and rewrites rather than acknowledging pkg as root."""
    path, flags = rooted_baseline
    original = path.read_bytes()
    target = path.with_name("new.json") if mode == "create" else path
    if mode == "create":
        flags = ["--baseline", str(target)]
    result = CliRunner().invoke(
        cli,
        ["baseline", mode, "a.py", "--tools", "ruff", *flags],
    )
    assert_that(result.exit_code).is_equal_to(1)
    assert_that(result.output).contains(
        "require running from the project root",
        str(workspace),
    )
    assert_that(path.read_bytes()).is_equal_to(original)
    if mode == "create":
        assert_that(target.exists()).is_false()
    assert_that(list(path.parent.glob("*.lock"))).is_empty()


def test_unconfigured_subdirectory_run_keeps_its_original_scope(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No root requirement or suppression is introduced without baseline opt-in."""
    nested = workspace / "pkg"
    nested.mkdir()
    (nested / "a.py").write_text("import os\n", encoding="utf-8")
    monkeypatch.chdir(nested)
    clear_config_cache()
    result = CliRunner().invoke(
        cli,
        ["chk", "a.py", "--tools", "ruff", "--yes", "--output-format", "json"],
    )
    assert_that(result.exit_code).is_equal_to(1)
    assert_that(result.stdout).contains("F401")
    assert_that(result.output).does_not_contain("require running from the project root")
