"""Tests for user-level global config loading and merge (#1235).

These tests exercise the ``~/.lintro-config.yaml`` global tier and its
deep-merge precedence with project config. Every test that touches the
user-level path monkeypatches ``Path.home`` and ``$XDG_CONFIG_HOME`` so the
real user home directory is never read.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli import cli
from lintro.config.config_loader import (
    _deep_merge,
    _find_global_config_file,
    _global_contributed_paths,
    clear_config_cache,
    load_config,
)


@pytest.fixture
def isolated_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Isolate the home and XDG dirs and clear the config cache.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        Path: The isolated fake home directory.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    clear_config_cache()
    return home


def _make_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create and change into an isolated project directory.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        Path: The project directory (also the new cwd).
    """
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    return project


# =============================================================================
# _deep_merge
# =============================================================================


def test_deep_merge_overrides_leaf_and_recurses() -> None:
    """Deep merge overrides leaves while merging nested mappings key-by-key."""
    base = {
        "enforce": {"line_length": 100, "target_python": "py311"},
        "ai": {"enabled": True, "provider": "anthropic"},
    }
    override = {
        "enforce": {"line_length": 120},
        "ai": {"enabled": False},
    }

    merged = _deep_merge(base=base, override=override)

    assert_that(merged["enforce"]["line_length"]).is_equal_to(120)
    assert_that(merged["enforce"]["target_python"]).is_equal_to("py311")
    assert_that(merged["ai"]["enabled"]).is_false()
    assert_that(merged["ai"]["provider"]).is_equal_to("anthropic")


def test_deep_merge_does_not_mutate_inputs() -> None:
    """Deep merge returns a new mapping without mutating its inputs."""
    base = {"tools": {"ruff": True}}
    override = {"tools": {"black": False}}

    merged = _deep_merge(base=base, override=override)

    assert_that(merged["tools"]).is_equal_to({"ruff": True, "black": False})
    assert_that(base["tools"]).is_equal_to({"ruff": True})
    assert_that(override["tools"]).is_equal_to({"black": False})


def test_deep_merge_list_replaces_wholesale() -> None:
    """A list value in the override replaces the base value entirely."""
    base = {"execution": {"enabled_tools": ["ruff", "black"]}}
    override = {"execution": {"enabled_tools": ["prettier"]}}

    merged = _deep_merge(base=base, override=override)

    assert_that(merged["execution"]["enabled_tools"]).is_equal_to(["prettier"])


# =============================================================================
# _global_contributed_paths
# =============================================================================


def test_global_contributed_paths_reports_unoverridden_leaves() -> None:
    """Only global leaves the project does not override are reported."""
    global_data = {
        "enforce": {"line_length": 100, "target_python": "py311"},
        "ai": {"enabled": True},
    }
    project_data = {"enforce": {"line_length": 120}}

    contributed = _global_contributed_paths(
        global_data=global_data,
        project_data=project_data,
    )

    assert_that(contributed).is_equal_to(["ai.enabled", "enforce.target_python"])


def test_global_contributed_paths_skips_scalar_tool_override() -> None:
    """A scalar project tool override must not report global tool children."""
    global_data = {
        "tools": {"ruff": {"select": ["E"], "ignore": ["E501"]}},
        "ai": {"enabled": True},
    }
    project_data = {"tools": {"ruff": False}}

    contributed = _global_contributed_paths(
        global_data=global_data,
        project_data=project_data,
    )

    assert_that(contributed).is_equal_to(["ai.enabled"])
    assert_that(contributed).does_not_contain("tools.ruff.select")
    assert_that(contributed).does_not_contain("tools.ruff.ignore")


# =============================================================================
# _find_global_config_file
# =============================================================================


def test_find_global_config_none_when_absent(isolated_home: Path) -> None:
    """No global config file is resolved when neither location exists.

    Args:
        isolated_home: Isolated fake home directory.
    """
    assert_that(_find_global_config_file()).is_none()


