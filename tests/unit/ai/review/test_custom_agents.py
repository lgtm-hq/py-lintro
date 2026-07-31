"""Tests for user-defined review agent discovery and validation (#1245)."""

from __future__ import annotations

from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.ai.review.custom_agents import (
    CustomAgentConfigError,
    CustomAgentSkipReason,
    custom_agent_directory,
    discover_custom_agents,
    files_for_agent,
    format_custom_agent_listing,
    parse_custom_agent,
    select_custom_agents,
)
from lintro.ai.review.enums.review_strictness import ReviewStrictness
from lintro.ai.review.models.review_finding import Severity

_VALID_AGENT = """---
name: no-raw-sql
description: SQL must go through the repository layer
include:
  - "src/**/*.py"
exclude:
  - "src/repositories/**"
severity: high
strictness: focused
---

Flag raw SQL executed outside the repository layer.
"""


def _write_agent(*, root: Path, file_name: str, text: str) -> Path:
    """Write an agent markdown file into a workspace.

    Args:
        root: Workspace root.
        file_name: Markdown file name.
        text: File contents.

    Returns:
        The path written.
    """
    directory = custom_agent_directory(workspace_root=root)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / file_name
    path.write_text(text, encoding="utf-8")
    return path


def test_parse_custom_agent_reads_front_matter_and_body(tmp_path: Path) -> None:
    """A valid agent file parses into a fully populated spec."""
    path = tmp_path / "no-raw-sql.md"

    agent = parse_custom_agent(path=path, text=_VALID_AGENT)

    assert_that(agent.name).is_equal_to("no-raw-sql")
    assert_that(agent.description).is_equal_to(
        "SQL must go through the repository layer",
    )
    assert_that(agent.include).is_equal_to(("src/**/*.py",))
    assert_that(agent.exclude).is_equal_to(("src/repositories/**",))
    assert_that(agent.severity).is_equal_to(Severity.P1)
    assert_that(agent.strictness).is_equal_to(ReviewStrictness.FOCUSED)
    assert_that(agent.model).is_none()
    assert_that(agent.enabled).is_true()
    assert_that(agent.body).starts_with("Flag raw SQL")


def test_parse_custom_agent_applies_defaults(tmp_path: Path) -> None:
    """Optional fields fall back to documented defaults."""
    text = '---\nname: minimal\ninclude: ["*.py"]\n---\n\nCheck things.\n'

    agent = parse_custom_agent(path=tmp_path / "minimal.md", text=text)

    assert_that(agent.description).is_equal_to("")
    assert_that(agent.exclude).is_equal_to(())
    assert_that(agent.severity).is_equal_to(Severity.P2)
    assert_that(agent.strictness).is_equal_to(ReviewStrictness.BALANCED)
    assert_that(agent.enabled).is_true()


def test_parse_custom_agent_accepts_scalar_include(tmp_path: Path) -> None:
    """A single glob string is accepted as a one-element list."""
    text = "---\nname: scalar\ninclude: 'src/**/*.py'\n---\n\nBody.\n"

    agent = parse_custom_agent(path=tmp_path / "scalar.md", text=text)

    assert_that(agent.include).is_equal_to(("src/**/*.py",))


def test_parse_custom_agent_model_default_means_no_override(tmp_path: Path) -> None:
    """``model: default`` normalizes to no per-agent override."""
    text = '---\nname: m\ninclude: ["*.py"]\nmodel: default\n---\n\nBody.\n'

    agent = parse_custom_agent(path=tmp_path / "m.md", text=text)

    assert_that(agent.model).is_none()


def test_parse_custom_agent_keeps_model_override(tmp_path: Path) -> None:
    """An explicit model name is preserved as an override."""
    text = '---\nname: m\ninclude: ["*.py"]\nmodel: gpt-4o\n---\n\nBody.\n'

    agent = parse_custom_agent(path=tmp_path / "m.md", text=text)

    assert_that(agent.model).is_equal_to("gpt-4o")


