"""Exercise every shipped rule against the actual Semgrep Python matcher."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess  # nosec B404 - fixed matcher command, shell disabled
from pathlib import Path
from typing import Any

import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli import cli
from lintro.parsers.semgrep.semgrep_issue import SemgrepIssue
from lintro.tools.semgrep.definition import SemgrepPlugin
from tests.integration._tools import require_tool

pytestmark = require_tool("semgrep")

FIXTURES = Path(__file__).parents[2] / "fixtures" / "rules" / "none_coercion"
PACK = Path(__file__).parents[3] / "lintro" / "rules" / "python" / "none-coercion.yaml"


@pytest.mark.parametrize(
    ("selection", "expected_ids"),
    [
        (
            "python/none-coercion|",
            {"local-rule", "lintro.python.none-coercion.or-fallback"},
        ),
        ("|", {"local-rule"}),
    ],
    ids=["one-pack", "disable-inherited"],
)
def test_cli_rule_pack_lists_replace_inherited_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    selection: str,
    expected_ids: set[str],
) -> None:
    """Documented CLI lists control actual matcher findings, retaining native IDs.

    Args:
        tmp_path: Project with both packs inherited and one native rule.
        monkeypatch: Isolate configuration discovery and Semgrep state.
        selection: Literal pipe-list value passed through Click.
        expected_ids: Complete rule ID set emitted by this CLI invocation.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SEMGREP_SETTINGS_FILE", str(tmp_path / "settings.yml"))
    monkeypatch.setenv("SEMGREP_LOG_FILE", str(tmp_path / "semgrep.log"))
    monkeypatch.setenv("SEMGREP_SEND_METRICS", "off")
    monkeypatch.setenv("SEMGREP_ENABLE_VERSION_CHECK", "0")
    (tmp_path / ".lintro-config.yaml").write_text(
        "tools:\n  semgrep:\n    rule_packs: "
        "[python/none-coercion, scripts/no-direct-formatter]\n",
        encoding="utf-8",
    )
    (tmp_path / ".semgrep.yaml").write_text(
        "rules:\n  - id: local-rule\n    languages: [python]\n"
        "    severity: WARNING\n    message: Local finding\n"
        "    pattern: local_call()\n",
        encoding="utf-8",
    )
    (tmp_path / "sample.py").write_text(
        "items = result.issues or []\nlocal_call()\n",
        encoding="utf-8",
    )
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "format.sh").write_text(
        "black sample.py\n",
        encoding="utf-8",
    )
    output = tmp_path / "findings.json"
    result = CliRunner().invoke(
        cli,
        [
            "check",
            ".",
            "--tools",
            "semgrep",
            "--tool-options",
            f"semgrep:config=.semgrep.yaml,semgrep:jobs=1,semgrep:rule_packs={selection}",
            "--output-format",
            "json",
            "--output",
            str(output),
        ],
    )

    assert_that(result.exit_code).described_as(result.output).is_equal_to(1)
    report = json.loads(output.read_text(encoding="utf-8"))
    assert_that(report["results"]).is_length(1)
    semgrep = report["results"][0]
    assert_that(semgrep["success"]).is_true()
    findings = semgrep["issues"]
    assert_that({finding["code"] for finding in findings}).is_equal_to(expected_ids)
    assert_that(findings).is_length(len(expected_ids))


