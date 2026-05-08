"""Documentation testing suite for Lintro.

This module tests various aspects of the project documentation to ensure
consistency, accuracy, and completeness.
"""

import re
import subprocess
from pathlib import Path

import pytest
from assertpy import assert_that


def test_scripts_have_help() -> None:
    """Test that all executable scripts support --help flag."""
    script_dir = Path("scripts")
    failed_scripts = []

    for script_file in script_dir.rglob("*.sh"):
        # Skip utility files that are sourced by other scripts
        if script_file.name in [
            "utils.sh",
            "install.sh",
            "tools-image-digest-helpers.sh",
        ]:
            continue

        try:
            result = subprocess.run(
                [str(script_file), "--help"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                failed_scripts.append(
                    f"{script_file}: exit code {result.returncode}",
                )
        except subprocess.TimeoutExpired:
            failed_scripts.append(f"{script_file}: timeout")
        except Exception as e:
            failed_scripts.append(f"{script_file}: {e}")

    if failed_scripts:
        pytest.fail("Scripts without --help support:\n" + "\n".join(failed_scripts))


def test_scripts_readme_coverage() -> None:
    """Test that all scripts are documented in scripts/README.md."""
    scripts_readme = Path("scripts/README.md")
    if not scripts_readme.exists():
        pytest.skip("scripts/README.md not found")

    with open(scripts_readme, encoding="utf-8") as f:
        content = f.read()

    # Get all script files
    script_files = set()
    for script_file in Path("scripts").rglob("*.sh"):
        script_files.add(script_file.name)
    for script_file in Path("scripts").rglob("*.py"):
        if script_file.name == "__init__.py":
            continue
        # Skip files inside private packages (e.g. ``scripts/ci/_generator/``);
        # those are implementation detail of a documented entry script, not
        # separately invokable scripts.
        if any(part.startswith("_") for part in script_file.parts):
            continue
        script_files.add(script_file.name)

    # Find documented scripts
    documented_scripts = set()
    for script_name in script_files:
        if script_name in content:
            documented_scripts.add(script_name)

    missing_docs = script_files - documented_scripts
    if missing_docs:
        pytest.fail(
            "Scripts not documented in scripts/README.md:\n" + "\n".join(missing_docs),
        )


def test_cli_help_works() -> None:
    """Test that lintro --help works and shows expected commands."""
    try:
        result = subprocess.run(
            ["uv", "run", "python", "-m", "lintro", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert_that(result.returncode).is_equal_to(0)
        assert_that(result.stdout).contains("check")
        assert_that(result.stdout).contains("format")
        assert_that(result.stdout).contains("list-tools")
    except subprocess.TimeoutExpired:
        pytest.fail("lintro --help timed out")


def test_internal_doc_links() -> None:
    """Test that internal documentation links are valid."""
    doc_files = [
        "README.md",
        "docs/getting-started.md",
        "docs/contributing.md",
        "docs/docker.md",
        "docs/github-integration.md",
        "scripts/README.md",
    ]

    broken_links = []
    for doc_file in doc_files:
        if not Path(doc_file).exists():
            continue

        with open(doc_file, encoding="utf-8") as f:
            content = f.read()

        # Find markdown links
        links = re.findall(r"\[([^\]]+)\]\(([^)]+)\)", content)
        for link_text, link_url in links:
            if link_url.startswith("docs/") or link_url.startswith("./docs/"):
                # Internal documentation link
                link_path = link_url
                if link_path.startswith("./"):
                    link_path = link_path[2:]

                if not Path(link_path).exists():
                    broken_links.append(f"{doc_file}: {link_text} -> {link_url}")

    if broken_links:
        pytest.fail("Broken internal links:\n" + "\n".join(broken_links))


def test_all_docs_have_titles() -> None:
    """Test that all documentation files have proper titles."""
    doc_files = [
        "README.md",
        "docs/getting-started.md",
        "docs/contributing.md",
        "docs/docker.md",
        "docs/github-integration.md",
        "docs/configuration.md",
        "scripts/README.md",
    ]

    files_without_titles = []
    for doc_file in doc_files:
        if not Path(doc_file).exists():
            continue

        with open(doc_file, encoding="utf-8") as f:
            first_line = f.readline().strip()

        if not first_line.startswith("# "):
            files_without_titles.append(doc_file)

    if files_without_titles:
        pytest.fail("Docs without titles:\n" + "\n".join(files_without_titles))


def test_command_consistency() -> None:
    """Test that CLI commands are consistently documented."""
    doc_files = [
        "README.md",
        "docs/getting-started.md",
        "docs/configuration.md",
    ]

    inconsistent_commands = []
    for doc_file in doc_files:
        if not Path(doc_file).exists():
            continue

        with open(doc_file, encoding="utf-8") as f:
            content = f.read()

        # Check for old command aliases that shouldn't be in docs
        old_aliases = ["lintro fmt", "lintro chk", "lintro ls"]
        for alias in old_aliases:
            if alias in content:
                inconsistent_commands.append(
                    f"{doc_file}: uses old alias '{alias}'",
                )

    if inconsistent_commands:
        pytest.fail(
            "Inconsistent command usage:\n" + "\n".join(inconsistent_commands),
        )
