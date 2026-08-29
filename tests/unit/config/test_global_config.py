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
session-scoped ``disable_global_config`` fixture in ``tests/conftest.py``,
which sets ``LINTRO_GLOBAL_CONFIG=off`` before session tool discovery runs.
"""

from __future__ import annotations

import json
import os
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


@pytest.mark.parametrize(
    "disable_value",
    ["off", "0", "false", "no", "none", "", "  OFF  "],
)
def test_env_override_disables_global_tier(
    disable_value: str,
    isolated_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every documented disable token turns the global tier off entirely.

    Args:
        disable_value: Value assigned to ``LINTRO_GLOBAL_CONFIG``.
        isolated_home: Isolated fake home directory.
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    (isolated_home / ".lintro-config.yaml").write_text(
        "enforce:\n  line_length: 100\n",
    )
    _make_project(tmp_path, monkeypatch)
    monkeypatch.setenv("LINTRO_GLOBAL_CONFIG", disable_value)

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


def test_env_override_missing_path_fails_closed(
    isolated_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``LINTRO_GLOBAL_CONFIG`` path that is not a file raises.

    Args:
        isolated_home: Isolated fake home directory.
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    (isolated_home / ".lintro-config.yaml").write_text(
        "enforce:\n  line_length: 100\n",
    )
    _make_project(tmp_path, monkeypatch)
    monkeypatch.setenv("LINTRO_GLOBAL_CONFIG", str(tmp_path / "missing.yaml"))

    assert_that(load_config).raises(FileNotFoundError).when_called_with(
        allow_pyproject_fallback=False,
    )


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


def test_load_config_project_tool_mapping_keeps_global_disable(
    isolated_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A project tool mapping without ``enabled`` keeps the global scalar off.

    Args:
        isolated_home: Isolated fake home directory.
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    (isolated_home / ".lintro-config.yaml").write_text("tools:\n  ruff: false\n")
    project = _make_project(tmp_path, monkeypatch)
    (project / ".lintro-config.yaml").write_text(
        "tools:\n  ruff:\n    config_source: ruff.toml\n",
    )

    config = load_config(allow_pyproject_fallback=False)

    assert_that(config.tools["ruff"].enabled).is_false()
    assert_that(config.tools["ruff"].config_source).is_equal_to("ruff.toml")
    assert_that(config.global_contributed_keys).contains("tools.ruff.enabled")


def test_load_config_tool_names_merge_case_insensitively(
    isolated_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mixed-case tool keys across tiers merge into a single entry.

    Args:
        isolated_home: Isolated fake home directory.
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    (isolated_home / ".lintro-config.yaml").write_text("tools:\n  Ruff: false\n")
    project = _make_project(tmp_path, monkeypatch)
    (project / ".lintro-config.yaml").write_text(
        "tools:\n  ruff:\n    config_source: ruff.toml\n",
    )

    config = load_config(allow_pyproject_fallback=False)

    assert_that(config.tools).contains_key("ruff")
    assert_that(config.tools["ruff"].enabled).is_false()
    assert_that(config.tools["ruff"].config_source).is_equal_to("ruff.toml")
    assert_that(config.global_contributed_keys).contains("tools.ruff.enabled")
    assert_that(config.global_contributed_keys).does_not_contain("tools.Ruff")


def test_load_config_defaults_keys_merge_case_insensitively(
    isolated_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mixed-case ``defaults`` keys across tiers merge into a single entry.

    Args:
        isolated_home: Isolated fake home directory.
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    (isolated_home / ".lintro-config.yaml").write_text(
        "defaults:\n  Prettier:\n    tabWidth: 4\n    semi: false\n",
    )
    project = _make_project(tmp_path, monkeypatch)
    (project / ".lintro-config.yaml").write_text(
        "defaults:\n  prettier:\n    singleQuote: true\n",
    )

    config = load_config(allow_pyproject_fallback=False)
    prettier = config.get_tool_defaults("prettier")

    assert_that(prettier.get("tabWidth")).is_equal_to(4)
    assert_that(prettier.get("semi")).is_false()
    assert_that(prettier.get("singleQuote")).is_true()
    assert_that(config.global_contributed_keys).contains("defaults.prettier.tabWidth")
    assert_that(config.global_contributed_keys).does_not_contain("defaults.Prettier")


def test_load_config_project_tool_mapping_may_re_enable(
    isolated_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit project ``enabled`` still wins over the global scalar.

    Args:
        isolated_home: Isolated fake home directory.
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    (isolated_home / ".lintro-config.yaml").write_text("tools:\n  ruff: false\n")
    project = _make_project(tmp_path, monkeypatch)
    (project / ".lintro-config.yaml").write_text(
        "tools:\n  ruff:\n    enabled: true\n",
    )

    config = load_config(allow_pyproject_fallback=False)

    assert_that(config.tools["ruff"].enabled).is_true()
    assert_that(config.global_contributed_keys).does_not_contain("tools.ruff.enabled")


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


def test_disabled_global_tier_ignores_home_dotfile_for_nested_project(
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``off`` keeps the home dotfile out of project discovery too.

    Disabling the global tier must be hermetic: the home dotfile may not come
    back as a *project* config for a cwd nested under home, which would
    reinstate the developer defaults CI asked to exclude.

    Args:
        isolated_home: Isolated fake home directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    (isolated_home / ".lintro-config.yaml").write_text(
        "enforce:\n  line_length: 100\nai:\n  enabled: true\n",
    )
    _make_project(isolated_home, monkeypatch)
    monkeypatch.setenv("LINTRO_GLOBAL_CONFIG", "off")

    config = load_config(allow_pyproject_fallback=False)

    assert_that(config.global_config_path).is_none()
    assert_that(config.config_path).is_none()
    assert_that(config.enforce.line_length).is_none()
    assert_that(config.ai).is_equal_to({})
    assert_that(config.global_contributed_keys).is_equal_to([])


def test_env_override_path_ignores_home_dotfile_for_nested_project(
    isolated_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit env path does not demote the home dotfile to a project config.

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
    _make_project(isolated_home, monkeypatch)
    monkeypatch.setenv("LINTRO_GLOBAL_CONFIG", str(override))

    config = load_config(allow_pyproject_fallback=False)

    assert_that(config.global_config_path).is_equal_to(str(override.resolve()))
    assert_that(config.config_path).is_none()
    assert_that(config.enforce.line_length).is_equal_to(55)


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


def test_load_config_reports_only_effective_contributions(
    isolated_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Global keys the section parsers drop are not reported as contributions.

    Args:
        isolated_home: Isolated fake home directory.
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    (isolated_home / ".lintro-config.yaml").write_text(
        "output:\n"
        "  art: false\n"
        "  typo: true\n"
        "enforce:\n"
        "  line_length: 100\n"
        "  bogus: 1\n"
        "tools:\n"
        "  ruff:\n"
        "    enabled: true\n"
        "    select:\n"
        "      - E\n"
        "review:\n"
        "  bogus: true\n"
        "  checklist:\n"
        "    items: []\n"
        "licenses:\n"
        "  allow:\n"
        "    - MIT\n",
    )
    _make_project(tmp_path, monkeypatch)

    config = load_config(allow_pyproject_fallback=False)

    assert_that(config.output.art).is_false()
    assert_that(config.global_contributed_keys).contains(
        "output.art",
        "enforce.line_length",
        "tools.ruff.enabled",
        # Nested model fields are walked segment by segment, not just level one.
        "review.checklist.items",
    )
    # Unknown leaves and sections load_config never applies are filtered out.
    assert_that(config.global_contributed_keys).does_not_contain(
        "output.typo",
        "enforce.bogus",
        "tools.ruff.select",
        "review.bogus",
        "licenses.allow",
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
    # The row label matches the ``contributed_keys`` field in --json output.
    assert_that(result.output).contains("contributed_keys")
    assert_that(result.output).does_not_contain("contributed_values")


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


def test_config_command_global_only_is_not_reported_as_defaults(
    isolated_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A global-only config is named as the source instead of "defaults".

    Args:
        isolated_home: Isolated fake home directory.
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    home_config = isolated_home / ".lintro-config.yaml"
    home_config.write_text("enforce:\n  line_length: 100\n")
    _make_project(tmp_path, monkeypatch)

    runner = CliRunner()
    result = runner.invoke(cli, ["config", "--json"])

    assert_that(result.exit_code).is_equal_to(0)
    payload = json.loads(result.output)
    assert_that(payload["config_source"]).is_equal_to(str(home_config.resolve()))


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


def test_disabled_global_tier_hides_home_plugins_section(
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``LINTRO_GLOBAL_CONFIG=off`` must not feed plugins: from the home file.

    Args:
        isolated_home: Isolated fake home directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from lintro.plugins.discovery import (
        ENV_ENABLE_EXTERNAL_PLUGINS,
        _resolve_plugin_trust,
    )

    (isolated_home / ".lintro-config.yaml").write_text(
        "plugins:\n  trusted:\n    - evil-plugin\n",
    )
    _make_project(isolated_home, monkeypatch)
    monkeypatch.setenv("LINTRO_GLOBAL_CONFIG", "off")
    monkeypatch.delenv(ENV_ENABLE_EXTERNAL_PLUGINS, raising=False)

    enabled, trusted = _resolve_plugin_trust()

    assert_that(enabled).is_false()
    assert_that(trusted).is_none()


def test_disabled_global_tier_hides_home_licenses_section(
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``LINTRO_GLOBAL_CONFIG=off`` must not feed licenses: from the home file.

    Args:
        isolated_home: Isolated fake home directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from lintro.config.licenses_config import load_licenses_config

    (isolated_home / ".lintro-config.yaml").write_text(
        "licenses:\n  policy: strict\n",
    )
    _make_project(isolated_home, monkeypatch)
    monkeypatch.setenv("LINTRO_GLOBAL_CONFIG", "off")

    config = load_licenses_config()

    assert_that(config.policy).is_equal_to("permissive")


def test_home_global_file_supplies_plugins_trust(
    isolated_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The home global file is the plugins: base tier, not a project config.

    Args:
        isolated_home: Isolated fake home directory.
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from lintro.plugins.discovery import (
        ENV_ENABLE_EXTERNAL_PLUGINS,
        _resolve_plugin_trust,
    )

    (isolated_home / ".lintro-config.yaml").write_text(
        "plugins:\n  trusted:\n    - home-plugin\n",
    )
    _make_project(tmp_path, monkeypatch)
    monkeypatch.delenv(ENV_ENABLE_EXTERNAL_PLUGINS, raising=False)

    enabled, trusted = _resolve_plugin_trust()

    assert_that(enabled).is_true()
    assert_that(trusted).is_equal_to(frozenset({"home-plugin"}))


def test_explicit_global_path_supplies_plugins_not_home_dotfile(
    isolated_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit LINTRO_GLOBAL_CONFIG path wins over the home plugins: list.

    Args:
        isolated_home: Isolated fake home directory.
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from lintro.plugins.discovery import (
        ENV_ENABLE_EXTERNAL_PLUGINS,
        _resolve_plugin_trust,
    )

    (isolated_home / ".lintro-config.yaml").write_text(
        "plugins:\n  trusted:\n    - home-plugin\n",
    )
    custom = tmp_path / "custom-global.yaml"
    custom.write_text("plugins:\n  trusted:\n    - env-plugin\n")
    monkeypatch.setenv("LINTRO_GLOBAL_CONFIG", str(custom))
    _make_project(isolated_home, monkeypatch)
    monkeypatch.delenv(ENV_ENABLE_EXTERNAL_PLUGINS, raising=False)

    enabled, trusted = _resolve_plugin_trust()

    assert_that(enabled).is_true()
    assert_that(trusted).is_equal_to(frozenset({"env-plugin"}))


def test_xdg_global_file_supplies_plugins_trust(
    isolated_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The XDG global file supplies plugins: when the home dotfile is absent.

    Args:
        isolated_home: Isolated fake home directory.
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from lintro.plugins.discovery import (
        ENV_ENABLE_EXTERNAL_PLUGINS,
        _resolve_plugin_trust,
    )

    xdg = isolated_home / ".config"
    (xdg / "lintro").mkdir(parents=True)
    (xdg / "lintro" / "config.yaml").write_text(
        "plugins:\n  trusted:\n    - xdg-plugin\n",
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    _make_project(tmp_path, monkeypatch)
    monkeypatch.delenv(ENV_ENABLE_EXTERNAL_PLUGINS, raising=False)

    enabled, trusted = _resolve_plugin_trust()

    assert_that(enabled).is_true()
    assert_that(trusted).is_equal_to(frozenset({"xdg-plugin"}))


def test_project_plugins_overlay_global_trusted_list(
    isolated_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A project plugins: trusted list replaces the global list wholesale.

    Args:
        isolated_home: Isolated fake home directory.
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from lintro.plugins.discovery import (
        ENV_ENABLE_EXTERNAL_PLUGINS,
        _resolve_plugin_trust,
    )

    (isolated_home / ".lintro-config.yaml").write_text(
        "plugins:\n  trusted:\n    - global-plugin\n",
    )
    project = _make_project(tmp_path, monkeypatch)
    (project / ".lintro-config.yaml").write_text(
        "plugins:\n  trusted:\n    - project-plugin\n",
    )
    monkeypatch.delenv(ENV_ENABLE_EXTERNAL_PLUGINS, raising=False)

    enabled, trusted = _resolve_plugin_trust()

    assert_that(enabled).is_true()
    assert_that(trusted).is_equal_to(frozenset({"project-plugin"}))


def test_project_plugins_disabled_overrides_global_trusted(
    isolated_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Project ``plugins.enabled: false`` disables external plugins globally.

    Deep merge keeps the global ``trusted`` allowlist, but an explicit project
    disable must win so inherited trust cannot opt back in.

    Args:
        isolated_home: Isolated fake home directory.
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from lintro.plugins.discovery import (
        ENV_ENABLE_EXTERNAL_PLUGINS,
        _resolve_plugin_trust,
    )

    (isolated_home / ".lintro-config.yaml").write_text(
        "plugins:\n  trusted:\n    - global-plugin\n",
    )
    project = _make_project(tmp_path, monkeypatch)
    (project / ".lintro-config.yaml").write_text(
        "plugins:\n  enabled: false\n",
    )
    monkeypatch.delenv(ENV_ENABLE_EXTERNAL_PLUGINS, raising=False)

    enabled, trusted = _resolve_plugin_trust()

    assert_that(enabled).is_false()
    assert_that(trusted).is_none()


@pytest.mark.parametrize(
    "home_filename",
    [
        ".lintro-config.yml",
        "lintro-config.yaml",
        "lintro-config.yml",
    ],
)
def test_disabled_global_tier_ignores_home_filename_variants_for_nested_project(
    home_filename: str,
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``off`` excludes every home-root global filename from project discovery.

    Args:
        home_filename: Basename of the home-level config file under test.
        isolated_home: Isolated fake home directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    (isolated_home / home_filename).write_text(
        "enforce:\n  line_length: 100\nai:\n  enabled: true\n",
    )
    _make_project(isolated_home, monkeypatch)
    monkeypatch.setenv("LINTRO_GLOBAL_CONFIG", "off")

    config = load_config(allow_pyproject_fallback=False)

    assert_that(config.global_config_path).is_none()
    assert_that(config.config_path).is_none()
    assert_that(config.enforce.line_length).is_none()
    assert_that(config.ai).is_equal_to({})
    assert_that(config.global_contributed_keys).is_equal_to([])


def test_explicit_global_path_supplies_licenses_not_home_dotfile(
    isolated_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit LINTRO_GLOBAL_CONFIG path wins over the home licenses: policy.

    Args:
        isolated_home: Isolated fake home directory.
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from lintro.config.licenses_config import load_licenses_config

    (isolated_home / ".lintro-config.yaml").write_text(
        "licenses:\n  policy: strict\n",
    )
    custom = tmp_path / "custom-global.yaml"
    custom.write_text("licenses:\n  policy: copyleft-ok\n")
    monkeypatch.setenv("LINTRO_GLOBAL_CONFIG", str(custom))
    _make_project(isolated_home, monkeypatch)

    config = load_licenses_config()

    assert_that(config.policy).is_equal_to("copyleft-ok")


def test_xdg_global_file_supplies_licenses_policy(
    isolated_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The XDG global file supplies licenses: when the home dotfile is absent.

    Args:
        isolated_home: Isolated fake home directory.
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from lintro.config.licenses_config import load_licenses_config

    xdg = isolated_home / ".config"
    (xdg / "lintro").mkdir(parents=True)
    (xdg / "lintro" / "config.yaml").write_text("licenses:\n  policy: strict\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    _make_project(tmp_path, monkeypatch)

    config = load_licenses_config()

    assert_that(config.policy).is_equal_to("strict")


def test_project_licenses_overlay_global_policy(
    isolated_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A project licenses: section overlays the global policy and lists.

    Args:
        isolated_home: Isolated fake home directory.
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from lintro.config.licenses_config import load_licenses_config

    (isolated_home / ".lintro-config.yaml").write_text(
        "licenses:\n  policy: strict\n  allowed:\n    - MIT\n",
    )
    project = _make_project(tmp_path, monkeypatch)
    (project / ".lintro-config.yaml").write_text(
        "licenses:\n  unknown_policy: deny\n",
    )

    config = load_licenses_config()

    assert_that(config.policy).is_equal_to("strict")
    assert_that(config.allowed).is_equal_to(["MIT"])
    assert_that(config.unknown_policy).is_equal_to("deny")


def test_chk_tool_selection_uses_global_config_when_env_unset(
    isolated_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unset LINTRO_GLOBAL_CONFIG still applies home-file tool scoping to chk.

    Args:
        isolated_home: Isolated fake home directory.
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    from lintro.utils.execution.tool_configuration import get_tools_to_run

    (isolated_home / ".lintro-config.yaml").write_text("tools:\n  ruff: false\n")
    _make_project(tmp_path, monkeypatch)

    assert_that("LINTRO_GLOBAL_CONFIG" in os.environ).is_false()

    result = get_tools_to_run(tools="ruff", action="check")
    skipped_by_name = {item.name: item.reason for item in result.skipped}

    assert_that(result.to_run).does_not_contain("ruff")
    assert_that(skipped_by_name).contains_key("ruff")
    assert_that(skipped_by_name["ruff"]).is_equal_to("disabled in config")
