"""Binary-gated integration tests for the stylelint tool.

These tests run the real stylelint binary against temporary CSS/SCSS fixtures
with a local config, exercising both the check and fix paths end to end. They
skip automatically when stylelint is not resolvable in the environment.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli import cli
from lintro.parsers.stylelint.stylelint_issue import StylelintIssue
from lintro.tools.stylelint.definition import StylelintPlugin
from tests.integration._tools import require_command

# Shared fixtures: single source of truth for stylelint sample content.
FIXTURES = Path("test_samples/tools/web/stylelint").resolve()


# Probe from a neutral cwd: bunx/npx resolution can succeed from the repo
# root (whose node_modules satisfy the CLI) while failing in the tmp
# directories the tests actually lint from.
pytestmark = require_command(
    "stylelint",
    StylelintPlugin()._get_executable_command(tool_name="stylelint"),
    timeout=30,
    cwd=tempfile.gettempdir(),
)


@pytest.fixture
def styled_project(tmp_path: Path) -> Path:
    """Create a temp project with a stylelint config.

    Args:
        tmp_path: Pytest temporary directory.

    Returns:
        Path: The project directory containing ``.stylelintrc.json``.
    """
    shutil.copy(FIXTURES / ".stylelintrc.json", tmp_path / ".stylelintrc.json")
    return tmp_path


def test_check_reports_css_violations(styled_project: Path) -> None:
    """Check surfaces real violations in a CSS fixture."""
    target = styled_project / "bad.css"
    shutil.copy(FIXTURES / "stylelint_violations.css", target)

    plugin = StylelintPlugin()
    result = plugin.check([str(target)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_greater_than(0)
    codes = {
        issue.code
        for issue in (result.issues or [])
        if isinstance(issue, StylelintIssue)
    }
    assert_that(codes).contains("color-hex-length")


def test_check_reports_scss_violations(styled_project: Path) -> None:
    """Check handles nested SCSS syntax and reports violations."""
    target = styled_project / "bad.scss"
    shutil.copy(FIXTURES / "stylelint_violations.scss", target)

    plugin = StylelintPlugin()
    result = plugin.check([str(target)], {})

    assert_that(result.success).is_false()
    codes = {
        issue.code
        for issue in (result.issues or [])
        if isinstance(issue, StylelintIssue)
    }
    assert_that(codes).contains("color-hex-length")


def test_check_passes_clean_css(styled_project: Path) -> None:
    """A clean CSS file produces no issues."""
    target = styled_project / "clean.css"
    shutil.copy(FIXTURES / "stylelint_clean.css", target)

    plugin = StylelintPlugin()
    result = plugin.check([str(target)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_fix_applies_and_preserves_invariant(styled_project: Path) -> None:
    """Fix auto-corrects fixable issues and preserves the count invariant."""
    target = styled_project / "fixme.css"
    target.write_text("a {\n  color: #FFFFFF;\n}\n.empty {\n}\n")

    plugin = StylelintPlugin()
    result = plugin.fix([str(target)], {})

    # color-hex-length is auto-fixable; block-no-empty is not.
    assert_that(result.initial_issues_count).is_greater_than(0)
    assert_that(result.fixed_issues_count).is_greater_than(0)
    fixed = result.fixed_issues_count or 0
    remaining = result.remaining_issues_count or 0
    assert_that(fixed + remaining).is_equal_to(result.initial_issues_count)
    assert_that(target.read_text()).contains("#FFF")
    assert_that(target.read_text()).does_not_contain("#FFFFFF")


def test_skips_without_config(tmp_path: Path) -> None:
    """Without a config, stylelint is skipped as a non-error."""
    target = tmp_path / "bad.css"
    target.write_text("a {\n  color: #FFFFFF;\n}\n")

    plugin = StylelintPlugin()
    result = plugin.check([str(target)], {})

    assert_that(result.success).is_true()
    assert_that(result.output).contains("no stylelint configuration")


@pytest.mark.parametrize("mechanism", ["stylelintignore", "ignoreFiles"])
def test_files_ignored_by_stylelint_config_are_no_files(
    styled_project: Path,
    mechanism: str,
) -> None:
    """Files Stylelint's own config ignores are "no files", not a failure (#3393).

    ``.stylelintignore`` leaves the report empty; ``ignoreFiles`` reports each
    file as a record flagged ``"ignored": true``.

    Args:
        styled_project: Temp project with a stylelint config.
        mechanism: Which of Stylelint's ignore mechanisms excludes the file.
    """
    target = styled_project / "bad.css"
    shutil.copy(FIXTURES / "stylelint_violations.css", target)
    if mechanism == "stylelintignore":
        (styled_project / ".stylelintignore").write_text("bad.css\n")
    else:
        config_path = styled_project / ".stylelintrc.json"
        config = json.loads(config_path.read_text())
        config["ignoreFiles"] = ["**/*.css"]
        config_path.write_text(json.dumps(config))

    plugin = StylelintPlugin()
    checked = plugin.check([str(target)], {})
    fixed = plugin.fix([str(target)], {})

    for result in (checked, fixed):
        assert_that(result.success).is_true()
        assert_that(result.no_files).is_true()
        assert_that(result.inspected_files).is_false()
        assert_that(result.execution_failed).is_false()
    assert_that(target.read_text()).is_equal_to(
        (FIXTURES / "stylelint_violations.css").read_text(),
    )


def test_broken_config_still_fails(tmp_path: Path) -> None:
    """A config Stylelint cannot load is still an execution failure.

    Args:
        tmp_path: Pytest temporary directory.
    """
    target = tmp_path / "a.css"
    target.write_text("a {\n  color: #fff;\n}\n")
    (tmp_path / ".stylelintrc.json").write_text("{not json")

    result = StylelintPlugin().check([str(target)], {})

    assert_that(result.no_files).is_false()
    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.findings_complete).is_false()
    assert_that(result.issues).is_empty()


def test_backslash_named_file_is_not_no_files(styled_project: Path) -> None:
    r"""Stylelint reads ``dir\\file.css`` as ``dir/file.css``: it must fail (#3393).

    With ``--allow-empty-input`` that run prints ``[]`` at exit 0, the same
    report an ignored file leaves, so it must not read as "no files".

    Args:
        styled_project: Temp project with a stylelint config.
    """
    target = styled_project / "dir\\file.css"
    shutil.copy(FIXTURES / "stylelint_violations.css", target)
    original = target.read_text()

    plugin = StylelintPlugin()
    checked = plugin.check([str(target)], {})
    fixed = plugin.fix([str(target)], {})

    for result in (checked, fixed):
        assert_that(result.no_files).is_false()
        assert_that(result.success).is_false()
        assert_that(result.execution_failed).is_true()
        assert_that(result.findings_complete).is_false()
    assert_that(target.read_text()).is_equal_to(original)


def test_bracket_named_file_is_linted_and_fixed(styled_project: Path) -> None:
    """Stylelint escapes glob syntax in an existing path, so it lints ``[slug]``.

    Args:
        styled_project: Temp project with a stylelint config.
    """
    target = styled_project / "[slug].css"
    shutil.copy(FIXTURES / "stylelint_violations.css", target)
    original = target.read_text()

    plugin = StylelintPlugin()
    checked = plugin.check([str(target)], {})
    fixed = plugin.fix([str(target)], {})

    assert_that(checked.no_files).is_false()
    assert_that(checked.success).is_false()
    assert_that(checked.inspected_files).is_true()
    codes = {
        issue.code
        for issue in (checked.issues or [])
        if isinstance(issue, StylelintIssue)
    }
    assert_that(codes).contains("color-hex-length")
    assert_that(fixed.no_files).is_false()
    assert_that(fixed.execution_failed).is_false()
    assert_that(fixed.fixed_issues_count).is_greater_than(0)
    assert_that(target.read_text()).is_not_equal_to(original)


def test_mixed_run_withholds_backslash_file_and_fails(styled_project: Path) -> None:
    r"""``dir\file.css`` is withheld; ``normal.css`` is still linted and fixed.

    Args:
        styled_project: Temp project with a stylelint config.
    """
    unread = styled_project / "dir\\file.css"
    normal = styled_project / "normal.css"
    for target in (unread, normal):
        shutil.copy(FIXTURES / "stylelint_violations.css", target)
    original = unread.read_bytes()

    plugin = StylelintPlugin()
    checked = plugin.check([str(unread), str(normal)], {})
    fixed = plugin.fix([str(unread), str(normal)], {})

    assert_that(
        {Path(issue.file).name for issue in checked.issues},
    ).is_equal_to({"normal.css"})
    for result in (checked, fixed):
        assert_that(result.success).is_false()
        assert_that(result.execution_failed).is_true()
        assert_that(result.findings_complete).is_false()
        assert_that(result.inspected_files).is_none()
        assert_that(result.output).contains("not inspected")
    assert_that(fixed.fixed_issues_count).is_greater_than(0)
    assert_that(normal.read_text()).does_not_contain("#FFFFFF")
    assert_that(unread.read_bytes()).is_equal_to(original)


def test_backslash_path_never_rewrites_the_file_it_resembles(
    styled_project: Path,
) -> None:
    r"""Asking for ``dir\file.css`` leaves an existing ``dir/file.css`` alone.

    Stylelint would read ``dir\file.css`` as ``dir/file.css`` and ``--fix``
    would rewrite that file, which the user never named.

    Args:
        styled_project: Temp project with a stylelint config.
    """
    lookalike = styled_project / "dir" / "file.css"
    lookalike.parent.mkdir()
    shutil.copy(FIXTURES / "stylelint_violations.css", lookalike)
    named = styled_project / "dir\\file.css"
    shutil.copy(FIXTURES / "stylelint_violations.css", named)
    before = lookalike.read_bytes()

    plugin = StylelintPlugin()
    checked = plugin.check([str(named)], {})
    fixed = plugin.fix([str(named)], {})

    assert_that(lookalike.read_bytes()).is_equal_to(before)
    for result in (checked, fixed):
        assert_that(result.success).is_false()
        assert_that(result.execution_failed).is_true()
        assert_that(result.issues).is_empty()


def test_dry_run_of_a_mixed_run_keeps_the_withheld_files_reason(
    styled_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    r"""``format --dry-run`` reports the withheld file instead of crashing.

    Promoting the partial preview to success used to raise
    ``execution_failed cannot report success`` and replace the reason with a
    generic error (#3393).

    Args:
        styled_project: Temp project with a stylelint config.
        monkeypatch: Runs the CLI from the project directory.
    """
    for name in ("normal.css", "dir\\file.css"):
        shutil.copy(FIXTURES / "stylelint_violations.css", styled_project / name)
    monkeypatch.chdir(styled_project)

    result = CliRunner().invoke(
        cli,
        [
            "format",
            "normal.css",
            "dir\\file.css",
            "--tools",
            "stylelint",
            "--dry-run",
            "--output-format",
            "json",
        ],
    )

    report = json.loads(result.stdout)
    (stylelint,) = (row for row in report["results"] if row["tool"] == "stylelint")
    assert_that(result.exit_code).is_equal_to(1)
    assert_that(stylelint["execution_failed"]).is_true()
    assert_that(stylelint["success"]).is_false()
    assert_that(stylelint["output"]).starts_with("Stylelint was not run on 1 file(s)")
    assert_that(stylelint["output"]).does_not_contain("ValueError")


@pytest.mark.parametrize(
    ("name", "decoy"),
    [
        pytest.param("[a\nb].css", "a.css", id="file-name"),
        pytest.param("[a\nb]/p.css", "a/p.css", id="directory-name"),
        pytest.param("a\tb.css", "ab.css", id="tab"),
    ],
)
def test_control_character_names_are_linted_literally(
    styled_project: Path,
    name: str,
    decoy: str,
) -> None:
    r"""Stylelint escapes an existing path, so control characters stay literal.

    Unlike html-validate, Stylelint 18 reads ``[a\\nb].css`` as the file it
    names, so such a path is linted and the decoy is not (#3393).

    Args:
        styled_project: Temp project with a stylelint config.
        name: A path holding a control character.
        decoy: A file a glob reading of the name could match.
    """
    lookalike = styled_project / decoy
    lookalike.parent.mkdir(exist_ok=True)
    shutil.copy(FIXTURES / "stylelint_violations.css", lookalike)
    target = styled_project / name
    target.parent.mkdir(exist_ok=True)
    shutil.copy(FIXTURES / "stylelint_violations.css", target)

    result = StylelintPlugin().check([str(target)], {})

    assert_that(result.execution_failed).is_false()
    assert_that(result.issues_count).is_greater_than(0)
    assert_that({Path(issue.file).name for issue in result.issues}).is_equal_to(
        {target.name},
    )
