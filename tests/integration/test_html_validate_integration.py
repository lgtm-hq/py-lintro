"""Integration tests for the html-validate tool.

These tests are gated on html-validate being resolvable (via bunx/npx or a
direct binary). They exercise the plugin end-to-end against a real HTML fixture.
"""

import shutil
import tempfile
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.parsers.html_validate.html_validate_issue import HtmlValidateIssue
from lintro.plugins import ToolRegistry
from tests.integration._tools import require_command

SAMPLE_FILE = "test_samples/tools/web/html_validate/html_validate_violations.html"


def find_html_validate_cmd() -> list[str] | None:
    """Resolve a command that runs html-validate.

    Returns:
        Command list if html-validate is resolvable, None otherwise.
    """
    if shutil.which("html-validate"):
        return ["html-validate"]
    if shutil.which("bunx"):
        return ["bunx", "html-validate"]
    if shutil.which("npx"):
        return ["npx", "--yes", "html-validate"]
    return None


pytestmark = require_command(
    "html-validate",
    find_html_validate_cmd(),
    # Probe from a neutral cwd: the tests lint files in tmp directories, and
    # bunx/npx resolution can succeed from the repo root (whose node_modules
    # satisfy the CLI's dependencies) while failing anywhere else. Probing
    # from the repo would let the tests run a broken tool.
    cwd=tempfile.gettempdir(),
)


@pytest.mark.html_validate
def test_html_validate_available() -> None:
    """html-validate resolves and reports a version."""
    cmd_base = find_html_validate_cmd()
    assert_that(cmd_base).is_not_none()


@pytest.mark.html_validate
def test_html_validate_detects_violations() -> None:
    """The plugin detects violations in the sample HTML fixture."""
    sample_path = Path(SAMPLE_FILE)
    tool = ToolRegistry.get("html_validate")
    assert_that(tool).is_not_none()
    tool.exclude_patterns = []
    result = tool.check([str(sample_path)], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("html_validate")
    assert_that(result.issues_count).is_greater_than(0)
    assert_that(result.success).is_false()
    assert_that(result.issues).is_not_none()
    issues = result.issues

    issue = issues[0]
    if not isinstance(issue, HtmlValidateIssue):
        pytest.fail("issue should be HtmlValidateIssue")
    assert_that(issue.file).is_not_empty()
    assert_that(issue.code).is_not_empty()
    assert_that(issue.message).is_not_empty()
    assert_that(issue.severity).is_in("error", "warning")


@pytest.mark.html_validate
def test_html_validate_clean_file_passes(tmp_path: Path) -> None:
    """A valid HTML fragment yields no issues under default (no-config) rules.

    Args:
        tmp_path: Pytest-managed temporary directory (keeps the transient
            fixture out of the versioned ``test_samples`` tree, which other
            tests may scan concurrently).
    """
    clean_file = tmp_path / "clean_fragment.html"
    clean_file.write_text("<p>Hello world</p>\n", encoding="utf-8")
    tool = ToolRegistry.get("html_validate")
    tool.exclude_patterns = []
    result = tool.check([str(clean_file)], {})
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.success).is_true()


@pytest.mark.html_validate
def test_html_validate_files_ignored_by_its_config_are_no_files(
    tmp_path: Path,
) -> None:
    """Files ``.htmlvalidateignore`` excludes are "no files" (#3393).

    Args:
        tmp_path: Pytest-managed temporary directory.
    """
    target = tmp_path / "ignored.html"
    shutil.copy(SAMPLE_FILE, target)
    (tmp_path / ".htmlvalidateignore").write_text("ignored.html\n", encoding="utf-8")
    tool = ToolRegistry.get("html_validate")
    tool.exclude_patterns = []
    result = tool.check([str(target)], {})

    assert_that(result.success).is_true()
    assert_that(result.no_files).is_true()
    assert_that(result.inspected_files).is_false()
    assert_that(result.execution_failed).is_false()
    assert_that(result.issues_count).is_zero()


@pytest.mark.html_validate
def test_html_validate_broken_config_still_fails(tmp_path: Path) -> None:
    """A config html-validate cannot load is still an execution failure.

    Args:
        tmp_path: Pytest-managed temporary directory.
    """
    target = tmp_path / "page.html"
    target.write_text("<p>Hello world</p>\n", encoding="utf-8")
    (tmp_path / ".htmlvalidate.json").write_text("{not json", encoding="utf-8")
    tool = ToolRegistry.get("html_validate")
    tool.exclude_patterns = []
    result = tool.check([str(target)], {})

    assert_that(result.no_files).is_false()
    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.findings_complete).is_false()


@pytest.mark.html_validate
@pytest.mark.parametrize("name", ["[slug].html", "dir\\file.html"])
def test_html_validate_glob_named_file_with_findings_does_not_pass(
    tmp_path: Path,
    name: str,
) -> None:
    """A file html-validate cannot match by its own name must not pass (#3393).

    html-validate expands its arguments as globs: a bracket name is a
    character class and a backslash becomes a path separator. Either way the
    literal name is reported as unmatched, exactly as for an ignored file.

    Args:
        tmp_path: Pytest-managed temporary directory.
        name: A file name that does not match itself as a glob.
    """
    target = tmp_path / name
    target.write_text("<p><div></p>\n", encoding="utf-8")
    tool = ToolRegistry.get("html_validate")
    tool.exclude_patterns = []
    result = tool.check([str(target)], {})

    assert_that(result.no_files).is_false()
    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.findings_complete).is_false()


