"""Contract tests for the Cloud Agent environment bootstrap files."""

from __future__ import annotations

import json
from pathlib import Path

from assertpy import assert_that

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENVIRONMENT_JSON = PROJECT_ROOT / ".cursor" / "environment.json"
INSTALL_SH = PROJECT_ROOT / ".cursor" / "install.sh"


def test_environment_json_is_valid_and_points_at_install_script() -> None:
    """Cloud Agent environment.json must parse and invoke the install script."""
    payload = json.loads(ENVIRONMENT_JSON.read_text(encoding="utf-8"))

    assert_that(payload).contains_key("name")
    assert_that(payload).contains_key("install")
    assert_that(payload["install"]).is_equal_to("bash .cursor/install.sh")
    assert_that(INSTALL_SH.exists()).is_true()


def test_install_script_is_idempotent_strict_bash() -> None:
    """The install script must fail fast and sync the full Python extra."""
    text = INSTALL_SH.read_text(encoding="utf-8")

    assert_that(text).starts_with("#!/usr/bin/env bash")
    assert_that(text).contains("set -euo pipefail")
    assert_that(text).contains("UV_LINK_MODE=copy")
    assert_that(text).contains("uv sync --dev --extra full")
    assert_that(INSTALL_SH.stat().st_mode & 0o111).is_not_equal_to(0)
