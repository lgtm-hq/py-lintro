# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""Tests for the release-manifest writer (#2562)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest
from assertpy import assert_that

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "ci" / "write-release-manifest.py"

FULL = "sha256:" + "a" * 64
BASE = "sha256:" + "b" * 64
AI = "sha256:" + "c" * 64


def _load_module() -> Any:
    """Load the hyphenated script as an importable module."""
    spec = importlib.util.spec_from_file_location("write_release_manifest", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def env() -> dict[str, str]:
    """A complete, valid environment for the writer."""
    return {
        "RELEASE_TAG": "v1.2.3",
        "RUN_ID": "123456",
        "BASE_DIGEST": BASE,
        "FULL_DIGEST": FULL,
        "AI_DIGEST": AI,
    }


def test_build_manifest_keys_digests_by_image(env: dict[str, str]) -> None:
    """Every image is recorded under its registry name with its digest."""
    manifest = _load_module().build_manifest(env)
    assert_that(manifest).is_equal_to(
        {
            "schema": 1,
            "tag": "v1.2.3",
            "run_id": "123456",
            "images": {
                "ghcr.io/lgtm-hq/py-lintro-base": BASE,
                "ghcr.io/lgtm-hq/py-lintro": FULL,
                "ghcr.io/lgtm-hq/py-lintro-ai": AI,
            },
        },
    )


@pytest.mark.parametrize(
    "missing",
    ["RELEASE_TAG", "RUN_ID", "BASE_DIGEST", "FULL_DIGEST", "AI_DIGEST"],
)
def test_build_manifest_requires_every_input(
    env: dict[str, str],
    missing: str,
) -> None:
    """An empty or absent input fails closed instead of writing a hole."""
    env[missing] = ""
    with pytest.raises(ValueError, match=missing):
        _load_module().build_manifest(env)


@pytest.mark.parametrize("bad", ["latest", "sha256:abc", "sha512:" + "a" * 64])
def test_build_manifest_rejects_malformed_digests(
    env: dict[str, str],
    bad: str,
) -> None:
    """Only a full sha256 digest may enter the manifest."""
    env["AI_DIGEST"] = bad
    with pytest.raises(ValueError, match="AI_DIGEST is not a sha256 digest"):
        _load_module().build_manifest(env)


def test_main_writes_the_output_file(
    env: dict[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CLI entry point writes pretty JSON to OUTPUT and exits 0."""
    output = tmp_path / "release-manifest.json"
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("OUTPUT", str(output))
    assert_that(_load_module().main()).is_equal_to(0)
    written = json.loads(output.read_text(encoding="utf-8"))
    assert_that(written["images"]["ghcr.io/lgtm-hq/py-lintro"]).is_equal_to(FULL)


def test_main_fails_on_a_bad_digest(
    env: dict[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A malformed digest is reported as a workflow error and nothing is written."""
    output = tmp_path / "release-manifest.json"
    env["FULL_DIGEST"] = "latest"
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("OUTPUT", str(output))
    assert_that(_load_module().main()).is_equal_to(1)
    assert_that(capsys.readouterr().err).contains("::error::FULL_DIGEST")
    assert_that(output.exists()).is_false()
