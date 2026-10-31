"""Tests for the ``lintro licenses`` CLI command."""

from __future__ import annotations

import csv
import io
import json
import sys
from pathlib import Path

import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli_utils.commands.licenses import licenses_command
from lintro.licenses.ecosystems import PythonLicenseAdapter
from lintro.licenses.models import PackageLicense


def test_licenses_json_output_is_parseable() -> None:
    """The command emits parseable JSON for installed packages."""
    result = CliRunner().invoke(
        licenses_command,
        ["--lang", "python", "--format", "json"],
    )
    assert_that(result.exit_code).is_equal_to(0)
    payload = json.loads(result.output)
    assert_that(payload).is_not_empty()


def test_licenses_grid_output_runs() -> None:
    """The default grid output renders without error."""
    result = CliRunner().invoke(licenses_command, ["--lang", "python"])
    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).contains("Total packages:")


def test_licenses_check_flag_exits_nonzero_on_violation() -> None:
    """The --check flag exits non-zero when a strict policy denies packages."""
    # Strict policy denies every unlisted license, so installed packages
    # will produce violations and force a non-zero exit.
    runner = CliRunner()
    with runner.isolated_filesystem() as tmp:
        from pathlib import Path

        Path(tmp, ".lintro-config.yaml").write_text(
            "licenses:\n  policy: strict\n",
        )
        result = runner.invoke(
            licenses_command,
            ["--lang", "python", "--check", "--format", "json"],
        )
    assert_that(result.exit_code).is_equal_to(1)


def test_licenses_attribution_outputs_markdown() -> None:
    """The --attribution flag emits a Markdown attribution document."""
    result = CliRunner().invoke(
        licenses_command,
        ["--lang", "python", "--attribution"],
    )
    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).contains("# Third-Party Licenses")


def test_licenses_spdx_format() -> None:
    """The spdx format emits an SPDX tag-value document."""
    result = CliRunner().invoke(
        licenses_command,
        ["--lang", "python", "--format", "spdx"],
    )
    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).contains("SPDXVersion: SPDX-2.3")


@pytest.mark.parametrize(
    "output_format",
    ["grid", "json", "csv", "spdx", "attribution"],
)
@pytest.mark.parametrize("check", [False, True])
@pytest.mark.parametrize("failure", ["json", "dangling-symlink"])
def test_licenses_reports_uncollected_npm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    output_format: str,
    check: bool,
    failure: str,
) -> None:
    """Every report identifies failed collection, including problems-only mode.

    Args:
        tmp_path: Temporary project directory.
        monkeypatch: Fixture for selecting the project directory.
        output_format: Report format, or attribution output.
        check: Whether collection failure must change the exit status.
        failure: Invalid JSON or a manifest symlink with a missing target.
    """
    monkeypatch.chdir(tmp_path)
    manifest = tmp_path / "package.json"
    if failure == "dangling-symlink":
        manifest.symlink_to(tmp_path / "missing.json")
    else:
        manifest.write_text("{")
    args = ["--lang", "npm", "--problems-only"]
    args += (
        ["--attribution"]
        if output_format == "attribution"
        else ["--format", output_format]
    )
    if check:
        args.append("--check")
    result = CliRunner().invoke(licenses_command, args)

    assert_that(result.exit_code).is_equal_to(1 if check else 0)
    assert_that(result.output).contains("npm", "uncollected", "package.json")
    assert_that(result.output).does_not_contain("No dependencies found", "Traceback")
    if output_format == "json":
        payload = json.loads(result.output)
        assert_that(payload).is_length(1)
        assert_that(payload[0]["status"]).is_equal_to("uncollected")
        assert_that(payload[0]["ecosystem"]).is_equal_to("npm")
        assert_that(payload[0]["reason"]).contains("package.json")
        if failure == "json":
            assert_that(payload[0]["reason"]).contains("Expecting")
        else:
            assert_that(payload[0]["reason"]).contains("No such file")
        assert_that(payload[0]).does_not_contain_key("name")
    elif output_format == "csv":
        rows = list(csv.DictReader(io.StringIO(result.output)))
        assert_that(rows).is_length(1)
        assert_that(rows[0]["status"]).is_equal_to("uncollected")
        assert_that(rows[0]["name"]).is_empty()
    elif output_format == "spdx":
        assert_that(result.output).contains(
            "DocumentComment: <text>npm inventory uncollected",
        )


@pytest.mark.parametrize(
    "content",
    [
        None,
        "{}",
        '{"dependencies": {}, "devDependencies": {}}',
        '\ufeff{"dependencies": {"missing": "1"}}',
    ],
)
def test_licenses_check_accepts_absent_and_empty_npm_inventory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    content: str | None,
) -> None:
    """No npm project and valid empty manifests retain their successful report.

    Args:
        tmp_path: Temporary project directory.
        monkeypatch: Fixture for selecting the project directory.
        content: Manifest contents, or None to leave the manifest absent.
    """
    monkeypatch.chdir(tmp_path)
    if content is not None:
        (tmp_path / "package.json").write_text(content)
    result = CliRunner().invoke(
        licenses_command,
        ["--lang", "npm", "--check", "--format", "json"],
    )
    assert_that(result.exit_code).is_equal_to(0)
    assert_that(json.loads(result.output)).is_empty()


