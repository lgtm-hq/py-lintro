"""Tests for Braille encoding/decoding."""

import numpy as np
import pytest

from ascii_resizer.braille import BRAILLE_BASE, BrailleCodec


class TestBrailleCodec:
    """Tests for BrailleCodec."""

    def test_is_braille_valid(self) -> None:
        """Braille characters should be detected."""
        # Empty braille (U+2800)
        assert BrailleCodec.is_braille("⠀") is True
        # Full braille (U+28FF)
        assert BrailleCodec.is_braille("⣿") is True
        # Random braille
        assert BrailleCodec.is_braille("⠃") is True

    def test_is_braille_invalid(self) -> None:
        """Non-Braille characters should not be detected."""
        assert BrailleCodec.is_braille("A") is False
        assert BrailleCodec.is_braille(" ") is False
        assert BrailleCodec.is_braille("█") is False
        assert BrailleCodec.is_braille("") is False
        assert BrailleCodec.is_braille("⠀⠀") is False  # Multi-char

    def test_char_to_dots_empty(self) -> None:
        """Empty braille character should have 0 dots."""
        assert BrailleCodec.char_to_dots("⠀") == 0

    def test_char_to_dots_full(self) -> None:
        """Full braille character should have all dots (255)."""
        assert BrailleCodec.char_to_dots("⣿") == 255

    def test_char_to_dots_specific(self) -> None:
        """Specific dot patterns should decode correctly."""
        # Dot 1 only (top-left)
        assert BrailleCodec.char_to_dots("⠁") == 0x01
        # Dot 4 only (top-right)
        assert BrailleCodec.char_to_dots("⠈") == 0x08
        # Dots 1 and 2
        assert BrailleCodec.char_to_dots("⠃") == 0x03

    def test_dots_to_char_roundtrip(self) -> None:
        """Converting dots to char and back should preserve value."""
        for dots in [0, 1, 127, 255]:
            char = BrailleCodec.dots_to_char(dots)
            assert BrailleCodec.char_to_dots(char) == dots

    def test_char_to_pixels_empty(self) -> None:
        """Empty braille should produce all-zero pixels."""
        pixels = BrailleCodec.char_to_pixels("⠀")
        assert pixels.shape == (4, 2)
        assert pixels.sum() == 0

    def test_char_to_pixels_full(self) -> None:
        """Full braille should produce all-one pixels."""
        pixels = BrailleCodec.char_to_pixels("⣿")
        assert pixels.shape == (4, 2)
        assert pixels.sum() == 8  # All 8 dots

    def test_char_to_pixels_specific(self) -> None:
        """Specific patterns should decode to correct pixel positions."""
        # Dot 1 is at position (0, 0)
        pixels = BrailleCodec.char_to_pixels("⠁")
        assert pixels[0, 0] == 1
        assert pixels.sum() == 1

        # Dot 4 is at position (0, 1)
        pixels = BrailleCodec.char_to_pixels("⠈")
        assert pixels[0, 1] == 1
        assert pixels.sum() == 1

        # Dot 8 is at position (3, 1)
        pixels = BrailleCodec.char_to_pixels("⢀")
        assert pixels[3, 1] == 1
        assert pixels.sum() == 1

    def test_pixels_to_char_empty(self) -> None:
        """All-zero pixels should produce empty braille."""
        pixels = np.zeros((4, 2), dtype=np.uint8)
        char = BrailleCodec.pixels_to_char(pixels, threshold=1)
        assert char == "⠀"

    def test_pixels_to_char_full(self) -> None:
        """All-one pixels should produce full braille."""
        pixels = np.ones((4, 2), dtype=np.uint8)
        char = BrailleCodec.pixels_to_char(pixels, threshold=1)
        assert char == "⣿"

    def test_encode_decode_roundtrip(self) -> None:
        """Encoding then decoding should preserve the image."""
        # Create a simple test pattern
        original = np.array(
            [
                [1, 0, 1, 0],
                [0, 1, 0, 1],
                [1, 1, 0, 0],
                [0, 0, 1, 1],
            ],
            dtype=np.uint8,
        )

        # Encode to braille
        lines = BrailleCodec.encode_art(original, threshold=1)

        # Should be 1 row, 2 chars wide (4 pixels / 2 pixels per char)
        assert len(lines) == 1
        assert len(lines[0]) == 2

        # Decode back
        decoded = BrailleCodec.decode_art(lines)

        # Should match original
        np.testing.assert_array_equal(decoded, original)

    def test_decode_art_handles_varying_widths(self) -> None:
        """Decode should handle lines of different lengths."""
        lines = ["⣿⣿⣿", "⣿⣿"]  # 3 chars, 2 chars

        pixels = BrailleCodec.decode_art(lines)

        # Should be padded to width of longest line
        assert pixels.shape == (8, 6)  # 2 rows * 4 height, 3 cols * 2 width

    def test_encode_art_pads_to_char_boundary(self) -> None:
        """Encode should pad images that don't fit char boundaries."""
        # 3x3 image (not multiple of 4x2)
        original = np.ones((3, 3), dtype=np.uint8)

        lines = BrailleCodec.encode_art(original, threshold=1)

        # Should produce 1 line (ceil(3/4) = 1), 2 chars (ceil(3/2) = 2)
        assert len(lines) == 1
        assert len(lines[0]) == 2
