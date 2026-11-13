"""Integration tests for the html-validate tool.

These tests are gated on html-validate being resolvable (via bunx/npx or a
direct binary). They exercise the plugin end-to-end against a real HTML fixture.
"""

import json
import os
import shutil
import tempfile
import time
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.models.core.tool_result import ToolResult
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


_FINDINGS = "<p><div></p>\n"
_CLEAN = "<p>clean</p>\n"


def _check(paths: list[Path]) -> ToolResult:
    """Run the registered html-validate plugin over some paths.

    Args:
        paths: Files to check.

    Returns:
        The plugin's result.
    """
    tool = ToolRegistry.get("html_validate")
    tool.exclude_patterns = []
    return tool.check([str(path) for path in paths], {})


def _reported(result: ToolResult, root: Path) -> set[str]:
    """Collect the paths findings were reported under, relative to a root.

    Args:
        result: The plugin's result.
        root: Directory the paths are made relative to.

    Returns:
        POSIX-style relative paths, one per file with findings.
    """
    return {Path(issue.file).relative_to(root).as_posix() for issue in result.issues}


@pytest.mark.html_validate
@pytest.mark.parametrize(
    "name",
    [
        pytest.param("[slug].html", id="character-class"),
        pytest.param("a{1,2}.html", id="brace-list"),
        pytest.param("a+(b).html", id="extglob"),
        pytest.param("x*?.html", id="star-question"),
        pytest.param("dir\\file.html", id="backslash"),
    ],
)
def test_html_validate_reports_glob_named_file_under_its_real_path(
    tmp_path: Path,
    name: str,
) -> None:
    """A glob-named file is validated through stdin under its own name (#3396).

    Args:
        tmp_path: Pytest-managed temporary directory.
        name: A file name html-validate's glob expansion would misread.
    """
    target = tmp_path / name
    target.write_text(_FINDINGS, encoding="utf-8")
    result = _check([target])

    assert_that(result.execution_failed).is_false()
    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_greater_than(0)
    assert_that(_reported(result, tmp_path)).is_equal_to({name})
    codes = {
        issue.code for issue in result.issues if isinstance(issue, HtmlValidateIssue)
    }
    assert_that(codes).contains("close-order")


@pytest.mark.html_validate
@pytest.mark.parametrize("name", ["[slug].html", "a{1,2}.html"])
def test_html_validate_clean_glob_named_file_passes_as_inspected(
    tmp_path: Path,
    name: str,
) -> None:
    """A clean glob-named file passes and counts as inspected.

    Args:
        tmp_path: Pytest-managed temporary directory.
        name: A file name html-validate's glob expansion would misread.
    """
    target = tmp_path / name
    target.write_text(_CLEAN, encoding="utf-8")
    result = _check([target])

    assert_that(result.success).is_true()
    assert_that(result.execution_failed).is_false()
    assert_that(result.inspected_files).is_true()
    assert_that(result.issues_count).is_zero()


@pytest.mark.html_validate
def test_html_validate_file_inside_a_slug_directory(tmp_path: Path) -> None:
    """A SvelteKit-style ``[slug]/page.html`` is reported under its real path.

    A clean sibling at the root keeps the working directory at ``tmp_path``,
    so the bracket directory is part of the path.

    Args:
        tmp_path: Pytest-managed temporary directory.
    """
    target = tmp_path / "[slug]" / "page.html"
    target.parent.mkdir()
    target.write_text(_FINDINGS, encoding="utf-8")
    sibling = tmp_path / "index.html"
    sibling.write_text(_CLEAN, encoding="utf-8")
    result = _check([target, sibling])

    assert_that(result.execution_failed).is_false()
    assert_that(result.success).is_false()
    assert_that(_reported(result, tmp_path)).is_equal_to({"[slug]/page.html"})


@pytest.mark.html_validate
def test_html_validate_mixed_run_merges_into_one_result(tmp_path: Path) -> None:
    """Plain and glob-named files with findings produce one merged verdict.

    Args:
        tmp_path: Pytest-managed temporary directory.
    """
    names = ["page.html", "[slug].html", "a{1,2}.html"]
    for name in names:
        (tmp_path / name).write_text(_FINDINGS, encoding="utf-8")
    clean = tmp_path / "[clean].html"
    clean.write_text(_CLEAN, encoding="utf-8")
    result = _check([tmp_path / name for name in names] + [clean])

    assert_that(result.name).is_equal_to("html_validate")
    assert_that(result.execution_failed).is_false()
    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(len(result.issues))
    assert_that(_reported(result, tmp_path)).is_equal_to(set(names))


