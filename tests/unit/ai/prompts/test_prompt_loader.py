"""Tests for the packaged prompt template loader.

Covers loader round-trip against the constants exposed by the prompt modules,
validation-error paths (missing template, no path parts), and presence of the
template resources inside a freshly built wheel.
"""

from __future__ import annotations

import subprocess
import tempfile
import zipfile
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.ai.prompts import fix, post_fix, review, summary
from lintro.ai.prompts._loader import load_prompt_template

# Maps each public prompt constant to the template resource that backs it.
_CONSTANT_TO_TEMPLATE: dict[str, tuple[object, tuple[str, ...]]] = {
    "FIX_SYSTEM": (fix, ("fix", "system.md")),
    "FIX_PROMPT_TEMPLATE": (fix, ("fix", "prompt.md")),
    "FIX_BATCH_PROMPT_TEMPLATE": (fix, ("fix", "batch_prompt.md")),
    "REFINEMENT_PROMPT_TEMPLATE": (fix, ("fix", "refinement.md")),
    "SUMMARY_SYSTEM": (summary, ("summary", "system.md")),
    "SUMMARY_PROMPT_TEMPLATE": (summary, ("summary", "prompt.md")),
    "POST_FIX_SUMMARY_PROMPT_TEMPLATE": (
        post_fix,
        ("post_fix", "summary_prompt.md"),
    ),
    "REVIEW_SYSTEM": (review, ("review", "system.md")),
    "REVIEW_USER_PROMPT_TEMPLATE": (review, ("review", "user.md")),
    "REVIEW_GIT_NATIVE_USER_PROMPT_TEMPLATE": (
        review,
        ("review", "git_native_user.md"),
    ),
    "REVIEW_GIT_NATIVE_DIFF_INLINE": (
        review,
        ("review", "git_native_diff_inline.md"),
    ),
    "REVIEW_GIT_NATIVE_DIFF_GIT_COMMAND": (
        review,
        ("review", "git_native_diff_git_command.md"),
    ),
    "REVIEW_GIT_NATIVE_DIFF_WORKTREE_COMMAND": (
        review,
        ("review", "git_native_diff_worktree_command.md"),
    ),
    "REVIEW_OUTPUT_SCHEMA": (review, ("review", "output_schema.json")),
    "REVIEW_GENERATE_QUESTIONS_TEMPLATE": (
        review,
        ("review", "generate_questions.md"),
    ),
    "REVIEW_ADVERSARIAL_SWEEP_TEMPLATE": (
        review,
        ("review", "adversarial_sweep.md"),
    ),
}

_EXPECTED_TEMPLATE_PATHS: tuple[str, ...] = tuple(
    "/".join(parts) for _module, parts in _CONSTANT_TO_TEMPLATE.values()
)


@pytest.fixture
def constant_names() -> tuple[str, ...]:
    """Return the public prompt constant names under test.

    Returns:
        Tuple of constant names covered by the loader mapping.
    """
    return tuple(_CONSTANT_TO_TEMPLATE.keys())


def test_module_constants_equal_loader_output(
    constant_names: tuple[str, ...],
) -> None:
    """Each module constant equals the loader output for its template."""
    for name in constant_names:
        module, parts = _CONSTANT_TO_TEMPLATE[name]
        assert_that(getattr(module, name)).is_equal_to(
            load_prompt_template(*parts),
        )


def test_loaded_templates_are_non_empty(
    constant_names: tuple[str, ...],
) -> None:
    """Every backing template resource loads non-empty UTF-8 text."""
    for name in constant_names:
        _module, parts = _CONSTANT_TO_TEMPLATE[name]
        text = load_prompt_template(*parts)
        assert_that(text).is_type_of(str)
        assert_that(text).is_not_empty()


def test_placeholder_braces_survive_migration() -> None:
    """`.format()` placeholders and `{{` escapes round-trip verbatim."""
    rendered = load_prompt_template("fix", "prompt.md").format(
        tool_name="ruff",
        code="E501",
        file="a.py",
        line=1,
        message="msg",
        context_start=1,
        context_end=2,
        boundary="BOUND",
        code_context="print()",
    )
    # `{{` escapes collapse to a literal JSON object brace after format().
    assert_that(rendered).contains('{\n  "original_code"')


def test_batch_template_double_escape_survives() -> None:
    """The batch template's `{{{{` escapes collapse to a literal `{{`."""
    rendered = load_prompt_template("fix", "batch_prompt.md").format(
        tool_name="ruff",
        file="a.py",
        issues_list="- E501",
        boundary="BOUND",
        file_content="print()",
    )
    # One str.format pass turns `{{{{` into `{{` so the model sees a JSON object brace.
    assert_that(rendered).contains('{{ "line"')


def test_missing_template_raises_file_not_found() -> None:
    """A missing template path raises FileNotFoundError with the path."""
    with pytest.raises(FileNotFoundError) as exc_info:
        load_prompt_template("review", "does_not_exist.md")
    assert_that(str(exc_info.value)).contains("does_not_exist.md")


def test_no_path_parts_raises_value_error() -> None:
    """Calling the loader without path parts raises ValueError."""
    with pytest.raises(ValueError):
        load_prompt_template()


def test_loader_is_cached_returns_same_object() -> None:
    """Repeated loads of one template return the cached string object."""
    first = load_prompt_template("review", "system.md")
    second = load_prompt_template("review", "system.md")
    assert_that(first).is_same_as(second)


@pytest.mark.slow
def test_templates_present_in_built_wheel() -> None:
    """A freshly built wheel ships every prompt template resource."""
    project_root = Path(__file__).parents[4]
    with tempfile.TemporaryDirectory() as tmpdir:
        dist_dir = Path(tmpdir) / "dist"
        build_result = subprocess.run(
            ["uv", "build", "--wheel", "--out-dir", str(dist_dir)],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert_that(build_result.returncode).described_as(
            build_result.stderr,
        ).is_equal_to(0)

        wheels = list(dist_dir.glob("*.whl"))
        assert_that(wheels).is_not_empty()

        with zipfile.ZipFile(wheels[0]) as wheel:
            names = set(wheel.namelist())

        for rel in _EXPECTED_TEMPLATE_PATHS:
            resource = f"lintro/ai/prompts/templates/{rel}"
            assert_that(names).described_as(resource).contains(resource)
