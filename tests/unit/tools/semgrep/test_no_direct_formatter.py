"""Keep the opt-in script pack aligned with declared formatting capabilities."""

from __future__ import annotations

import shlex
from pathlib import Path

import pytest
import yaml
from assertpy import assert_that

import lintro.rules
from lintro.enums.capability import Cap
from lintro.exceptions.errors import ConfigurationError
from lintro.plugins.file_discovery import discover_files
from lintro.plugins.registry import ToolRegistry
from lintro.rules import available_rule_packs, resolve_rule_pack, rule_pack_info
from lintro.tools.semgrep.definition import (
    SEMGREP_FILE_PATTERNS,
    SemgrepPlugin,
    _unanchored_pack_warning,
)

PACK = "scripts/no-direct-formatter"
OTHER_PACK = "python/none-coercion"
RUNNER_TOKENS = {"bunx", "npx", "uvx", "--yes", "uv", "tool", "run"}


def _invoked_formatter(command: str) -> str:
    """Name the executable a literal example runs once runner prefixes go.

    Args:
        command: Example command line from the pack metadata.

    Returns:
        The executable name without any ``@version`` pin.
    """
    tokens = shlex.split(command)
    while tokens and tokens[0] in RUNNER_TOKENS:
        tokens.pop(0)
    return tokens[0].partition("@")[0] if tokens else ""


def _pack_examples() -> dict[str, str]:
    """Load the formatter examples declared in the pack metadata.

    Returns:
        Example command per formatter name.
    """
    rules = yaml.safe_load(resolve_rule_pack(PACK).read_text(encoding="utf-8"))["rules"]
    return {
        name: command
        for rule in rules
        for name, command in rule["metadata"]["lintro_formatters"].items()
    }


@pytest.mark.parametrize(
    ("command", "formatter"),
    [
        ("black x.py", "black"),
        ("uvx ruff@0.6.0 format x.py", "ruff"),
        ("bunx --yes prettier@3 --write .", "prettier"),
        ("uv tool run black x.py", "black"),
    ],
)
def test_invoked_formatter_reads_past_runners_and_pins(
    command: str,
    formatter: str,
) -> None:
    """The example check names the formatter, so a swapped example cannot pass.

    Args:
        command: Literal command line.
        formatter: Executable it runs.
    """
    assert_that(_invoked_formatter(command)).is_equal_to(formatter)


@pytest.mark.parametrize(("name", "command"), sorted(_pack_examples().items()))
def test_each_example_runs_its_own_formatter(name: str, command: str) -> None:
    """A ``ruff:`` entry holding ``black x.py`` would match but prove nothing.

    Args:
        name: Formatter key in the pack metadata.
        command: Example command declared for that key.
    """
    assert_that(_invoked_formatter(command)).described_as(name).is_equal_to(name)


def test_pack_metadata_drives_discovery_and_anchoring() -> None:
    """Pack flags come from YAML, not from a pack name hard-coded in the plugin."""
    scripts = rule_pack_info(PACK)
    other = rule_pack_info(OTHER_PACK)
    assert_that(scripts.discover_all_files).is_true()
    assert_that(scripts.anchors_directories).is_true()
    assert_that(scripts.rule_ids).contains(
        "lintro.scripts.no-direct-formatter.native-command",
    )
    assert_that(other.discover_all_files).is_false()
    assert_that(other.anchors_directories).is_false()
    plugin = SemgrepPlugin()
    plugin.set_options(rule_packs=[OTHER_PACK])
    assert_that(plugin.definition.file_patterns).is_equal_to(SEMGREP_FILE_PATTERNS)
    plugin.set_options(rule_packs=[OTHER_PACK, PACK])
    assert_that(plugin.definition.file_patterns).is_equal_to(["*"])


def test_definition_reads_skip_pack_validation_until_packs_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeated reads reuse the definition; a changed selection is validated again.

    Args:
        tmp_path: Empty rules directory that no pack name resolves in.
        monkeypatch: Point pack resolution at the empty directory.
    """
    plugin = SemgrepPlugin()
    plugin.set_options(rule_packs=[PACK])
    first = plugin.definition
    # With no packs installed, any validation or rules-directory glob would raise.
    monkeypatch.setattr(lintro.rules, "_RULES_ROOT", tmp_path)
    for _ in range(3):
        assert_that(plugin.definition).is_same_as(first)
    assert_that(first.file_patterns).is_equal_to(["*"])
    plugin.options["rule_packs"] = [OTHER_PACK]
    assert_that(lambda: plugin.definition).raises(ConfigurationError).when_called_with()


@pytest.mark.parametrize(
    ("packs", "marker", "warns"),
    [
        ([PACK], None, True),
        ([PACK], "pyproject.toml", False),
        ([PACK], ".git", False),
        ([OTHER_PACK], None, False),
        ([], None, False),
    ],
    ids=["no-marker", "pyproject", "git", "unanchored-pack", "no-packs"],
)
def test_unanchored_pack_warning_needs_directory_globs_and_no_marker(
    tmp_path: Path,
    packs: list[str],
    marker: str | None,
    warns: bool,
) -> None:
    """Only directory-scoped packs in a marker-less working directory warn.

    Args:
        tmp_path: Working directory the Semgrep subprocess would use.
        packs: Enabled packs.
        marker: Project marker created in the working directory, if any.
        warns: Whether a warning is expected.
    """
    if marker is not None:
        (tmp_path / marker).touch()
    message = _unanchored_pack_warning(packs, str(tmp_path))
    if warns:
        assert_that(message).contains(PACK, str(tmp_path), "pyproject.toml")
    else:
        assert_that(message).is_none()


def test_pack_tracks_builtin_format_claimants() -> None:
    """Adding or removing FORMAT requires updating executable pack examples."""
    declared = {
        name
        for name, definition in ToolRegistry.get_definitions().items()
        if ToolRegistry.get_origin(name) == ToolRegistry.BUILTIN_ORIGIN
        and any(Cap.FORMAT in claim.capabilities for claim in definition.claims)
    }
    assert_that(set(_pack_examples())).is_equal_to(declared)
    assert_that(available_rule_packs()).contains(PACK)


@pytest.mark.parametrize("selected", [False, True], ids=["default-off", "opt-in"])
def test_pack_discovers_scripts_only_after_opt_in(
    tmp_path: Path,
    selected: bool,
) -> None:
    """Makefiles and extensionless scripts reach the engine only when selected.

    Args:
        tmp_path: Temporary candidate tree.
        selected: Whether to enable the script pack.
    """
    script = tmp_path / "scripts" / "generate"
    script.parent.mkdir()
    makefile = tmp_path / "Makefile"
    module = tmp_path / "build.mjs"
    for path in (script, makefile, module):
        path.write_text("black generated.py\n", encoding="utf-8")
    plugin = SemgrepPlugin()
    if selected:
        plugin.set_options(rule_packs=[PACK])
    files = discover_files([str(tmp_path)], plugin.definition, [])
    expected = {str(script), str(makefile), str(module)} if selected else set()
    assert_that(set(files)).is_equal_to(expected)

    plugin.reset_options()
    assert_that(discover_files([str(tmp_path)], plugin.definition, [])).is_empty()
