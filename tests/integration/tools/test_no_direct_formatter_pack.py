"""Exercise script targeting, native commands and suppressions with real Semgrep."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess  # nosec B404 - fixed local Semgrep command, shell disabled
from pathlib import Path
from typing import Any

import pytest
import yaml
from assertpy import assert_that

from lintro.parsers.semgrep.semgrep_issue import SemgrepIssue
from lintro.rules import resolve_rule_pack
from lintro.tools.semgrep.definition import SemgrepPlugin
from tests.integration._tools import require_tool

pytestmark = require_tool("semgrep")

PACK = "scripts/no-direct-formatter"
RULE_ID = "lintro.scripts.no-direct-formatter.native-command"
FIXTURES = Path(__file__).parents[2] / "fixtures" / "rules" / "no_direct_formatter"


@pytest.fixture(scope="module")
def script_project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Materialize fixtures under the paths the pack actually targets.

    Args:
        tmp_path_factory: Isolated module workspace factory.

    Returns:
        Project root containing positive, clean, ignored and suppressed scripts.
    """
    root = tmp_path_factory.mktemp("no-direct-formatter")
    (root / "pyproject.toml").write_text(
        '[project]\nname = "script-pack-test"\nversion = "0.0.0"\n',
        encoding="utf-8",
    )
    for fixture in FIXTURES.rglob("*.txt"):
        target = root / fixture.relative_to(FIXTURES).with_suffix("")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(fixture, target)
    return root


@pytest.fixture(scope="module")
def script_report(script_project: Path) -> dict[str, Any]:
    """Run offline with isolated settings and stable rule identifiers.

    Args:
        script_project: Fixture project with its original script paths.

    Returns:
        JSON report from the actual matcher.
    """
    env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT") if key in os.environ}
    env.update(
        HOME=str(script_project),
        TMPDIR=str(script_project),
        SEMGREP_SETTINGS_FILE=str(script_project / "settings.yml"),
        SEMGREP_LOG_FILE=str(script_project / "semgrep.log"),
    )
    proc = subprocess.run(  # nosec B603 B607 - fixed argv, local rules and fixtures
        [
            "semgrep",
            "scan",
            "--config",
            str(resolve_rule_pack(PACK)),
            "--json",
            "--metrics=off",
            "--disable-version-check",
            "--no-rewrite-rule-ids",
            "--no-git-ignore",
            "--jobs=1",
            ".",
        ],
        cwd=script_project,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=90,
    )
    assert_that(proc.returncode).described_as(proc.stderr).is_zero()
    report: dict[str, Any] = json.loads(proc.stdout)
    assert_that(report["errors"]).is_empty()
    return report


@pytest.mark.parametrize(
    "fixture",
    sorted(path.relative_to(FIXTURES).as_posix() for path in FIXTURES.rglob("*.txt")),
)
def test_script_findings_match_annotations(
    script_report: dict[str, Any],
    fixture: str,
) -> None:
    """Report exactly the annotated lines, including zero for every control.

    Args:
        script_report: Actual Semgrep output for all script fixtures.
        fixture: Source fixture path relative to the fixture root.
    """
    expected = []
    source = FIXTURES / fixture
    for line, text in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
        annotation = re.search(r"(?:#|//) ruleid: (\S+)", text)
        if annotation:
            expected.append((annotation[1], line + 1))
    actual = [
        (finding["check_id"], finding["start"]["line"])
        for finding in script_report["results"]
        if finding["path"] == Path(fixture).with_suffix("").as_posix()
    ]
    assert_that(sorted(actual)).described_as(fixture).is_equal_to(sorted(expected))


@pytest.mark.parametrize("scope", [".", "scripts/direct.sh", "Makefile", "build.mjs"])
def test_plugin_reports_native_commands(
    script_project: Path,
    monkeypatch: pytest.MonkeyPatch,
    scope: str,
) -> None:
    """The plugin discovers script inputs and returns useful, stable findings.

    Args:
        script_project: Fixture tree with a project marker.
        monkeypatch: Isolate current directory and Semgrep state.
        scope: Directory or explicit script path handed to the plugin.
    """
    monkeypatch.chdir(script_project)
    monkeypatch.setenv("SEMGREP_SETTINGS_FILE", str(script_project / "settings.yml"))
    monkeypatch.setenv("SEMGREP_LOG_FILE", str(script_project / "semgrep.log"))
    monkeypatch.setenv("SEMGREP_SEND_METRICS", "off")
    monkeypatch.setenv("SEMGREP_ENABLE_VERSION_CHECK", "0")
    result = SemgrepPlugin().check([scope], {"rule_packs": [PACK], "jobs": 1})
    assert_that(result.success).is_true()
    assert_that(result.inspected_files).is_true()
    assert_that(result.skipped).is_false()
    assert_that(result.issues_count).is_greater_than(0)
    findings = [issue for issue in result.issues if isinstance(issue, SemgrepIssue)]
    assert_that({issue.check_id for issue in findings}).is_equal_to({RULE_ID})
    assert_that({issue.severity for issue in findings}).is_equal_to({"WARNING"})
    assert_that(findings[0].message).contains("lintro fmt", "uv run lintro fmt")


def test_every_declared_formatter_example_matches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Execute the pack's capability-guarded examples rather than trusting metadata.

    Args:
        tmp_path: Isolated scripts directory for one command per formatter.
        monkeypatch: Isolate project discovery and Semgrep state.
    """
    rules = yaml.safe_load(resolve_rule_pack(PACK).read_text(encoding="utf-8"))["rules"]
    examples = rules[0]["metadata"]["lintro_formatters"]
    (tmp_path / "scripts").mkdir()
    for name, command in examples.items():
        (tmp_path / "scripts" / f"{name}.sh").write_text(
            command + "\n",
            encoding="utf-8",
        )
    (tmp_path / "pyproject.toml").touch()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SEMGREP_SETTINGS_FILE", str(tmp_path / "settings.yml"))
    monkeypatch.setenv("SEMGREP_LOG_FILE", str(tmp_path / "semgrep.log"))
    monkeypatch.setenv("SEMGREP_SEND_METRICS", "off")
    monkeypatch.setenv("SEMGREP_ENABLE_VERSION_CHECK", "0")
    result = SemgrepPlugin().check(["."], {"rule_packs": [PACK], "jobs": 1})
    assert_that(result.success).is_true()
    assert_that(result.inspected_files).is_true()
    assert_that(result.issues_count).is_equal_to(len(examples))
    actual = {
        Path(issue.file).stem
        for issue in result.issues
        if isinstance(issue, SemgrepIssue)
    }
    assert_that(actual).is_equal_to(set(examples))
