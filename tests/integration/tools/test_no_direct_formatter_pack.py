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
from click.testing import CliRunner

from lintro.cli import cli
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


def _markerless_script(tmp_path: Path) -> Path:
    """Write ``tree/scripts/fmt.sh`` running black, with no project marker.

    Args:
        tmp_path: Directory that holds the tree.

    Returns:
        The script path.
    """
    script = tmp_path / "tree" / "scripts" / "fmt.sh"
    script.parent.mkdir(parents=True)
    script.write_text("black x.py\n", encoding="utf-8")
    if has_project_marker(get_execution_cwd([str(script)])):
        pytest.skip("an ancestor of the temporary directory holds a project marker")
    return script


@pytest.mark.parametrize(
    ("run_from", "scope"),
    [
        ("tree", "."),
        ("tree", "scripts"),
        ("tree", "scripts/fmt.sh"),
        ("scripts", "."),
        ("scripts", "fmt.sh"),
    ],
)
def test_tree_without_project_marker_is_incomplete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    run_from: str,
    scope: str,
) -> None:
    """Semgrep runs inside scripts/, so the pack is incomplete, not clean (#3437).

    Args:
        tmp_path: Holds the marker-less tree and, beside it, Semgrep's state.
        monkeypatch: Isolate current directory and Semgrep state.
        run_from: Directory the plugin is called from.
        scope: Path handed to the plugin, as in the reported reproduction.
    """
    script = _markerless_script(tmp_path)
    start = script.parent if run_from == "scripts" else script.parent.parent
    _isolate_semgrep(monkeypatch, start, state=tmp_path)
    result = SemgrepPlugin().check([scope], {"rule_packs": [PACK], "jobs": 1})
    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.findings_complete).is_false()
    assert_that(result.issues).is_empty()
    assert_that(result.output).contains(
        PACK,
        "1 file(s) under scripts/ were not checked",
        "(first: fmt.sh)",
    )


def test_markerless_checkout_below_a_scripts_ancestor_stays_clean(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``scripts`` directory above the scanned tree does not hide its files.

    Args:
        tmp_path: Holds the marker-less checkout and Semgrep's state.
        monkeypatch: Isolate current directory and Semgrep state.
    """
    package = tmp_path / "scripts" / "repo" / "packages" / "pkg"
    (package / "src").mkdir(parents=True)
    (package / "src" / "a.py").write_text("VALUE = 1\n", encoding="utf-8")
    if has_project_marker(get_execution_cwd([str(package / "src" / "a.py")])):
        pytest.skip("an ancestor of the temporary directory holds a project marker")
    _isolate_semgrep(monkeypatch, package, state=tmp_path)
    result = SemgrepPlugin().check(["."], {"rule_packs": [PACK], "jobs": 1})
    assert_that(result.success).described_as(str(result.output)).is_true()
    assert_that(result.issues).is_empty()


_RUN = ["check", "scripts", "--tools", "semgrep", "--tool-options"]


@pytest.mark.parametrize("output_format", ["json", "plain"])
def test_markerless_report_never_shows_a_clean_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    output_format: str,
) -> None:
    """Plain and JSON reports both fail and name the unchecked files (#3437).

    Args:
        tmp_path: Holds the marker-less tree and, beside it, Semgrep's state.
        monkeypatch: Isolate current directory and Semgrep state.
        output_format: Report format requested from ``lintro check``.
    """
    script = _markerless_script(tmp_path)
    _isolate_semgrep(monkeypatch, script.parent.parent, state=tmp_path)
    report = tmp_path / "report.json"
    result = CliRunner().invoke(
        cli,
        [
            *_RUN,
            f"semgrep:jobs=1,semgrep:rule_packs={PACK}|",
            "--output-format",
            output_format,
            "--output",
            str(report),
        ],
    )
    assert_that(result.exit_code).described_as(result.output).is_equal_to(1)
    if output_format == "plain":
        assert_that(result.output).contains("were not checked by these packs")
        return
    semgrep = json.loads(report.read_text(encoding="utf-8"))["results"][0]
    assert_that(semgrep["status"]).is_equal_to("failed")
    assert_that(semgrep["issues_count"]).is_equal_to(0)
    assert_that(semgrep["output"]).contains("were not checked by these packs")


@pytest.mark.parametrize(
    "config",
    ["", "semgrep:config=.semgrep.yaml,"],
    ids=["native-discovery", "relative-config"],
)
def test_markerless_user_rules_resolve_as_before(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    config: str,
) -> None:
    """Rooted user filters and a relative config keep their findings (#3437).

    Semgrep keeps running from the files' common directory, so a rooted
    ``/src/**`` include, a rooted exclude and ``config=.semgrep.yaml`` all
    resolve there as they did before; only the pack is reported incomplete.

    Args:
        tmp_path: Holds the marker-less tree and, beside it, Semgrep's state.
        monkeypatch: Isolate current directory and Semgrep state.
        config: Extra tool option selecting the user config explicitly, if any.
    """
    script = _markerless_script(tmp_path)
    scripts = script.parent
    (scripts / "src" / "skip").mkdir(parents=True)
    (scripts / "src" / "a.py").write_text("danger(1)\n", encoding="utf-8")
    (scripts / "src" / "skip" / "b.py").write_text("danger(2)\n", encoding="utf-8")
    (scripts / ".semgrep.yaml").write_text(
        "rules:\n  - id: danger-call\n    languages: [python]\n"
        "    severity: WARNING\n    message: danger\n    pattern: danger(...)\n"
        '    paths:\n      include: ["/src/**"]\n      exclude: ["/src/skip/**"]\n',
        encoding="utf-8",
    )
    _isolate_semgrep(monkeypatch, scripts.parent, state=tmp_path)
    report = tmp_path / "report.json"
    result = CliRunner().invoke(
        cli,
        [
            *_RUN,
            f"{config}semgrep:jobs=1,semgrep:rule_packs={PACK}|",
            "--output-format",
            "json",
            "--output",
            str(report),
        ],
    )
    assert_that(result.exit_code).described_as(result.output).is_equal_to(1)
    semgrep = json.loads(report.read_text(encoding="utf-8"))["results"][0]
    assert_that(
        [(issue["file"], issue["code"]) for issue in semgrep["issues"]],
    ).is_equal_to([("src/a.py", "danger-call")])
    assert_that(semgrep["output"]).contains("were not checked by these packs")
