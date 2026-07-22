"""Documentation testing suite for Lintro.

This module tests various aspects of the project documentation to ensure
consistency, accuracy, and completeness.
"""

import re
import shutil
import subprocess  # nosec B404 - subprocess is used to drive the tool/CLI under test; invocations use shell=False
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
        ]:
            continue

        try:
            result = subprocess.run(  # nosec B603 - fixed argv run against a real binary in a controlled test; shell=False, no user shell input
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
        # Skip files inside private packages (e.g. ``scripts/ci/_generator/``);
        # those are implementation detail of a documented entry script, not
        # separately invokable scripts.
        if any(part.startswith("_") for part in script_file.parts):
            continue
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
        result = subprocess.run(  # nosec B603 B607 - fixed argv run against a real binary in a controlled test; binary name resolved from PATH, not attacker-controlled; shell=False, no user shell input
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

        # `chk`, `fmt`, and `ls` are valid, current CLI aliases (registered in
        # lintro/cli.py), but end-user docs should prefer the canonical command
        # names (`check`, `format`, `list-tools`) for clarity and discoverability.
        alias_to_canonical = {
            "lintro fmt": "lintro format",
            "lintro chk": "lintro check",
            "lintro ls": "lintro list-tools",
        }
        for alias, canonical in alias_to_canonical.items():
            if alias in content:
                inconsistent_commands.append(
                    f"{doc_file}: prefer canonical '{canonical}' over alias '{alias}'",
                )

    if inconsistent_commands:
        pytest.fail(
            "Inconsistent command usage:\n" + "\n".join(inconsistent_commands),
        )


def test_justfile_parses() -> None:
    """Test that the developer justfile exists and parses via `just --list`.

    Skips when the `just` binary is unavailable (e.g. minimal CI images) so the
    suite stays green while still validating the recipe file wherever `just` is
    installed.
    """
    assert_that(Path("justfile").exists()).is_true()

    just_bin = shutil.which("just")
    if just_bin is None:
        pytest.skip("`just` binary not installed; skipping justfile parse check")

    try:
        result = subprocess.run(
            [just_bin, "--list"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("`just --list` timed out")

    assert_that(result.returncode).is_equal_to(0)
    for recipe in ("setup", "lint", "format", "test", "clean"):
        assert_that(result.stdout).contains(recipe)


# ---------------------------------------------------------------------------
# Configuration-contract tests (issue #1224)
#
# These pin the documentation to the runtime so the config story stays truthful:
# the tier count, the env vars that are actually read, the loader's parsing of
# documented execution keys, and the SECURITY.md supported-version table.
# ---------------------------------------------------------------------------

# Env vars the docs are allowed to advertise: they MUST be read by the runtime.
_DOCUMENTED_LINTRO_ENV_VARS = {
    "LINTRO_LOG_DIR",
    "LINTRO_VERSION_TIMEOUT",
    "LINTRO_DOCKER",
    "LINTRO_CONFIG",
    "LINTRO_ENABLE_EXTERNAL_PLUGINS",
}

# Env vars that were historically documented but are NOT read by the runtime.
# They must never reappear in user-facing configuration docs.
_PHANTOM_LINTRO_ENV_VARS = {
    "LINTRO_DEFAULT_TIMEOUT",
    "LINTRO_VERBOSE",
    "LINTRO_EXCLUDE",
    "LINTRO_DEFAULT_FORMAT",
    "LINTRO_AUTO_INSTALL_DEPS",
}


def _project_version() -> str:
    """Return the project version from pyproject.toml.

    Returns:
        str: The ``[project].version`` string.
    """
    import tomllib

    with open("pyproject.toml", "rb") as f:
        data = tomllib.load(f)
    return str(data["project"]["version"])


def test_docs_agree_on_tier_count() -> None:
    """README and the Configuration Guide must state the same tier count."""
    readme = Path("README.md").read_text(encoding="utf-8")
    config_doc = Path("docs/configuration.md").read_text(encoding="utf-8")

    readme_tiers = set(re.findall(r"(\d+)-tier", readme))
    config_tiers = set(re.findall(r"(\d+)-tier", config_doc, flags=re.IGNORECASE))

    assert_that(readme_tiers).described_as("README N-tier phrase").is_not_empty()
    assert_that(config_tiers).described_as("config guide N-tier").is_not_empty()
    assert_that(readme_tiers).is_equal_to(config_tiers)
    # The runtime LintroConfig model documents a 5-tier core model.
    assert_that(config_tiers).contains("5")


def test_documented_env_vars_are_handled() -> None:
    """Every LINTRO_* env var in the config docs must be read by the runtime."""
    config_doc = Path("docs/configuration.md").read_text(encoding="utf-8")

    # Collect the source text once so we can confirm each var is referenced.
    source_text = "\n".join(
        p.read_text(encoding="utf-8", errors="ignore")
        for p in Path("lintro").rglob("*.py")
    )

    documented = set(re.findall(r"LINTRO_[A-Z_]+", config_doc))
    # Ignore install/plugin-only identifiers that are not user-facing env vars.
    documented -= {"LINTRO_PLUGIN_API_VERSION"}

    for var in documented:
        assert_that(_DOCUMENTED_LINTRO_ENV_VARS).described_as(
            f"{var} documented in configuration.md must be an allowed env var",
        ).contains(var)
        assert_that(source_text).described_as(
            f"{var} must be referenced in lintro/ source",
        ).contains(var)

    # Phantom vars must not have crept back into the docs.
    for var in _PHANTOM_LINTRO_ENV_VARS:
        assert_that(config_doc).described_as(
            f"phantom env var {var} must not be documented",
        ).does_not_contain(var)


def test_config_loader_parses_documented_execution_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Documented execution keys (max_workers, artifacts) must be parsed."""
    from lintro.config.config_loader import clear_config_cache, load_config

    config_file = tmp_path / ".lintro-config.yaml"
    config_file.write_text(
        "execution:\n"
        "  parallel: true\n"
        "  max_workers: 7\n"
        "  artifacts:\n"
        "    - json\n"
        "    - sarif\n",
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)
    clear_config_cache()
    config = load_config(config_path=config_file)

    assert_that(config.execution.max_workers).is_equal_to(7)
    assert_that(config.execution.artifacts).is_equal_to(["json", "sarif"])
    assert_that(config.execution.parallel).is_true()

    clear_config_cache()


def test_parallel_is_enabled_by_default() -> None:
    """The runtime default for parallel execution must be True (per docs/ROADMAP)."""
    from lintro.config.execution_config import ExecutionConfig

    assert_that(ExecutionConfig().parallel).is_true()


def test_security_md_supports_current_minor() -> None:
    """SECURITY.md must list the current major.minor line as supported."""
    version = _project_version()
    major, minor, *_ = version.split(".")
    current_line = f"{major}.{minor}.x"

    security = Path("SECURITY.md").read_text(encoding="utf-8")
    supported_rows = [
        line for line in security.splitlines() if "|" in line and "✅" in line
    ]

    assert_that(supported_rows).described_as(
        "SECURITY.md must have a supported-version row",
    ).is_not_empty()
    assert_that("\n".join(supported_rows)).described_as(
        f"SECURITY.md must support current line {current_line}",
    ).contains(current_line)
