"""cargo-audit audits every Cargo.lock it is handed (#3549).

The scope rule: every lockfile lintro's file discovery hands over is audited,
with no built-in ``vendor/`` or ``target/`` exclusion. Lockfiles are left out
only through lintro's normal exclude patterns, and the ones left out are named.

cargo-audit itself is replaced by a fake that reads the ``--file`` argument and
answers from the lockfile's content, so these tests pass without cargo-audit on
PATH. Every crate fixture has a ``Cargo.toml`` next to its ``Cargo.lock``.
"""

from __future__ import annotations

import json
import os
import subprocess  # nosec B404 - only TimeoutExpired is used, nothing is run
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.config.config_loader import clear_config_cache, load_config
from lintro.enums.action import Action
from lintro.models.core.tool_outcome import ToolOutcome, Unavailable
from lintro.models.core.tool_result import ToolResult
from lintro.plugins.subprocess_executor import SubprocessResult
from lintro.tools.cargo_audit.definition import CargoAuditPlugin
from lintro.utils.config import clear_pyproject_cache
from lintro.utils.execution.tool_configuration import configure_tool_for_execution
from lintro.utils.file_cache import ToolCache
from lintro.utils.fingerprint_baseline import fingerprint
from lintro.utils.tool_options import parse_tool_options
from lintro.utils.unified_config import UnifiedConfigManager

VULNERABLE_LOCK = """version = 3

[[package]]
name = "smallvec"
version = "1.6.0"
source = "registry+https://github.com/rust-lang/crates.io-index"
"""
CLEAN_LOCK = "version = 3\n"

VULNERABLE_REPORT = json.dumps(
    {
        "vulnerabilities": {
            "found": True,
            "count": 1,
            "list": [
                {
                    "advisory": {
                        "id": "RUSTSEC-2021-0003",
                        "title": "Buffer overflow in SmallVec::insert_many",
                        "severity": "HIGH",
                    },
                    "package": {"name": "smallvec", "version": "1.6.0"},
                },
            ],
        },
    },
)
CLEAN_REPORT = json.dumps(
    {"vulnerabilities": {"found": False, "count": 0, "list": []}},
)


def _crate(root: Path, rel: str, *, vulnerable: bool = False) -> Path:
    """Write a crate with a Cargo.toml and a Cargo.lock, as real crates have.

    Args:
        root: Directory the crate is created under.
        rel: Crate directory relative to ``root``.
        vulnerable: Whether the lockfile pins a crate with an advisory.

    Returns:
        The resolved path of the crate's Cargo.lock.
    """
    crate = root / rel
    crate.mkdir(parents=True, exist_ok=True)
    name = crate.name.lstrip("-") or "crate"
    (crate / "Cargo.toml").write_text(
        f'[package]\nname = "{name}"\nversion = "0.1.0"\n',
    )
    lock = crate / "Cargo.lock"
    lock.write_text(VULNERABLE_LOCK if vulnerable else CLEAN_LOCK)
    return lock.resolve()