@pytest.mark.parametrize(
    ("text", "expected_field"),
    [
        ("no front matter at all\n", "front-matter"),
        ("---\nname: x\ninclude: ['*.py']\n\nunclosed\n", "front-matter"),
        ("---\nname: [1, 2\n---\n\nbody\n", "front-matter"),
        ("---\n---\n\nbody\n", "front-matter"),
        ("---\n- a\n- b\n---\n\nbody\n", "front-matter"),
        ("---\ninclude: ['*.py']\n---\n\nbody\n", "name"),
        ("---\nname: 'bad name'\ninclude: ['*.py']\n---\n\nbody\n", "name"),
        ("---\nname: x\n---\n\nbody\n", "include"),
        ("---\nname: x\ninclude: []\n---\n\nbody\n", "include"),
        ("---\nname: x\ninclude: [3]\n---\n\nbody\n", "include"),
        ("---\nname: x\ninclude: 'a'\nexclude: 5\n---\n\nbody\n", "exclude"),
        (
            "---\nname: x\ninclude: ['*.py']\nseverity: nope\n---\n\nbody\n",
            "severity",
        ),
        (
            "---\nname: x\ninclude: ['*.py']\nstrictness: nope\n---\n\nbody\n",
            "strictness",
        ),
        (
            "---\nname: x\ninclude: ['*.py']\nenabled: maybe\n---\n\nbody\n",
            "enabled",
        ),
        ("---\nname: x\ninclude: ['*.py']\nmodel: ''\n---\n\nbody\n", "model"),
        (
            "---\nname: x\ninclude: ['*.py']\ndescription: [a]\n---\n\nbody\n",
            "description",
        ),
        ("---\nname: x\ninclude: ['*.py']\nbogus: 1\n---\n\nbody\n", "bogus"),
        ("---\nname: x\ninclude: ['*.py']\n---\n\n   \n", "body"),
        # YAML 1.1 resolves an unquoted `on:` key to the boolean True; a
        # non-string front-matter key must never crash the join() that
        # reports unknown fields (see issue #1245 review follow-up).
        (
            "---\nname: x\ninclude: ['*.py']\non: true\n---\n\nbody\n",
            "front-matter",
        ),
    ],
)
def test_parse_custom_agent_rejects_invalid_files(
    tmp_path: Path,
    text: str,
    expected_field: str,
) -> None:
    """Invalid files raise a structured error naming the offending field."""
    with pytest.raises(CustomAgentConfigError) as excinfo:
        parse_custom_agent(path=tmp_path / "bad.md", text=text)

    assert_that(excinfo.value.field).is_equal_to(expected_field)
    assert_that(str(excinfo.value)).is_not_empty()


def test_discover_custom_agents_returns_empty_when_directory_missing(
    tmp_path: Path,
) -> None:
    """A workspace without the agent directory discovers nothing."""
    discovery = discover_custom_agents(workspace_root=tmp_path)

    assert_that(discovery.agents).is_empty()
    assert_that(discovery.issues).is_empty()
    assert_that(str(discovery.directory)).ends_with("review-agents")


def test_discover_custom_agents_skips_invalid_file_without_failing(
    tmp_path: Path,
) -> None:
    """An invalid file is reported as an issue and never aborts discovery."""
    _write_agent(root=tmp_path, file_name="good.md", text=_VALID_AGENT)
    _write_agent(
        root=tmp_path,
        file_name="bad.md",
        text="---\nname: bad\ninclude: ['*.py']\nseverity: nope\n---\n\nbody\n",
    )

    discovery = discover_custom_agents(workspace_root=tmp_path)

    assert_that([agent.name for agent in discovery.agents]).is_equal_to(
        ["no-raw-sql"],
    )
    assert_that(discovery.issues).is_length(1)
    assert_that(discovery.issues[0].field).is_equal_to("severity")
    assert_that(discovery.issues[0].format()).contains("bad.md")


def test_discover_custom_agents_rejects_duplicate_names(tmp_path: Path) -> None:
    """A second file reusing an agent name is skipped with a named issue."""
    _write_agent(root=tmp_path, file_name="a.md", text=_VALID_AGENT)
    _write_agent(root=tmp_path, file_name="b.md", text=_VALID_AGENT)

    discovery = discover_custom_agents(workspace_root=tmp_path)

    assert_that(discovery.agents).is_length(1)
    assert_that(discovery.issues).is_length(1)
    assert_that(discovery.issues[0].field).is_equal_to("name")
    assert_that(discovery.issues[0].message).contains("duplicate")