@pytest.mark.html_validate
def test_html_validate_clean_mixed_run_passes(tmp_path: Path) -> None:
    """Clean plain and glob-named files pass together as inspected.

    Args:
        tmp_path: Pytest-managed temporary directory.
    """
    paths = [tmp_path / "page.html", tmp_path / "[slug].html"]
    for path in paths:
        path.write_text(_CLEAN, encoding="utf-8")
    result = _check(paths)

    assert_that(result.success).is_true()
    assert_that(result.inspected_files).is_true()
    assert_that(result.output).is_none()


@pytest.mark.html_validate
@pytest.mark.parametrize(
    ("name", "decoys"),
    [
        pytest.param("[slug].html", ("s.html",), id="class-matches-another-file"),
        pytest.param("dir\\file.html", ("dir/file.html",), id="backslash-separator"),
        pytest.param("a+(b).html", ("ab.html",), id="extglob"),
        pytest.param("[a\nb].html", ("a.html", "b.html"), id="class-across-newline"),
        pytest.param(
            "[a\nb]/p.html",
            ("a/p.html", "b/p.html"),
            id="directory-class-across-newline",
        ),
    ],
)
def test_html_validate_never_validates_the_file_a_name_resembles(
    tmp_path: Path,
    name: str,
    decoys: tuple[str, ...],
) -> None:
    r"""The named file is validated, never a clean file its glob would match.

    Passed positionally, html-validate would expand ``[slug].html`` to
    ``s.html``, read ``dir\\file.html`` as ``dir/file.html`` and match
    ``a+(b).html`` to ``ab.html``, validate the clean decoy and pass.

    Args:
        tmp_path: Pytest-managed temporary directory.
        name: The file named on the command line.
        decoys: Clean files the misread name would match.
    """
    for decoy in decoys:
        (tmp_path / decoy).parent.mkdir(exist_ok=True)
        (tmp_path / decoy).write_text(_CLEAN, encoding="utf-8")
    sibling = tmp_path / "index.html"
    sibling.write_text(_CLEAN, encoding="utf-8")
    target = tmp_path / name
    target.parent.mkdir(exist_ok=True)
    target.write_text(_FINDINGS, encoding="utf-8")
    result = _check([target, sibling])

    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_false()
    assert_that(_reported(result, tmp_path)).is_equal_to({name})


@pytest.mark.html_validate
def test_html_validate_stdin_uses_the_files_own_directory_config(
    tmp_path: Path,
) -> None:
    """A config beside the glob-named file applies, as it would positionally.

    Args:
        tmp_path: Pytest-managed temporary directory.
    """
    route = tmp_path / "[slug]"
    route.mkdir()
    (route / ".htmlvalidate.json").write_text(
        '{"extends": ["html-validate:recommended"], "rules": '
        '{"close-order": "off", "no-implicit-close": "off"}}',
        encoding="utf-8",
    )
    target = route / "page.html"
    target.write_text(_FINDINGS, encoding="utf-8")
    result = _check([target])

    assert_that(result.success).is_true()
    assert_that(result.inspected_files).is_true()
    assert_that(result.issues).is_empty()


_IGNORE = ".htmlvalidateignore"
_CONFIG = ".htmlvalidate.json"


def _config_with_transform(key: str) -> str:
    """Build an html-validate config declaring one transformer.

    Args:
        key: The transform pattern.

    Returns:
        The JSON config.
    """
    return json.dumps(
        {
            "extends": ["html-validate:recommended"],
            "transform": {key: "lintro-test-missing-transformer"},
        },
    )


_TRANSFORM_CONFIGURED = "a transform is configured"

# A transformer that returns its source unchanged, as a function value:
# ``--print-config`` serialises it to ``"transform": {}``.
_FUNCTION_TRANSFORM_CJS = (
    "module.exports = {\n"
    '  extends: ["html-validate:recommended"],\n'
    '  transform: { "slug": function (source) { return [source]; } },\n'
    "};\n"
)


