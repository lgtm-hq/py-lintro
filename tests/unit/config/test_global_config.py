"""Tests for user-level global config loading and merge (#1235).

These tests exercise the ``~/.lintro-config.yaml`` global tier and its
deep-merge precedence with project config through the public contract only:
``load_config()`` and the ``lintro config`` command. Merge, discovery and
contribution-tracking behavior is observed via ``LintroConfig`` fields
(``config_path``, ``global_config_path``, ``global_contributed_keys`` and the
parsed sections) rather than by importing private loader helpers, so renaming
or inlining those helpers cannot break this suite.

Every test monkeypatches ``Path.home`` and ``$XDG_CONFIG_HOME`` so the real
user home directory is never read. The rest of the suite is isolated by the
autouse ``disable_global_config`` fixture in ``tests/conftest.py``, which sets
``LINTRO_GLOBAL_CONFIG=off``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli import cli
from lintro.config.config_loader import clear_config_cache, load_config


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
    monkeypatch.delenv("LINTRO_GLOBAL_CONFIG", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    clear_config_cache()
    return home


def _make_project(parent: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create and change into an isolated project directory.

    Args:
        parent: Directory to create the project directory inside. Pass the
            fake home to exercise a project nested under the home directory.
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        Path: The project directory (also the new cwd).
    """
    project = parent / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    return project


# =============================================================================
# Global config discovery (observed via LintroConfig.global_config_path)
# =============================================================================