def test_discover_custom_agents_is_sorted_by_file_name(tmp_path: Path) -> None:
    """Discovery order is deterministic across runs."""
    for name in ("zeta", "alpha", "mid"):
        _write_agent(
            root=tmp_path,
            file_name=f"{name}.md",
            text=f"---\nname: {name}\ninclude: ['*.py']\n---\n\nBody.\n",
        )

    discovery = discover_custom_agents(workspace_root=tmp_path)

    assert_that([agent.name for agent in discovery.agents]).is_equal_to(
        ["alpha", "mid", "zeta"],
    )


def test_files_for_agent_applies_include_and_exclude(tmp_path: Path) -> None:
    """Include globs select files and exclude globs remove them."""
    agent = parse_custom_agent(path=tmp_path / "a.md", text=_VALID_AGENT)

    files = files_for_agent(
        agent=agent,
        changed_paths=(
            "src/api/handler.py",
            "src/repositories/user.py",
            "docs/readme.md",
        ),
    )

    assert_that(files).is_equal_to(("src/api/handler.py",))


def test_select_custom_agents_skips_disabled_agent(tmp_path: Path) -> None:
    """An agent with ``enabled: false`` is skipped, not run."""
    agent = parse_custom_agent(
        path=tmp_path / "off.md",
        text=("---\nname: legacy\ninclude: ['*.py']\nenabled: false\n---\n\nBody.\n"),
    )

    selection = select_custom_agents(agents=(agent,), changed_paths=("main.py",))

    assert_that(selection.selected).is_empty()
    assert_that(selection.skipped).is_length(1)
    assert_that(selection.skipped[0].reason).is_equal_to(
        CustomAgentSkipReason.DISABLED,
    )


def test_select_custom_agents_skips_agent_with_no_matching_files(
    tmp_path: Path,
) -> None:
    """An agent whose globs match nothing in the diff is skipped."""
    agent = parse_custom_agent(path=tmp_path / "a.md", text=_VALID_AGENT)

    selection = select_custom_agents(
        agents=(agent,),
        changed_paths=("docs/readme.md",),
    )

    assert_that(selection.selected).is_empty()
    assert_that(selection.skipped[0].reason).is_equal_to(
        CustomAgentSkipReason.NO_MATCHING_FILES,
    )


def test_select_custom_agents_scopes_agent_to_matching_files(
    tmp_path: Path,
) -> None:
    """A selected agent carries only the files it is scoped to."""
    agent = parse_custom_agent(path=tmp_path / "a.md", text=_VALID_AGENT)

    selection = select_custom_agents(
        agents=(agent,),
        changed_paths=("src/api/handler.py", "docs/readme.md"),
    )

    assert_that(selection.skipped).is_empty()
    assert_that(selection.selected).is_length(1)
    assert_that(selection.selected[0].files).is_equal_to(("src/api/handler.py",))


def test_format_custom_agent_listing_reports_agents_and_issues(
    tmp_path: Path,
) -> None:
    """The listing shows each agent's scope plus any skipped invalid files."""
    _write_agent(root=tmp_path, file_name="good.md", text=_VALID_AGENT)
    _write_agent(
        root=tmp_path,
        file_name="legacy.md",
        text=("---\nname: legacy\ninclude: ['*.py']\nenabled: false\n---\n\nBody.\n"),
    )
    _write_agent(
        root=tmp_path,
        file_name="bad.md",
        text="---\nname: bad\n---\n\nbody\n",
    )

    listing = format_custom_agent_listing(
        discovery=discover_custom_agents(workspace_root=tmp_path),
    )

    assert_that(listing).contains("no-raw-sql (enabled)")
    assert_that(listing).contains("legacy (disabled)")
    assert_that(listing).contains("include: src/**/*.py")
    assert_that(listing).contains("exclude: src/repositories/**")
    assert_that(listing).contains("severity: P1")
    assert_that(listing).contains("strictness: focused")
    assert_that(listing).contains("Invalid agent files (skipped): 1")
    assert_that(listing).contains("bad.md: include")


def test_format_custom_agent_listing_reports_none_found(tmp_path: Path) -> None:
    """An empty workspace lists no agents rather than erroring."""
    listing = format_custom_agent_listing(
        discovery=discover_custom_agents(workspace_root=tmp_path),
    )

    assert_that(listing).contains("(none found)")
