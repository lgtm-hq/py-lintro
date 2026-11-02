"""Tests for license policy configuration loading."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
import yaml
from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli import cli
from lintro.config.licenses_config import (
    LicensesConfig,
    PackageException,
    load_licenses_config,
)
from lintro.exceptions.errors import ConfigurationError


@pytest.fixture
def license_config_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path]:
    """Provide independent global and project YAML files.

    Args:
        tmp_path: Temporary directory for both configuration tiers.
        monkeypatch: Fixture for selecting the global file.

    Returns:
        tuple[Path, Path]: Global and project configuration paths.
    """
    global_file = tmp_path / "global.yaml"
    project = tmp_path / "project"
    project.mkdir()
    project_file = project / ".lintro-config.yaml"
    for path in (global_file, project_file):
        path.write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("LINTRO_GLOBAL_CONFIG", str(global_file))
    return global_file, project_file


def test_defaults_are_permissive() -> None:
    """A config with no data defaults to the permissive preset."""
    config = LicensesConfig()
    assert_that(config.policy).is_equal_to("permissive")
    assert_that(config.unknown_policy).is_equal_to("warn")
    assert_that(config.ignore_dev_dependencies).is_true()


def test_exception_lookup_normalizes_name() -> None:
    """Exception lookup ignores case and underscore/hyphen differences."""
    config = LicensesConfig(
        exceptions=[PackageException(package="Some_Lib", reason="ok")],
    )
    assert_that(config.exception_for("some-lib")).is_not_none()
    assert_that(config.exception_for("missing")).is_none()


def test_load_from_yaml(tmp_path: Path) -> None:
    """The loader reads the licenses section from a YAML config file.

    Args:
        tmp_path: Temporary project directory.
    """
    (tmp_path / ".lintro-config.yaml").write_text(
        "licenses:\n  policy: strict\n  allowed:\n    - MIT\n  unknown_policy: deny\n",
    )
    config = load_licenses_config(start_dir=tmp_path)
    assert_that(config.policy).is_equal_to("strict")
    assert_that(config.allowed).contains("MIT")
    assert_that(config.unknown_policy).is_equal_to("deny")


def test_load_from_pyproject(tmp_path: Path) -> None:
    """The loader falls back to [tool.lintro.licenses] in pyproject.toml.

    Args:
        tmp_path: Temporary project directory.
    """
    (tmp_path / "pyproject.toml").write_text(
        '[tool.lintro.licenses]\npolicy = "copyleft-ok"\ndenied = ["AGPL-3.0-only"]\n',
    )
    config = load_licenses_config(start_dir=tmp_path)
    assert_that(config.policy).is_equal_to("copyleft-ok")
    assert_that(config.denied).contains("AGPL-3.0-only")


def test_load_defaults_when_absent(tmp_path: Path) -> None:
    """The loader returns defaults when no config is present.

    Args:
        tmp_path: Empty temporary directory.
    """
    config = load_licenses_config(start_dir=tmp_path)
    assert_that(config.policy).is_equal_to("permissive")


@pytest.mark.parametrize("tier", ["global", "project"])
@pytest.mark.parametrize("boundary", ["document", "licenses"])
@pytest.mark.parametrize(
    ("value", "actual"),
    [
        ("", "null"),
        ("# comment\n", "null"),
        ("null\n", "null"),
        ("~\n", "null"),
        ("false\n", "bool"),
        ("0\n", "int"),
        ('""\n', "str"),
        ("invalid\n", "str"),
        ("[]\n", "list"),
        ("[MIT]\n", "list"),
    ],
)
def test_invalid_license_yaml_fails_before_overlay_or_fallback(
    license_config_files: tuple[Path, Path],
    tier: str,
    boundary: str,
    value: str,
    actual: str,
) -> None:
    """Invalid input cannot be hidden by another tier or a pyproject policy.

    Args:
        license_config_files: Independent global and project YAML paths.
        tier: Source containing the invalid value.
        boundary: Whether the document or its licenses section is invalid.
        value: YAML spelling of the invalid input.
        actual: Parsed input type to identify in the error.
    """
    global_file, project_file = license_config_files
    for path in license_config_files:
        path.write_text("licenses:\n  policy: strict\n", encoding="utf-8")
    invalid_file = global_file if tier == "global" else project_file
    content = value if boundary == "document" else f"licenses: {value}"
    invalid_file.write_text(content, encoding="utf-8")
    (project_file.parent / "pyproject.toml").write_text(
        '[tool.lintro.licenses]\npolicy = "permissive"\n',
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError) as error:
        load_licenses_config(start_dir=project_file.parent)

    assert_that(str(error.value)).contains(
        str(invalid_file),
        "Config document" if boundary == "document" else "licenses",
        f"must be a mapping, got {actual}",
    )


@pytest.mark.parametrize(
    "global_content",
    [None, "{}\n", "enforce: {}\n", "licenses: {}\n"],
)
@pytest.mark.parametrize(
    "project_content",
    [None, "{}\n", "enforce: {}\n", "licenses: {}\n"],
)
def test_absent_and_empty_license_mappings_keep_defaults(
    license_config_files: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    global_content: str | None,
    project_content: str | None,
) -> None:
    """Omission and empty mappings in either tier still resolve to defaults.

    Args:
        license_config_files: Independent global and project YAML paths.
        monkeypatch: Fixture for disabling an absent global tier.
        global_content: Valid global document, or None to omit the file.
        project_content: Valid project document, or None to omit the file.
    """
    global_file, project_file = license_config_files
    if global_content is None:
        global_file.unlink()
        monkeypatch.setenv("LINTRO_GLOBAL_CONFIG", "off")
    else:
        global_file.write_text(global_content, encoding="utf-8")
    if project_content is None:
        project_file.unlink()
    else:
        project_file.write_text(project_content, encoding="utf-8")

    config = load_licenses_config(start_dir=project_file.parent)

    assert_that(config).is_equal_to(LicensesConfig())


@pytest.mark.parametrize("content", [None, "{}\n", "enforce: {}\n", "licenses: {}\n"])
@pytest.mark.parametrize("pyproject_fallback", [False, True])
def test_empty_project_license_mappings_keep_inheritance_and_fallback(
    license_config_files: tuple[Path, Path],
    content: str | None,
    pyproject_fallback: bool,
) -> None:
    """Empty project inputs preserve global policy and optional TOML overrides.

    Args:
        license_config_files: Independent global and project YAML paths.
        content: Valid project document, or None to omit the file.
        pyproject_fallback: Whether to supply a TOML project overlay.
    """
    global_file, project_file = license_config_files
    global_file.write_text(
        "licenses:\n  policy: strict\n  allowed: [MIT]\n",
        encoding="utf-8",
    )
    if content is None:
        project_file.unlink()
    else:
        project_file.write_text(content, encoding="utf-8")
    if pyproject_fallback:
        (project_file.parent / "pyproject.toml").write_text(
            '[tool.lintro.licenses]\nunknown_policy = "deny"\n',
            encoding="utf-8",
        )

    config = load_licenses_config(start_dir=project_file.parent)

    assert_that(config.policy).is_equal_to("strict")
    assert_that(config.allowed).is_equal_to(["MIT"])
    assert_that(config.unknown_policy).is_equal_to(
        "deny" if pyproject_fallback else "warn",
    )


def test_project_license_mapping_replaces_lists_and_inherits_other_keys(
    license_config_files: tuple[Path, Path],
) -> None:
    """A populated project mapping keeps per-key merge and YAML precedence.

    Args:
        license_config_files: Independent global and project YAML paths.
    """
    global_file, project_file = license_config_files
    global_file.write_text(
        "licenses:\n  policy: strict\n  allowed: [MIT]\n  denied: [GPL-3.0-only]\n",
        encoding="utf-8",
    )
    project_file.write_text(
        "licenses:\n  allowed: []\n  unknown_policy: deny\n",
        encoding="utf-8",
    )
    (project_file.parent / "pyproject.toml").write_text(
        '[tool.lintro.licenses]\npolicy = "permissive"\n',
        encoding="utf-8",
    )

    config = load_licenses_config(start_dir=project_file.parent)

    assert_that(config.policy).is_equal_to("strict")
    assert_that(config.allowed).is_empty()
    assert_that(config.denied).is_equal_to(["GPL-3.0-only"])
    assert_that(config.unknown_policy).is_equal_to("deny")


@pytest.mark.parametrize("tier", ["global", "project"])
@pytest.mark.parametrize("content", ["null\n", "licenses: null\n"])
def test_licenses_cli_fails_for_invalid_yaml(
    license_config_files: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    tier: str,
    content: str,
) -> None:
    """The licenses command cannot report success with an invalid YAML policy.

    Args:
        license_config_files: Independent global and project YAML paths.
        monkeypatch: Fixture for selecting the project directory.
        tier: Source containing the invalid input.
        content: Invalid document or licenses section.
    """
    global_file, project_file = license_config_files
    invalid_file = global_file if tier == "global" else project_file
    invalid_file.write_text(content, encoding="utf-8")
    monkeypatch.chdir(project_file.parent)

    result = CliRunner().invoke(
        cli,
        ["licenses", "--lang", "npm", "--check", "--format", "json"],
    )

    assert_that(result.exit_code).is_not_equal_to(0)
    assert_that(result.stderr).contains(str(invalid_file), "must be a mapping")
    assert_that(result.output).does_not_contain("[]")


@pytest.mark.parametrize(
    "value",
    ['""', '"invalid"', "false", "0", "1.5", "[]", '["MIT"]'],
)
def test_non_mapping_pyproject_licenses_cannot_fall_back_to_global(
    license_config_files: tuple[Path, Path],
    value: str,
) -> None:
    """A present invalid TOML policy cannot silently inherit global settings.

    Args:
        license_config_files: Independent global and project YAML paths.
        value: Non-table TOML value for the licenses section.
    """
    global_file, project_file = license_config_files
    global_file.write_text("licenses:\n  policy: strict\n", encoding="utf-8")
    pyproject = project_file.parent / "pyproject.toml"
    pyproject.write_text(f"[tool.lintro]\nlicenses = {value}\n", encoding="utf-8")

    with pytest.raises(ConfigurationError) as error:
        load_licenses_config(start_dir=project_file.parent)

    assert_that(str(error.value)).contains(
        str(pyproject),
        "tool.lintro.licenses",
        "must be a mapping",
    )


@pytest.mark.parametrize("section", ["tool", "tool.lintro"])
def test_non_mapping_pyproject_parent_has_a_configuration_diagnostic(
    license_config_files: tuple[Path, Path],
    section: str,
) -> None:
    """Invalid parent tables must not crash traversal to the license policy.

    Args:
        license_config_files: Independent global and project YAML paths.
        section: Parent table replaced by a scalar.
    """
    _, project_file = license_config_files
    pyproject = project_file.parent / "pyproject.toml"
    pyproject.write_text(f"{section} = false\n", encoding="utf-8")

    with pytest.raises(ConfigurationError) as error:
        load_licenses_config(start_dir=project_file.parent)

    assert_that(str(error.value)).contains(str(pyproject), section, "must be a mapping")


@pytest.mark.parametrize(
    "content",
    ["[project]\n", "[tool.lintro]\n", "[tool.lintro.licenses]\n"],
)
def test_absent_and_empty_pyproject_licenses_preserve_global_policy(
    license_config_files: tuple[Path, Path],
    content: str,
) -> None:
    """An absent or empty TOML table keeps the global base intact.

    Args:
        license_config_files: Independent global and project YAML paths.
        content: TOML with an omitted or empty license table.
    """
    global_file, project_file = license_config_files
    global_file.write_text(
        "licenses:\n  policy: strict\n  allowed: [MIT]\n",
        encoding="utf-8",
    )
    (project_file.parent / "pyproject.toml").write_text(content, encoding="utf-8")

    config = load_licenses_config(start_dir=project_file.parent)

    assert_that(config).is_equal_to(LicensesConfig(policy="strict", allowed=["MIT"]))


@pytest.mark.parametrize("content", ["[tool.lintro]\nlicenses = []\n", "invalid = [\n"])
def test_populated_yaml_licenses_do_not_read_lower_priority_toml(
    license_config_files: tuple[Path, Path],
    content: str,
) -> None:
    """A populated YAML section retains precedence over invalid lower-tier TOML.

    Args:
        license_config_files: Independent global and project YAML paths.
        content: Invalid lower-priority TOML configuration.
    """
    _, project_file = license_config_files
    project_file.write_text("licenses:\n  policy: copyleft-ok\n", encoding="utf-8")
    (project_file.parent / "pyproject.toml").write_text(content, encoding="utf-8")

    config = load_licenses_config(start_dir=project_file.parent)

    assert_that(config.policy).is_equal_to("copyleft-ok")


@pytest.mark.parametrize("tier", ["global", "project"])
@pytest.mark.parametrize(
    "failure",
    ["syntax", "encoding", "read", "constructor", "recursion"],
)
def test_yaml_failures_are_path_attributed_configuration_errors(
    license_config_files: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    tier: str,
    failure: str,
) -> None:
    """YAML parsing, construction and read errors retain their source and cause.

    Args:
        license_config_files: Independent global and project YAML paths.
        monkeypatch: Fixture for injecting an otherwise platform-dependent read error.
        tier: Source containing the invalid input.
        failure: Parse, construction, recursion, decode or read failure to exercise.
    """
    global_file, project_file = license_config_files
    invalid_file = global_file if tier == "global" else project_file
    contents = {
        "syntax": b"licenses: [\n",
        "constructor": b"licenses:\n  policy: 2001-13-45\n",
        "recursion": b"licenses: " + b"[" * 20_000 + b"0" + b"]" * 20_000,
    }
    invalid_file.write_bytes(contents.get(failure, b"\xff"))
    if failure == "read":
        original_read = Path.read_text

        def read_text(
            path: Path,
            encoding: str | None = None,
            errors: str | None = None,
        ) -> str:
            """Deny only the selected config file while leaving other reads intact."""
            if path == invalid_file:
                raise PermissionError("injected license config read failure")
            return original_read(path, encoding=encoding, errors=errors)

        monkeypatch.setattr(Path, "read_text", read_text)

    with pytest.raises(ConfigurationError) as error:
        load_licenses_config(start_dir=project_file.parent)

    assert_that(str(error.value)).contains(
        str(invalid_file),
        "Failed to read" if failure in {"encoding", "read"} else "Failed to parse",
    )
    causes = {
        "syntax": yaml.YAMLError,
        "constructor": ValueError,
        "recursion": RecursionError,
        "encoding": UnicodeDecodeError,
        "read": PermissionError,
    }
    assert_that(error.value.__cause__).is_instance_of(causes[failure])
    if failure == "read":
        assert_that(str(error.value)).contains("injected license config read failure")


@pytest.mark.parametrize(
    ("content", "cause_type", "diagnostic"),
    [
        (b"[tool.lintro.licenses\n", tomllib.TOMLDecodeError, "Failed to parse"),
        (b"\xff", UnicodeDecodeError, "Failed to read"),
    ],
)
def test_toml_failures_are_path_attributed_configuration_errors(
    license_config_files: tuple[Path, Path],
    content: bytes,
    cause_type: type[Exception],
    diagnostic: str,
) -> None:
    """Invalid TOML cannot lose its source or silently inherit the global policy.

    Args:
        license_config_files: Independent global and project YAML paths.
        content: Malformed or undecodable TOML bytes.
        cause_type: Original exception type retained by the loader.
        diagnostic: Expected read or parse diagnostic.
    """
    global_file, project_file = license_config_files
    global_file.write_text("licenses:\n  policy: strict\n", encoding="utf-8")
    pyproject = project_file.parent / "pyproject.toml"
    pyproject.write_bytes(content)

    with pytest.raises(ConfigurationError) as error:
        load_licenses_config(start_dir=project_file.parent)

    assert_that(str(error.value)).contains(str(pyproject), diagnostic, "TOML config")
    assert_that(error.value.__cause__).is_instance_of(cause_type)
