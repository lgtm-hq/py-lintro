"""Unit tests for checkov plugin options and definition."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.enums.capability import Cap
from lintro.enums.tool_name import ToolName
from lintro.enums.tool_type import ToolType
from lintro.models.core.tool_result import ToolResult
from lintro.tools.checkov.definition import (
    CHECKOV_DEFAULT_TIMEOUT,
    CHECKOV_FILE_PATTERNS,
    CHECKOV_FRAMEWORKS,
    CheckovPlugin,
)
from lintro.tools.core.version_parsing import TOOLS_WITH_SIMPLE_VERSION_PATTERN
from lintro.utils.project_detection import detect_project_languages
from lintro.utils.tool_options import parse_tool_options


def test_definition_metadata(checkov_plugin: CheckovPlugin) -> None:
    """The definition exposes the expected metadata.

    Args:
        checkov_plugin: The plugin under test.
    """
    definition = checkov_plugin.definition
    assert_that(definition.name).is_equal_to("checkov")
    assert_that(definition.can_fix).is_false()
    assert_that(definition.tool_type).is_equal_to(
        ToolType.SECURITY | ToolType.INFRASTRUCTURE,
    )
    assert_that(definition.file_patterns).is_equal_to(["*.tf", "*.tf.json"])
    assert_that(definition.version_command).is_equal_to(["checkov", "--version"])
    assert_that(definition.native_configs).contains(".checkov.yaml", ".checkov.yml")
    # Checkov builds a resource graph before evaluating policies, so it is far
    # slower than a line-oriented linter; the generic default would time a real
    # Terraform tree out and report an execution failure instead of findings.
    assert_that(definition.default_timeout).is_equal_to(CHECKOV_DEFAULT_TIMEOUT)
    assert_that(CHECKOV_DEFAULT_TIMEOUT).is_greater_than_or_equal_to(120)
    # ``checkov --version`` prints a bare version, which is what puts it in the
    # simple-pattern set. Membership is asserted directly: the extractor's
    # generic fallback returns the same string either way, so a parser test
    # alone cannot fail if the entry is dropped.
    assert_that(TOOLS_WITH_SIMPLE_VERSION_PATTERN).contains(ToolName.CHECKOV)


def test_definition_claims_check_only(checkov_plugin: CheckovPlugin) -> None:
    """Checkov claims its Terraform patterns for CHECK and nothing else.

    A FIX or FORMAT capability would put checkov in the mutating phase of the
    derived execution order and contradict ``can_fix=False``.

    Args:
        checkov_plugin: The plugin under test.
    """
    claims = checkov_plugin.definition.claims

    assert_that(claims).is_length(1)
    assert_that(claims[0].patterns).is_equal_to(CHECKOV_FILE_PATTERNS)
    assert_that(set(claims[0].capabilities)).is_equal_to({Cap.CHECK})


def test_definition_is_not_partitionable(checkov_plugin: CheckovPlugin) -> None:
    """Checkov sees the whole file list at once.

    The ``CKV2_*`` graph checks relate resources across files, so a sharded run
    would silently drop findings whose two halves landed in different shards.

    Args:
        checkov_plugin: The plugin under test.
    """
    assert_that(checkov_plugin.definition.partitionable).is_false()
    assert_that(checkov_plugin.definition.reads_tree).is_true()


def test_file_patterns_exclude_surfaces_other_tools_own(
    checkov_plugin: CheckovPlugin,
) -> None:
    """Checkov never claims Dockerfiles, YAML or generic JSON.

    Checkov can scan all three, but hadolint owns ``Dockerfile*`` and yamllint
    owns YAML; claiming them here would double-report the same file under two
    rule sets, and a bare ``*.json`` glob would hand every ``package.json`` and
    CI config to an IaC scanner.

    Args:
        checkov_plugin: The plugin under test.
    """
    patterns = checkov_plugin.definition.file_patterns

    assert_that(patterns).does_not_contain(
        "Dockerfile",
        "Dockerfile.*",
        "*.yaml",
        "*.yml",
        "*.json",
    )


def test_default_command_is_hermetic(checkov_plugin: CheckovPlugin) -> None:
    """The built command never downloads policies, modules, or uploads results.

    Args:
        checkov_plugin: The plugin under test.
    """
    cmd = checkov_plugin._build_command(files=["main.tf"])

    assert_that(cmd).contains("--output", "json", "--skip-download", "--compact")
    assert_that(" ".join(cmd)).contains("--download-external-modules False")
    assert_that(cmd).does_not_contain("--bc-api-key")


def test_command_pins_the_terraform_framework(
    checkov_plugin: CheckovPlugin,
) -> None:
    """Only the terraform framework runs, never the secrets framework.

    Checkov defaults to every framework it supports, so without this the
    secrets framework also scans the ``.tf`` files and emits ``CKV_SECRET_*``
    findings — a surface gitleaks and trufflehog already own in lintro.

    Args:
        checkov_plugin: The plugin under test.
    """
    cmd = checkov_plugin._build_command(files=["main.tf"])

    # Adjacency, not membership: ``contains`` would pass for ``--framework all``
    # as long as the string "terraform" appeared anywhere else in the argv.
    assert_that(" ".join(cmd)).contains(f"--framework {CHECKOV_FRAMEWORKS}")
    # One framework per claimed pattern. checkov parses Terraform's JSON syntax
    # only under ``terraform_json``; a pin naming just ``terraform`` evaluates
    # zero policies against a .tf.json file and still exits 0.
    assert_that(CHECKOV_FRAMEWORKS.split(",")).is_length(
        len(CHECKOV_FILE_PATTERNS),
    )
    assert_that(CHECKOV_FRAMEWORKS.split(",")).is_equal_to(
        ["terraform", "terraform_json"],
    )


def test_command_forbids_a_result_upload(checkov_plugin: CheckovPlugin) -> None:
    """Results never leave the machine, whatever the environment exports.

    Not passing ``--bc-api-key`` is not a guarantee on its own: checkov also
    reads ``BC_API_KEY`` from the environment, so an operator's exported key
    would otherwise put a lintro run into platform mode and upload findings.

    Args:
        checkov_plugin: The plugin under test.
    """
    cmd = checkov_plugin._build_command(files=["main.tf"])

    assert_that(cmd).contains("--skip-results-upload")
    assert_that(cmd).does_not_contain("--bc-api-key")


def test_offline_flags_cannot_be_disabled_by_options(
    checkov_plugin: CheckovPlugin,
) -> None:
    """No option removes the offline flags from the argv.

    The hermetic guarantee is by construction, not by default value: an option
    that could switch it off would make the guarantee unenforceable.

    Args:
        checkov_plugin: The plugin under test.
    """
    # The plugin exposes no switch for them, so the only way a caller could
    # reach the flags is an unknown option smuggled through **kwargs. Both are
    # tried, alongside a real option, to prove neither path drops a flag.
    checkov_plugin.set_options(
        compact=False,
        skip_download=False,
        download_external_modules=True,
    )
    cmd = checkov_plugin._build_command(files=["main.tf"])

    assert_that(cmd).contains("--skip-download", "--skip-results-upload")
    assert_that(" ".join(cmd)).contains(f"--framework {CHECKOV_FRAMEWORKS}")
    assert_that(" ".join(cmd)).contains("--download-external-modules False")
    assert_that(cmd).does_not_contain("--compact")


def test_each_file_gets_its_own_file_flag(checkov_plugin: CheckovPlugin) -> None:
    """``-f`` is repeated per file.

    Checkov's ``-f`` attaches exactly one path; several paths after a single
    flag leave all but the first unscanned, which would report a clean run over
    files checkov never opened.

    Args:
        checkov_plugin: The plugin under test.
    """
    cmd = checkov_plugin._build_command(files=["a.tf", "b.tf", "c.tf"])

    assert_that(cmd.count("-f")).is_equal_to(3)
    assert_that(cmd[-6:]).is_equal_to(["-f", "a.tf", "-f", "b.tf", "-f", "c.tf"])


def test_set_options_checks_and_skip_checks(checkov_plugin: CheckovPlugin) -> None:
    """Check selection options reach the command as comma-joined values.

    Args:
        checkov_plugin: The plugin under test.
    """
    checkov_plugin.set_options(
        checks=["CKV_AWS_260"],
        skip_checks=["CKV_AWS_18", "CKV_AWS_21"],
    )
    cmd = checkov_plugin._build_command(files=["main.tf"])

    assert_that(cmd).contains("--check", "CKV_AWS_260")
    assert_that(cmd).contains("--skip-check", "CKV_AWS_18,CKV_AWS_21")


def test_set_options_accepts_a_single_check_string(
    checkov_plugin: CheckovPlugin,
) -> None:
    """A bare check id is normalized to a one-element list.

    Args:
        checkov_plugin: The plugin under test.
    """
    checkov_plugin.set_options(skip_checks="CKV_AWS_18")

    assert_that(checkov_plugin._build_command(files=["main.tf"])).contains(
        "--skip-check",
        "CKV_AWS_18",
    )


def test_set_options_rejects_bad_type(checkov_plugin: CheckovPlugin) -> None:
    """A non-boolean compact value raises ValueError.

    Args:
        checkov_plugin: The plugin under test.
    """
    with pytest.raises(ValueError):
        checkov_plugin.set_options(compact="yes")  # type: ignore[arg-type]


def test_doc_url_returns_policy_index(checkov_plugin: CheckovPlugin) -> None:
    """doc_url returns the policy index for a code and None for empty input.

    Args:
        checkov_plugin: The plugin under test.
    """
    assert_that(checkov_plugin.doc_url("CKV_AWS_23")).contains("checkov.io")
    assert_that(checkov_plugin.doc_url("")).is_none()


def test_documented_tool_options_examples_parse(
    checkov_plugin: CheckovPlugin,
) -> None:
    """The ``--tool-options`` strings in the docs reach checkov's argv.

    ``--tool-options`` splits on commas, so several check ids can only arrive
    pipe-delimited; this pins the documented spelling.

    Args:
        checkov_plugin: The plugin under test.
    """
    parsed = parse_tool_options(
        "checkov:skip_checks=CKV_AWS_18|CKV_AWS_21,checkov:checks=CKV_AWS_260",
    )
    options: dict[str, Any] = dict(parsed["checkov"])
    checkov_plugin.set_options(**options)
    cmd = checkov_plugin._build_command(files=["main.tf"])

    assert_that(cmd).contains("--skip-check", "CKV_AWS_18,CKV_AWS_21")
    assert_that(cmd).contains("--check", "CKV_AWS_260")


def test_language_detection_suffixes_match_the_file_patterns(tmp_path: Path) -> None:
    """Terraform detection and checkov's globs stay in lockstep.

    ``detect_project_languages`` re-lists the suffixes ``CHECKOV_FILE_PATTERNS``
    declares. If the two drift, either a Terraform tree stops selecting checkov
    or checkov is selected for a tree with nothing it can match.

    Args:
        tmp_path: Temporary project directory.
    """
    for pattern in CHECKOV_FILE_PATTERNS:
        suffix = pattern.removeprefix("*")
        probe = tmp_path / f"probe{suffix}"
        probe.write_text('output "noop" {\n  value = "ok"\n}\n')
        languages = detect_project_languages(root=tmp_path)
        assert_that(languages).described_as(pattern).contains("terraform")
        probe.unlink()


def test_non_terraform_tree_is_not_detected_as_terraform(tmp_path: Path) -> None:
    """A plain JSON file does not select checkov.

    ``*.tf.json`` is matched by name rather than by suffix precisely so that a
    ``package.json`` never counts as Terraform.

    Args:
        tmp_path: Temporary project directory.
    """
    (tmp_path / "package.json").write_text('{"name": "x"}\n')

    assert_that(detect_project_languages(root=tmp_path)).does_not_contain("terraform")


def test_vendored_terraform_cache_does_not_select_checkov(tmp_path: Path) -> None:
    """`terraform init` downloads are third-party and must not count.

    ``.terraform/`` holds provider plugins and remote modules fetched from the
    registry. Their ``.tf`` files are not the project's code, so a tree whose
    only Terraform lives there must not select checkov — and checkov must never
    be handed them to scan.

    Args:
        tmp_path: Temporary project directory.
    """
    vendored = tmp_path / ".terraform" / "modules" / "vpc"
    vendored.mkdir(parents=True)
    (vendored / "main.tf").write_text('resource "aws_s3_bucket" "b" {}\n')

    assert_that(detect_project_languages(root=tmp_path)).does_not_contain("terraform")

    (tmp_path / "main.tf").write_text('output "noop" {\n  value = "ok"\n}\n')

    assert_that(detect_project_languages(root=tmp_path)).contains("terraform")


def test_vendored_terraform_cache_is_never_handed_to_the_tool(
    checkov_plugin: CheckovPlugin,
    tmp_path: Path,
) -> None:
    """`.terraform/` downloads are excluded from discovery, not just detection.

    Pruning the directory in ``project_detection`` only decides whether checkov
    is *selected*; without the discovery exclusion the tool would still be
    handed every provider and remote module ``terraform init`` downloaded, and
    report findings nobody in the repository can fix.

    ``prepare()`` runs for real so discovery is exercised rather than mocked,
    but its version probe is stubbed: on a unit runner with no checkov on PATH
    the probe would return a skipped ``ToolResult`` and the test would never
    reach file discovery at all.

    Args:
        checkov_plugin: The plugin under test.
        tmp_path: Temporary project directory.
    """
    vendored = tmp_path / ".terraform" / "modules" / "vpc"
    vendored.mkdir(parents=True)
    (vendored / "main.tf").write_text('resource "aws_s3_bucket" "b" {}\n')
    own = tmp_path / "main.tf"
    own.write_text('output "noop" {\n  value = "ok"\n}\n')

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        ctx = checkov_plugin.prepare([str(tmp_path)], {"timeout": 30})

    assert_that(isinstance(ctx, ToolResult)).described_as("skipped").is_false()
    files = list(ctx.files)  # type: ignore[union-attr]
    assert_that(files).is_length(1)
    assert_that(files[0]).ends_with("main.tf")
    assert_that(files[0]).does_not_contain(".terraform")


def test_default_options_reach_the_built_command(
    checkov_plugin: CheckovPlugin,
) -> None:
    """Every declared default is visible in the argv built with no overrides.

    Args:
        checkov_plugin: The plugin under test.
    """
    defaults = checkov_plugin.definition.default_options
    cmd = checkov_plugin._build_command(files=["main.tf"])

    assert_that(defaults["compact"]).is_true()
    assert_that(cmd).contains("--compact")
    # Defaults that are None must contribute no flag at all.
    assert_that(cmd).does_not_contain("--check", "--skip-check")


_CONFIGURATION_DOCS = Path(__file__).resolve().parents[4] / "docs" / "configuration.md"


def _documented_default(section: str, option: str) -> str:
    """Return the ``Default`` cell the options table records for an option.

    Args:
        section: The ``#### Checkov Configuration`` section text.
        option: Option name as it appears in the first column.

    Returns:
        The default cell, stripped of markdown code fencing.
    """
    row = re.search(
        rf"^\|\s*`{option}`\s*\|[^|]*\|\s*([^|]*?)\s*\|",
        section,
        re.MULTILINE,
    )
    assert_that(row).described_as(f"{option} row").is_not_none()
    return row.group(1).strip().strip("`")  # type: ignore[union-attr]


def test_documented_option_defaults_match_the_definition(
    checkov_plugin: CheckovPlugin,
) -> None:
    """``docs/configuration.md`` records the definition's actual defaults.

    The completeness suite only asserts that the section heading exists, so a
    later ``timeout`` bump or a ``compact`` flip would leave the published
    table wrong with nothing failing. This pins the table to its source.

    Args:
        checkov_plugin: The plugin under test.
    """
    docs = _CONFIGURATION_DOCS.read_text(encoding="utf-8")
    section = docs.split("#### Checkov Configuration", 1)[1].split("\n#### ", 1)[0]
    defaults = checkov_plugin.definition.default_options

    assert_that(_documented_default(section, "timeout")).is_equal_to(
        str(defaults["timeout"]),
    )
    assert_that(_documented_default(section, "compact")).is_equal_to(
        str(defaults["compact"]).lower(),
    )
    # Options with no default are documented as "-", not as a value.
    for option in ("checks", "skip_checks"):
        assert_that(defaults[option]).is_none()
        assert_that(_documented_default(section, option)).is_equal_to("-")


def test_offline_flags_survive_hostile_tool_options(
    checkov_plugin: CheckovPlugin,
) -> None:
    """No ``--tool-options`` value can turn an offline flag off.

    ``--download-external-modules`` takes a value, so the hermetic guarantee
    documented in ``docs/configuration.md`` depends on argv assembly rather
    than on argparse's last-wins behaviour. It holds because the builder
    consults three named keys only: an unrecognised option is stored but never
    read, and a value smuggled into ``checks`` stays one argv element (the
    subprocess runs ``shell=False``) so it cannot become a flag of its own.

    Args:
        checkov_plugin: The plugin under test.
    """
    smuggled: dict[str, Any] = {
        "download_external_modules": True,
        "skip_download": False,
    }
    checkov_plugin.set_options(
        checks=["CKV_AWS_18 --download-external-modules True"],
        **smuggled,
    )
    cmd = checkov_plugin._build_command(files=["main.tf"])

    assert_that(cmd.count("--download-external-modules")).is_equal_to(1)
    assert_that(cmd[cmd.index("--download-external-modules") + 1]).is_equal_to("False")
    assert_that(cmd).contains("--skip-download", "--skip-results-upload")
    # The smuggled text stays inside the --check value.
    assert_that(cmd[cmd.index("--check") + 1]).contains(
        "--download-external-modules True",
    )