@dataclass
class _FakeCargoAudit:
    """Stand-in for cargo-audit that answers from the lockfile it is given.

    Attributes:
        commands: Every command run, in order.
        timeouts: The timeout passed with each command.
        broken: Lockfiles for which cargo-audit prints no usable report.
        exit_one: Lockfiles whose clean report comes with exit status 1.
        clock: Fake monotonic clock, advanced by ``seconds_per_run``.
        seconds_per_run: Seconds each run takes on the fake clock.
        hang_on: Lockfile whose audit raises ``TimeoutExpired``.
        reports: Lockfiles answered with a fixed exit status and stdout.
        stderr: Standard error printed with a fixed report, empty if unset.
    """

    commands: list[list[str]] = field(default_factory=list)
    timeouts: list[float] = field(default_factory=list)
    broken: set[Path] = field(default_factory=set)
    exit_one: set[Path] = field(default_factory=set)
    clock: list[float] = field(default_factory=lambda: [1000.0])
    seconds_per_run: float = 0.0
    hang_on: Path | None = None
    reports: dict[Path, tuple[int, str]] = field(default_factory=dict)
    stderr: dict[Path, str] = field(default_factory=dict)

    def monotonic(self) -> float:
        """Return the fake clock.

        Returns:
            The current fake time.
        """
        return self.clock[0]

    def __call__(
        self,
        cmd: list[str],
        timeout: float | None = None,
        cwd: str | None = None,
    ) -> SubprocessResult:
        """Audit the lockfile named by ``--file=``.

        Args:
            cmd: The cargo-audit command.
            timeout: Seconds allowed.
            cwd: Working directory.

        Returns:
            A cargo-audit result for that lockfile.

        Raises:
            subprocess.TimeoutExpired: For the lockfile in ``hang_on``.
        """
        self.commands.append(cmd)
        self.timeouts.append(float(timeout or 0))
        self.clock[0] += self.seconds_per_run
        lock = Path(
            next(a for a in cmd if a.startswith("--file=")).removeprefix("--file="),
        )
        assert_that(cwd).is_equal_to(str(lock.parent))
        if lock == self.hang_on:
            raise subprocess.TimeoutExpired(cmd, timeout or 0)
        if lock in self.broken:
            return SubprocessResult(
                returncode=1,
                stdout="",
                stderr="boom",
                output="boom",
            )
        if lock in self.reports:
            returncode, stdout = self.reports[lock]
            return SubprocessResult(
                returncode=returncode,
                stdout=stdout,
                stderr=self.stderr.get(lock, ""),
                output=stdout + self.stderr.get(lock, ""),
            )
        content = lock.read_text()
        vulnerable = "smallvec" in content
        report = VULNERABLE_REPORT if vulnerable else CLEAN_REPORT
        if not content.strip():
            # What cargo-audit 0.23 prints, at exit 0, for a zero-byte file.
            report = json.dumps(
                {
                    "lockfile": {"dependency-count": 0},
                    "vulnerabilities": {"found": False, "count": 0, "list": []},
                },
            )
        return SubprocessResult(
            returncode=1 if vulnerable or lock in self.exit_one else 0,
            stdout=report,
            stderr="",
            output=report,
        )

    def audited(self) -> list[Path]:
        """Return the lockfiles audited, in run order.

        Returns:
            One path per command.
        """
        return [
            Path(next(a for a in c if a.startswith("--file=")).removeprefix("--file="))
            for c in self.commands
        ]


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a git repository and make it the process directory.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Used to change the working directory.

    Returns:
        The repository root, resolved.
    """
    root = (tmp_path / "repo").resolve()
    (root / ".git").mkdir(parents=True)
    monkeypatch.chdir(root)
    return root


@pytest.fixture
def fake() -> Iterator[_FakeCargoAudit]:
    """Provide a fake cargo-audit and skip the version probe.

    Yields:
        _FakeCargoAudit: The fake, for configuring and inspecting runs.
    """
    audit = _FakeCargoAudit()
    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        yield audit


def _check(
    fake: _FakeCargoAudit,
    paths: list[str],
    *,
    exclude: list[str] | None = None,
    timeout: int | None = None,
    options: dict[str, object] | None = None,
    include_vendored: bool | None = None,
) -> ToolResult:
    """Run the plugin's check with cargo-audit replaced by ``fake``.

    Args:
        fake: The fake cargo-audit.
        paths: Paths handed to ``check``.
        exclude: Extra exclude patterns, as ``--exclude`` would add.
        timeout: Timeout budget for the check.
        options: Options passed to ``check``, such as ``diff_base``.
        include_vendored: The cargo_audit option, when set.

    Returns:
        The check result.
    """
    plugin = CargoAuditPlugin()
    if exclude:
        plugin.set_options(exclude_patterns=exclude)
    if timeout is not None:
        plugin.set_options(timeout=timeout)
    if include_vendored is not None:
        plugin.set_options(include_vendored=include_vendored)
    with patch.object(plugin, "_run_subprocess_result", side_effect=fake):
        return plugin.check(paths, options or {})


def test_every_lockfile_is_audited_and_each_finding_names_its_own(
    repo: Path,
    fake: _FakeCargoAudit,
) -> None:
    """Separate crates are all audited; findings keep apart in the baseline.

    Args:
        repo: Repository root.
        fake: The fake cargo-audit.
    """
    _crate(repo, "app")
    second = _crate(repo, "tools/second", vulnerable=True)
    third = _crate(repo, "zeta", vulnerable=True)

    result = _check(fake, ["."])

    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_false()
    assert_that(result.issues_count).is_equal_to(2)
    assert_that([i.file for i in result.issues or []]).is_equal_to(
        [str(second), str(third)],
    )
    entries = [
        fingerprint(issue, tool="cargo_audit", root=repo)
        for issue in result.issues or []
    ]
    assert_that([e.file for e in entries if e]).is_equal_to(
        ["tools/second/Cargo.lock", "zeta/Cargo.lock"],
    )
    assert_that(result.output).contains("tools/second/Cargo.lock", "zeta/Cargo.lock")


def test_later_lockfiles_reuse_the_fetched_advisory_database(
    repo: Path,
    fake: _FakeCargoAudit,
) -> None:
    """Only audits before the first parsed report may fetch the database.

    Args:
        repo: Repository root.
        fake: The fake cargo-audit.
    """
    fake.broken.add(_crate(repo, "a"))
    _crate(repo, "b")
    _crate(repo, "c")

    _check(fake, ["."])

    fetches = ["--no-fetch" not in cmd for cmd in fake.commands]
    assert_that(fetches).is_equal_to([True, True, False])


@pytest.mark.parametrize(
    "vendored",
    ["vendor/dep", "target/package/app-0.1.0"],
    ids=["vendor", "target"],
)
@pytest.mark.parametrize("include", [False, True], ids=["default", "included"])
def test_vendored_and_build_tree_copies_are_skipped_unless_included(
    repo: Path,
    fake: _FakeCargoAudit,
    vendored: str,
    include: bool,
) -> None:
    """Copies are skipped and named by default; include_vendored audits them.

    Args:
        repo: Repository root.
        fake: The fake cargo-audit.
        vendored: Directory of the vendored or packaged copy.
        include: Whether include_vendored is set.
    """
    app = _crate(repo, "app")
    copy = _crate(repo, vendored, vulnerable=True)

    result = _check(fake, ["."], include_vendored=include or None)

    assert_that(fake.audited()).is_equal_to([app, copy] if include else [app])
    assert_that([i.file for i in result.issues or []]).is_equal_to(
        [str(copy)] if include else [],
    )
    assert_that(result.success).is_equal_to(not include)
    note = (
        f"Not audited, lockfiles under a vendor/ or target/ directory (1): "
        f"{vendored}/Cargo.lock; cargo builds from the top-level Cargo.lock. To "
        "audit them, set the cargo_audit option include_vendored=True."
    )
    if include:
        assert_that(result.output).does_not_contain("Not audited, lockfiles under")
    else:
        assert_that(result.output).is_equal_to(note)


def _layout(base: Path, kind: str) -> tuple[Path, list[str]]:
    """Build one repository layout and the paths a caller would pass.

    Args:
        base: Temporary directory to build under.
        kind: Which layout and caller to model.

    Returns:
        The repository root and the paths handed to ``check``.
    """
    parent = base / "vendor" if kind == "repo-under-unrelated-vendor" else base
    root = (parent / "repo").resolve()
    root.mkdir(parents=True)
    if kind != "no-repository":
        (root / ".git").mkdir()
    app = _crate(root, "app", vulnerable=True)
    dep = _crate(root, "vendor/dep", vulnerable=True)
    if kind == "vendored-submodule":
        (dep.parent / ".git").write_text("gitdir: ../../.git/modules/dep\n")
    paths = {
        "watch-relative-files": ["vendor/dep/Cargo.lock", "app/Cargo.lock"],
        "absolute-files": [str(dep), str(app)],
        "absolute-directory": [str(root)],
        "from-subdirectory": [".."],
    }.get(kind, ["."])
    return root, paths


LAYOUTS = [
    "repo-root",
    "watch-relative-files",
    "absolute-files",
    "absolute-directory",
    "vendored-submodule",
    "repo-under-unrelated-vendor",
    "no-repository",
    "from-subdirectory",
]


@pytest.mark.parametrize("kind", LAYOUTS)
def test_excluded_lockfile_gets_the_same_answer_for_every_caller(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake: _FakeCargoAudit,
    kind: str,
) -> None:
    """An exclude pattern drops the vendored copy, and the output names it.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Used to change the working directory.
        fake: The fake cargo-audit.
        kind: Layout and caller under test.
    """
    root, paths = _layout(tmp_path, kind)
    monkeypatch.chdir(root / "app" if kind == "from-subdirectory" else root)

    result = _check(fake, paths, exclude=["vendor/"])

    assert_that(fake.audited()).is_equal_to([root / "app" / "Cargo.lock"])
    assert_that(result.issues_count).is_equal_to(1)
    assert_that(result.output).contains("excluded by lintro's exclude patterns (1): ")
    assert_that(result.output).contains("vendor/dep/Cargo.lock.")


@pytest.mark.parametrize("kind", LAYOUTS)
def test_default_skip_reads_only_components_below_the_requested_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake: _FakeCargoAudit,
    kind: str,
) -> None:
    """vendor/dep is skipped below a requested directory, whatever lies above.

    A lockfile requested by name is its own root, so the file-list callers
    audit it. A repository inside an unrelated ``vendor`` folder is audited.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Used to change the working directory.
        fake: The fake cargo-audit.
        kind: Layout and caller under test.
    """
    root, paths = _layout(tmp_path, kind)
    monkeypatch.chdir(root / "app" if kind == "from-subdirectory" else root)
    app, dep = root / "app" / "Cargo.lock", root / "vendor" / "dep" / "Cargo.lock"
    named = kind in {"watch-relative-files", "absolute-files"}

    result = _check(fake, paths)

    assert_that(sorted(fake.audited())).is_equal_to(
        sorted([app, dep] if named else [app]),
    )
    assert_that(result.issues_count).is_equal_to(2 if named else 1)
    if named:
        assert_that(result.output).does_not_contain("Not audited, lockfiles under")
    else:
        assert_that(result.output).contains("lockfiles under a vendor/ or target/ ")
        assert_that(result.output).contains("vendor/dep/Cargo.lock;")


@pytest.mark.parametrize(
    ("requested", "audited"),
    [
        pytest.param(["."], ["my-target", "vendored"], id="whole-components"),
        pytest.param(["app/vendor/dep/Cargo.lock"], ["app/vendor/dep"], id="file-by-name"),
        pytest.param(["target"], ["target/package/x"], id="copy-dir-as-root"),
        pytest.param([".", "app/vendor"], ["app/vendor/dep", "my-target", "vendored"], id="nested-root"),
    ],
)  # fmt: skip
def test_copy_rule_matches_whole_components_below_the_root(
    repo: Path,
    fake: _FakeCargoAudit,
    requested: list[str],
    audited: list[str],
) -> None:
    """Only a whole ``vendor``/``target`` component below the root is a copy.

    Args:
        repo: Repository root.
        fake: The fake cargo-audit.
        requested: Paths handed to ``check``.
        audited: Crate directories whose lockfiles must be audited.
    """
    for crate in (
        "vendored",
        "my-target",
        "app/vendor/dep",
        "target/package/x",
        "target/package/x/vendor/y",
    ):
        _crate(repo, crate)

    _check(fake, requested)

    assert_that(sorted(fake.audited())).is_equal_to(
        sorted(repo / crate / "Cargo.lock" for crate in audited),
    )


def test_include_vendored_must_be_a_boolean() -> None:
    """A non-boolean include_vendored is rejected like other options."""
    options: dict[str, Any] = {"include_vendored": "yes"}
    with pytest.raises(ValueError, match="include_vendored"):
        CargoAuditPlugin().set_options(**options)


@pytest.mark.parametrize("source", ["tool-options", "pyproject"])
def test_include_vendored_reaches_the_plugin_from_config(
    repo: Path,
    fake: _FakeCargoAudit,
    monkeypatch: pytest.MonkeyPatch,
    source: str,
) -> None:
    """``--tool-options cargo_audit:include_vendored=True`` and pyproject work.

    Args:
        repo: Repository root.
        fake: The fake cargo-audit.
        monkeypatch: Keeps the enclosing checkout's config out of discovery.
        source: Where the option comes from.
    """
    app = _crate(repo, "app")
    copy = _crate(repo, "vendor/dep")
    if source == "pyproject":
        (repo / "pyproject.toml").write_text(
            "[tool.lintro.cargo_audit]\ninclude_vendored = true\n",
        )
    monkeypatch.setattr("lintro.config.config_loader._find_config_file", lambda: None)
    cli = parse_tool_options("cargo_audit:include_vendored=True")
    plugin = configure_tool_for_execution(
        tool=CargoAuditPlugin(),
        tool_name="cargo_audit",
        config_manager=UnifiedConfigManager(),
        tool_option_dict=cli if source == "tool-options" else {},
        exclude=None,
        include_venv=False,
        incremental=False,
        action=Action.CHECK,
        selected_tools={"cargo_audit"},
        lintro_config=load_config(None),
    )

    with patch.object(plugin, "_run_subprocess_result", side_effect=fake):
        plugin.check(["."], {})

    assert_that(fake.audited()).is_equal_to([app, copy])


def test_every_lockfile_excluded_is_no_files_naming_them(
    repo: Path,
    fake: _FakeCargoAudit,
) -> None:
    """Nothing in scope by configuration is "no files", and names the lockfiles.

    It is not a skip: the no-silent-skip gate treats it as every other tool
    whose inputs the user's exclude patterns remove.

    Args:
        repo: Repository root.
        fake: The fake cargo-audit.
    """
    _crate(repo, "vendor/a", vulnerable=True)
    _crate(repo, "vendor/b")

    result = _check(fake, ["vendor/a/Cargo.lock", "."], exclude=["vendor/"])

    assert_that(fake.commands).is_empty()
    assert_that(result.skipped).is_false()
    assert_that(result.no_files).is_true()
    assert_that(result.success).is_true()
    assert_that(result.output).is_equal_to(
        "No Cargo.lock in scope to audit.\n\nNot audited, excluded by lintro's "
        "exclude patterns (2): vendor/a/Cargo.lock, vendor/b/Cargo.lock.",
    )


@pytest.mark.parametrize("failure", ["no-report", "exit-without-findings"])
def test_a_failed_lockfile_fails_the_check_beside_clean_ones(
    repo: Path,
    fake: _FakeCargoAudit,
    failure: str,
) -> None:
    """One lockfile without a complete report is never hidden by the others.

    Args:
        repo: Repository root.
        fake: The fake cargo-audit.
        failure: How the failing audit goes wrong.
    """
    _crate(repo, "a")
    bad = _crate(repo, "b")
    _crate(repo, "c")
    (fake.broken if failure == "no-report" else fake.exit_one).add(bad)

    result = _check(fake, ["."])

    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.findings_complete).is_false()
    assert_that(result.issues_count).is_zero()
    assert_that(result.parse_failures_count).is_equal_to(
        1 if failure == "no-report" else 0,
    )
    assert_that(result.output).contains(
        "did not produce a complete report, not audited (1): b/Cargo.lock (",
    )


def test_one_timeout_budget_covers_every_lockfile(
    repo: Path,
    fake: _FakeCargoAudit,
) -> None:
    """Each audit gets what is left; lockfiles past the budget are named.

    Args:
        repo: Repository root.
        fake: The fake cargo-audit.
    """
    for name in ("a", "b", "c", "d"):
        _crate(repo, name)
    fake.seconds_per_run = 50

    with patch(
        "lintro.tools.cargo_audit.definition.time.monotonic",
        side_effect=fake.monotonic,
    ):
        result = _check(fake, ["."], timeout=120)

    assert_that(fake.timeouts).is_equal_to([120.0, 70.0, 20.0])
    assert_that(result.timed_out).is_true()
    assert_that(result.success).is_false()
    assert_that(result.output).contains(
        "timed out after 120s; not audited (1): d/Cargo.lock.",
    )


def test_timeout_next_to_a_failed_lockfile_names_both(
    repo: Path,
    fake: _FakeCargoAudit,
) -> None:
    """A timeout does not swallow a separate failed audit.

    Args:
        repo: Repository root.
        fake: The fake cargo-audit.
    """
    fake.broken.add(_crate(repo, "a"))
    fake.hang_on = _crate(repo, "b")
    _crate(repo, "c")

    result = _check(fake, ["."])

    assert_that(result.timed_out).is_true()
    assert_that(result.execution_failed).is_true()
    assert_that(result.failure_besides_timeout).contains("a/Cargo.lock")
    assert_that(result.output).contains("not audited (2): b/Cargo.lock, c/Cargo.lock.")


def test_lockfile_path_starting_with_dash_is_not_an_option(
    repo: Path,
    fake: _FakeCargoAudit,
) -> None:
    """A ``-``-leading directory reaches cargo-audit as part of one absolute token.

    Args:
        repo: Repository root.
        fake: The fake cargo-audit.
    """
    lock = _crate(repo, "-crate", vulnerable=True)

    result = _check(fake, [os.path.join("-crate", "Cargo.lock")])

    (cmd,) = fake.commands
    assert_that(cmd).contains(f"--file={lock}")
    assert_that([a for a in cmd if a.startswith("-") and "crate" in a]).is_equal_to(
        [f"--file={lock}"],
    )
    assert_that(cmd).does_not_contain("-", "-f")
    assert_that([i.file for i in result.issues or []]).is_equal_to([str(lock)])


SMALLVEC_RECORD = json.loads(VULNERABLE_REPORT)["vulnerabilities"]["list"][0]


def _report(records: list[object], *, count: int, found: bool) -> str:
    """Render a cargo-audit report with the given records and summary fields.

    Args:
        records: The ``vulnerabilities.list`` entries.
        count: The report's ``count``.
        found: The report's ``found``.

    Returns:
        The JSON report.
    """
    return json.dumps(
        {"vulnerabilities": {"found": found, "count": count, "list": records}},
    )


@pytest.mark.parametrize(
    ("returncode", "records", "count", "found", "problem", "kept"),
    [
        pytest.param(0, [{}], 1, True, "1 of 1 vulnerability records unreadable", 0, id="unreadable-record"),
        pytest.param(1, [SMALLVEC_RECORD, {"advisory": {}}], 2, True, "1 of 2 vulnerability records unreadable", 1, id="one-of-two-unreadable"),
        pytest.param(1, [SMALLVEC_RECORD], 2, True, "report says found=True, count=2 but lists 1", 1, id="count-disagrees"),
        pytest.param(0, [], 0, True, "report says found=True, count=0 but lists 0", 0, id="found-disagrees"),
        pytest.param(2, [SMALLVEC_RECORD], 1, True, "exit status 2, expected 1", 1, id="exit-2-with-findings"),
        pytest.param(0, [SMALLVEC_RECORD], 1, True, "exit status 0, expected 1", 1, id="exit-0-with-findings"),
        pytest.param(1, [{**SMALLVEC_RECORD, "package": {"version": "1.6.0"}}], 1, True, "1 of 1 vulnerability records without package name", 1, id="no-package-name"),
        pytest.param(1, [{**SMALLVEC_RECORD, "package": {"name": "smallvec"}}], 1, True, "1 of 1 vulnerability records without package version", 1, id="no-package-version"),
        pytest.param(1, [{"advisory": {"id": "RUSTSEC-2021-0003"}}], 1, True, "1 of 1 vulnerability records without package name or package version", 1, id="no-package"),
        pytest.param(1, [{**SMALLVEC_RECORD, "advisory": {"title": "t"}}], 1, True, "1 of 1 vulnerability records unreadable", 0, id="no-advisory-id"),
    ],
)  # fmt: skip
def test_incomplete_report_fails_and_keeps_its_valid_findings(
    repo: Path,
    fake: _FakeCargoAudit,
    returncode: int,
    records: list[object],
    count: int,
    found: bool,
    problem: str,
    kept: int,
) -> None:
    """A report that does not add up is a failed audit, never a clean one.

    cargo-audit 0.23 exits 0 when clean, 1 when it found vulnerabilities and
    2 on an error. Only a valid report with the matching exit is complete.

    Args:
        repo: Repository root.
        fake: The fake cargo-audit.
        returncode: cargo-audit's exit status.
        records: The report's vulnerability records.
        count: The report's ``count``.
        found: The report's ``found``.
        problem: The reason the output gives.
        kept: How many valid findings are still reported.
    """
    _crate(repo, "app")
    bad = _crate(repo, "bad")
    fake.reports[bad] = (returncode, _report(records, count=count, found=found))

    result = _check(fake, ["."])

    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.findings_complete).is_false()
    assert_that(result.inspected_files).is_none()
    assert_that(result.issues_count).is_equal_to(kept)
    assert_that([i.file for i in result.issues or []]).is_equal_to([str(bad)] * kept)
    assert_that(result.output).contains(
        f"did not produce a complete report, not audited (1): bad/Cargo.lock ({problem})",
    )


def test_exit_one_with_a_valid_report_is_a_complete_audit(
    repo: Path,
    fake: _FakeCargoAudit,
) -> None:
    """Exit 1 is cargo-audit's "vulnerabilities found", so files were inspected.

    Args:
        repo: Repository root.
        fake: The fake cargo-audit.
    """
    _crate(repo, "app", vulnerable=True)

    result = _check(fake, ["."])

    assert_that(result.issues_count).is_equal_to(1)
    assert_that(result.execution_failed).is_false()
    assert_that(result.inspected_files).is_true()
    assert_that(result.output).does_not_contain("not audited")


def test_empty_lockfile_is_not_audited(repo: Path, fake: _FakeCargoAudit) -> None:
    """A lockfile listing no packages fails; cargo always writes the root one.

    Args:
        repo: Repository root.
        fake: The fake cargo-audit.
    """
    _crate(repo, "app")
    (repo / "app" / "Cargo.lock").write_text("")

    result = _check(fake, ["."])

    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.output).contains(
        "not audited (1): app/Cargo.lock (lockfile lists no packages)",
    )


def _broken_entries(repo: Path) -> list[str]:
    """Create entries named Cargo.lock that no tool can read as a lockfile.

    Args:
        repo: Repository root.

    Returns:
        Their paths relative to ``repo``, sorted.
    """
    (repo / "dir" / "Cargo.lock").mkdir(parents=True)
    (repo / "dangling").mkdir()
    (repo / "dangling" / "Cargo.lock").symlink_to(repo / "missing.lock")
    (repo / "loop").mkdir()
    (repo / "loop" / "Cargo.lock").symlink_to(repo / "loop" / "Cargo.lock")
    return ["dangling/Cargo.lock", "dir/Cargo.lock", "loop/Cargo.lock"]


@pytest.mark.parametrize("beside_clean", [True, False], ids=["beside-clean", "alone"])
def test_unreadable_lockfile_entries_fail_the_check_and_are_named(
    repo: Path,
    fake: _FakeCargoAudit,
    beside_clean: bool,
) -> None:
    """A directory, dangling link or link loop named Cargo.lock is not audited.

    Args:
        repo: Repository root.
        fake: The fake cargo-audit.
        beside_clean: Whether a readable, clean lockfile is also present.
    """
    clean = [_crate(repo, "app")] if beside_clean else []
    names = _broken_entries(repo)

    result = _check(fake, ["."])

    assert_that(fake.audited()).is_equal_to(clean)
    assert_that(result.success).is_false()
    assert_that(result.no_files).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.inspected_files).is_none()
    assert_that(result.output).contains(
        f"named Cargo.lock but not a readable file (3): {', '.join(names)}.",
    )


def test_symlink_outside_the_project_is_audited_under_its_own_name(
    tmp_path: Path,
    repo: Path,
    fake: _FakeCargoAudit,
) -> None:
    """A linked lockfile is reported where the user sees it, and flagged.

    Args:
        tmp_path: Pytest temporary directory, holding the outside target.
        repo: Repository root.
        fake: The fake cargo-audit.
    """
    target = _crate(tmp_path, "elsewhere", vulnerable=True)
    (repo / "linked").mkdir()
    link = repo / "linked" / "Cargo.lock"
    link.symlink_to(target)

    result = _check(fake, ["."])

    assert_that(fake.audited()).is_equal_to([link])
    assert_that([i.file for i in result.issues or []]).is_equal_to([str(link)])
    assert_that(result.output).contains(
        f"linked/Cargo.lock is a symlink to {target}, outside the project; "
        "audited and reported as linked/Cargo.lock.",
    )


def test_long_lists_of_lockfiles_are_capped(repo: Path, fake: _FakeCargoAudit) -> None:
    """Thousands of excluded lockfiles give a count and ten names, not all.

    Args:
        repo: Repository root.
        fake: The fake cargo-audit.
    """
    _crate(repo, "app")
    for index in range(25):
        _crate(repo, f"vendor/dep{index:02d}")

    result = _check(fake, ["."], exclude=["vendor/"])

    shown = ", ".join(f"vendor/dep{index:02d}/Cargo.lock" for index in range(10))
    assert_that(result.output).contains(
        f"exclude patterns (25): {shown} and 15 more.",
    )
    assert_that(result.output).does_not_contain("vendor/dep10/Cargo.lock")


def _git(repo: Path, *args: str) -> None:
    """Run git in a scratch repository with a throwaway identity.

    Args:
        repo: The scratch repository.
        *args: Git arguments.
    """
    subprocess.run(  # nosec B603 B607 - fixed git command in a test repository
        [
            "git",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "user.name=test",
            "-c",
            "user.email=test@example.invalid",
            *args,
        ],
        cwd=repo,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def git_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a committed repository with app and an excluded vendored crate.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Used to change directory and isolate the incremental cache.

    Returns:
        The repository root, resolved.
    """
    root = (tmp_path / "repo").resolve()
    root.mkdir()
    monkeypatch.chdir(root)
    monkeypatch.setattr("lintro.utils.file_cache.CACHE_DIR", tmp_path / "cache")
    _git(root, "init", "-q")
    _crate(root, "app")
    _crate(root, "vendor/dep")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    return root


