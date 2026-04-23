"""Tests for `scripts/ci/tools-image-update-digest.sh`.

Contract tests that guard against the #870/#872 regression class — when
`action.yml` lost its literal digest pattern but the update script still
required it, `main` CI went red. These tests copy the script into a tmp
project tree with controllable target files and verify each combination
(Dockerfile alone, + action.yml without pattern, + action.yml with
pattern, + docker-compose, invalid digest) keeps the exit code and
substitution behaviour sane.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterable
from pathlib import Path

import pytest
from assertpy import assert_that

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_SOURCE = REPO_ROOT / "scripts" / "ci" / "tools-image-update-digest.sh"

OLD_DIGEST = "sha256:" + ("a" * 64)
NEW_DIGEST = "sha256:" + ("b" * 64)
IMAGE = "ghcr.io/lgtm-hq/lintro-tools:latest"

# Sentinel for ``action_yml_digest`` that means "do not create action.yml at
# all" — distinct from ``None`` which means "create it without the digest
# pattern". The script has separate code paths for each case and both need
# to be covered.
ACTION_YML_MISSING = object()


def _make_project(
    tmp_path: Path,
    *,
    dockerfile_digest: str | None = OLD_DIGEST,
    action_yml_digest: str | None | object = None,
    compose_digest: str | None = None,
) -> Path:
    """Build a minimal project tree with the update script and target files.

    Args:
        tmp_path: Base tmp directory from the pytest fixture.
        dockerfile_digest: Digest literal to seed into Dockerfile. ``None``
            omits the pinned line (the script should then fail).
        action_yml_digest: Digest literal to seed into action.yml. ``None``
            writes an action.yml *without* the digest pattern (the post-#870
            shape). Pass ``ACTION_YML_MISSING`` to omit the file entirely —
            this exercises the script's ``ACTION_YML_AVAILABLE=false`` branch.
        compose_digest: Digest literal to seed into docker-compose.yml.
            ``None`` omits docker-compose entirely.

    Returns:
        Path to the copied update-digest script inside ``tmp_path``.
    """
    project = tmp_path / "project"
    (project / "scripts" / "ci").mkdir(parents=True)
    (project / ".github" / "actions" / "resolve-tools-image").mkdir(parents=True)

    script_dest = project / "scripts" / "ci" / SCRIPT_SOURCE.name
    shutil.copy2(SCRIPT_SOURCE, script_dest)
    script_dest.chmod(0o755)

    dockerfile = project / "Dockerfile"
    if dockerfile_digest is None:
        dockerfile.write_text("FROM scratch\n")
    else:
        dockerfile.write_text(
            f"FROM scratch\nARG TOOLS_IMAGE={IMAGE}@{dockerfile_digest}\n",
        )

    action_yml = project / ".github" / "actions" / "resolve-tools-image" / "action.yml"
    if action_yml_digest is ACTION_YML_MISSING:
        pass  # leave the directory empty — exercises the missing-file branch
    elif action_yml_digest is None:
        action_yml.write_text(
            "name: Resolve Tools Image\ninputs:\n  stable-image:\n    default: ''\n",
        )
    else:
        action_yml.write_text(
            "name: Resolve Tools Image\ninputs:\n  stable-image:\n"
            f"    default: '{IMAGE}@{action_yml_digest}'\n",
        )

    if compose_digest is not None:
        (project / "docker-compose.yml").write_text(
            "services:\n  lintro:\n    build:\n      args:\n"
            f"        TOOLS_IMAGE: ${{TOOLS_IMAGE:-{IMAGE}@{compose_digest}}}\n",
        )

    return script_dest


def _run(script: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Invoke the script with given args, capturing output.

    Args:
        script: Path to the copied script.
        *args: CLI arguments to forward.

    Returns:
        Completed process with captured stdout/stderr.
    """
    return subprocess.run(
        ["bash", str(script), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _files_contain(files: Iterable[Path], needle: str) -> bool:
    """Return True iff every file in ``files`` contains ``needle``.

    Args:
        files: Paths to inspect.
        needle: Substring that must appear in each file.

    Returns:
        Boolean result of the all-match check.
    """
    return all(needle in f.read_text() for f in files)


def test_update_digest_succeeds_when_action_yml_has_no_pattern(
    tmp_path: Path,
) -> None:
    """action.yml without the digest pattern is informational, not fatal.

    This is the post-#870 reality: `stable-image` default is empty and the
    resolver reads the Dockerfile digest at runtime. The update script must
    not `exit 1` on that shape.
    """
    script = _make_project(tmp_path, action_yml_digest=None)

    result = _run(script, NEW_DIGEST)

    assert_that(result.returncode).described_as(result.stderr).is_equal_to(0)
    assert_that(result.stdout).contains("No digest reference found in action.yml")
    assert_that((script.parent.parent.parent / "Dockerfile").read_text()).contains(
        NEW_DIGEST,
    )


def test_update_digest_substitutes_all_present_files(tmp_path: Path) -> None:
    """When action.yml AND docker-compose carry the pattern, both update."""
    script = _make_project(
        tmp_path,
        action_yml_digest=OLD_DIGEST,
        compose_digest=OLD_DIGEST,
    )
    project = script.parent.parent.parent

    result = _run(script, NEW_DIGEST)

    assert_that(result.returncode).described_as(result.stderr).is_equal_to(0)
    assert_that(
        _files_contain(
            [
                project / "Dockerfile",
                project / ".github/actions/resolve-tools-image/action.yml",
                project / "docker-compose.yml",
            ],
            NEW_DIGEST,
        ),
    ).is_true()


def test_update_digest_handles_missing_action_yml(tmp_path: Path) -> None:
    """action.yml absent entirely must still update Dockerfile and exit 0.

    Covers the ``ACTION_YML_AVAILABLE=false`` path in the script, which is
    distinct from "action.yml present but without the digest pattern".
    """
    script = _make_project(tmp_path, action_yml_digest=ACTION_YML_MISSING)
    project = script.parent.parent.parent

    result = _run(script, NEW_DIGEST)

    assert_that(result.returncode).described_as(result.stderr).is_equal_to(0)
    assert_that(
        (project / ".github/actions/resolve-tools-image/action.yml").exists(),
    ).is_false()
    assert_that((project / "Dockerfile").read_text()).contains(NEW_DIGEST)


def test_update_digest_handles_missing_docker_compose(tmp_path: Path) -> None:
    """docker-compose.yml is optional; absence is a no-op path, not an error."""
    script = _make_project(tmp_path, action_yml_digest=OLD_DIGEST)
    project = script.parent.parent.parent

    result = _run(script, NEW_DIGEST)

    assert_that(result.returncode).described_as(result.stderr).is_equal_to(0)
    assert_that((project / "docker-compose.yml").exists()).is_false()


def test_update_digest_rejects_malformed_digest(tmp_path: Path) -> None:
    """Digest validator blocks anything that is not sha256:<64-hex>."""
    script = _make_project(tmp_path)

    result = _run(script, "not-a-digest")

    assert_that(result.returncode).is_not_equal_to(0)
    assert_that(result.stderr).contains("Invalid digest format")


def test_update_digest_fails_when_dockerfile_missing_pattern(
    tmp_path: Path,
) -> None:
    """Dockerfile is the authoritative pin; a missing pattern must abort.

    The resolver reads Dockerfile at runtime, so a silent no-op there would
    ship an unpinned image. Only action.yml and docker-compose are optional.
    """
    script = _make_project(tmp_path, dockerfile_digest=None)

    result = _run(script, NEW_DIGEST)

    assert_that(result.returncode).is_not_equal_to(0)
    assert_that(result.stderr).contains("Pattern not found in Dockerfile")


@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_update_digest_help_flag_exits_zero(tmp_path: Path, flag: str) -> None:
    """Help flags short-circuit argument validation cleanly."""
    script = _make_project(tmp_path)

    result = _run(script, flag)

    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("Usage:")