@pytest.mark.html_validate
@pytest.mark.parametrize(
    ("setting", "content", "held", "reason"),
    [
        pytest.param(
            _IGNORE,
            "?slug?.html\n",
            "[slug].html",
            ".htmlvalidateignore may ignore it",
            id="ignore-pattern-matches",
        ),
        pytest.param(
            _IGNORE,
            "?SLUG?.HTML\n",
            "[slug].html",
            ".htmlvalidateignore may ignore it",
            id="ignore-pattern-other-case",
        ),
        pytest.param(
            _IGNORE,
            "x/\n!x/?slug?.html\n",
            "x/[slug].html",
            ".htmlvalidateignore may ignore it",
            id="ignore-excluded-parent",
        ),
        pytest.param(
            _IGNORE,
            "[z-a].html\n",
            "[slug].html",
            ".htmlvalidateignore cannot be read or parsed",
            id="ignore-invalid-range",
        ),
        pytest.param(
            _CONFIG,
            _config_with_transform("slug"),
            "[slug].html",
            _TRANSFORM_CONFIGURED,
            id="transform-matches",
        ),
        pytest.param(
            _CONFIG,
            _config_with_transform("^.*\\.vue$"),
            "[slug].html",
            _TRANSFORM_CONFIGURED,
            id="vue-only-transform",
        ),
        pytest.param(
            _CONFIG,
            _config_with_transform("\\bfoo\\.html$"),
            "[x]éfoo.html",
            _TRANSFORM_CONFIGURED,
            id="js-word-boundary-differs-from-python",
        ),
        pytest.param(
            ".htmlvalidate.cjs",
            _FUNCTION_TRANSFORM_CJS,
            "[slug].html",
            "a transform may be configured",
            id="function-transform-in-cjs",
        ),
    ],
)
def test_html_validate_holds_back_what_stdin_cannot_validate(
    tmp_path: Path,
    setting: str,
    content: str,
    held: str,
    reason: str,
) -> None:
    """An ignore pattern or transform that may apply holds the file back.

    stdin skips ``.htmlvalidateignore`` and transforms. lintro checks both
    conservatively, so a file html-validate might ignore or transform is
    never validated untransformed. The plain file is still validated and
    keeps its findings; the run fails and names the held-back file with
    the reason that applied (#3393).

    Args:
        tmp_path: Pytest-managed temporary directory.
        setting: The file holding the setting.
        content: The setting.
        held: The glob-named file that must be held back.
        reason: The reason the output must give.
    """
    (tmp_path / setting).write_text(content, encoding="utf-8")
    target = tmp_path / held
    target.parent.mkdir(exist_ok=True)
    target.write_text(_FINDINGS, encoding="utf-8")
    plain = tmp_path / "page.html"
    plain.write_text(_FINDINGS, encoding="utf-8")
    result = _check([target, plain])

    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.findings_complete).is_false()
    assert_that(result.output).contains(f"{held!r}: ", reason, "not inspected")
    assert_that(_reported(result, tmp_path)).is_equal_to({"page.html"})


@pytest.mark.html_validate
@pytest.mark.parametrize(
    ("setting", "content"),
    [
        pytest.param(_IGNORE, "dist/\n# [slug].html\n", id="ignore-pattern-elsewhere"),
        pytest.param(
            _CONFIG,
            json.dumps({"extends": ["html-validate:recommended"]}),
            id="json-config-without-transform",
        ),
    ],
)
def test_html_validate_validates_through_stdin_when_settings_do_not_apply(
    tmp_path: Path,
    setting: str,
    content: str,
) -> None:
    """An ignore file or transformer for other files does not hold one back.

    Args:
        tmp_path: Pytest-managed temporary directory.
        setting: The file holding the setting.
        content: A setting that matches no file in the run.
    """
    (tmp_path / setting).write_text(content, encoding="utf-8")
    target = tmp_path / "[slug].html"
    target.write_text(_FINDINGS, encoding="utf-8")
    result = _check([target])

    assert_that(result.execution_failed).is_false()
    assert_that(result.success).is_false()
    assert_that(_reported(result, tmp_path)).is_equal_to({"[slug].html"})


