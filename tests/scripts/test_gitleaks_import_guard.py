"""Keep the Gitleaks build closure free of the OpenPGP packages (GO-2026-5932)."""

from __future__ import annotations

import re
import runpy
import shutil
import tomllib
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import cast

import pytest
from assertpy import assert_that

from lintro.plugins.subprocess_executor import run_subprocess

REPO_ROOT = Path(__file__).resolve().parents[2]
GUARD = REPO_ROOT / "scripts/utils/gitleaks/verify_imports.py"
COMMAND = "github.com/zricethezav/gitleaks/v8"


@pytest.mark.parametrize(
    "affected",
    ["", "/packet", "/armor", "/clearsign", "/errors", "/elgamal", "/s2k"],
)
def test_import_guard_rejects_each_affected_package(affected: str) -> None:
    """An affected package must fail even when the command itself is present.

    Args:
        affected: OpenPGP package suffix named by the external Go advisory.
    """
    validate = cast(
        Callable[[Sequence[str]], None],
        runpy.run_path(str(GUARD))["validate_imports"],
    )
    with pytest.raises(ValueError, match="GO-2026-5932"):
        validate([COMMAND, f"golang.org/x/crypto/openpgp{affected}"])


def test_import_guard_accepts_unaffected_crypto_packages() -> None:
    """Unrelated cryptographic packages do not imply OpenPGP applicability."""
    validate = cast(
        Callable[[Sequence[str]], None],
        runpy.run_path(str(GUARD))["validate_imports"],
    )
    validate(["runtime", "golang.org/x/crypto/ssh", COMMAND])


@pytest.mark.parametrize("imports", [[], ["runtime"], [f"{COMMAND}/cmd"]])
def test_import_guard_rejects_incomplete_closures(imports: list[str]) -> None:
    """Missing command evidence must not create false non-applicability.

    Args:
        imports: Empty or incomplete package inventory.
    """
    validate = cast(
        Callable[[Sequence[str]], None],
        runpy.run_path(str(GUARD))["validate_imports"],
    )
    with pytest.raises(ValueError, match="missing Gitleaks"):
        validate(imports)


def test_openpgp_advisory_is_fixed_in_the_closure_not_suppressed() -> None:
    """GO-2026-5932 is closed by the x/crypto pin, not by an OSV exception.

    x/crypto carved ``openpgp`` out into its own module in
    ``v0.58.1-0.20261009181718-2f1f834330b2``, the version OSV lists as
    fixed. The closure selects that commit or later, so the advisory-wide
    exception that only recorded package absence is gone (#3281) and must not
    come back; the import guard above stays as defence in depth.
    """
    config = tomllib.loads((REPO_ROOT / ".osv-scanner.toml").read_text())
    ids = [e["id"] for e in config.get("IgnoredVulns", [])]
    assert_that(ids).does_not_contain("GO-2026-5932")
    module = (GUARD.parent / "go.mod").read_text()
    match = re.search(
        r"^\s*golang\.org/x/crypto v(\d+)\.(\d+)\.(\d+)(?:-0\.(\d{14})-\w+)?",
        module,
        re.MULTILINE,
    )
    assert_that(match).is_not_none()
    assert match is not None
    major, minor, patch, stamp = match.groups()
    # A pseudo-version sorts before its patch release; a tag counts as newest.
    selected = (int(major), int(minor), int(patch), int(stamp or "9" * 14))
    fixed = (0, 58, 1, 20261009181718)
    assert_that(selected >= fixed).described_as(
        f"x/crypto {match.group(0).split()[-1]} predates the fix {fixed}",
    ).is_true()


def test_gitleaks_closure_is_the_only_non_fixture_go_module() -> None:
    """OSV and the import guard only cover the one reviewed Go module."""
    assert_that(list(GUARD.parent.rglob("*.go"))).is_empty()
    git = shutil.which("git")
    assert_that(git).is_not_none()
    result = run_subprocess(
        [str(git), "ls-files", "--", "go.mod", "**/go.mod"],
        cwd=str(REPO_ROOT),
        timeout=10,
    )
    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout.splitlines()).contains_only(
        "scripts/utils/gitleaks/go.mod",
        "test_samples/tools/go/golangci_lint/go.mod",
    )


def test_image_checks_the_actual_build_graph_before_installing() -> None:
    """The same flags and readonly lock must bind applicability and install."""
    text = (REPO_ROOT / "docker/tools.Dockerfile").read_text().replace("\\\n", " ")
    command = (
        "go -C /app/scripts/utils/gitleaks list -mod=readonly -deps -tags gore2regex"
    )
    assert_that(text).contains(command)
    assert_that(text).contains(
        "github.com/zricethezav/gitleaks/v8 | "
        "python3 scripts/utils/gitleaks/verify_imports.py &&",
    )
    assert_that(text.index(command)).is_less_than(
        text.index("go -C /app/scripts/utils/gitleaks install"),
    )
