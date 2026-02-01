"""Command-line interface for ascii-resizer."""

import sys
from pathlib import Path

import click

from ascii_resizer.resizer import (
    AsciiResizer,
    ResizeMethod,
    format_sections,
    parse_sections,
)


@click.group()
@click.version_option()
def main() -> None:
    """Resize and standardize ASCII art."""


@main.command()
@click.argument("input_file", type=click.Path(exists=True, path_type=Path))
@click.argument("output_file", type=click.Path(path_type=Path), required=False)
@click.option(
    "-w",
    "--width",
    type=int,
    required=True,
    help="Target width in characters.",
)
@click.option(
    "-h",
    "--height",
    type=int,
    required=True,
    help="Target height in characters (lines).",
)
@click.option(
    "-m",
    "--method",
    type=click.Choice(["nearest", "bilinear", "bicubic", "lanczos"]),
    default="nearest",
    help="Resize interpolation method. Default: nearest (best for pixel art).",
)
@click.option(
    "-t",
    "--threshold",
    type=int,
    default=128,
    help="Binarization threshold (0-255). Default: 128.",
)
@click.option(
    "--in-place",
    "-i",
    is_flag=True,
    help="Modify input file in place.",
)
@click.option(
    "--stretch",
    is_flag=True,
    help="Stretch to fill target dimensions (don't preserve aspect ratio).",
)
def resize(
    input_file: Path,
    output_file: Path | None,
    width: int,
    height: int,
    method: str,
    threshold: int,
    in_place: bool,
    stretch: bool,
) -> None:
    """Resize ASCII art file to target dimensions.

    INPUT_FILE: Path to ASCII art file (sections separated by blank lines).
    OUTPUT_FILE: Output path. If not specified, prints to stdout.
    """
    if in_place and output_file:
        raise click.UsageError("Cannot use --in-place with an output file.")

    if in_place:
        output_file = input_file

    # Parse resize method
    resize_method = ResizeMethod[method.upper()]

    # Read input
    content = input_file.read_text(encoding="utf-8")
    sections = parse_sections(content)

    if not sections:
        click.echo("No sections found in input file.", err=True)
        sys.exit(1)

    # Resize each section
    resizer = AsciiResizer(method=resize_method, threshold=threshold)
    resized_sections = []

    for section in sections:
        resized = resizer.resize(section, width, height, preserve_aspect=not stretch)
        resized_sections.append(resized)

    # Format output
    output = format_sections(resized_sections)

    # Write output
    if output_file:
        output_file.write_text(output, encoding="utf-8")
        click.echo(f"Resized {len(sections)} sections to {width}x{height}")
    else:
        click.echo(output, nl=False)


@main.command()
@click.argument("input_file", type=click.Path(exists=True, path_type=Path))
def info(input_file: Path) -> None:
    """Show information about ASCII art file."""
    content = input_file.read_text(encoding="utf-8")
    sections = parse_sections(content)

    click.echo(f"File: {input_file}")
    click.echo(f"Sections: {len(sections)}")

    if not sections:
        return

    # Analyze each section
    resizer = AsciiResizer()

    for i, section in enumerate(sections, 1):
        art_type = resizer.detect_art_type(section)
        width = max(len(line) for line in section) if section else 0
        height = len(section)

        type_str = art_type.value if art_type else "unknown"
        click.echo(f"  Section {i}: {width}x{height} chars ({type_str})")


@main.command()
@click.argument("input_file", type=click.Path(exists=True, path_type=Path))
@click.option(
    "-n",
    "--section",
    type=int,
    default=None,
    help="Section number to display (1-indexed). Shows all if not specified.",
)
def show(input_file: Path, section: int | None) -> None:
    """Display ASCII art sections."""
    content = input_file.read_text(encoding="utf-8")
    sections = parse_sections(content)

    if not sections:
        click.echo("No sections found.", err=True)
        sys.exit(1)

    if section is not None:
        if section < 1 or section > len(sections):
            msg = f"Section {section} not found (file has {len(sections)} sections)."
            click.echo(msg, err=True)
            sys.exit(1)
        sections = [sections[section - 1]]
        indices = [section]
    else:
        indices = list(range(1, len(sections) + 1))

    for idx, sec in zip(indices, sections):
        click.echo(f"=== Section {idx} ===")
        for line in sec:
            click.echo(line)
        click.echo()


if __name__ == "__main__":
    main()
