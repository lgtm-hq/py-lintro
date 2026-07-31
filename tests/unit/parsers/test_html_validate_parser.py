"""Unit tests for the html-validate JSON output parser.

These tests validate that the parser handles empty, null, and malformed input,
and that it extracts rule id, severity, location, selector, and documentation
URL from real html-validate ``--formatter json`` output.
"""

from assertpy import assert_that

from lintro.enums.severity_level import SeverityLevel
from lintro.parsers.html_validate.html_validate_issue import HtmlValidateIssue
from lintro.parsers.html_validate.html_validate_parser import (
    parse_html_validate_output,
)

# Real captured output from ``html-validate --formatter json`` (v11.5.5)
# run against an HTML file with several violations.
_REAL_OUTPUT = (
    '[{"filePath":"/tmp/hvtest.html","messages":['
    '{"ruleId":"element-required-attributes","severity":2,'
    '"message":"<html> is missing required \\"lang\\" attribute",'
    '"offset":17,"line":2,"column":2,"size":4,"selector":"html",'
    '"ruleUrl":"https://html-validate.org/rules/element-required-attributes.html"},'
    '{"ruleId":"wcag/h37","severity":2,'
    '"message":"<img> is missing required \\"alt\\" attribute",'
    '"offset":64,"line":5,"column":2,"size":3,"selector":"html > body > img",'
    '"ruleUrl":"https://html-validate.org/rules/wcag/h37.html"},'
    '{"ruleId":"no-implicit-button-type","severity":1,'
    '"message":"<button> is missing recommended \\"type\\" attribute",'
    '"offset":82,"line":6,"column":2,"size":6,"selector":"html > body > button",'
    '"ruleUrl":"https://html-validate.org/rules/no-implicit-button-type.html"}],'
    '"errorCount":2,"warningCount":1,"source":"..."}]'
)


def test_parse_empty_returns_empty_list() -> None:
    """Return an empty list for empty parser input."""
    assert_that(parse_html_validate_output("")).is_equal_to([])
    assert_that(parse_html_validate_output("   ")).is_equal_to([])


def test_parse_none_returns_empty_list() -> None:
    """Return an empty list when the parser input is None."""
    assert_that(parse_html_validate_output(None)).is_equal_to([])


def test_parse_clean_run_empty_array() -> None:
    """Return an empty list for a clean run (``[]``)."""
    assert_that(parse_html_validate_output("[]")).is_equal_to([])


def test_parse_malformed_json_returns_empty_list() -> None:
    """Return an empty list for malformed JSON rather than raising."""
    assert_that(parse_html_validate_output("not json at all")).is_equal_to([])
    assert_that(parse_html_validate_output("{ broken")).is_equal_to([])


def test_parse_non_list_json_returns_empty_list() -> None:
    """Return an empty list when JSON is valid but not a list."""
    assert_that(parse_html_validate_output('{"filePath": "x"}')).is_equal_to([])


def test_parse_extracts_all_fields() -> None:
    """Parse real output and extract every field for the first issue."""
    issues = parse_html_validate_output(_REAL_OUTPUT)
    assert_that(len(issues)).is_equal_to(3)

    first = issues[0]
    assert_that(first).is_instance_of(HtmlValidateIssue)
    assert_that(first.file).is_equal_to("/tmp/hvtest.html")
    assert_that(first.line).is_equal_to(2)
    assert_that(first.column).is_equal_to(2)
    assert_that(first.code).is_equal_to("element-required-attributes")
    assert_that(first.severity).is_equal_to("error")
    assert_that(first.selector).is_equal_to("html")
    assert_that(first.message).contains("missing required")
    assert_that(first.doc_url).is_equal_to(
        "https://html-validate.org/rules/element-required-attributes.html",
    )


def test_parse_severity_mapping() -> None:
    """Map numeric severity 2 to error and 1 to warning."""
    issues = parse_html_validate_output(_REAL_OUTPUT)
    assert_that(issues[0].get_severity()).is_equal_to(SeverityLevel.ERROR)
    assert_that(issues[1].get_severity()).is_equal_to(SeverityLevel.ERROR)
    assert_that(issues[2].severity).is_equal_to("warning")
    assert_that(issues[2].get_severity()).is_equal_to(SeverityLevel.WARNING)


def test_parse_namespaced_rule_id() -> None:
    """Preserve namespaced rule ids such as ``wcag/h37``."""
    issues = parse_html_validate_output(_REAL_OUTPUT)
    wcag = issues[1]
    assert_that(wcag.code).is_equal_to("wcag/h37")
    assert_that(wcag.selector).is_equal_to("html > body > img")


def test_parse_multiple_files() -> None:
    """Aggregate issues across multiple file results."""
    output = (
        '[{"filePath":"a.html","messages":['
        '{"ruleId":"void-style","severity":2,"message":"m1","line":1,"column":1,'
        '"selector":"img","ruleUrl":"https://html-validate.org/rules/void-style.html"}]},'
        '{"filePath":"b.html","messages":['
        '{"ruleId":"no-dup-id","severity":2,"message":"m2","line":3,"column":4,'
        '"selector":null,"ruleUrl":"https://html-validate.org/rules/no-dup-id.html"}]}]'
    )
    issues = parse_html_validate_output(output)
    assert_that(len(issues)).is_equal_to(2)
    assert_that(issues[0].file).is_equal_to("a.html")
    assert_that(issues[1].file).is_equal_to("b.html")
    # A null selector should degrade to an empty string, not the string "None".
    assert_that(issues[1].selector).is_equal_to("")


def test_parse_file_with_no_messages() -> None:
    """Skip file entries that have an empty messages array."""
    output = '[{"filePath":"clean.html","messages":[],"errorCount":0}]'
    assert_that(parse_html_validate_output(output)).is_equal_to([])


def test_parse_missing_optional_fields() -> None:
    """Tolerate messages missing optional line/column/selector fields."""
    output = (
        '[{"filePath":"x.html","messages":[{"ruleId":"r","severity":2,"message":"m"}]}]'
    )
    issues = parse_html_validate_output(output)
    assert_that(len(issues)).is_equal_to(1)
    assert_that(issues[0].line).is_equal_to(0)
    assert_that(issues[0].column).is_equal_to(0)
    assert_that(issues[0].selector).is_equal_to("")
    assert_that(issues[0].doc_url).is_equal_to("")