@pytest.mark.html_validate
@pytest.mark.parametrize(
    ("name", "neighbour"),
    [
        pytest.param("[slug].html", "s.html", id="class-matches-another-file"),
        pytest.param("dir\\file.html", "dir/file.html", id="backslash-separator"),
    ],
)
def test_html_validate_never_validates_the_file_a_name_resembles(
    tmp_path: Path,
    name: str,
    neighbour: str,
) -> None:
    r"""A misread name is withheld, even when its glob matches a clean file.

    html-validate would expand ``[slug].html`` to ``s.html`` and read
    ``dir\\file.html`` as ``dir/file.html``, validate that clean file and
    pass.

    Args:
        tmp_path: Pytest-managed temporary directory.
        name: The file named on the command line.
        neighbour: A clean file the misread name would match.
    """
    (tmp_path / neighbour).parent.mkdir(exist_ok=True)
    (tmp_path / neighbour).write_text("<p>clean</p>\n", encoding="utf-8")
    target = tmp_path / name
    target.write_text("<p><div></p>\n", encoding="utf-8")
    tool = ToolRegistry.get("html_validate")
    tool.exclude_patterns = []
    result = tool.check([str(target)], {})

    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.inspected_files).is_none()
    assert_that(result.output).contains("not inspected")


@pytest.mark.html_validate
def test_html_validate_mixed_run_validates_the_rest_and_fails(
    tmp_path: Path,
) -> None:
    """The readable file keeps its findings; the withheld one fails the run.

    Args:
        tmp_path: Pytest-managed temporary directory.
    """
    withheld = tmp_path / "[slug].html"
    withheld.write_text("<p><div></p>\n", encoding="utf-8")
    readable = tmp_path / "page.html"
    readable.write_text("<p><div></p>\n", encoding="utf-8")
    tool = ToolRegistry.get("html_validate")
    tool.exclude_patterns = []
    result = tool.check([str(withheld), str(readable)], {})

    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.findings_complete).is_false()
    assert_that({Path(issue.file).name for issue in result.issues}).is_equal_to(
        {"page.html"},
    )


@pytest.mark.html_validate
@pytest.mark.parametrize(
    "name",
    [
        pytest.param("(app)/page.html", id="route-group-directory"),
        pytest.param("page (1).html", id="copy-suffix"),
    ],
)
def test_html_validate_validates_paths_with_bare_parentheses(
    tmp_path: Path,
    name: str,
) -> None:
    """A bare parenthesis is literal, so the file is validated as named.

    A clean sibling at the root keeps the working directory at ``tmp_path``,
    so the parentheses are part of the path html-validate receives.

    Args:
        tmp_path: Pytest-managed temporary directory.
        name: A path whose parentheses follow no extglob prefix.
    """
    target = tmp_path / name
    target.parent.mkdir(exist_ok=True)
    target.write_text("<p><div></p>\n", encoding="utf-8")
    sibling = tmp_path / "index.html"
    sibling.write_text("<p>clean</p>\n", encoding="utf-8")
    tool = ToolRegistry.get("html_validate")
    tool.exclude_patterns = []
    result = tool.check([str(target), str(sibling)], {})

    assert_that(result.execution_failed).is_false()
    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_greater_than(0)
    assert_that(
        {Path(issue.file).relative_to(tmp_path).as_posix() for issue in result.issues},
    ).is_equal_to({name})


@pytest.mark.html_validate
def test_html_validate_holds_back_extglob_name(tmp_path: Path) -> None:
    """``a+(b).html`` would validate ``ab.html``; it is held back instead.

    Args:
        tmp_path: Pytest-managed temporary directory.
    """
    (tmp_path / "ab.html").write_text("<p><div></p>\n", encoding="utf-8")
    target = tmp_path / "a+(b).html"
    target.write_text("<p><div></p>\n", encoding="utf-8")
    tool = ToolRegistry.get("html_validate")
    tool.exclude_patterns = []
    result = tool.check([str(target)], {})

    assert_that(result.execution_failed).is_true()
    assert_that(result.findings_complete).is_false()
    assert_that(result.issues).is_empty()
    assert_that(result.output).contains("a+(b).html", "not inspected")


@pytest.mark.html_validate
@pytest.mark.parametrize(
    ("name", "decoys"),
    [
        pytest.param("[a\nb].html", ("a.html", "b.html"), id="file-name"),
        pytest.param("[a\nb]/p.html", ("a/p.html", "b/p.html"), id="directory-name"),
    ],
)
def test_html_validate_holds_back_a_class_spanning_a_newline(
    tmp_path: Path,
    name: str,
    decoys: tuple[str, ...],
) -> None:
    r"""A ``[...]`` class across a newline would validate the clean decoys.

    html-validate reads ``[a\\nb]`` as a class matching ``a`` or ``b``: it
    validated the decoys, printed ``[]`` and the run passed (#3393).

    Args:
        tmp_path: Pytest-managed temporary directory.
        name: A path whose class spans a newline.
        decoys: Clean files the class would match instead.
    """
    for decoy in decoys:
        (tmp_path / decoy).parent.mkdir(exist_ok=True)
        (tmp_path / decoy).write_text("<p>clean</p>\n", encoding="utf-8")
    sibling = tmp_path / "index.html"
    sibling.write_text("<p>clean</p>\n", encoding="utf-8")
    target = tmp_path / name
    target.parent.mkdir(exist_ok=True)
    target.write_text("<p><div></p>\n", encoding="utf-8")
    tool = ToolRegistry.get("html_validate")
    tool.exclude_patterns = []
    result = tool.check([str(target), str(sibling)], {})

    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.inspected_files).is_none()
    assert_that(result.output).contains("not inspected")
