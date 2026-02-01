"""ASCII art resizing using image processing techniques."""

from enum import StrEnum, auto
from typing import Literal

import numpy as np
from numpy.typing import NDArray
from PIL import Image

from ascii_resizer.braille import BrailleCodec


class ResizeMethod(StrEnum):
    """Available resize interpolation methods."""

    NEAREST = auto()  # Best for pixel art, preserves hard edges
    BILINEAR = auto()  # Smooth, good for general use
    BICUBIC = auto()  # Smoother, can introduce artifacts
    LANCZOS = auto()  # High quality, best for downscaling


# Map our enum to PIL constants
PIL_RESAMPLE = {
    ResizeMethod.NEAREST: Image.Resampling.NEAREST,
    ResizeMethod.BILINEAR: Image.Resampling.BILINEAR,
    ResizeMethod.BICUBIC: Image.Resampling.BICUBIC,
    ResizeMethod.LANCZOS: Image.Resampling.LANCZOS,
}


class ArtType(StrEnum):
    """Types of ASCII art we can handle."""

    BRAILLE = auto()
    # Future: BLOCK, TRADITIONAL


class AsciiResizer:
    """Resize ASCII art to target dimensions."""

    def __init__(
        self,
        method: ResizeMethod = ResizeMethod.NEAREST,
        threshold: int = 128,
    ) -> None:
        """Initialize resizer.

        Args:
            method: Interpolation method for resizing.
            threshold: Pixel value threshold for binarization (0-255).
        """
        self.method = method
        self.threshold = threshold

    @staticmethod
    def detect_art_type(lines: list[str]) -> ArtType | None:
        """Detect the type of ASCII art.

        Args:
            lines: Lines of ASCII art.

        Returns:
            Detected art type, or None if unknown.
        """
        braille_count = 0
        total_printable = 0

        for line in lines:
            for char in line:
                if char.strip():
                    total_printable += 1
                    if BrailleCodec.is_braille(char):
                        braille_count += 1

        if total_printable == 0:
            return None

        # If >80% Braille characters, it's Braille art
        if braille_count / total_printable > 0.8:
            return ArtType.BRAILLE

        return None

    def resize_bitmap(
        self,
        pixels: NDArray[np.uint8],
        target_width: int,
        target_height: int,
    ) -> NDArray[np.uint8]:
        """Resize a pixel bitmap using PIL.

        Args:
            pixels: Source bitmap (0/1 or 0-255 values).
            target_width: Target width in pixels.
            target_height: Target height in pixels.

        Returns:
            Resized bitmap.
        """
        if pixels.size == 0:
            return np.zeros((target_height, target_width), dtype=np.uint8)

        # Convert to PIL Image (scale to 0-255 if binary)
        if pixels.max() <= 1:
            img_array = pixels.astype(np.uint8) * 255
        else:
            img_array = pixels.astype(np.uint8)

        img = Image.fromarray(img_array, mode="L")

        # Resize
        resized = img.resize(
            (target_width, target_height),
            resample=PIL_RESAMPLE[self.method],
        )

        # Convert back to array and binarize
        result = np.array(resized)

        # Apply threshold for binary output
        return (result >= self.threshold).astype(np.uint8)

    def resize_braille(
        self,
        lines: list[str],
        target_chars_wide: int,
        target_chars_tall: int,
        preserve_aspect: bool = True,
    ) -> list[str]:
        """Resize Braille art to target character dimensions.

        Args:
            lines: Source Braille art lines.
            target_chars_wide: Target width in characters.
            target_chars_tall: Target height in characters.
            preserve_aspect: If True, preserve aspect ratio and pad with empty space.

        Returns:
            Resized Braille art lines.
        """
        # Decode to pixels
        pixels = BrailleCodec.decode_art(lines)

        empty_char = BrailleCodec.dots_to_char(0)

        if pixels.size == 0:
            # Return empty art at target size
            return [empty_char * target_chars_wide] * target_chars_tall

        # Calculate target pixel dimensions
        target_pixel_width = target_chars_wide * BrailleCodec.CHAR_WIDTH
        target_pixel_height = target_chars_tall * BrailleCodec.CHAR_HEIGHT

        src_height, src_width = pixels.shape

        if preserve_aspect:
            # Calculate scale factor that fits content while preserving aspect ratio
            scale_x = target_pixel_width / src_width
            scale_y = target_pixel_height / src_height
            scale = min(scale_x, scale_y)

            # Calculate scaled dimensions
            scaled_width = int(src_width * scale)
            scaled_height = int(src_height * scale)

            # Ensure dimensions are multiples of Braille char size for clean encoding
            char_w = BrailleCodec.CHAR_WIDTH
            char_h = BrailleCodec.CHAR_HEIGHT
            scaled_width = max(char_w, (scaled_width // char_w) * char_w)
            scaled_height = max(char_h, (scaled_height // char_h) * char_h)

            # Resize the bitmap to scaled size
            resized = self.resize_bitmap(pixels, scaled_width, scaled_height)

            # Create target canvas and center the resized content
            canvas = np.zeros((target_pixel_height, target_pixel_width), dtype=np.uint8)
            offset_x = (target_pixel_width - scaled_width) // 2
            offset_y = (target_pixel_height - scaled_height) // 2
            y_end = offset_y + scaled_height
            x_end = offset_x + scaled_width
            canvas[offset_y:y_end, offset_x:x_end] = resized

            # Encode back to Braille
            return BrailleCodec.encode_art(canvas, threshold=1)
        else:
            # Stretch to fill (original behavior)
            resized = self.resize_bitmap(
                pixels, target_pixel_width, target_pixel_height
            )
            return BrailleCodec.encode_art(resized, threshold=1)

    def resize(
        self,
        lines: list[str],
        target_width: int,
        target_height: int,
        art_type: ArtType | None = None,
        preserve_aspect: bool = True,
    ) -> list[str]:
        """Resize ASCII art to target dimensions.

        Args:
            lines: Source ASCII art lines.
            target_width: Target width (interpretation depends on art type).
            target_height: Target height (interpretation depends on art type).
            art_type: Art type, or None to auto-detect.
            preserve_aspect: If True, preserve aspect ratio and pad with empty space.

        Returns:
            Resized ASCII art lines.
        """
        if art_type is None:
            art_type = self.detect_art_type(lines)

        if art_type == ArtType.BRAILLE:
            return self.resize_braille(
                lines, target_width, target_height, preserve_aspect
            )

        # Fallback: pad/truncate (no actual resizing)
        result = []
        for i in range(target_height):
            if i < len(lines):
                line = lines[i]
                if len(line) < target_width:
                    line = line + " " * (target_width - len(line))
                elif len(line) > target_width:
                    line = line[:target_width]
                result.append(line)
            else:
                result.append(" " * target_width)

        return result


def parse_sections(content: str) -> list[list[str]]:
    """Parse a file into sections separated by blank lines.

    Args:
        content: File content.

    Returns:
        List of sections, each section is a list of lines.
    """
    sections: list[list[str]] = []
    current: list[str] = []

    for line in content.split("\n"):
        # Preserve the line content but strip only trailing whitespace
        stripped = line.rstrip()
        if stripped:
            current.append(stripped)
        elif current:
            sections.append(current)
            current = []

    if current:
        sections.append(current)

    return sections


def format_sections(sections: list[list[str]]) -> str:
    """Format sections back into file content.

    Args:
        sections: List of sections.

    Returns:
        File content with sections separated by blank lines.
    """
    return "\n\n".join("\n".join(section) for section in sections) + "\n"
