"""The fixed list of parser messages that drops an excluded file's error (#3553).

The corpus in ``captured_parser_messages`` holds what osv-scanner 2.7.1
printed for deliberately broken files. Every captured message must match its
template, and every template must be matched by a captured message, so the
list carries nothing that was not seen. Text a template does not describe is
never dropped.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.tools.osv_scanner.extraction_log import (
    PARSER_MESSAGE_TEMPLATES,
    excluded_file_template,
    read_stderr,
)
from tests.unit.tools.osv_scanner.captured_parser_messages import (
    CAPTURED,
    CapturedMessage,
)

#: How osv-scanner printed the excluded file's path.
_PRINTED = "repo/vendor/broken.lock"


def _template_of(captured: CapturedMessage) -> str | None:
    """Return the template a captured message matches, for its own file.

    Args:
        captured: The captured message.

    Returns:
        The template's name, or None.
    """
    printed = f"repo/vendor/{captured.file_name}"
    return excluded_file_template(captured.error.replace("<file>", printed), printed)


@pytest.mark.parametrize("shape", sorted(CAPTURED))
def test_captured_message_matches_its_template(shape: str) -> None:
    """Each real message is matched, and by the template recorded for it."""
    captured = CAPTURED[shape]

    assert_that(_template_of(captured)).is_equal_to(captured.template)


def test_every_template_is_exercised_by_a_captured_message() -> None:
    """No template exists without a real message behind it."""
    names = [name for name, _ in PARSER_MESSAGE_TEMPLATES]
    exercised = {_template_of(captured) for captured in CAPTURED.values()}

    assert_that(sorted(set(names) - exercised)).is_empty()
    assert_that(len(set(names))).is_equal_to(len(names))


#: Each family's prefix as osv-scanner prints it, with ``<file>`` for the
#: excluded file's own path.
_FAMILY_PREFIXES: tuple[str, ...] = (
    "could not extract: ",
    "could not extract ",
    "could not extract: json: ",
    "could not extract json: ",
    "failed to decode packages.lock.json file: ",
    "could not extract: yaml: ",
    "could not extract: yaml: line 1: ",
    "could not extract: toml: line 1: ",
    'could not extract: toml: line 1 (last key "a"): ',
    "XML syntax error on line 1: ",
    "could not extract pom from <file>: ",
    "could not extract pom from <file>: XML syntax error on line 1: ",
    "could not extract: <file>:1: ",
    "could not extract: <file>:1:2: ",
    "Open(<file>): ",
)

#: Ordinary failure text, none of it a parser message.
_FAILURE_PHRASES: tuple[str, ...] = (
    "permission denied",
    "connection refused",
    "connection reset by peer",
    "no such file or directory",
    "timed out",
    "i/o timeout",
    "failed reading X",
    "failed reading config: permission denied",
    "rpc error: code = Unavailable desc = unavailable",
    "context deadline exceeded",
    "too many open files",
    "unexpected EOF reading config",
    "EOF while reading the registry",
)


@pytest.mark.parametrize("prefix", _FAMILY_PREFIXES)
def test_family_prefix_followed_by_failure_text_is_never_dropped(
    prefix: str,
) -> None:
    """A parser prefix does not make ordinary failure text a parser message."""
    errors = [
        (prefix + phrase).replace("<file>", _PRINTED) for phrase in _FAILURE_PHRASES
    ]

    dropped = [
        error for error in errors if excluded_file_template(error, _PRINTED) is not None
    ]

    assert_that(dropped).is_empty()


@pytest.mark.parametrize(
    "line",
    [
        'could not extract: toml: line 1 (last key "' + "a" * 100_000,
        "could not extract: yaml: line 1: " + "a " * 50_000,
        "XML syntax error on line 1: " + "<a>" * 34_000,
        "could not extract: invalid character '" + "\\" * 100_000,
        "could not extract: json: cannot unmarshal number into Go struct field "
        + "map[" * 25_000,
        f"could not extract: {_PRINTED}:" + "1:" * 50_000,
    ],
    ids=["toml-key", "words", "xml-elements", "backslashes", "go-type", "numbers"],
)
def test_adversarial_line_is_classified_quickly(line: str, tmp_path: Path) -> None:
    """A 100 KB line for an excluded file does not backtrack for long."""
    (tmp_path / _PRINTED).parent.mkdir(parents=True)
    (tmp_path / _PRINTED).write_text("broken")
    stderr = f"Error during extraction: (extracting as x/y) {_PRINTED}: {line}\n"

    started = time.perf_counter()
    report = read_stderr(stderr, tmp_path, is_excluded=lambda _path: True)
    elapsed = time.perf_counter() - started

    assert_that(len(line)).is_greater_than_or_equal_to(100_000)
    assert_that(report.errors).is_length(1)
    assert_that(report.excluded).is_zero()
    assert_that(elapsed).is_less_than(0.5)
