# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""Tests for the release gate's manifest writer (#2562)."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest
from assertpy import assert_that

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "ci" / "release-gate" / "write_manifest.py"

FULL = "sha256:" + "a" * 64
BASE = "sha256:" + "b" * 64
AI = "sha256:" + "c" * 64


def _load_module() -> Any:
    """Load the gate's manifest writer as an importable module."""
    spec = importlib.util.spec_from_file_location("release_gate_write_manifest", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_assets(assets: Path, *, bundles: bool = True) -> dict[str, str]:
    """Create a release-assets directory with a matching SHA256SUMS."""
    assets.mkdir()
    names = ["lintro-1.2.3.tar.gz", "lintro-macos-arm64", "lintro.1"]
    for name in names:
        (assets / name).write_bytes(f"bytes of {name}\n".encode())
    if bundles:
        for name in names[:2]:
            (assets / f"{name}.intoto.jsonl").write_text('{"bundle": true}\n')
    digests = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(assets.iterdir())
    }
    (assets / "SHA256SUMS").write_text(
        "".join(f"{digest}  {name}\n" for name, digest in sorted(digests.items())),
    )
    return digests


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    """A complete, valid environment for the writer."""
    _write_assets(tmp_path / "release-assets")
    return {
        "RELEASE_TAG": "v1.2.3",
        "RUN_ID": "123456",
        "BASE_DIGEST": BASE,
        "FULL_DIGEST": FULL,
        "AI_DIGEST": AI,
        "ASSETS_DIR": str(tmp_path / "release-assets"),
    }


def test_manifest_carries_images_and_files(env: dict[str, str]) -> None:
    """The image section from PR (b) survives and every asset is described."""
    manifest = _load_module().build_manifest(env)
    assert_that(manifest["schema"]).is_equal_to(1)
    assert_that(manifest["images"]).is_equal_to(
        {
            "ghcr.io/lgtm-hq/py-lintro-base": BASE,
            "ghcr.io/lgtm-hq/py-lintro": FULL,
            "ghcr.io/lgtm-hq/py-lintro-ai": AI,
        },
    )
    files = manifest["files"]
    assert_that(set(files)).is_equal_to(
        {"lintro-1.2.3.tar.gz", "lintro-macos-arm64", "lintro.1"},
    )
    assert_that(files["lintro-macos-arm64"]["bundle"]).is_equal_to(
        "lintro-macos-arm64.intoto.jsonl",
    )
    assert_that(files["lintro.1"]["bundle"]).is_none()
    expected = hashlib.sha256(b"bytes of lintro-macos-arm64\n").hexdigest()
    assert_that(files["lintro-macos-arm64"]["sha256"]).is_equal_to(expected)
    assert_that(files["lintro-macos-arm64"]["size"]).is_equal_to(
        len(b"bytes of lintro-macos-arm64\n"),
    )


def test_manifest_rejects_a_checksum_mismatch(env: dict[str, str]) -> None:
    """A tampered asset must not be recorded with a stale digest."""
    (Path(env["ASSETS_DIR"]) / "lintro.1").write_text("tampered\n")
    with pytest.raises(ValueError, match="SHA256SUMS disagrees with lintro.1"):
        _load_module().build_manifest(env)


def test_manifest_rejects_checksums_listing_absent_files(env: dict[str, str]) -> None:
    """SHA256SUMS naming a file that is not on disk is a broken assembly."""
    sums = Path(env["ASSETS_DIR"]) / "SHA256SUMS"
    sums.write_text(sums.read_text() + "0" * 64 + "  ghost\n")
    with pytest.raises(ValueError, match="absent"):
        _load_module().build_manifest(env)


def test_manifest_requires_assets_dir_and_checksums(
    env: dict[str, str],
    tmp_path: Path,
) -> None:
    """An empty ASSETS_DIR or a directory without SHA256SUMS fails closed."""
    module = _load_module()
    env["ASSETS_DIR"] = ""
    with pytest.raises(ValueError, match="ASSETS_DIR is required"):
        module.build_manifest(env)
    bare = tmp_path / "bare"
    bare.mkdir()
    env["ASSETS_DIR"] = str(bare)
    with pytest.raises(ValueError, match="SHA256SUMS"):
        module.build_manifest(env)


def test_manifest_still_validates_the_image_digests(env: dict[str, str]) -> None:
    """The PR (b) digest validation is reused, not reimplemented."""
    env["AI_DIGEST"] = "latest"
    with pytest.raises(ValueError, match="AI_DIGEST is not a sha256 digest"):
        _load_module().build_manifest(env)


def test_main_writes_the_output_file(
    env: dict[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CLI entry point writes JSON to OUTPUT and exits 0."""
    output = tmp_path / "release-manifest.json"
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("OUTPUT", str(output))
    assert_that(_load_module().main()).is_equal_to(0)
    written = json.loads(output.read_text(encoding="utf-8"))
    assert_that(written["files"]["lintro-1.2.3.tar.gz"]["bundle"]).is_equal_to(
        "lintro-1.2.3.tar.gz.intoto.jsonl",
    )
    assert_that(written["tag"]).is_equal_to("v1.2.3")
