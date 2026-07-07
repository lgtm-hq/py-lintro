"""Schema-validation tests for the consumer-facing ``.pre-commit-hooks.yaml``.

These tests guard the pre-commit framework integration that lets other repos
run lintro as a hook. They verify the hook manifest parses, carries the keys
pre-commit requires, and that every ``entry`` resolves to a real lintro CLI
command so a released tag never ships a broken hook definition.
"""

from pathlib import Path

import click
import pytest
import yaml
from assertpy import assert_that

from lintro.cli import cli

# Repo root: tests/unit/ -> tests/ -> <root>
PROJECT_ROOT = Path(__file__).parent.parent.parent
HOOKS_FILE = PROJECT_ROOT / ".pre-commit-hooks.yaml"

# pre-commit's supported hook languages (subset relevant to a Python CLI tool).
VALID_LANGUAGES = {"python", "system", "script", "docker", "docker_image"}

# Keys every hook definition must declare for pre-commit to load it.
REQUIRED_KEYS = {"id", "name", "entry", "language"}

EXPECTED_HOOK_IDS = {"lintro-check", "lintro-format"}


@pytest.fixture
def hooks() -> list[dict]:
    """Parse ``.pre-commit-hooks.yaml`` into a list of hook definitions.

    Returns:
        list[dict]: The parsed hook definitions.
    """
    content = HOOKS_FILE.read_text(encoding="utf-8")
    return yaml.safe_load(content)


def test_hooks_file_exists() -> None:
    """The hook manifest must live at the repo root where pre-commit looks."""
    assert_that(HOOKS_FILE.is_file()).is_true()


def test_hooks_file_parses_to_list(hooks: list[dict]) -> None:
    """The manifest must be a non-empty YAML list of mappings."""
    assert_that(hooks).is_type_of(list)
    assert_that(hooks).is_not_empty()
    for hook in hooks:
        assert_that(hook).is_type_of(dict)


def test_expected_hook_ids_present(hooks: list[dict]) -> None:
    """Both the check and format hooks must be defined."""
    ids = {hook["id"] for hook in hooks}
    assert_that(ids).is_equal_to(EXPECTED_HOOK_IDS)


def test_hook_ids_are_unique(hooks: list[dict]) -> None:
    """pre-commit rejects duplicate hook ids within a repo."""
    ids = [hook["id"] for hook in hooks]
    assert_that(sorted(set(ids))).is_equal_to(sorted(ids))


def test_hooks_have_required_keys(hooks: list[dict]) -> None:
    """Every hook must declare the keys pre-commit requires."""
    for hook in hooks:
        assert_that(set(hook.keys())).contains(*REQUIRED_KEYS)


def test_hook_languages_are_valid(hooks: list[dict]) -> None:
    """Each hook must use a language pre-commit understands."""
    for hook in hooks:
        assert_that(VALID_LANGUAGES).contains(hook["language"])


def test_hook_entries_resolve_to_real_cli_commands(hooks: list[dict]) -> None:
    """Every ``entry`` must invoke ``lintro`` with a real CLI command.

    Guards against typos or renamed commands leaving a released hook pointing
    at a non-existent subcommand.
    """
    ctx = click.Context(cli)
    known_commands = set(cli.list_commands(ctx))

    for hook in hooks:
        tokens = hook["entry"].split()
        assert_that(tokens).is_not_empty()
        assert_that(tokens[0]).is_equal_to("lintro")
        assert_that(tokens).is_length(2)  # "lintro <command>"
        assert_that(known_commands).contains(tokens[1])


def test_hooks_use_serial_execution(hooks: list[dict]) -> None:
    """Lintro coordinates multiple tools, so it must run once over all files.

    ``require_serial`` prevents pre-commit from splitting files across parallel
    lintro invocations (which would fragment output and re-run every tool).
    """
    for hook in hooks:
        assert_that(hook.get("require_serial")).is_true()
