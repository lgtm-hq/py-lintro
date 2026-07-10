"""Schema-validation tests for the consumer-facing ``.pre-commit-hooks.yaml``.

These tests guard the pre-commit framework integration that lets other repos
run lintro as a hook. They verify the hook manifest parses, carries the keys
pre-commit requires, and that every ``entry`` resolves to a real lintro CLI
command so a released tag never ships a broken hook definition.
"""

from pathlib import Path
from typing import cast

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

# Default hooks run the system-installed lintro (full tool set, native
# binaries included); the -python variants run in a pre-commit-managed
# isolated env (Python-based tools only).
EXPECTED_HOOK_IDS = {
    "lintro-check",
    "lintro-format",
    "lintro-check-python",
    "lintro-format-python",
}

# Hook id -> required language: default hooks must stay `system` so the
# native (non-Python) tools remain available; the -python variants must
# stay `python` so pre-commit provisions the isolated env.
EXPECTED_HOOK_LANGUAGES = {
    "lintro-check": "system",
    "lintro-format": "system",
    "lintro-check-python": "python",
    "lintro-format-python": "python",
}


@pytest.fixture
def hooks() -> list[dict[str, object]]:
    """Parse ``.pre-commit-hooks.yaml`` into a list of hook definitions.

    Returns:
        list[dict[str, object]]: The parsed hook definitions.
    """
    content = HOOKS_FILE.read_text(encoding="utf-8")
    loaded = yaml.safe_load(content)
    assert_that(loaded).is_type_of(list)
    assert isinstance(loaded, list)
    return cast(list[dict[str, object]], loaded)


def test_hooks_file_exists() -> None:
    """The hook manifest must live at the repo root where pre-commit looks."""
    assert_that(HOOKS_FILE.is_file()).is_true()


def test_hooks_file_parses_to_list(hooks: list[dict[str, object]]) -> None:
    """The manifest must be a non-empty YAML list of mappings."""
    assert_that(hooks).is_type_of(list)
    assert_that(hooks).is_not_empty()
    for hook in hooks:
        assert_that(hook).is_type_of(dict)


def test_expected_hook_ids_present(hooks: list[dict[str, object]]) -> None:
    """The system-default and isolated-python hook variants must be defined."""
    ids = {hook["id"] for hook in hooks}
    assert_that(ids).is_equal_to(EXPECTED_HOOK_IDS)


def test_hook_ids_are_unique(hooks: list[dict[str, object]]) -> None:
    """pre-commit rejects duplicate hook ids within a repo."""
    ids = [str(hook["id"]) for hook in hooks]
    assert_that(sorted(set(ids))).is_equal_to(sorted(ids))


def test_hooks_have_required_keys(hooks: list[dict[str, object]]) -> None:
    """Every hook must declare the keys pre-commit requires."""
    for hook in hooks:
        assert_that(set(hook.keys())).contains(*REQUIRED_KEYS)


def test_hook_languages_are_valid(hooks: list[dict[str, object]]) -> None:
    """Each hook must use a language pre-commit understands."""
    for hook in hooks:
        assert_that(VALID_LANGUAGES).contains(hook["language"])


def test_hook_languages_match_variant(hooks: list[dict[str, object]]) -> None:
    """Default hooks stay ``system``; ``-python`` variants stay ``python``.

    The system default keeps lintro's native (non-Python) tools available;
    flipping a language silently changes which tools run for consumers.
    """
    for hook in hooks:
        assert_that(hook["language"]).is_equal_to(
            EXPECTED_HOOK_LANGUAGES[str(hook["id"])],
        )


def test_hook_entries_resolve_to_real_cli_commands(
    hooks: list[dict[str, object]],
) -> None:
    """Every ``entry`` must invoke ``lintro`` with a real CLI command.

    Guards against typos or renamed commands leaving a released hook pointing
    at a non-existent subcommand.
    """
    ctx = click.Context(cli)
    known_commands = set(cli.list_commands(ctx))

    for hook in hooks:
        entry = hook["entry"]
        assert_that(entry).is_type_of(str)
        tokens = str(entry).split()
        assert_that(tokens).is_not_empty()
        assert_that(tokens[0]).is_equal_to("lintro")
        assert_that(tokens).is_length(2)  # "lintro <command>"
        assert_that(known_commands).contains(tokens[1])


def test_hooks_use_serial_execution(hooks: list[dict[str, object]]) -> None:
    """Lintro coordinates multiple tools, so it must run once over all files.

    ``require_serial`` prevents pre-commit from splitting files across parallel
    lintro invocations (which would fragment output and re-run every tool).
    """
    for hook in hooks:
        assert_that(hook.get("require_serial")).is_true()
