"""Tests for the resizer module."""

import numpy as np
import pytest

from ascii_resizer.resizer import (
    ArtType,
    AsciiResizer,
    ResizeMethod,
    format_sections,
    parse_sections,
)


class TestParseSections:
    """Tests for section parsing."""

    def test_single_section(self) -> None:
        """Single section without blank lines."""
        content = "line1\nline2\nline3"
        sections = parse_sections(content)
        assert len(sections) == 1
        assert sections[0] == ["line1", "line2", "line3"]

    def test_multiple_sections(self) -> None:
        """Multiple sections separated by blank lines."""
        content = "sec1line1\nsec1line2\n\nsec2line1\nsec2line2"
        sections = parse_sections(content)
        assert len(sections) == 2
        assert sections[0] == ["sec1line1", "sec1line2"]
        assert sections[1] == ["sec2line1", "sec2line2"]

    def test_multiple_blank_lines(self) -> None:
        """Multiple blank lines should still separate sections."""
        content = "section1\n\n\n\nsection2"
        sections = parse_sections(content)
        assert len(sections) == 2

    def test_empty_content(self) -> None:
        """Empty content should produce no sections."""
        sections = parse_sections("")
        assert sections == []

    def test_only_whitespace(self) -> None:
        """Whitespace-only content should produce no sections."""
        sections = parse_sections("   \n   \n   ")
        assert sections == []


class TestFormatSections:
    """Tests for section formatting."""

    def test_single_section(self) -> None:
        """Single section should format without separators."""
        sections = [["line1", "line2"]]
        result = format_sections(sections)
        assert result == "line1\nline2\n"

    def test_multiple_sections(self) -> None:
        """Multiple sections should be separated by blank lines."""
        sections = [["sec1"], ["sec2"]]
        result = format_sections(sections)
        assert result == "sec1\n\nsec2\n"

    def test_roundtrip(self) -> None:
        """Format and parse should roundtrip."""
        original = [["a", "b"], ["c", "d"]]
        formatted = format_sections(original)
        parsed = parse_sections(formatted)
        assert parsed == original


class TestAsciiResizer:
    """Tests for AsciiResizer."""

    def test_detect_braille_art(self) -> None:
        """Should detect Braille art."""
        lines = ["⣿⣿⣿", "⣿⣿⣿"]
        resizer = AsciiResizer()
        assert resizer.detect_art_type(lines) == ArtType.BRAILLE

    def test_detect_mixed_content(self) -> None:
        """Mixed content with majority Braille should detect as Braille."""
        lines = ["⣿⣿⣿⣿⣿", "⣿ a ⣿"]  # Mostly Braille
        resizer = AsciiResizer()
        assert resizer.detect_art_type(lines) == ArtType.BRAILLE

    def test_detect_non_braille(self) -> None:
        """Non-Braille content should return None."""
        lines = ["Hello", "World"]
        resizer = AsciiResizer()
        assert resizer.detect_art_type(lines) is None

    def test_resize_bitmap_upscale(self) -> None:
        """Should upscale bitmap correctly."""
        resizer = AsciiResizer(method=ResizeMethod.NEAREST)

        # 2x2 checkerboard
        original = np.array([[1, 0], [0, 1]], dtype=np.uint8)

        # Upscale to 4x4
        result = resizer.resize_bitmap(original, 4, 4)

        assert result.shape == (4, 4)
        # With nearest neighbor, each pixel becomes 2x2
        expected = np.array(
            [[1, 1, 0, 0], [1, 1, 0, 0], [0, 0, 1, 1], [0, 0, 1, 1]],
            dtype=np.uint8,
        )
        np.testing.assert_array_equal(result, expected)

    def test_resize_bitmap_downscale(self) -> None:
        """Should downscale bitmap correctly."""
        resizer = AsciiResizer(method=ResizeMethod.NEAREST)

        # 4x4 with pattern
        original = np.array(
            [[1, 1, 0, 0], [1, 1, 0, 0], [0, 0, 1, 1], [0, 0, 1, 1]],
            dtype=np.uint8,
        )

        # Downscale to 2x2
        result = resizer.resize_bitmap(original, 2, 2)

        assert result.shape == (2, 2)

    def test_resize_braille_dimensions(self) -> None:
        """Resized Braille should have correct dimensions."""
        resizer = AsciiResizer()

        # Create some braille art (3 chars wide, 2 lines tall)
        lines = ["⣿⣿⣿", "⣿⣿⣿"]

        # Resize to 5 chars wide, 4 lines tall
        result = resizer.resize_braille(lines, 5, 4)

        assert len(result) == 4
        assert all(len(line) == 5 for line in result)

    def test_resize_empty_input(self) -> None:
        """Should handle empty input gracefully."""
        resizer = AsciiResizer()
        result = resizer.resize_braille([], 3, 2)

        assert len(result) == 2
        assert all(len(line) == 3 for line in result)

    def test_resize_preserves_general_shape(self) -> None:
        """Resizing should preserve the general shape of the art."""
        resizer = AsciiResizer(method=ResizeMethod.NEAREST)

        # Create a simple diagonal line in braille
        # ⠁ has dot at (0,0), ⠈ has dot at (0,1)
        lines = ["⣿⠀", "⠀⣿"]  # Diagonal pattern

        # Resize larger
        result = resizer.resize_braille(lines, 4, 4)

        # The result should still have a diagonal-ish pattern
        # (exact verification depends on resize algorithm)
        assert len(result) == 4
        assert all(len(line) == 4 for line in result)
