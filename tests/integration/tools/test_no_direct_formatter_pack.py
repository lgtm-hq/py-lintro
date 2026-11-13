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
from loguru import logger

from lintro.parsers.semgrep.semgrep_issue import SemgrepIssue
from lintro.rules import resolve_rule_pack
from lintro.tools.semgrep.definition import SemgrepPlugin
from lintro.utils.execution_paths import get_execution_cwd, has_project_marker
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


def _annotated_scripts() -> set[str]:
    """Return the materialized fixture paths that carry a ruleid annotation.

    Returns:
        Project-relative POSIX paths expected to produce findings.
    """
    return {
        fixture.relative_to(FIXTURES).with_suffix("").as_posix()
        for fixture in FIXTURES.rglob("*.txt")
        if re.search(r"(?:#|//) ruleid: ", fixture.read_text(encoding="utf-8"))
    }


def _isolate_semgrep(
    monkeypatch: pytest.MonkeyPatch,
    root: Path,
    state: Path | None = None,
) -> None:
    """Run the plugin from a project root with offline, private Semgrep state.

    Args:
        monkeypatch: Fixture used to set the directory and environment.
        root: Directory to run from.
        state: Directory for Semgrep's settings and log; defaults to ``root``.
    """
    state = root if state is None else state
    monkeypatch.chdir(root)
    monkeypatch.setenv("SEMGREP_SETTINGS_FILE", str(state / "settings.yml"))
    monkeypatch.setenv("SEMGREP_LOG_FILE", str(state / "semgrep.log"))
    monkeypatch.setenv("SEMGREP_SEND_METRICS", "off")
    monkeypatch.setenv("SEMGREP_ENABLE_VERSION_CHECK", "0")


# Out-of-scope files that run a formatter, passed explicitly beside one script.
OUT_OF_SCOPE = ["format.sh", "descripts/generate.sh", "src/ordinary.js"]


@pytest.mark.parametrize(
    ("scope", "expected"),
    [
        (["."], None),
        (["scripts/direct.sh"], {"scripts/direct.sh"}),
        (["Makefile"], {"Makefile"}),
        (["build.mjs"], {"build.mjs"}),
        (["scripts/direct.sh", *OUT_OF_SCOPE], {"scripts/direct.sh"}),
    ],
    ids=["project", "script", "makefile", "module", "explicit-out-of-scope"],
)
def test_plugin_reports_native_commands(
    script_project: Path,
    monkeypatch: pytest.MonkeyPatch,
    scope: list[str],
    expected: set[str] | None,
) -> None:
    """The plugin reports exactly the in-scope scripts it is handed.

    Args:
        script_project: Fixture tree with a project marker.
        monkeypatch: Isolate current directory and Semgrep state.
        scope: Directories or explicit file paths handed to the plugin.
        expected: Files that must be reported; None means every annotated one.
    """
    _isolate_semgrep(monkeypatch, script_project)
    result = SemgrepPlugin().check(scope, {"rule_packs": [PACK], "jobs": 1})
    assert_that(result.success).is_true()
    assert_that(result.inspected_files).is_true()
    assert_that(result.skipped).is_false()
    findings = [issue for issue in result.issues if isinstance(issue, SemgrepIssue)]
    reported = {Path(issue.file).as_posix() for issue in findings}
    assert_that(reported).is_equal_to(
        _annotated_scripts() if expected is None else expected,
    )
    assert_that(reported).does_not_contain(*OUT_OF_SCOPE)
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
    _isolate_semgrep(monkeypatch, tmp_path)
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


@pytest.mark.parametrize("scope", [".", "scripts", "scripts/fmt.sh"])
def test_tree_without_project_marker_warns_instead_of_passing_silently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scope: str,
) -> None:
    """A marker-less tree anchors on scripts/ itself, so the user is told why.

    Args:
        tmp_path: Holds the marker-less tree and, beside it, Semgrep's state.
        monkeypatch: Isolate current directory and Semgrep state.
        scope: Path handed to the plugin, as in the reported reproduction.
    """
    tree = tmp_path / "tree"
    script = tree / "scripts" / "fmt.sh"
    script.parent.mkdir(parents=True)
    script.write_text("black x.py\n", encoding="utf-8")
    if has_project_marker(get_execution_cwd([str(script)])):
        pytest.skip("an ancestor of the temporary directory holds a project marker")
    _isolate_semgrep(monkeypatch, tree, state=tmp_path)
    warnings: list[str] = []
    sink = logger.add(warnings.append, level="WARNING", format="{message}")
    try:
        result = SemgrepPlugin().check([scope], {"rule_packs": [PACK], "jobs": 1})
    finally:
        logger.remove(sink)
    assert_that(result.success).is_true()
    unanchored = [text for text in warnings if "no project marker" in text]
    assert_that(unanchored).is_length(1)
    assert_that(unanchored[0]).contains(PACK, str(script.parent))