def test_no_global_config_when_absent(
    isolated_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No global config is resolved when neither location exists.

    Args:
        isolated_home: Isolated fake home directory.
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    _make_project(tmp_path, monkeypatch)

    config = load_config(allow_pyproject_fallback=False)

    assert_that(config.global_config_path).is_none()


def test_home_dotfile_preferred_over_xdg(
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
    _make_project(tmp_path, monkeypatch)

    config = load_config(allow_pyproject_fallback=False)

    assert_that(config.global_config_path).is_equal_to(str(home_config.resolve()))
    assert_that(config.enforce.line_length).is_equal_to(100)


def test_xdg_fallback_used_when_no_home_dotfile(
    isolated_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ``$XDG_CONFIG_HOME`` fallback is used when no home dotfile exists.

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
    _make_project(tmp_path, monkeypatch)

    config = load_config(allow_pyproject_fallback=False)

    assert_that(config.global_config_path).is_equal_to(str(xdg_config.resolve()))
    assert_that(config.enforce.line_length).is_equal_to(77)


def test_default_xdg_dir_used_when_env_unset(
    isolated_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``~/.config/lintro/config.yaml`` is used when ``$XDG_CONFIG_HOME`` is unset.

    Args:
        isolated_home: Isolated fake home directory.
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    xdg_dir = isolated_home / ".config" / "lintro"
    xdg_dir.mkdir(parents=True)
    xdg_config = xdg_dir / "config.yaml"
    xdg_config.write_text("enforce:\n  line_length: 66\n")
    _make_project(tmp_path, monkeypatch)

    config = load_config(allow_pyproject_fallback=False)

    assert_that(config.global_config_path).is_equal_to(str(xdg_config.resolve()))
    assert_that(config.enforce.line_length).is_equal_to(66)


def test_env_override_disables_global_tier(
    isolated_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``LINTRO_GLOBAL_CONFIG=off`` disables the global tier entirely.

    Args:
        isolated_home: Isolated fake home directory.
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    (isolated_home / ".lintro-config.yaml").write_text(
        "enforce:\n  line_length: 100\n",
    )
    _make_project(tmp_path, monkeypatch)
    monkeypatch.setenv("LINTRO_GLOBAL_CONFIG", "off")

    config = load_config(allow_pyproject_fallback=False)

    assert_that(config.global_config_path).is_none()
    assert_that(config.enforce.line_length).is_none()


def test_env_override_selects_explicit_path(
    isolated_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``LINTRO_GLOBAL_CONFIG`` points the global tier at an explicit file.

    Args:
        isolated_home: Isolated fake home directory.
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    (isolated_home / ".lintro-config.yaml").write_text(
        "enforce:\n  line_length: 100\n",
    )
    override = tmp_path / "override.yaml"
    override.write_text("enforce:\n  line_length: 55\n")
    _make_project(tmp_path, monkeypatch)
    monkeypatch.setenv("LINTRO_GLOBAL_CONFIG", str(override))

    config = load_config(allow_pyproject_fallback=False)

    assert_that(config.global_config_path).is_equal_to(str(override.resolve()))
    assert_that(config.enforce.line_length).is_equal_to(55)


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
    assert_that(config.ai.get("enabled")).is_true()
    assert_that(config.global_config_path).is_not_none()
    assert_that(config.config_path).is_none()
    assert_that(config.global_contributed_keys).is_equal_to(
        ["ai.enabled", "enforce.line_length"],
    )


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
    assert_that(config.ai.get("enabled")).is_false()
    # Global fills nested keys the project did not override.
    assert_that(config.enforce.target_python).is_equal_to("py311")
    assert_that(config.ai.get("provider")).is_equal_to("anthropic")
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


def test_load_config_project_list_replaces_global_list(
    isolated_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A list in the project config replaces the global list wholesale.

    Args:
        isolated_home: Isolated fake home directory.
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    (isolated_home / ".lintro-config.yaml").write_text(
        "execution:\n  enabled_tools:\n    - ruff\n    - black\n",
    )
    project = _make_project(tmp_path, monkeypatch)
    (project / ".lintro-config.yaml").write_text(
        "execution:\n  enabled_tools:\n    - prettier\n",
    )

    config = load_config(allow_pyproject_fallback=False)

    assert_that(config.execution.enabled_tools).is_equal_to(["prettier"])
    assert_that(config.global_contributed_keys).does_not_contain(
        "execution.enabled_tools",
    )


def test_load_config_scalar_tool_override_hides_global_tool_children(
    isolated_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A scalar project tool override must not report global tool children.

    Args:
        isolated_home: Isolated fake home directory.
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    (isolated_home / ".lintro-config.yaml").write_text(
        "tools:\n"
        "  ruff:\n"
        "    enabled: true\n"
        "    config_source: ruff.toml\n"
        "ai:\n"
        "  enabled: true\n",
    )
    project = _make_project(tmp_path, monkeypatch)
    (project / ".lintro-config.yaml").write_text("tools:\n  ruff: false\n")

    config = load_config(allow_pyproject_fallback=False)

    assert_that(config.tools["ruff"].enabled).is_false()
    assert_that(config.tools["ruff"].config_source).is_none()
    assert_that(config.global_contributed_keys).is_equal_to(["ai.enabled"])


def test_load_config_global_merges_with_pyproject_project_tier(
    isolated_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``[tool.lintro]`` project tier deep-merges over the global config.

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
        "  enabled: true\n",
    )
    project = _make_project(tmp_path, monkeypatch)
    pyproject = project / "pyproject.toml"
    pyproject.write_text(
        '[project]\nname = "demo"\nversion = "0.0.0"\n\n'
        "[tool.lintro]\nline_length = 120\n",
    )

    config = load_config()

    # Project (pyproject) wins on the key it sets.
    assert_that(config.enforce.line_length).is_equal_to(120)
    assert_that(config.config_path).is_equal_to(str(pyproject.resolve()))
    # Global still fills the keys pyproject leaves out.
    assert_that(config.enforce.target_python).is_equal_to("py311")
    assert_that(config.ai.get("enabled")).is_true()
    assert_that(config.global_contributed_keys).contains(
        "ai.enabled",
        "enforce.target_python",
    )
    assert_that(config.global_contributed_keys).does_not_contain(
        "enforce.line_length",
    )


def test_load_config_project_nested_under_home_uses_file_once(
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A project under home does not adopt the global file as its project tier.

    The upward ``.lintro-config.yaml`` search reaches ``~/.lintro-config.yaml``
    when the project lives inside the home directory. That single file must be
    reported as the global tier only; counting it as the project tier as well
    would zero out ``global_contributed_keys`` and report the same path twice.

    Args:
        isolated_home: Isolated fake home directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    home_config = isolated_home / ".lintro-config.yaml"
    home_config.write_text(
        "enforce:\n  line_length: 100\nai:\n  enabled: true\n",
    )
    _make_project(isolated_home, monkeypatch)

    config = load_config(allow_pyproject_fallback=False)

    assert_that(config.enforce.line_length).is_equal_to(100)
    assert_that(config.global_config_path).is_equal_to(str(home_config.resolve()))
    assert_that(config.config_path).is_none()
    assert_that(config.global_contributed_keys).is_equal_to(
        ["ai.enabled", "enforce.line_length"],
    )


def test_load_config_project_nested_under_home_with_own_config(
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A nested project's own config still wins over the home dotfile.

    Args:
        isolated_home: Isolated fake home directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    (isolated_home / ".lintro-config.yaml").write_text(
        "enforce:\n  line_length: 100\n  target_python: py311\n",
    )
    project = _make_project(isolated_home, monkeypatch)
    project_config = project / ".lintro-config.yaml"
    project_config.write_text("enforce:\n  line_length: 120\n")

    config = load_config(allow_pyproject_fallback=False)

    assert_that(config.enforce.line_length).is_equal_to(120)
    assert_that(config.enforce.target_python).is_equal_to("py311")
    assert_that(config.config_path).is_equal_to(str(project_config.resolve()))
    assert_that(config.global_contributed_keys).is_equal_to(
        ["enforce.target_python"],
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
    _make_project(tmp_path, monkeypatch)

    runner = CliRunner()
    result = runner.invoke(cli, ["config", "--json"])

    assert_that(result.exit_code).is_equal_to(0)
    payload = json.loads(result.output)
    assert_that(payload["global_config"]["found"]).is_false()
    assert_that(payload["global_config"]["path"]).is_none()
