"""Unit tests for merge_pr_comment utilities.

Tests cover:
- Basic merge functionality
- History extraction and flattening
- Maximum history limit enforcement
- Timestamp extraction
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from assertpy import assert_that

# Add scripts directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts" / "utils"))

from merge_pr_comment import (
    MAX_HISTORY_RUNS,
    _extract_details_blocks,
    _extract_timestamp_from_details,
    merge_comment_bodies,
)

# =============================================================================
# Tests for _extract_details_blocks
# =============================================================================


def test_extract_details_blocks_no_blocks() -> None:
    """Content without details blocks returns content and empty list."""
    content = "Some content without details"

    remaining, blocks = _extract_details_blocks(content)

    assert_that(remaining).is_equal_to(content)
    assert_that(blocks).is_empty()


def test_extract_details_blocks_single_block() -> None:
    """Extract single details block from content."""
    content = """Main content

<details>
<summary>Previous run</summary>

Historical content
</details>

After details"""

    remaining, blocks = _extract_details_blocks(content)

    assert_that(remaining).contains("Main content")
    assert_that(remaining).contains("After details")
    assert_that(blocks).is_length(1)
    assert_that(blocks[0]).contains("Previous run")
    assert_that(blocks[0]).contains("Historical content")


def test_extract_details_blocks_multiple_blocks() -> None:
    """Extract multiple details blocks from content."""
    content = """Main content

<details>
<summary>Run #2</summary>
Content 2
</details>

<details>
<summary>Run #1</summary>
Content 1
</details>"""

    remaining, blocks = _extract_details_blocks(content)

    assert_that(remaining).contains("Main content")
    assert_that(blocks).is_length(2)
    assert_that(blocks[0]).contains("Run #2")
    assert_that(blocks[1]).contains("Run #1")


def test_extract_details_blocks_no_newline_after_tag() -> None:
    """Extract details block when no newline follows opening tag."""
    content = """Main content

<details><summary>Previous run (2026-01-25)</summary>

Inner content
</details>

After details"""

    remaining, blocks = _extract_details_blocks(content)

    assert_that(remaining).contains("Main content")
    assert_that(remaining).contains("After details")
    assert_that(blocks).is_length(1)
    assert_that(blocks[0]).contains("Previous run")
    assert_that(blocks[0]).contains("Inner content")


def test_extract_details_blocks_preserves_non_history_blocks() -> None:
    """Non-history details blocks are preserved in remaining content."""
    content = """Main content

<details>
<summary>Click to expand</summary>

User-created collapsible content
</details>

<details>
<summary>Previous run (2026-01-25)</summary>

Historical content
</details>

After details"""

    remaining, blocks = _extract_details_blocks(content)

    # Non-history block should be in remaining content
    assert_that(remaining).contains("Click to expand")
    assert_that(remaining).contains("User-created collapsible content")
    # History block should be extracted
    assert_that(blocks).is_length(1)
    assert_that(blocks[0]).contains("Previous run")
    assert_that(blocks[0]).contains("Historical content")


# =============================================================================
# Tests for _extract_timestamp_from_details
# =============================================================================


@pytest.mark.parametrize(
    ("block", "expected"),
    [
        pytest.param(
            "<details>\n<summary>Previous run (2026-01-25 19:00:00 UTC)</summary>",
            "2026-01-25 19:00:00 UTC",
            id="standard_format",
        ),
        pytest.param(
            "<details>\n<summary>📜 Run #2 (2026-01-25 18:00:00 UTC)</summary>",
            "2026-01-25 18:00:00 UTC",
            id="run_number_format",
        ),
    ],
)
def test_extract_timestamp_from_details_valid(block: str, expected: str) -> None:
    """Extract timestamp from details block with valid timestamp."""
    result = _extract_timestamp_from_details(block)

    assert_that(result).is_equal_to(expected)


def test_extract_timestamp_from_details_no_timestamp() -> None:
    """Return None when no timestamp is found."""
    block = "<details>\n<summary>Some content without timestamp</summary>"

    result = _extract_timestamp_from_details(block)

    assert_that(result).is_none()


# =============================================================================
# Tests for merge_comment_bodies - basic merge
# =============================================================================


def test_merge_no_previous_body() -> None:
    """First run creates simple merged body with marker."""
    marker = "<!-- lintro-report -->"
    new_body = "## Results\nAll good!"

    result = merge_comment_bodies(
        marker=marker,
        previous_body=None,
        new_body=new_body,
    )

    assert_that(result).starts_with(marker)
    assert_that(result).contains("All good!")
    assert_that(result).does_not_contain("<details>")


def test_merge_second_run_creates_history() -> None:
    """Second run wraps previous content in collapsed section."""
    marker = "<!-- lintro-report -->"
    previous = "<!-- lintro-report -->\n\n## First Run\nFirst results"
    new_body = "## Second Run\nNew results"

    result = merge_comment_bodies(
        marker=marker,
        previous_body=previous,
        new_body=new_body,
    )

    assert_that(result).starts_with(marker)
    assert_that(result).contains("## Second Run")
    assert_that(result).contains("<details>")
    assert_that(result).contains("First results")
    assert_that(result).contains("Previous run")


# =============================================================================
# Tests for merge_comment_bodies - content placement
# =============================================================================


def test_merge_new_content_above_history() -> None:
    """New content appears above historical sections by default."""
    marker = "<!-- test -->"
    previous = "<!-- test -->\n\nOld content"
    new_body = "New content"

    result = merge_comment_bodies(
        marker=marker,
        previous_body=previous,
        new_body=new_body,
    )

    new_pos = result.find("New content")
    details_pos = result.find("<details>")

    assert_that(new_pos).is_less_than(details_pos)


def test_merge_place_new_below() -> None:
    """New content can be placed below history when specified."""
    marker = "<!-- test -->"
    previous = "<!-- test -->\n\nOld content"
    new_body = "New content"

    result = merge_comment_bodies(
        marker=marker,
        previous_body=previous,
        new_body=new_body,
        place_new_above=False,
    )

    new_pos = result.find("New content")
    details_pos = result.find("<details>")

    assert_that(details_pos).is_less_than(new_pos)


# =============================================================================
# Tests for merge_comment_bodies - history management
# =============================================================================


def test_merge_preserves_existing_history_blocks() -> None:
    """Multiple runs preserve all historical sections in flat structure."""
    marker = "<!-- test -->"
    previous = """<!-- test -->

