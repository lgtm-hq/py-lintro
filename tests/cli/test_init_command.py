"""Tests for the init command."""

from pathlib import Path

from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli import cli
from lintro.cli_utils.commands.init import init_command


def test_init_command_help() -> None:
    """Test that init command displays help."""
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--help"])
    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).contains("Initialize Lintro configuration")
    assert_that(result.output).contains("--with-native-configs")


def test_init_creates_minimal_config() -> None:
    """Test that init --minimal creates minimal config."""
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(init_command, ["--minimal"])
        assert_that(result.exit_code).is_equal_to(0)
        assert_that(Path(".lintro-config.yaml").exists()).is_true()

        content = Path(".lintro-config.yaml").read_text()
        assert_that(content).contains("line_length: 88")
        # Minimal config should be shorter
        assert_that(len(content)).is_less_than(500)


def test_init_overwrites_with_force() -> None:
    """Test that init --force overwrites existing config."""
    runner = CliRunner()
    with runner.isolated_filesystem():
        # Create existing config
        Path(".lintro-config.yaml").write_text("existing: true")

        result = runner.invoke(init_command, ["--force"])
        assert_that(result.exit_code).is_equal_to(0)

        # Content should be replaced
        content = Path(".lintro-config.yaml").read_text()
        assert_that(content).contains("line_length: 88")
        assert_that(content).does_not_contain("existing: true")


def test_with_native_configs_creates_all_files() -> None:
    """Test that --with-native-configs creates native tool config files."""
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(init_command, ["--with-native-configs"])
        assert_that(result.exit_code).is_equal_to(0)

        # Check all files were created
        assert_that(Path(".lintro-config.yaml").exists()).is_true()
        assert_that(Path(".markdownlint-cli2.jsonc").exists()).is_true()


def test_markdownlint_config_has_correct_content() -> None:
    """Test that .markdownlint-cli2.jsonc has correct content."""
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(init_command, ["--with-native-configs"])
        assert_that(result.exit_code).is_equal_to(0)

        # Use string assertions since JSONC files may contain comments
        content = Path(".markdownlint-cli2.jsonc").read_text()
        assert_that(content).contains('"config"')
        assert_that(content).contains("MD013")


def test_native_configs_skips_existing_without_force() -> None:
    """Test that native configs are skipped if they exist without --force."""
    runner = CliRunner()
    with runner.isolated_filesystem():
        # Create existing markdownlint config
        Path(".markdownlint-cli2.jsonc").write_text('{"existing": true}')

        result = runner.invoke(init_command, ["--with-native-configs"])
        assert_that(result.exit_code).is_equal_to(0)
        assert_that(result.output).contains("Skipped .markdownlint-cli2.jsonc")

        # Original content should be preserved
        content = Path(".markdownlint-cli2.jsonc").read_text()
        assert_that(content).is_equal_to('{"existing": true}')


def test_output_shows_all_created_files() -> None:
    """Test that output lists all created files."""
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(init_command, ["--with-native-configs"])
        assert_that(result.exit_code).is_equal_to(0)
        # Should show multiple files were created
        assert_that(result.output).matches(r"Created \d+ files")
        assert_that(result.output).contains(".lintro-config.yaml")
        assert_that(result.output).contains(".markdownlint-cli2.jsonc")


def test_init_via_cli() -> None:
    """Test that init works via the main CLI."""
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(cli, ["init"])
        assert_that(result.exit_code).is_equal_to(0)
        assert_that(Path(".lintro-config.yaml").exists()).is_true()


def test_init_always_prints_detected_line_in_empty_project() -> None:
    """The Detected line prints even when no languages are detected.

    Regression test for #1423: init output must be consistent across runs;
    an empty project shows "Detected: none" rather than dropping the line.
    """
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(init_command, [])
        assert_that(result.exit_code).is_equal_to(0)
        assert_that(result.output).contains("Detected:")
        assert_that(result.output).contains("Profile:")
