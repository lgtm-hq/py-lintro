# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""Tests for lintro-pre-commit mirror release automation scripts."""

from __future__ import annotations

import importlib.util
import os
import subprocess  # nosec B404 - drives repo shell scripts with shell=False
import textwrap
from pathlib import Path
from typing import Any

import pytest
from assertpy import assert_that

ROOT = Path(__file__).resolve().parents[3]
MIRROR_DIR = ROOT / "scripts" / "ci" / "mirror"
RESOLVE_SCRIPT = MIRROR_DIR / "resolve-version.sh"
BUMP_SCRIPT = MIRROR_DIR / "bump_pin.py"
WAIT_WHEEL_SCRIPT = MIRROR_DIR / "wait-for-pypi-wheel.sh"
PUBLISH_SCRIPT = MIRROR_DIR / "publish-mirror-release.sh"
CLASSIFY_SCRIPT = ROOT / "scripts" / "ci" / "classify-release-tag.py"


def _load_bump_pin_module() -> Any:
    """Load bump_pin.py as an importable module."""
    spec = importlib.util.spec_from_file_location("bump_pin", BUMP_SCRIPT)
    assert_that(spec).is_not_none()
    assert spec is not None
    assert_that(spec.loader).is_not_none()
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_resolve(
    *,
    release_tag: str,
    github_output: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run resolve-version.sh with the given release tag."""
    env = os.environ.copy()
    env["RELEASE_TAG"] = release_tag
    if github_output is not None:
        env["GITHUB_OUTPUT"] = str(github_output)
    else:
        env.pop("GITHUB_OUTPUT", None)
    return subprocess.run(  # nosec B603 - fixed argv against repo script; shell=False
        [str(RESOLVE_SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        cwd=ROOT,
    )


@pytest.mark.parametrize(
    ("tag", "expected_prerelease"),
    [
        ("v1.2.3", "false"),
        ("1.2.3", "false"),
        ("v1.2.3+build.1", "false"),
        ("v1.2.3rc1", "true"),
        ("v1.2.3-rc.1", "true"),
        ("v1.2.3-alpha.1", "true"),
        ("v1.2.3.dev1", "true"),
        ("v1.2.3RC1", "true"),
        ("garbage", "true"),
    ],
)
def test_resolve_version_matches_classifier(
    tag: str,
    expected_prerelease: str,
    tmp_path: Path,
) -> None:
    """resolve-version.sh classifies tags like classify-release-tag.py."""
    output_file = tmp_path / "gh_output"
    result = _run_resolve(release_tag=tag, github_output=output_file)

    assert_that(result.returncode).is_equal_to(0)
    body = output_file.read_text(encoding="utf-8")
    assert_that(body).contains(f"is_prerelease={expected_prerelease}")
    assert_that(body).contains(f"version={tag.lstrip('v')}")


def test_resolve_version_writes_tag_and_version(
    tmp_path: Path,
) -> None:
    """resolve-version.sh emits tag and version outputs."""
    output_file = tmp_path / "gh_output"
    result = _run_resolve(release_tag="v0.69.0", github_output=output_file)

    assert_that(result.returncode).is_equal_to(0)
    body = output_file.read_text(encoding="utf-8")
    assert_that(body).contains("tag=v0.69.0")
    assert_that(body).contains("version=0.69.0")
    assert_that(body).contains("is_prerelease=false")


def test_bump_updates_real_dependency_not_decoy_comment(tmp_path: Path) -> None:
    """bump_pin.py rewrites the parsed dependency, not an earlier decoy string."""
    module = _load_bump_pin_module()
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        textwrap.dedent(
            """\
            # Example only: "lintro==0.1.0"
            [project]
            name = "lintro-pre-commit"
            dependencies = [
              "lintro==0.69.0",
            ]
            """,
        ),
        encoding="utf-8",
    )

    changed = module.bump(path=pyproject, version="0.70.0")

    assert_that(changed).is_true()
    updated = pyproject.read_text(encoding="utf-8")
    assert_that(updated).contains('"lintro==0.70.0"')
    assert_that(updated).contains('# Example only: "lintro==0.1.0"')


def test_bump_check_reports_drift(tmp_path: Path) -> None:
    """--check exits non-zero when the parsed pin does not match."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        textwrap.dedent(
            """\
            [project]
            dependencies = ["lintro==0.69.0"]
            """,
        ),
        encoding="utf-8",
    )

    ok = subprocess.run(  # nosec B603 - fixed argv; shell=False
        [
            "python3",
            str(BUMP_SCRIPT),
            "--pyproject",
            str(pyproject),
            "--version",
            "0.69.0",
            "--check",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    drift = subprocess.run(  # nosec B603 - fixed argv; shell=False
        [
            "python3",
            str(BUMP_SCRIPT),
            "--pyproject",
            str(pyproject),
            "--version",
            "0.70.0",
            "--check",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert_that(ok.returncode).is_equal_to(0)
    assert_that(drift.returncode).is_equal_to(1)
    assert_that(drift.stderr).contains("Drift")


def test_bump_missing_pin_raises(tmp_path: Path) -> None:
    """Missing lintro dependency raises a clear error."""
    module = _load_bump_pin_module()
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        textwrap.dedent(
            """\
            [project]
            dependencies = ["other==1.0.0"]
            """,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="No 'lintro==<version>' pin"):
        module.bump(path=pyproject, version="1.0.0")


def test_bump_multiple_pins_raises(tmp_path: Path) -> None:
    """Multiple lintro pins in dependency tables fail closed."""
    module = _load_bump_pin_module()
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        textwrap.dedent(
            """\
            [project]
            dependencies = ["lintro==0.69.0"]
            optional-dependencies.dev = ["lintro==0.68.0"]
            """,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Expected exactly one"):
        module.bump(path=pyproject, version="1.0.0")


def test_resolve_version_rejects_empty_tag() -> None:
    """Whitespace-only RELEASE_TAG fails closed instead of publishing."""
    result = _run_resolve(release_tag="   ")

    assert_that(result.returncode).is_not_equal_to(0)


def test_publish_script_fetches_bump_branch_before_lease() -> None:
    """Retry pushes fetch the existing bump branch so --force-with-lease works."""
    body = PUBLISH_SCRIPT.read_text(encoding="utf-8")

    assert_that(body).contains("push_bump_branch()")  # definition
    assert_that(body).contains(
        'git fetch origin "refs/heads/${BRANCH}:refs/remotes/origin/${BRANCH}"',
    )
    assert_that(body).contains('git push --force-with-lease origin "HEAD:${BRANCH}"')
    assert_that(body.count("\npush_bump_branch\n")).is_equal_to(2)


def _write_fake_curl(bin_dir: Path, payload: str) -> None:
    """Install a curl stub that prints *payload* and ignores URL/flags."""
    curl = bin_dir / "curl"
    curl.write_text(
        "#!/usr/bin/env bash\n" "cat <<'EOF'\n" f"{payload}\n" "EOF\n",
        encoding="utf-8",
    )
    curl.chmod(0o755)


def _run_wait_wheel(
    *,
    bin_dir: Path,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run wait-for-pypi-wheel.sh with a stub curl ahead of PATH."""
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    if extra_env:
        env.update(extra_env)
    return subprocess.run(  # nosec B603 - fixed argv against repo script; shell=False
        [str(WAIT_WHEEL_SCRIPT), "lintro", "1.2.3", "1", "0"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        cwd=ROOT,
    )


def test_wait_for_pypi_wheel_requires_bdist_wheel(tmp_path: Path) -> None:
    """sdist-only PyPI metadata is not enough to pass the wheel gate."""
    _write_fake_curl(tmp_path, '{"urls":[{"packagetype":"sdist"}]}')
    result = _run_wait_wheel(bin_dir=tmp_path)

    assert_that(result.returncode).is_equal_to(1)
    assert_that(result.stderr + result.stdout).contains("Timeout")


def test_wait_for_pypi_wheel_accepts_bdist_wheel(tmp_path: Path) -> None:
    """A bdist_wheel URL in the PyPI JSON is sufficient."""
    _write_fake_curl(tmp_path, '{"urls":[{"packagetype":"bdist_wheel"}]}')
    result = _run_wait_wheel(bin_dir=tmp_path)

    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stderr + result.stdout).contains("wheel is available")


def test_wait_for_pypi_wheel_times_out_without_metadata(tmp_path: Path) -> None:
    """Empty curl output (metadata not published yet) exits 1 after attempts."""
    curl = tmp_path / "curl"
    curl.write_text("#!/usr/bin/env bash\nexit 22\n", encoding="utf-8")
    curl.chmod(0o755)
    result = _run_wait_wheel(bin_dir=tmp_path)

    assert_that(result.returncode).is_equal_to(1)
    assert_that(result.stderr + result.stdout).contains("Timeout")
