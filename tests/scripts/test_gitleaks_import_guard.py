"""Keep the build-only OpenPGP advisory exception bound to absent packages."""

from __future__ import annotations

import runpy
import shutil
import tomllib
from collections.abc import Callable, Sequence
from datetime import date, timedelta
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


def test_openpgp_exception_is_temporary_and_has_no_other_go_consumers() -> None:
    """A global advisory exception must expire and cannot hide new modules."""
    config = tomllib.loads((REPO_ROOT / ".osv-scanner.toml").read_text())
    entries = [e for e in config["IgnoredVulns"] if e["id"] == "GO-2026-5932"]
    assert_that(entries).is_length(1)
    expiry = entries[0]["ignoreUntil"]
    assert_that(expiry).is_greater_than(date.today())
    assert_that(expiry).is_less_than_or_equal_to(date.today() + timedelta(days=31))
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