@pytest.mark.html_validate
def test_html_validate_validates_a_name_starting_with_a_dash(tmp_path: Path) -> None:
    """``-x.html`` is passed as a path, not read as an unknown option.

    Args:
        tmp_path: Pytest-managed temporary directory.
    """
    target = tmp_path / "-x.html"
    target.write_text(_FINDINGS, encoding="utf-8")
    result = _check([target])

    assert_that(result.execution_failed).is_false()
    assert_that(result.success).is_false()
    assert_that(_reported(result, tmp_path)).is_equal_to({"-x.html"})


def _wrap_html_validate(root: Path, script: str) -> None:
    """Install a project-local html-validate that wraps the real one.

    The plugin prefers ``node_modules/.bin`` in the directory it runs in.

    Args:
        root: The project directory.
        script: Shell lines run before handing over to the real binary.
    """
    real = shutil.which("html-validate")
    if real is None:
        pytest.skip("needs html-validate on PATH to wrap")
    wrapper = root / "node_modules" / ".bin" / "html-validate"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text(
        f'#!/bin/sh\n{script}\nexec "{real}" "$@"\n',
        encoding="utf-8",
    )
    wrapper.chmod(0o755)


@pytest.mark.html_validate
@pytest.mark.skipif(os.name == "nt", reason="uses a POSIX shell wrapper")
def test_html_validate_keeps_findings_when_the_binary_vanishes(
    tmp_path: Path,
) -> None:
    """A launch failure mid-run makes an incomplete run that keeps findings.

    The wrapper deletes itself during the first stdin call, so the next
    stdin call cannot start.

    Args:
        tmp_path: Pytest-managed temporary directory.
    """
    _wrap_html_validate(
        tmp_path,
        'case " $* " in *" --stdin-filename "*) rm -f "$0";; esac',
    )
    for name in ("page.html", "[a].html", "[b].html"):
        (tmp_path / name).write_text(_FINDINGS, encoding="utf-8")
    result = _check([tmp_path])

    assert_that(result.execution_failed).is_true()
    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(len(result.issues))
    assert_that(_reported(result, tmp_path)).is_equal_to({"page.html", "[a].html"})
    assert_that(result.output).contains(
        f"{tmp_path / '[b].html'}: html-validate could not be started",
    )


@pytest.mark.html_validate
@pytest.mark.skipif(os.name == "nt", reason="uses a POSIX shell wrapper")
def test_html_validate_stops_after_a_stdin_timeout(tmp_path: Path) -> None:
    """After one stdin deadline the remaining files are named, not started.

    Args:
        tmp_path: Pytest-managed temporary directory.
    """
    _wrap_html_validate(tmp_path, 'case "$*" in *slow*) sleep 30;; esac')
    for name in ("page.html", "[0slow].html", "[a].html", "[b].html"):
        (tmp_path / name).write_text(_FINDINGS, encoding="utf-8")
    tool = ToolRegistry.get("html_validate")
    tool.exclude_patterns = []
    started = time.monotonic()
    result = tool.check([str(tmp_path)], {"timeout": 2})
    elapsed = time.monotonic() - started

    assert_that(elapsed).is_less_than(20)
    assert_that(result.timed_out).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.issues_count).is_equal_to(len(result.issues))
    assert_that(_reported(result, tmp_path)).is_equal_to({"page.html"})
    assert_that(result.output).contains(
        f"{tmp_path / '[0slow].html'}: ",
        "timed out",
        "not run on 2 file(s) because an earlier html-validate call timed out",
        "'[a].html'",
        "'[b].html'",
    )


@pytest.mark.html_validate
@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root reads files regardless of their mode",
)
def test_html_validate_holds_back_an_unreadable_glob_named_file(
    tmp_path: Path,
) -> None:
    """A glob-named file stdin cannot open falls back to the hold-back.

    The directory is passed, because lintro rejects an unreadable file named
    on the command line before any tool runs.

    Args:
        tmp_path: Pytest-managed temporary directory.
    """
    held = tmp_path / "[slug].html"
    held.write_text(_FINDINGS, encoding="utf-8")
    plain = tmp_path / "page.html"
    plain.write_text(_FINDINGS, encoding="utf-8")
    held.chmod(0)
    try:
        result = _check([tmp_path])
    finally:
        held.chmod(0o644)

    assert_that(result.execution_failed).is_true()
    assert_that(result.inspected_files).is_none()
    assert_that(_reported(result, tmp_path)).is_equal_to({"page.html"})
    assert_that(result.output).contains("[slug].html", "not inspected")


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