## Run 2
Current content

<details>
<summary>📜 Previous run (2026-01-25 18:00:00 UTC)</summary>

## Run 1
First content
</details>"""

    new_body = "## Run 3\nLatest content"

    result = merge_comment_bodies(
        marker=marker,
        previous_body=previous,
        new_body=new_body,
    )

    assert_that(result).contains("## Run 3")
    # Should have 2 separate details blocks (not nested)
    details_count = result.count("<details>")
    assert_that(details_count).is_equal_to(2)
    assert_that(result).contains("Run 2")
    assert_that(result).contains("Run 1")


def test_merge_history_limit_enforced() -> None:
    """History is limited to MAX_HISTORY_RUNS entries."""
    marker = "<!-- test -->"

    # Create previous body with MAX_HISTORY_RUNS history blocks
    # Use "Run #N" format to match the history pattern
    history_blocks = "\n\n".join(
        f"<details>\n<summary>Run #{i} (2026-01-25 0{i}:00:00 UTC)</summary>\n"
        f"Content {i}\n</details>"
        for i in range(MAX_HISTORY_RUNS)
    )
    previous = f"<!-- test -->\n\nCurrent content\n\n{history_blocks}"
    new_body = "Latest content"

    result = merge_comment_bodies(
        marker=marker,
        previous_body=previous,
        new_body=new_body,
    )

    # Previous current becomes history, oldest history is dropped
    details_count = result.count("<details>")
    assert_that(details_count).is_equal_to(MAX_HISTORY_RUNS)


# =============================================================================
# Tests for merge_comment_bodies - marker handling
# =============================================================================


def test_merge_marker_only_appears_once() -> None:
    """Marker appears exactly once at the top of merged body."""
    marker = "<!-- lintro-report -->"
    # New body contains the marker (which should be removed)
    new_body = "<!-- lintro-report -->\n\n## Results"
    previous = "<!-- lintro-report -->\n\nOld results"

    result = merge_comment_bodies(
        marker=marker,
        previous_body=previous,
        new_body=new_body,
    )

    marker_count = result.count(marker)
    assert_that(marker_count).is_equal_to(1)
    assert_that(result).starts_with(marker)


# =============================================================================
# Tests for merge_comment_bodies - newline normalization
# =============================================================================


def test_merge_normalizes_newlines() -> None:
    """Windows-style newlines are normalized to Unix-style."""
    marker = "<!-- test -->"
    new_body = "Line 1\r\nLine 2\rLine 3"

    result = merge_comment_bodies(
        marker=marker,
        previous_body=None,
        new_body=new_body,
    )

    assert_that(result).does_not_contain("\r\n")
    assert_that(result).does_not_contain("\r")
    assert_that(result).contains("Line 1\nLine 2\nLine 3")