@pytest.mark.parametrize("failure", ["integer", "nesting"])
def test_licenses_reports_json_decoder_limits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    """JSON decoder limits produce collection diagnostics instead of tracebacks.

    Args:
        tmp_path: Temporary project directory.
        monkeypatch: Fixture for selecting the project directory and decoder.
        failure: Integer conversion or nesting limit to exceed.
    """
    monkeypatch.chdir(tmp_path)
    if failure == "integer":
        limit = sys.get_int_max_str_digits()
        if not limit:
            pytest.skip("Integer conversion limit is disabled")
        content = '{"value": ' + "1" * (limit + 1) + "}"
        reason = "integer string conversion"
    else:
        content = "{}"
        reason = "recursion depth"
    (tmp_path / "package.json").write_text(content)

    def fail_decode(document: str) -> object:
        raise RecursionError("maximum recursion depth exceeded while decoding JSON")

    with monkeypatch.context() as decoder_patch:
        if failure == "nesting":
            # Nesting limits differ between Python's JSON decoder versions.
            decoder_patch.setattr(json, "loads", fail_decode)
        result = CliRunner().invoke(
            licenses_command,
            ["--lang", "npm", "--check", "--format", "json"],
        )

    assert_that(result.exit_code).is_equal_to(1)
    payload = json.loads(result.output)
    assert_that(payload).is_length(1)
    assert_that(payload[0]["status"]).is_equal_to("uncollected")
    assert_that(payload[0]["reason"]).contains("npm", "package.json", reason)


@pytest.mark.parametrize("name", ["@scope</text>/extra", "@scope<text>/pkg/extra"])
def test_licenses_spdx_contains_invalid_dependency_diagnostic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
) -> None:
    """Manifest-controlled delimiters stay inside one SPDX comment text block.

    Args:
        tmp_path: Temporary project directory.
        monkeypatch: Fixture for selecting the project directory.
        name: Invalid scoped dependency name containing an SPDX delimiter.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "package.json").write_text(
        json.dumps({"dependencies": {name: "1"}}),
    )

    result = CliRunner().invoke(
        licenses_command,
        ["--lang", "npm", "--check", "--format", "spdx"],
    )

    assert_that(result.exit_code).is_equal_to(1)
    assert_that(result.output).contains(
        "invalid scoped name",
        "npm inventory uncollected",
    )
    assert_that(result.output.count("<text>")).is_equal_to(1)
    assert_that(result.output.count("</text>")).is_equal_to(1)
    assert_that(result.output.rstrip()).ends_with("</text>")


@pytest.mark.parametrize("failure", ["directory", "encoding", "schema"])
def test_licenses_check_fails_for_unreadable_or_invalid_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    """Filesystem, decoding, and schema failures produce a clear check failure.

    Args:
        tmp_path: Temporary project directory.
        monkeypatch: Fixture for selecting the project directory.
        failure: Root-manifest failure to exercise.
    """
    monkeypatch.chdir(tmp_path)
    manifest = tmp_path / "package.json"
    if failure == "directory":
        manifest.mkdir()
    elif failure == "encoding":
        manifest.write_bytes(b"\xff")
    else:
        manifest.write_text('{"dependencies": null}')
    result = CliRunner().invoke(licenses_command, ["--lang", "npm", "--check"])

    assert_that(result.exit_code).is_equal_to(1)
    assert_that(result.output).contains("npm", "uncollected", "package.json")
    assert_that(result.output).does_not_contain("No dependencies found")


@pytest.mark.parametrize(
    "langs",
    [[], ["--lang", "python"], ["--lang", "npm", "--lang", "python"]],
)
def test_licenses_preserves_other_ecosystems_on_npm_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    langs: list[str],
) -> None:
    """Npm failure preserves Python output and affects only requested ecosystems.

    Args:
        tmp_path: Temporary project directory.
        monkeypatch: Fixture for selecting the directory and Python inventory.
        langs: Ecosystem selection, including the default selection.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "package.json").write_text("null")
    monkeypatch.setattr(
        PythonLicenseAdapter,
        "get_installed_licenses",
        lambda self: [PackageLicense(name="python-lib", version="1", license_id="MIT")],
    )
    result = CliRunner().invoke(
        licenses_command,
        [*langs, "--check", "--format", "json"],
    )
    payload = json.loads(result.output)
    npm_selected = not langs or "npm" in langs

    assert_that(result.exit_code).is_equal_to(1 if npm_selected else 0)
    assert_that(payload[0]["name"]).is_equal_to("python-lib")
    assert_that(payload[0]["status"]).is_equal_to("allowed")
    assert_that(payload).is_length(2 if npm_selected else 1)
    if npm_selected:
        assert_that(payload[1]["status"]).is_equal_to("uncollected")
