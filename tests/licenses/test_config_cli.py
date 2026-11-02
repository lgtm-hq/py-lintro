"""Process-boundary controls for license configuration failures."""

from __future__ import annotations

import json
import os
import subprocess  # nosec B404 - fixed CLI argv in owned temporary directories
import sys
from pathlib import Path

import pytest
from assertpy import assert_that


@pytest.fixture
def license_cli_files(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    """Own every config tier and pin subprocess imports to this source checkout.

    Args:
        tmp_path: Temporary home containing the project and global config.

    Returns:
        tuple[Path, Path, dict[str, str]]: Project path, global file and child environment.
    """
    home = tmp_path / "home"
    project = home / "project"
    project.mkdir(parents=True)
    global_file = home / ".lintro-config.yaml"
    global_file.write_text("licenses:\n  policy: strict\n", encoding="utf-8")
    (project / ".lintro-config.yaml").write_text("{}\n", encoding="utf-8")
    (project / "pyproject.toml").write_text("[tool.lintro]\n", encoding="utf-8")
    # Fence the upward TOML search when a control removes the project file.
    (home / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (project / "package.json").write_text(
        '{"dependencies": {"stage6-package": "1.0.0"}}',
        encoding="utf-8",
    )
    package = project / "node_modules" / "stage6-package"
    package.mkdir(parents=True)
    (package / "package.json").write_text(
        '{"name": "stage6-package", "version": "1.0.0", "license": "MIT"}',
        encoding="utf-8",
    )
    env = {
        key: os.environ[key]
        for key in (
            "PATH",
            "SYSTEMROOT",
            "COMSPEC",
            "PATHEXT",
            "TMPDIR",
            "TEMP",
            "TMP",
            "LANG",
            "LC_ALL",
            "PYTHONUTF8",
            "PYTHONIOENCODING",
        )
        if key in os.environ
    }
    env.update(
        HOME=str(home),
        USERPROFILE=str(home),
        HOMEDRIVE=home.drive,
        HOMEPATH=str(home)[len(home.drive) :],
        APPDATA=str(home / "AppData" / "Roaming"),
        LOCALAPPDATA=str(home / "AppData" / "Local"),
        XDG_CONFIG_HOME=str(home / ".config"),
        LINTRO_GLOBAL_CONFIG=str(global_file),
        LINTRO_LOG_DIR=str(tmp_path / "logs"),
        PYTHONPATH=str(Path(__file__).resolve().parents[2]),
        PYTHONDONTWRITEBYTECODE="1",
    )
    return project, global_file, env


def _run_license_cli(
    project: Path,
    env: dict[str, str],
    *,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Invoke the actual module entry point using the active test interpreter.

    Args:
        project: Owned working directory.
        env: Environment with an owned HOME/global config and pinned source path.
        check: Whether policy violations should affect the exit status.

    Returns:
        subprocess.CompletedProcess[str]: Separate stdout, stderr and exit status.
    """
    args = [
        sys.executable,
        "-m",
        "lintro",
        "licenses",
        "--lang",
        "npm",
        "--format",
        "json",
    ]
    if check:
        args.append("--check")
    return subprocess.run(  # nosec B603 - fixed module argv, shell=False
        args,
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


@pytest.mark.parametrize("check", [False, True])
@pytest.mark.parametrize(
    ("source", "content", "diagnostic"),
    [
        ("global", b"null\n", "must be a mapping"),
        ("project", b"licenses: null\n", "must be a mapping"),
        ("global", b"licenses: [\n", "Failed to parse"),
        ("project", b"licenses: [\n", "Failed to parse"),
        ("global", b"licenses:\n  policy: 2001-13-45\n", "Failed to parse"),
        ("project", b"licenses:\n  policy: 2001-13-45\n", "Failed to parse"),
        pytest.param(
            "global",
            b"licenses: " + b"[" * 20_000 + b"0" + b"]" * 20_000,
            "Failed to parse",
            id="global-yaml-recursion",
        ),
        pytest.param(
            "project",
            b"licenses: " + b"[" * 20_000 + b"0" + b"]" * 20_000,
            "Failed to parse",
            id="project-yaml-recursion",
        ),
        ("global", b"\xff", "Failed to read"),
        ("project", b"\xff", "Failed to read"),
        ("pyproject", b"[tool.lintro]\nlicenses = []\n", "must be a mapping"),
        ("pyproject", b"[tool.lintro.licenses\n", "Failed to parse"),
        pytest.param(
            "pyproject",
            b"[tool.lintro]\nlicenses = " + b"[" * 20_000 + b"]" * 20_000 + b"\n",
            "Failed to parse",
            id="pyproject-toml-recursion",
        ),
        ("pyproject", b"\xff", "Failed to read"),
        ("project", b"licenses:\n  policy: invalid\n", "validation error"),
        (
            "pyproject",
            b'[tool.lintro.licenses]\npolicy = "invalid"\n',
            "validation error",
        ),
    ],
)
def test_license_config_failure_is_friendly_at_process_boundary(
    license_cli_files: tuple[Path, Path, dict[str, str]],
    check: bool,
    source: str,
    content: bytes,
    diagnostic: str,
) -> None:
    """Config failures go to stderr with exit 1 before any license report.

    Args:
        license_cli_files: Owned project, global file and subprocess environment.
        check: Whether the policy-check flag is present.
        source: Config source to invalidate.
        content: Invalid configuration bytes.
        diagnostic: Expected actionable diagnostic fragment.
    """
    project, global_file, env = license_cli_files
    paths = {
        "global": global_file,
        "project": project / ".lintro-config.yaml",
        "pyproject": project / "pyproject.toml",
    }
    invalid_file = paths[source]
    invalid_file.write_bytes(content)

    result = _run_license_cli(project, env, check=check)

    assert_that(result.returncode).is_equal_to(1)
    assert_that(result.stdout).is_empty()
    assert_that(result.stderr).contains("Error:", diagnostic)
    if diagnostic != "validation error":
        assert_that(result.stderr).contains(str(invalid_file))
    assert_that(result.stderr).does_not_contain("Traceback")


def test_license_cli_fixture_home_matches_native_and_ntpath_expansion(
    license_cli_files: tuple[Path, Path, dict[str, str]],
) -> None:
    """Fixture control for native Path.home and stdlib ntpath home expansion.

    Args:
        license_cli_files: Owned project, global file and subprocess environment.
    """
    project, global_file, env = license_cli_files
    result = subprocess.run(  # nosec B603 - fixed Python probe, shell=False
        [
            sys.executable,
            "-c",
            """import json
import ntpath
import os
from pathlib import Path

homes = [str(Path.home()), ntpath.expanduser("~")]
os.environ.pop("USERPROFILE", None)
homes.append(ntpath.expanduser("~"))
print(json.dumps(homes))
""",
        ],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stderr).is_empty()
    assert_that(json.loads(result.stdout)).is_equal_to([str(global_file.parent)] * 3)


@pytest.mark.parametrize("failure", ["project-read", "missing-global"])
def test_license_cli_reports_file_access_failures(
    license_cli_files: tuple[Path, Path, dict[str, str]],
    failure: str,
) -> None:
    """Real file access failures cannot become a report or an uncaught traceback.

    Args:
        license_cli_files: Owned project, global file and subprocess environment.
        failure: An unreadable project path or an explicitly missing global file.
    """
    project, global_file, env = license_cli_files
    invalid_file = (
        global_file if failure == "missing-global" else project / ".lintro-config.yaml"
    )
    invalid_file.unlink()
    if failure == "project-read":
        invalid_file.mkdir()

    result = _run_license_cli(project, env)

    assert_that(result.returncode).is_equal_to(1)
    assert_that(result.stdout).is_empty()
    assert_that(result.stderr).contains("Error:", str(invalid_file))
    assert_that(result.stderr).does_not_contain("Traceback")


@pytest.mark.parametrize(
    "yaml_content",
    [None, "{}\n", "enforce: {}\n", "licenses: {}\n"],
)
@pytest.mark.parametrize(
    ("toml_content", "status"),
    [
        (None, "denied"),
        ("[tool.lintro]\n", "denied"),
        ("[tool.lintro.licenses]\n", "denied"),
        ('[tool.lintro.licenses]\nallowed = ["MIT"]\n', "allowed"),
    ],
)
def test_license_cli_keeps_empty_mapping_fallback_and_global_merge(
    license_cli_files: tuple[Path, Path, dict[str, str]],
    yaml_content: str | None,
    toml_content: str | None,
    status: str,
) -> None:
    """Real package verdicts prove inheritance and TOML fallback remain active.

    Args:
        license_cli_files: Owned project, global file and subprocess environment.
        yaml_content: Absent, empty or unrelated YAML project policy.
        toml_content: Absent, empty or populated TOML license table.
        status: Expected verdict under the merged strict policy.
    """
    project, _, env = license_cli_files
    for path, content in (
        (project / ".lintro-config.yaml", yaml_content),
        (project / "pyproject.toml", toml_content),
    ):
        if content is None:
            path.unlink()
        else:
            path.write_text(content, encoding="utf-8")

    result = _run_license_cli(project, env, check=True)

    assert_that(result.returncode).is_equal_to(0 if status == "allowed" else 1)
    assert_that(result.stderr).is_empty()
    report = json.loads(result.stdout)
    assert_that(report).is_length(1)
    assert_that(report[0]["name"]).is_equal_to("stage6-package")
    assert_that(report[0]["status"]).is_equal_to(status)