@pytest.fixture(scope="module")
def matcher_report(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """Scan the fixtures once without registry rules, metrics or rule ID rewriting.

    Args:
        tmp_path_factory: Factory for an isolated matcher workspace.

    Returns:
        Complete Semgrep JSON report.
    """
    root = tmp_path_factory.mktemp("none-coercion")
    for fixture in FIXTURES.glob("*.py.txt"):
        shutil.copyfile(fixture, root / fixture.stem)
    env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT") if key in os.environ}
    env["HOME"] = str(root)
    env["TMPDIR"] = str(root)
    env["SEMGREP_SETTINGS_FILE"] = str(root / "settings.yml")
    env["SEMGREP_LOG_FILE"] = str(root / "semgrep.log")
    proc = subprocess.run(  # nosec B603 B607 - fixed semgrep argv, local fixtures only
        [
            "semgrep",
            "scan",
            "--config",
            str(PACK),
            "--json",
            "--metrics=off",
            "--disable-version-check",
            "--no-rewrite-rule-ids",
            "--no-git-ignore",
            "--jobs=1",
            str(root),
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=90,
    )
    assert_that(proc.returncode).described_as(proc.stderr).is_zero()
    report: dict[str, Any] = json.loads(proc.stdout)
    assert_that(report["errors"]).is_empty()
    assert_that(report["paths"]["scanned"]).is_length(8)
    return report


@pytest.mark.parametrize(
    "fixture",
    [
        "getattr_default",
        "or_fallback",
        "except_return",
        "none_guard_return",
        "stringified_get",
        "decoded_get_default",
        "failed_tool_result",
        "capability_empty",
    ],
)
def test_none_coercion_matches_exactly(
    matcher_report: dict[str, Any],
    fixture: str,
) -> None:
    """Every positive annotation matches once; every other line stays unreported.

    Args:
        matcher_report: Output from the real matcher across all fixture files.
        fixture: Rule-specific fixture including nearby false-positive cases.
    """
    source = FIXTURES / f"{fixture}.py.txt"
    expected = []
    for line, text in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
        annotation = re.search(r"# ruleid: (\S+)", text)
        if annotation:
            expected.append((annotation[1], line + 1))
    actual = [
        (finding["check_id"], finding["start"]["line"])
        for finding in matcher_report["results"]
        if Path(finding["path"]).name == f"{fixture}.py"
    ]
    assert_that(expected).is_not_empty()
    assert_that(sorted(actual)).is_equal_to(sorted(expected))


@pytest.mark.parametrize("explicit_config", [False, True], ids=["native", "explicit"])
def test_plugin_runs_pack_and_native_rules_together(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    explicit_config: bool,
) -> None:
    """Sequential scans retain native rules and enable packaged findings once.

    Args:
        tmp_path: Isolated project with native rules and source to scan.
        monkeypatch: Keep Semgrep settings, logging and project discovery local.
        explicit_config: Select a user configuration in a subdirectory explicitly.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SEMGREP_SETTINGS_FILE", str(tmp_path / "settings.yml"))
    monkeypatch.setenv("SEMGREP_LOG_FILE", str(tmp_path / "semgrep.log"))
    monkeypatch.setenv("SEMGREP_SEND_METRICS", "off")
    monkeypatch.setenv("SEMGREP_ENABLE_VERSION_CHECK", "0")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "pack-test"\nversion = "0.0.0"\n',
        encoding="utf-8",
    )
    config = (
        tmp_path / "custom" / "rules.yaml"
        if explicit_config
        else tmp_path / ".semgrep.yaml"
    )
    config.parent.mkdir(exist_ok=True)
    config.write_text(
        "rules:\n  - id: local-rule\n    languages: [python]\n"
        "    severity: WARNING\n    message: Local finding\n"
        "    pattern: local_call()\n",
        encoding="utf-8",
    )
    source = tmp_path / "sample.py"
    source.write_text("items = result.issues or []\nlocal_call()\n", encoding="utf-8")
    options: dict[str, object] = {"jobs": 1}
    native_options = {**options, "config": str(config.relative_to(tmp_path))}
    if explicit_config:
        options = native_options
    plugin = SemgrepPlugin()
    original = plugin.check(
        [str(source)],
        native_options,
    )
    assert_that(original.success).is_true()
    original_ids = {
        issue.check_id
        for issue in original.issues or []
        if isinstance(issue, SemgrepIssue)
    }
    assert_that(original_ids).is_length(1)

    result = plugin.check(
        [str(source)],
        {**options, "rule_packs": ["python/none-coercion"]},
    )

    assert_that(result.success).is_true()
    assert_that(result.inspected_files).is_true()
    assert_that(result.issues_count).is_equal_to(2)
    findings = [issue for issue in result.issues if isinstance(issue, SemgrepIssue)]
    assert_that({issue.check_id for issue in findings}).is_equal_to(
        original_ids | {"lintro.python.none-coercion.or-fallback"},
    )
    assert_that({issue.severity for issue in findings}).is_equal_to({"INFO", "WARNING"})

    restored = plugin.check([str(source)], native_options)
    assert_that(restored.success).is_true()
    assert_that(restored.issues_count).is_equal_to(1)
    assert_that(
        {
            issue.check_id
            for issue in restored.issues
            if isinstance(issue, SemgrepIssue)
        },
    ).is_equal_to(original_ids)
