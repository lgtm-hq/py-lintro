"""Tests for idiom-review signature extraction."""

from __future__ import annotations

from assertpy import assert_that

from lintro.tools.idiom_review.signatures import (
    extract_python_signatures,
    render_signature_map,
)

_SOURCE = """\
def top_level(a, b):
    return a + b


class Widget:
    def method(self, x):
        return x

    async def afetch(self):
        return None
"""


def test_extract_python_signatures_captures_functions_and_classes() -> None:
    """Functions, classes, and methods are all extracted with names."""
    sigs = extract_python_signatures("mod.py", _SOURCE)
    names = {s.name for s in sigs}

    assert_that(names).contains("top_level")
    assert_that(names).contains("Widget")
    assert_that(names).contains("Widget.method")
    assert_that(names).contains("Widget.afetch")


def test_extract_python_signatures_records_location_and_kind() -> None:
    """Each signature records file, kind, and 1-based line."""
    sigs = extract_python_signatures("mod.py", _SOURCE)
    top = next(s for s in sigs if s.name == "top_level")

    assert_that(top.file).is_equal_to("mod.py")
    assert_that(top.kind).is_equal_to("function")
    assert_that(top.line).is_equal_to(1)
    assert_that(top.signature).contains("def top_level(a, b)")


def test_extract_python_signatures_unparseable_returns_empty() -> None:
    """Syntactically invalid source yields no signatures, no exception."""
    assert_that(
        extract_python_signatures("bad.py", "def (:\n"),
    ).is_empty()


def test_render_signature_map_includes_locations() -> None:
    """The rendered map tags each entry with file and line."""
    sigs = extract_python_signatures("mod.py", _SOURCE)
    rendered = render_signature_map(sigs)

    assert_that(rendered).contains("# mod.py:1 (function)")
    assert_that(rendered).contains("def top_level(a, b)")


def test_render_signature_map_empty() -> None:
    """An empty signature list renders to an empty string."""
    assert_that(render_signature_map([])).is_equal_to("")
