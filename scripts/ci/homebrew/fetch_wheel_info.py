#!/usr/bin/env python3
"""Fetch wheel information from PyPI for Homebrew formula generation.

Generates Homebrew resource stanzas for packages that require wheel installation
(e.g., packages that need Rust/maturin or poetry to build from source).

Usage:
    # Universal wheel (py3-none-any)
    python3 fetch_wheel_info.py pydoclint --type universal

    # Platform-specific wheels (macos arm64/x86_64)
    python3 fetch_wheel_info.py pydantic_core --type platform

    # Specific version
    python3 fetch_wheel_info.py pydantic_core --type platform --version 2.41.5
"""

import argparse
import sys

from pypi_utils import (
    WheelInfo,
    fetch_pypi_json,
    find_macos_wheel,
    find_universal_wheel,
)


def generate_universal_resource(
    package: str,
    wheel: WheelInfo,
    comment: str,
) -> str:
    """Generate Homebrew resource stanza for universal wheel.

    Args:
        package: Package name.
        wheel: Wheel information containing URL and SHA256.
        comment: Comment to add above the resource stanza.

    Returns:
        Homebrew resource stanza as a string.
    """
    return f"""  # {comment}
  resource "{package}" do
    url "{wheel.url}"
    sha256 "{wheel.sha256}"
  end"""


def generate_platform_resource(
    package: str,
    arm_wheel: WheelInfo,
    intel_wheel: WheelInfo,
    comment: str,
) -> str:
    """Generate Homebrew resource stanza for platform-specific wheels.

    Args:
        package: Package name.
        arm_wheel: Wheel information for ARM64 architecture.
        intel_wheel: Wheel information for x86_64 architecture.
        comment: Comment to add above the resource stanza.

    Returns:
        Homebrew resource stanza as a string.
    """
    return f"""  # {comment}
  resource "{package}" do
    on_arm do
      url "{arm_wheel.url}"
      sha256 "{arm_wheel.sha256}"
    end
    on_intel do
      url "{intel_wheel.url}"
      sha256 "{intel_wheel.sha256}"
    end
  end"""


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Fetch wheel info from PyPI")
    parser.add_argument("package", help="Package name")
    parser.add_argument(
        "--type",
        choices=["universal", "platform"],
        default="universal",
        help="Wheel type to fetch",
    )
    parser.add_argument(
        "--version",
        default=None,
        help="Specific version to fetch (default: latest)",
    )
    parser.add_argument(
        "--comment",
        default="",
        help="Comment to add above resource stanza",
    )
    args = parser.parse_args()

    data = fetch_pypi_json(args.package, version=args.version)

    if args.type == "universal":
        wheel = find_universal_wheel(data)
        if not wheel:
            print(
                f"Error: No universal wheel found for {args.package}",
                file=sys.stderr,
            )
            sys.exit(1)
        comment = args.comment or f"{args.package} - using wheel"
        print(generate_universal_resource(args.package, wheel, comment))
    else:
        arm_wheel = find_macos_wheel(data, "arm64")
        intel_wheel = find_macos_wheel(data, "x86_64")
        if not arm_wheel or not intel_wheel:
            print(
                f"Error: Missing platform wheels for {args.package}",
                file=sys.stderr,
            )
            print(f"  arm64: {'found' if arm_wheel else 'missing'}", file=sys.stderr)
            print(f"  x86_64: {'found' if intel_wheel else 'missing'}", file=sys.stderr)
            sys.exit(1)
        comment = args.comment or f"{args.package} - using platform-specific wheels"
        print(generate_platform_resource(args.package, arm_wheel, intel_wheel, comment))


if __name__ == "__main__":
    main()
