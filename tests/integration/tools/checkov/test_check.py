"""Integration tests for CheckovPlugin against a real checkov binary."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from assertpy import assert_that

from lintro.parsers.checkov.checkov_issue import CheckovIssue
from lintro.parsers.checkov.checkov_parser import CHECKOV_PARSE_ERROR_CODE
from lintro.tools.checkov.definition import CHECKOV_FILE_PATTERNS
from tests.integration._tools import CHECKOV_PROBE_TIMEOUT, require_tool

if TYPE_CHECKING:
    from lintro.plugins.base import BaseToolPlugin

pytestmark = require_tool("checkov", timeout=CHECKOV_PROBE_TIMEOUT)


def test_check_detects_seeded_misconfigurations(
    get_plugin: Callable[[str], BaseToolPlugin],
    checkov_violation_file: str,
) -> None:
    """Checkov detects the seeded S3 and security-group misconfigurations.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        checkov_violation_file: Path to the seeded-misconfiguration fixture.
    """
    plugin = get_plugin("checkov")
    result = plugin.check([checkov_violation_file], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("checkov")
    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_greater_than(0)

    codes = {str(getattr(issue, "check_id", "")) for issue in (result.issues or [])}
    assert_that([code for code in codes if code.startswith("CKV_AWS_")]).is_not_empty()
    # Both seeded resources must be reported, not just one: a fixture or scope
    # regression that stopped scanning either would still leave CKV_AWS_ codes.
    resources = {str(getattr(issue, "resource", "")) for issue in (result.issues or [])}
    assert_that(resources).contains(
        "aws_s3_bucket.example",
        "aws_security_group.allow_all",
    )


def test_check_preserves_resource_attribution(
    get_plugin: Callable[[str], BaseToolPlugin],
    checkov_violation_file: str,
) -> None:
    """Findings carry the resource address checkov's SARIF output drops.

    Resource attribution is the reason the native JSON parser is used instead
    of the shared SARIF path, so it is asserted end to end rather than only in
    the parser unit tests.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        checkov_violation_file: Path to the seeded-misconfiguration fixture.
    """
    plugin = get_plugin("checkov")
    result = plugin.check([checkov_violation_file], {})

    issues = [
        issue for issue in (result.issues or []) if isinstance(issue, CheckovIssue)
    ]
    assert_that(issues).is_not_empty()
    resources = {issue.resource for issue in issues}
    assert_that(resources).contains("aws_security_group.allow_all")
    for issue in issues:
        assert_that(issue.line).is_greater_than(0)
        # ``--skip-download`` suppresses the platform metadata a native
        # ``guideline`` would come from, so the issue carries none and the
        # plugin's policy-index fallback is what the report renders.
        assert_that(issue.guideline).is_none()
        assert_that(issue.doc_url).is_equal_to(plugin.doc_url(issue.check_id))
        assert_that(issue.doc_url).contains("checkov.io")


def test_check_clean_file_passes(
    get_plugin: Callable[[str], BaseToolPlugin],
    checkov_clean_file: str,
) -> None:
    """Checkov reports no issues on a file with no scannable resources.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        checkov_clean_file: Path to the clean fixture.
    """
    plugin = get_plugin("checkov")
    result = plugin.check([checkov_clean_file], {})

    assert_that(result).is_not_none()
    assert_that(result.success).is_true()
    # skipped must be False as well: a skipped result is also successful with
    # zero issues, so success alone cannot tell "ran and found nothing" from
    # "never ran".
    assert_that(result.skipped).is_false()
    assert_that(result.issues_count).is_equal_to(0)


def test_skip_checks_suppresses_a_reported_policy(
    get_plugin: Callable[[str], BaseToolPlugin],
    checkov_violation_file: str,
) -> None:
    """``skip_checks`` removes exactly the policy it names.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        checkov_violation_file: Path to the seeded-misconfiguration fixture.
    """
    plugin = get_plugin("checkov")
    baseline = plugin.check([checkov_violation_file], {})
    codes = sorted(
        {str(getattr(issue, "check_id", "")) for issue in (baseline.issues or [])},
    )
    assert_that(codes).is_not_empty()
    target = codes[0]

    plugin.set_options(skip_checks=[target])
    filtered = plugin.check([checkov_violation_file], {})

    remaining = {
        str(getattr(issue, "check_id", "")) for issue in (filtered.issues or [])
    }
    # Exactly the named policy disappears: asserting only that it is gone would
    # also pass for a bug that skipped every check.
    assert_that(remaining).is_equal_to(set(codes) - {target})


def test_json_syntax_terraform_is_scanned(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """The claimed ``*.tf.json`` pattern is exercised end to end.

    ``.tf.json`` has ``Path.suffix == ".json"``, so both discovery and language
    detection reach it only through a name match. Nothing else in the suite
    hands checkov a file in Terraform's JSON syntax, so a regression there
    would leave the second claimed pattern silently dead.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
    """
    (tmp_path / "main.tf.json").write_text(
        '{"resource": {"aws_s3_bucket": {"example": {"bucket": "insecure"}}}}\n',
    )
    plugin = get_plugin("checkov")
    result = plugin.check([str(tmp_path)], {})

    assert_that(result).is_not_none()
    assert_that(result.skipped).is_false()
    # Positive evidence that checkov *evaluated* the file, not merely that
    # discovery matched it: the bucket has no encryption, versioning or
    # logging, so a real evaluation fails several CKV_AWS policies. Asserting
    # only on the "no files" message would pass if checkov silently parsed
    # nothing out of Terraform's JSON syntax.
    assert_that(result.issues_count).is_greater_than(0)
    assert_that(result.output or "").does_not_contain("found to check")


#: Truncated HCL: the resource block is never closed, so checkov's Terraform
#: parser cannot read the file and lists it under ``results.parsing_errors``.
_UNPARSEABLE_HCL = 'resource "aws_s3_bucket" "broken" {\n  bucket = "x"\n'


def test_unparseable_file_fails_the_run_and_keeps_real_findings(
    get_plugin: Callable[[str], BaseToolPlugin],
    checkov_violation_file: str,
    tmp_path: Path,
) -> None:
    """A file checkov cannot read is reported, and does not hide the rest.

    The synthetic parser tests assert lintro's handling of a
    ``results.parsing_errors`` payload; this locks the empirical premise that
    the real binary emits one for truncated HCL, and that surfacing it does
    not cost the findings from the files that did parse.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        checkov_violation_file: Path to the seeded-misconfiguration fixture.
        tmp_path: Pytest fixture providing a temporary directory.
    """
    broken = tmp_path / "broken.tf"
    broken.write_text(_UNPARSEABLE_HCL)
    plugin = get_plugin("checkov")

    result = plugin.check([str(broken), checkov_violation_file], {})

    assert_that(result.success).is_false()
    codes = {str(getattr(issue, "check_id", "")) for issue in (result.issues or [])}
    assert_that(codes).contains(CHECKOV_PARSE_ERROR_CODE)
    parse_issues = [
        issue
        for issue in (result.issues or [])
        if getattr(issue, "check_id", "") == CHECKOV_PARSE_ERROR_CODE
    ]
    assert_that(parse_issues).is_not_empty()
    assert_that(any("broken.tf" in issue.file for issue in parse_issues)).is_true()
    # The seeded fixture's real policy failures are still reported.
    assert_that([code for code in codes if code.startswith("CKV_AWS_")]).is_not_empty()


def test_unparseable_file_fails_a_run_checkov_exits_zero_on(
    get_plugin: Callable[[str], BaseToolPlugin],
    checkov_clean_file: str,
    tmp_path: Path,
) -> None:
    """Exit 0 with only a parsing error must not read as a clean scan.

    Checkov derives its exit code from failed checks alone, so an unreadable
    file alongside a clean one produces exit 0, an empty ``failed_checks`` and
    a populated ``parsing_errors`` — the fail-open shape this plugin exists to
    close. Verified against the real binary rather than a synthetic payload.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        checkov_clean_file: Path to the clean fixture.
        tmp_path: Pytest fixture providing a temporary directory.
    """
    broken = tmp_path / "truncated.tf"
    broken.write_text(_UNPARSEABLE_HCL)
    plugin = get_plugin("checkov")

    result = plugin.check([str(broken), checkov_clean_file], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(1)
    issues = [
        issue for issue in (result.issues or []) if isinstance(issue, CheckovIssue)
    ]
    assert_that(issues).is_length(1)
    assert_that(issues[0].check_id).is_equal_to(CHECKOV_PARSE_ERROR_CODE)
    assert_that(issues[0].file).contains("truncated.tf")


def test_check_empty_directory(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """Checkov handles a directory with no Terraform files gracefully.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
    """
    plugin = get_plugin("checkov")
    result = plugin.check([str(tmp_path)], {})

    assert_that(result).is_not_none()
    # success matters as much as the count: a fail-closed empty argv would also
    # report zero issues, so asserting only the count would not distinguish
    # "nothing to do" from "the invocation broke".
    assert_that(result.success).is_true()
    assert_that(result.skipped).is_false()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.output).starts_with("No ")
    assert_that(result.output).contains("found to check")
    # Every claimed extension is named, and independently: ``contains(".tf")``
    # alone is satisfied by ``.tf.json``, so the bare extension is asserted
    # against a copy with the JSON one removed. Literal extensions, not a loop
    # over the same constant the message is built from: deriving the
    # expectation from the source of truth would pass even if both drifted
    # together.
    assert_that(result.output).contains(".tf.json")
    assert_that((result.output or "").replace(".tf.json", "")).contains(".tf")
    assert_that(sorted(CHECKOV_FILE_PATTERNS)).is_equal_to(["*.tf", "*.tf.json"])
