"""Braille character encoding/decoding for ASCII art.

Braille Unicode characters (U+2800-U+28FF) represent a 2x4 dot grid.
Each character encodes 8 pixels, making them ideal for pixel art.

Dot positions and their bit values:
    1 4     (0x01) (0x08)
    2 5     (0x02) (0x10)
    3 6     (0x04) (0x20)
    7 8     (0x40) (0x80)
"""

import numpy as np
from numpy.typing import NDArray

# Braille Unicode block starts at U+2800
BRAILLE_BASE = 0x2800

# Dot positions map to bits: dots are numbered 1-8
# Dot layout in 2x4 grid (row, col) -> bit value
DOT_BITS: dict[tuple[int, int], int] = {
    (0, 0): 0x01,  # dot 1
    (1, 0): 0x02,  # dot 2
    (2, 0): 0x04,  # dot 3
    (0, 1): 0x08,  # dot 4
    (1, 1): 0x10,  # dot 5
    (2, 1): 0x20,  # dot 6
    (3, 0): 0x40,  # dot 7
    (3, 1): 0x80,  # dot 8
}


class BrailleCodec:
    """Encode and decode Braille characters to/from pixel arrays."""

    # Pixels per Braille character
    CHAR_HEIGHT = 4
    CHAR_WIDTH = 2

    @staticmethod
    def is_braille(char: str) -> bool:
        """Check if a character is a Braille Unicode character."""
        if len(char) != 1:
            return False
        code = ord(char)
        return BRAILLE_BASE <= code <= BRAILLE_BASE + 0xFF

    @staticmethod
    def char_to_dots(char: str) -> int:
        """Convert a Braille character to its dot pattern (0-255)."""
        if not BrailleCodec.is_braille(char):
            return 0
        return ord(char) - BRAILLE_BASE

    @staticmethod
    def dots_to_char(dots: int) -> str:
        """Convert a dot pattern (0-255) to a Braille character."""
        return chr(BRAILLE_BASE + (dots & 0xFF))

    @staticmethod
    def char_to_pixels(char: str) -> NDArray[np.uint8]:
        """Decode a single Braille character to a 4x2 pixel array.

        Args:
            char: A single Braille Unicode character.

        Returns:
            4x2 numpy array where 1 = dot present, 0 = no dot.
        """
        pixels = np.zeros((4, 2), dtype=np.uint8)
        dots = BrailleCodec.char_to_dots(char)

        for (row, col), bit in DOT_BITS.items():
            if dots & bit:
                pixels[row, col] = 1

        return pixels

    @staticmethod
    def pixels_to_char(pixels: NDArray[np.uint8], threshold: int = 128) -> str:
        """Encode a 4x2 pixel array to a Braille character.

        Args:
            pixels: 4x2 array of pixel values (0-255 or 0-1).
            threshold: Values >= threshold become dots. Default 128 for
                       grayscale, use 1 for binary arrays.

        Returns:
            A single Braille Unicode character.
        """
        dots = 0
        for (row, col), bit in DOT_BITS.items():
            if row < pixels.shape[0] and col < pixels.shape[1]:
                if pixels[row, col] >= threshold:
                    dots |= bit

        return BrailleCodec.dots_to_char(dots)

    @classmethod
    def decode_art(cls, lines: list[str]) -> NDArray[np.uint8]:
        """Decode Braille art to a pixel bitmap.

        Args:
            lines: List of strings containing Braille art.

        Returns:
            2D numpy array (height, width) of pixel values (0 or 1).
        """
        if not lines:
            return np.zeros((0, 0), dtype=np.uint8)

        # Find dimensions
        max_chars_wide = max(len(line) for line in lines)
        char_rows = len(lines)

        # Create pixel array
        pixel_height = char_rows * cls.CHAR_HEIGHT
        pixel_width = max_chars_wide * cls.CHAR_WIDTH
        pixels = np.zeros((pixel_height, pixel_width), dtype=np.uint8)

        # Decode each character
        for row_idx, line in enumerate(lines):
            for col_idx, char in enumerate(line):
                if cls.is_braille(char):
                    char_pixels = cls.char_to_pixels(char)
                    y = row_idx * cls.CHAR_HEIGHT
                    x = col_idx * cls.CHAR_WIDTH
                    pixels[y : y + cls.CHAR_HEIGHT, x : x + cls.CHAR_WIDTH] = (
                        char_pixels
                    )

        return pixels

    @classmethod
    def encode_art(
        cls,
        pixels: NDArray[np.uint8],
        threshold: int = 1,
    ) -> list[str]:
        """Encode a pixel bitmap to Braille art.

        Args:
            pixels: 2D array (height, width) of pixel values.
            threshold: Pixel value threshold for "on" dots.

        Returns:
            List of strings containing Braille art.
        """
        if pixels.size == 0:
            return []

        height, width = pixels.shape

        # Pad to multiple of character dimensions
        pad_height = (cls.CHAR_HEIGHT - height % cls.CHAR_HEIGHT) % cls.CHAR_HEIGHT
        pad_width = (cls.CHAR_WIDTH - width % cls.CHAR_WIDTH) % cls.CHAR_WIDTH

        if pad_height or pad_width:
            pixels = np.pad(
                pixels,
                ((0, pad_height), (0, pad_width)),
                mode="constant",
                constant_values=0,
            )

        height, width = pixels.shape
        char_rows = height // cls.CHAR_HEIGHT
        char_cols = width // cls.CHAR_WIDTH

        lines = []
        for row in range(char_rows):
            line_chars = []
            for col in range(char_cols):
                y = row * cls.CHAR_HEIGHT
                x = col * cls.CHAR_WIDTH
                block = pixels[y : y + cls.CHAR_HEIGHT, x : x + cls.CHAR_WIDTH]
                char = cls.pixels_to_char(block, threshold=threshold)
                line_chars.append(char)
            lines.append("".join(line_chars))

        return lines
