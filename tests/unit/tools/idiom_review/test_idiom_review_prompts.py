"""Tests for idiom-review prompt construction."""

from __future__ import annotations

from assertpy import assert_that

from lintro.tools.idiom_review.prompts import (
    build_duplication_prompt,
    build_file_review_prompt,
)


def test_build_file_review_prompt_substitutes_placeholders() -> None:
    """The rendered prompt embeds file, language, and numbered source."""
    source = "found = False\nfor x in items:\n    found = True\n"
    system, user = build_file_review_prompt(
        file_path="src/mod.py",
        source=source,
        language="python",
    )

    assert_that(system).contains("IDIOMATIC MISSES")
    assert_that(user).contains("Language: python")
    assert_that(user).contains("File: src/mod.py")
    # Line numbers are prefixed so the model can cite spans.
    assert_that(user).contains("1: found = False")
    assert_that(user).contains("3:     found = True")
    # The idiom checklist and JSON contract are present.
    assert_that(user).contains("any()/all()")
    assert_that(user).contains('"findings"')
    assert_that(user).contains("confidence")


def test_build_file_review_prompt_marks_source_as_untrusted() -> None:
    """Untrusted source is delimited by boundary markers."""
    _system, user = build_file_review_prompt(
        file_path="a.py",
        source="print('hi')",
        language="python",
    )

    assert_that(user).contains("UNTRUSTED_SOURCE")
    assert_that(user).contains("DATA")


def test_build_duplication_prompt_embeds_signature_map() -> None:
    """The duplication prompt embeds the signature map and JSON contract."""
    signature_map = "# a.py:1 (function)\ndef slugify(text)\nreturn text"
    system, user = build_duplication_prompt(signature_map)

    assert_that(system).contains("DUPLICATION")
    assert_that(user).contains("def slugify(text)")
    assert_that(user).contains('"duplicate_groups"')
    assert_that(user).contains("UNTRUSTED_SOURCE")