@pytest.mark.parametrize("mode", ["diff", "incremental"])
def test_scope_without_changed_lockfiles_is_not_blamed_on_exclusion(
    git_repo: Path,
    fake: _FakeCargoAudit,
    mode: str,
) -> None:
    """An unchanged app lockfile next to an excluded one stays "no files".

    Args:
        git_repo: Committed repository.
        fake: The fake cargo-audit.
        mode: Which changed-files scope the run uses.
    """
    options: dict[str, object] = (
        {"diff_base": "HEAD"} if mode == "diff" else {"incremental": True}
    )
    if mode == "incremental":
        _check(fake, ["."], exclude=["vendor/"], options=options)
        fake.commands.clear()

    result = _check(fake, ["."], exclude=["vendor/"], options=options)

    assert_that(fake.commands).is_empty()
    assert_that(result.no_files).is_true()
    assert_that(result.output).does_not_contain("excluded")


def test_diff_with_only_an_excluded_lockfile_changed_names_it(
    git_repo: Path,
    fake: _FakeCargoAudit,
) -> None:
    """When every changed lockfile is excluded, the result names them.

    Args:
        git_repo: Committed repository.
        fake: The fake cargo-audit.
    """
    (git_repo / "vendor" / "dep" / "Cargo.lock").write_text("version = 4\n")

    result = _check(fake, ["."], exclude=["vendor/"], options={"diff_base": "HEAD"})

    assert_that(fake.commands).is_empty()
    assert_that(result.no_files).is_true()
    assert_that(result.output).contains(
        "excluded by lintro's exclude patterns (1): vendor/dep/Cargo.lock.",
    )