def test_find_global_config_prefers_home_dotfile(
    isolated_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The home dotfile wins over the XDG fallback when both exist.

    Args:
        isolated_home: Isolated fake home directory.
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    home_config = isolated_home / ".lintro-config.yaml"
    home_config.write_text("enforce:\n  line_length: 100\n")

    xdg_home = tmp_path / "xdg"
    xdg_dir = xdg_home / "lintro"
    xdg_dir.mkdir(parents=True)
    (xdg_dir / "config.yaml").write_text("enforce:\n  line_length: 77\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_home))

    resolved = _find_global_config_file()

    assert_that(str(resolved)).is_equal_to(str(home_config))


def test_find_global_config_xdg_fallback(
    isolated_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The XDG fallback is used when no home dotfile exists.

    Args:
        isolated_home: Isolated fake home directory.
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    xdg_home = tmp_path / "xdg"
    xdg_dir = xdg_home / "lintro"
    xdg_dir.mkdir(parents=True)
    xdg_config = xdg_dir / "config.yaml"
    xdg_config.write_text("enforce:\n  line_length: 77\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_home))

    resolved = _find_global_config_file()

    assert_that(str(resolved)).is_equal_to(str(xdg_config))


# =============================================================================
# load_config: precedence combinations
# =============================================================================


def test_load_config_neither_uses_defaults(
    isolated_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no global and no project config, defaults apply.

    Args:
        isolated_home: Isolated fake home directory.
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    _make_project(tmp_path, monkeypatch)

    config = load_config(allow_pyproject_fallback=False)

    assert_that(config.enforce.line_length).is_none()
    assert_that(config.global_config_path).is_none()
    assert_that(config.global_contributed_keys).is_equal_to([])


def test_load_config_global_only(
    isolated_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Global-only config supplies values and records its path.

    Args:
        isolated_home: Isolated fake home directory.
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    (isolated_home / ".lintro-config.yaml").write_text(
        "enforce:\n  line_length: 100\nai:\n  enabled: true\n",
    )
    _make_project(tmp_path, monkeypatch)

    config = load_config(allow_pyproject_fallback=False)

    assert_that(config.enforce.line_length).is_equal_to(100)
    assert_that(config.ai.enabled).is_true()
    assert_that(config.global_config_path).is_not_none()
    assert_that(config.config_path).is_none()
    assert_that(config.global_contributed_keys).contains("enforce.line_length")
    assert_that(config.global_contributed_keys).contains("ai.enabled")


def test_load_config_project_only(
    isolated_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Project-only config behaves as before with no global contribution.

    Args:
        isolated_home: Isolated fake home directory.
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    project = _make_project(tmp_path, monkeypatch)
    (project / ".lintro-config.yaml").write_text("enforce:\n  line_length: 120\n")

    config = load_config(allow_pyproject_fallback=False)

    assert_that(config.enforce.line_length).is_equal_to(120)
    assert_that(config.global_config_path).is_none()
    assert_that(config.global_contributed_keys).is_equal_to([])


def test_load_config_both_project_wins_and_deep_merges(
    isolated_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Project overrides per key while global fills nested gaps (ai, tools).

    Args:
        isolated_home: Isolated fake home directory.
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    (isolated_home / ".lintro-config.yaml").write_text(
        "enforce:\n"
        "  line_length: 100\n"
        "  target_python: py311\n"
        "ai:\n"
        "  enabled: true\n"
        "  provider: anthropic\n"
        "tools:\n"
        "  ruff: true\n",
    )
    project = _make_project(tmp_path, monkeypatch)
    (project / ".lintro-config.yaml").write_text(
        "enforce:\n"
        "  line_length: 120\n"
        "ai:\n"
        "  enabled: false\n"
        "tools:\n"
        "  black: false\n",
    )

    config = load_config(allow_pyproject_fallback=False)

    # Project wins per key.
    assert_that(config.enforce.line_length).is_equal_to(120)
    assert_that(config.ai.enabled).is_false()
    # Global fills nested keys the project did not override.
    assert_that(config.enforce.target_python).is_equal_to("py311")
    assert_that(config.ai.provider).is_equal_to("anthropic")
    # Both tool entries survive the deep merge.
    assert_that(config.tools).contains_key("ruff")
    assert_that(config.tools).contains_key("black")
    assert_that(config.tools["ruff"].enabled).is_true()
    assert_that(config.tools["black"].enabled).is_false()
    # Contribution tracking reflects only unoverridden global leaves.
    assert_that(config.global_contributed_keys).contains(
        "enforce.target_python",
        "ai.provider",
        "tools.ruff",
    )
    assert_that(config.global_contributed_keys).does_not_contain(
        "enforce.line_length",
        "ai.enabled",
    )


def test_load_config_malformed_global_raises(
    isolated_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Malformed global YAML surfaces a parse error rather than silent success.

    Args:
        isolated_home: Isolated fake home directory.
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    import yaml

    (isolated_home / ".lintro-config.yaml").write_text(
        "enforce:\n  line_length: [unterminated\n",
    )
    _make_project(tmp_path, monkeypatch)

    assert_that(load_config).raises(yaml.YAMLError).when_called_with(
        allow_pyproject_fallback=False,
    )


def test_load_config_empty_global_is_not_an_error(
    isolated_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty global file is tolerated and contributes nothing.

    Args:
        isolated_home: Isolated fake home directory.
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    (isolated_home / ".lintro-config.yaml").write_text("")
    _make_project(tmp_path, monkeypatch)

    config = load_config(allow_pyproject_fallback=False)

    assert_that(config.global_config_path).is_not_none()
    assert_that(config.global_contributed_keys).is_equal_to([])
    assert_that(config.enforce.line_length).is_none()


# =============================================================================
# lintro config command: Global Config section
# =============================================================================


def test_config_command_shows_global_config_section(
    isolated_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ``lintro config`` output includes a Global Config section.

    Args:
        isolated_home: Isolated fake home directory.
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    (isolated_home / ".lintro-config.yaml").write_text(
        "enforce:\n  line_length: 100\n",
    )
    _make_project(tmp_path, monkeypatch)

    runner = CliRunner()
    result = runner.invoke(cli, ["config"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).contains("Global Config")
    assert_that(result.output).contains(".lintro-config.yaml")
    assert_that(result.output).contains("enforce.line_length")


def test_config_command_json_reports_global_config(
    isolated_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The JSON output exposes global config discovery details.

    Args:
        isolated_home: Isolated fake home directory.
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    import json

    (isolated_home / ".lintro-config.yaml").write_text(
        "enforce:\n  line_length: 100\n",
    )
    _make_project(tmp_path, monkeypatch)

    runner = CliRunner()
    result = runner.invoke(cli, ["config", "--json"])

    assert_that(result.exit_code).is_equal_to(0)
    payload = json.loads(result.output)
    assert_that(payload["global_config"]["found"]).is_true()
    assert_that(payload["global_config"]["path"]).is_not_none()
    assert_that(payload["global_config"]["contributed_keys"]).contains(
        "enforce.line_length",
    )


def test_config_command_json_no_global(
    isolated_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The JSON output reports no global config when none exists.

    Args:
        isolated_home: Isolated fake home directory.
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    import json

    _make_project(tmp_path, monkeypatch)

    runner = CliRunner()
    result = runner.invoke(cli, ["config", "--json"])

    assert_that(result.exit_code).is_equal_to(0)
    payload = json.loads(result.output)
    assert_that(payload["global_config"]["found"]).is_false()
    assert_that(payload["global_config"]["path"]).is_none()