def test_pyproject_exclude_patterns_leave_a_lockfile_out(
    repo: Path,
    fake: _FakeCargoAudit,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The documented ``[tool.lintro.cargo_audit]`` exclusion takes effect.

    Args:
        repo: Repository root.
        fake: The fake cargo-audit.
        monkeypatch: Keeps the enclosing checkout's config out of discovery.
    """
    _crate(repo, "app")
    _crate(repo, "vendor/dep", vulnerable=True)
    (repo / "pyproject.toml").write_text(
        '[tool.lintro.cargo_audit]\nexclude_patterns = ["vendor/"]\n',
    )
    monkeypatch.setattr("lintro.config.config_loader._find_config_file", lambda: None)
    plugin = configure_tool_for_execution(
        tool=CargoAuditPlugin(),
        tool_name="cargo_audit",
        config_manager=UnifiedConfigManager(),
        tool_option_dict={},
        exclude=None,
        include_venv=False,
        incremental=False,
        action=Action.CHECK,
        selected_tools={"cargo_audit"},
        lintro_config=load_config(None),
    )

    with patch.object(plugin, "_run_subprocess_result", side_effect=fake):
        result = plugin.check(["."], {})

    assert_that(fake.audited()).is_equal_to([repo / "app" / "Cargo.lock"])
    assert_that(result.success).is_true()
    assert_that(result.output).contains(
        "excluded by lintro's exclude patterns (1): vendor/dep/Cargo.lock.",
    )


def test_unknown_record_fields_are_tolerated(repo: Path, fake: _FakeCargoAudit) -> None:
    """A complete record with fields lintro does not read is still complete.

    Args:
        repo: Repository root.
        fake: The fake cargo-audit.
    """
    lock = _crate(repo, "app")
    record = {**SMALLVEC_RECORD, "affected": {"functions": {}}, "extra": 1}
    fake.reports[lock] = (1, _report([record], count=1, found=True))

    result = _check(fake, ["."])

    assert_that(result.issues_count).is_equal_to(1)
    assert_that(result.execution_failed).is_false()
    assert_that(result.inspected_files).is_true()


def test_denied_warnings_fail_a_complete_audit(
    repo: Path,
    fake: _FakeCargoAudit,
) -> None:
    """Exit 1 with denied warnings is an audit that ran, not a missing report.

    The report is what cargo-audit 0.23 prints with ``-D warnings`` or
    ``[output] deny`` for an unmaintained crate: no vulnerabilities, exit 1,
    and the warning marked ``"denied": true``.

    Args:
        repo: Repository root.
        fake: The fake cargo-audit.
    """
    lock = _crate(repo, "app")
    warning = {
        "kind": "unmaintained",
        "package": {"name": "ansi_term", "version": "0.12.1"},
        "advisory": {"id": "RUSTSEC-2021-0139"},
        "denied": True,
    }
    report = json.loads(_report([], count=0, found=False))
    report["warnings"] = {"unmaintained": [warning]}
    fake.reports[lock] = (1, json.dumps(report))

    result = _check(fake, ["."])

    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_false()
    assert_that(result.inspected_files).is_true()
    assert_that(result.output).contains(
        "cargo-audit denied warnings, audited but failing (1): app/Cargo.lock "
        "(unmaintained ansi_term (RUSTSEC-2021-0139)).",
    )
    assert_that(result.output).does_not_contain("did not produce a complete report")


@pytest.mark.parametrize("requested", ["app/Cargo.lock/", "app"])
def test_requested_directory_named_cargo_lock_fails(
    repo: Path,
    fake: _FakeCargoAudit,
    requested: str,
) -> None:
    """A directory named Cargo.lock fails whether it or its parent is passed.

    Args:
        repo: Repository root.
        fake: The fake cargo-audit.
        requested: The path handed to ``check``.
    """
    (repo / "app" / "Cargo.lock").mkdir(parents=True)

    result = _check(fake, [requested])

    assert_that(fake.commands).is_empty()
    assert_that(result.success).is_false()
    assert_that(result.no_files).is_false()
    assert_that(result.output).contains(
        "named Cargo.lock but not a readable file (1): app/Cargo.lock.",
    )


def test_diff_run_leaves_out_an_unchanged_unreadable_entry(
    git_repo: Path,
    fake: _FakeCargoAudit,
) -> None:
    """A committed dangling Cargo.lock link does not fail a diff run.

    Args:
        git_repo: Committed repository.
        fake: The fake cargo-audit.
    """
    (git_repo / "broken").mkdir()
    (git_repo / "broken" / "Cargo.lock").symlink_to(git_repo / "missing.lock")
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-q", "-m", "broken link")

    result = _check(fake, ["."], options={"diff_base": "HEAD"})

    assert_that(fake.commands).is_empty()
    assert_that(result.no_files).is_true()
    assert_that(result.success).is_true()
    assert_that(result.output or "").does_not_contain("broken/Cargo.lock")


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root reads a file whatever its mode",
)
def test_diff_run_names_a_changed_unreadable_lockfile(
    git_repo: Path,
    fake: _FakeCargoAudit,
) -> None:
    """A changed Cargo.lock that cannot be read fails the diff run, named.

    Args:
        git_repo: Committed repository.
        fake: The fake cargo-audit.
    """
    lock = _crate(git_repo, "locked")
    lock.chmod(0)

    result = _check(fake, ["."], options={"diff_base": "HEAD"})

    assert_that(fake.commands).is_empty()
    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.output).contains(
        "named Cargo.lock but not a readable file (1): locked/Cargo.lock.",
    )


def test_diff_run_names_only_changed_excluded_lockfiles(
    git_repo: Path,
    fake: _FakeCargoAudit,
) -> None:
    """The excluded note of a diff run lists only excluded lockfiles that changed.

    Args:
        git_repo: Committed repository.
        fake: The fake cargo-audit.
    """
    (git_repo / "app" / "Cargo.lock").write_text("version = 4\n")

    result = _check(fake, ["."], exclude=["vendor/"], options={"diff_base": "HEAD"})

    assert_that(fake.audited()).is_equal_to([git_repo / "app" / "Cargo.lock"])
    assert_that(result.output or "").does_not_contain("vendor/dep/Cargo.lock")


# The report cargo-audit prints for ansi_term 0.12.1 under ``[output] deny =
# ["unmaintained"]`` in the release the tools image ships (recorded from CI run
# 44762771950). It exits 1 and, unlike 0.23, does not mark the warning
# ``"denied": true``.
REPORT_0_22_UNMAINTAINED = (
    Path(__file__).with_name("cargo_audit_0_22_0_unmaintained_warning.json").read_text()
)


@pytest.mark.parametrize("returncode", [1, 0], ids=["denied", "informational"])
def test_unmarked_warnings_count_as_denied_only_at_exit_one(
    repo: Path,
    fake: _FakeCargoAudit,
    returncode: int,
) -> None:
    """cargo-audit 0.22's denied warnings fail as denied; at exit 0 they pass.

    Args:
        repo: Repository root.
        fake: The fake cargo-audit.
        returncode: cargo-audit's exit status with the recorded report.
    """
    lock = _crate(repo, "app")
    fake.reports[lock] = (returncode, REPORT_0_22_UNMAINTAINED)

    result = _check(fake, ["."])

    assert_that(result.execution_failed).is_false()
    assert_that(result.inspected_files).is_true()
    assert_that(result.success).is_equal_to(returncode == 0)
    if returncode:
        assert_that(result.output).contains(
            "cargo-audit denied warnings, audited but failing (1): app/Cargo.lock "
            "(unmaintained ansi_term (RUSTSEC-2021-0139)).",
        )
    else:
        assert_that(result.output).is_none()


def test_only_copies_found_is_no_files_naming_them(
    repo: Path,
    fake: _FakeCargoAudit,
) -> None:
    """Every lockfile skipped as a copy is "no files", named, with the option.

    Args:
        repo: Repository root.
        fake: The fake cargo-audit.
    """
    _crate(repo, "vendor/a")
    _crate(repo, "target/package/b")

    result = _check(fake, ["."])

    assert_that(fake.commands).is_empty()
    assert_that(result.no_files).is_true()
    assert_that(result.skipped).is_false()
    assert_that(result.output).is_equal_to(
        "No Cargo.lock in scope to audit.\n\nNot audited, lockfiles under a vendor/ "
        "or target/ directory (2): target/package/b/Cargo.lock, vendor/a/Cargo.lock; "
        "cargo builds from the top-level Cargo.lock. To audit them, set the "
        "cargo_audit option include_vendored=True.",
    )


@pytest.mark.parametrize("changed", ["app", "vendor/dep", "nothing"])
def test_diff_run_names_only_copies_in_the_diff(
    git_repo: Path,
    fake: _FakeCargoAudit,
    changed: str,
) -> None:
    """The copy note of a diff run lists only copies that changed.

    Args:
        git_repo: Committed repository with app and vendor/dep.
        fake: The fake cargo-audit.
        changed: Which crate's lockfile changed.
    """
    if changed != "nothing":
        (git_repo / changed / "Cargo.lock").write_text("version = 4\n")

    result = _check(fake, ["."], options={"diff_base": "HEAD"})

    audited = [git_repo / "app" / "Cargo.lock"] if changed == "app" else []
    assert_that(fake.audited()).is_equal_to(audited)
    assert_that(result.no_files).is_equal_to(changed != "app")
    if changed == "vendor/dep":
        assert_that(result.output).contains("directory (1): vendor/dep/Cargo.lock;")
    else:
        assert_that(result.output or "").does_not_contain("vendor/dep")


def test_incremental_rerun_still_names_unchanged_copies(
    git_repo: Path,
    fake: _FakeCargoAudit,
) -> None:
    """A skipped copy was never audited, so every incremental run names it.

    The audited app lockfile is unchanged and not audited again.

    Args:
        git_repo: Committed repository with app and vendor/dep.
        fake: The fake cargo-audit.
    """
    first = _check(fake, ["."], options={"incremental": True})
    fake.commands.clear()

    second = _check(fake, ["."], options={"incremental": True})

    note = "directory (1): vendor/dep/Cargo.lock;"
    assert_that(first.output).contains(note)
    assert_that(fake.commands).is_empty()
    assert_that(second.no_files).is_true()
    assert_that(second.output).contains(note)


@pytest.mark.parametrize(
    "reason",
    [
        "default-skip",
        "named-later",
        "excluded",
        pytest.param(
            "unreadable",
            marks=pytest.mark.skipif(
                hasattr(os, "geteuid") and os.geteuid() == 0,
                reason="root reads a file whatever its mode",
            ),
        ),
        "timeout",
        "failed",
    ],
)
def test_a_lockfile_left_unaudited_is_audited_by_the_next_incremental_run(
    tmp_path: Path,
    repo: Path,
    fake: _FakeCargoAudit,
    monkeypatch: pytest.MonkeyPatch,
    reason: str,
) -> None:
    """No reason for leaving a lockfile unaudited records it as checked.

    The second incremental run audits exactly the lockfile the first left
    unaudited; the audited, unchanged app lockfile is not audited again.

    Args:
        tmp_path: Pytest temporary directory, holding the cache.
        repo: Repository root.
        fake: The fake cargo-audit.
        monkeypatch: Points the incremental cache at ``tmp_path``.
        reason: Why the first run left the lockfile unaudited.
    """
    monkeypatch.setattr("lintro.utils.file_cache.CACHE_DIR", tmp_path / "cache")
    _crate(repo, "app")
    copy = reason in {"default-skip", "named-later"}
    lib = _crate(repo, "vendor/lib" if copy else "lib")
    if reason == "unreadable":
        lib.chmod(0)
    fake.hang_on = lib if reason == "timeout" else None
    if reason == "failed":
        fake.broken.add(lib)
    incremental: dict[str, object] = {"incremental": True}
    exclude = ["lib/"] if reason == "excluded" else None
    _check(fake, ["."], options=incremental, exclude=exclude)
    lib.chmod(0o644)
    fake.hang_on = None
    fake.broken.clear()
    fake.commands.clear()

    _check(
        fake,
        ["vendor/lib/Cargo.lock"] if reason == "named-later" else ["."],
        options=incremental,
        include_vendored=True if reason == "default-skip" else None,
    )

    assert_that(fake.audited()).is_equal_to([lib])


def _cached(repo: Path) -> list[str]:
    """Return the lockfiles cargo_audit's incremental cache records as checked.

    Args:
        repo: Repository root the entries are shown relative to.

    Returns:
        Cached Cargo.lock paths relative to ``repo``, sorted.
    """
    keys = ToolCache.load("cargo_audit").fingerprints
    return sorted(
        str(Path(k).relative_to(repo)) for k in keys if k.endswith("Cargo.lock")
    )


@pytest.fixture
def three_crates(
    tmp_path: Path,
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path]:
    """Create app, lib and a vendored copy, with the cache in ``tmp_path``.

    Args:
        tmp_path: Pytest temporary directory, holding the cache.
        repo: Repository root.
        monkeypatch: Points the incremental cache at ``tmp_path``.

    Returns:
        The app and lib lockfiles.
    """
    monkeypatch.setattr("lintro.utils.file_cache.CACHE_DIR", tmp_path / "cache")
    _crate(repo, "vendor/dep")
    return _crate(repo, "app"), _crate(repo, "lib")


@pytest.mark.parametrize(
    "refusal",
    [
        Unavailable("cargo-audit is not installed"),
        Unavailable("security scanner did not run: below minimum", strict=True),
    ],
    ids=["unavailable", "refused-version"],
)
def test_an_early_result_from_prepare_records_nothing_as_checked(
    repo: Path,
    fake: _FakeCargoAudit,
    three_crates: tuple[Path, Path],
    refusal: Unavailable,
) -> None:
    """A run stopped by the version gate leaves every lockfile due.

    Args:
        repo: Repository root.
        fake: The fake cargo-audit.
        three_crates: The app and lib lockfiles.
        refusal: What the version gate reports.
    """
    early = ToolResult.from_outcome(ToolOutcome(refusal), name="cargo_audit")
    incremental: dict[str, object] = {"incremental": True}
    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=early,
    ):
        first = _check(fake, ["."], options=incremental)

    assert_that(first.unavailable_reason).is_equal_to(refusal.reason)
    assert_that(_cached(repo)).is_empty()
    _check(fake, ["."], options=incremental)
    assert_that(fake.audited()).is_equal_to(list(three_crates))


@pytest.mark.parametrize(
    "error",
    [RuntimeError("boom"), KeyboardInterrupt()],
    ids=["exception", "keyboard-interrupt"],
)
def test_an_interrupted_audit_records_only_completed_audits(
    repo: Path,
    fake: _FakeCargoAudit,
    three_crates: tuple[Path, Path],
    error: BaseException,
) -> None:
    """Stopping mid-audit keeps app checked and leaves lib due.

    Args:
        repo: Repository root.
        fake: The fake cargo-audit.
        three_crates: The app and lib lockfiles.
        error: What the audit of lib raises.
    """
    _, lib = three_crates
    incremental: dict[str, object] = {"incremental": True}
    clean = SubprocessResult(
        returncode=0,
        stdout=CLEAN_REPORT,
        stderr="",
        output=CLEAN_REPORT,
    )
    plugin = CargoAuditPlugin()
    with (
        patch.object(plugin, "_run_subprocess_result", side_effect=[clean, error]),
        pytest.raises(type(error)),
    ):
        plugin.check(["."], incremental)

    assert_that(_cached(repo)).is_equal_to(["app/Cargo.lock"])
    _check(fake, ["."], options=incremental)
    assert_that(fake.audited()).is_equal_to([lib])


# cargo-audit 0.23.0's --json report for the same ansi_term lockfile under
# ``-D warnings``, recorded locally: exit 1, the warning marked denied, and
# nothing on stderr.
REPORT_MARKED_DENIED = (
    Path(__file__).with_name("cargo_audit_marked_denied_warning.json").read_text()
)


@pytest.mark.parametrize(
    "stderr",
    ["", "error: could not complete audit"],
    ids=["real-stderr", "error"],
)
@pytest.mark.parametrize("marked", [False, True], ids=["unmarked", "marked"])
def test_denied_warnings_read_as_audited_unless_cargo_audit_reports_an_error(
    tmp_path: Path,
    repo: Path,
    fake: _FakeCargoAudit,
    monkeypatch: pytest.MonkeyPatch,
    stderr: str,
    marked: bool,
) -> None:
    """An error on stderr makes a denied-warning run incomplete and uncached.

    The recorded reports, with the empty stderr cargo-audit really prints in
    --json mode, stay "audited but failing" and are cached.

    Args:
        tmp_path: Pytest temporary directory, holding the cache.
        repo: Repository root.
        fake: The fake cargo-audit.
        monkeypatch: Points the incremental cache at ``tmp_path``.
        stderr: What cargo-audit prints on standard error.
        marked: Whether the report marks the warning denied (0.23) or not (0.22).
    """
    monkeypatch.setattr("lintro.utils.file_cache.CACHE_DIR", tmp_path / "cache")
    lock = _crate(repo, "app")
    report = REPORT_MARKED_DENIED if marked else REPORT_0_22_UNMAINTAINED
    fake.reports[lock] = (1, report)
    fake.stderr[lock] = stderr
    incremental: dict[str, object] = {"incremental": True}

    first = _check(fake, ["."], options=incremental)
    fake.commands.clear()
    _check(fake, ["."], options=incremental)

    assert_that(first.success).is_false()
    assert_that(first.execution_failed).is_equal_to(bool(stderr))
    if stderr:
        assert_that(first.output).contains(
            "not audited (1): app/Cargo.lock "
            "(cargo-audit reported 'error: could not complete audit')",
        )
        assert_that(first.output).does_not_contain("denied warnings")
    else:
        assert_that(first.output).contains("denied warnings, audited but failing")
    assert_that(fake.audited()).is_equal_to([lock] if stderr else [])


@pytest.mark.parametrize(
    ("stderr", "complete"),
    [
        ("", True),
        ("error: 1 vulnerability found!", True),
        ("    Scanning Cargo.lock for vulnerabilities\nerror: could not complete audit", False),
        ("error[E0001]: failed", False),
    ],
    ids=["none", "verdict-line", "error", "error-code"],
)  # fmt: skip
def test_an_error_on_stderr_makes_a_run_with_findings_incomplete(
    repo: Path,
    fake: _FakeCargoAudit,
    stderr: str,
    complete: bool,
) -> None:
    """Exit 1 with findings is complete only without an error on stderr.

    Findings are kept either way; cargo-audit's own verdict line is not an
    error.

    Args:
        repo: Repository root.
        fake: The fake cargo-audit.
        stderr: What cargo-audit prints on standard error.
        complete: Whether the audit counts as complete.
    """
    lock = _crate(repo, "app")
    fake.reports[lock] = (1, VULNERABLE_REPORT)
    fake.stderr[lock] = stderr

    result = _check(fake, ["."])

    assert_that(result.issues_count).is_equal_to(1)
    assert_that(result.execution_failed).is_equal_to(not complete)
    assert_that(result.inspected_files).is_equal_to(True if complete else None)


@pytest.mark.parametrize("source", ["tool-options", "pyproject"])
@pytest.mark.parametrize("value", ["1", "true", "True"])
def test_incremental_given_as_any_truthy_value_restores_the_cache(
    tmp_path: Path,
    repo: Path,
    fake: _FakeCargoAudit,
    monkeypatch: pytest.MonkeyPatch,
    source: str,
    value: str,
) -> None:
    """``incremental`` set to 1, "true" or True as a tool option restores too.

    Shared discovery runs incrementally for any truthy value, so the cache
    must be restored by the same rule: a copy skipped in the first run is
    audited once include_vendored is set.

    Args:
        tmp_path: Pytest temporary directory, holding the cache.
        repo: Repository root.
        fake: The fake cargo-audit.
        monkeypatch: Points the cache and config discovery at the test.
        source: Where the option comes from.
        value: The option's spelling.
    """
    monkeypatch.setattr("lintro.utils.file_cache.CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr("lintro.config.config_loader._find_config_file", lambda: None)
    _crate(repo, "app")
    copy = _crate(repo, "vendor/dep")
    toml = {"1": "1", "true": '"true"', "True": "true"}[value]

    def configured(include: bool) -> CargoAuditPlugin:
        """Configure the plugin as the CLI would.

        Args:
            include: Whether include_vendored is set.

        Returns:
            The configured plugin.
        """
        clear_config_cache()
        clear_pyproject_cache()
        extra = ",cargo_audit:include_vendored=True" if include else ""
        cli = parse_tool_options(f"cargo_audit:incremental={value}{extra}")
        (repo / "pyproject.toml").write_text(
            f"[tool.lintro.cargo_audit]\nincremental = {toml}\n"
            + ("include_vendored = true\n" if include else ""),
        )
        plugin = configure_tool_for_execution(
            tool=CargoAuditPlugin(),
            tool_name="cargo_audit",
            config_manager=UnifiedConfigManager(),
            tool_option_dict=cli if source == "tool-options" else {},
            exclude=None,
            include_venv=False,
            incremental=False,
            action=Action.CHECK,
            selected_tools={"cargo_audit"},
            lintro_config=load_config(None),
        )
        assert isinstance(plugin, CargoAuditPlugin)
        return plugin

    for include in (False, True):
        plugin = configured(include)
        with patch.object(plugin, "_run_subprocess_result", side_effect=fake):
            plugin.check(["."], {})

    assert_that(fake.audited()[-1:]).is_equal_to([copy])
