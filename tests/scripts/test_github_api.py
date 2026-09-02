"""Tests for the shared GitHub CLI API helper."""

from __future__ import annotations

import importlib.util
import subprocess  # nosec B404 - test patches the fixed subprocess call
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from assertpy import assert_that

_REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def github_api_module() -> ModuleType:
    """Load the shared GitHub API helper as a standalone module."""
    path = _REPO_ROOT / "scripts" / "ci" / "github_api.py"
    spec = importlib.util.spec_from_file_location("github_api_under_test", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_gh_json_prefers_gh_token(
    *,
    github_api_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The explicit GH_TOKEN takes precedence over GITHUB_TOKEN."""
    captured: dict[str, str] = {}

    def fake_run(*args: object, **kwargs: object) -> SimpleNamespace:
        environment = kwargs.get("env")
        if not isinstance(environment, dict):
            raise TypeError("gh_json must pass an environment mapping")
        captured["GH_TOKEN"] = str(environment["GH_TOKEN"])
        return SimpleNamespace(returncode=0, stderr="", stdout='{"ok": true}')

    monkeypatch.setenv("GH_TOKEN", "preferred")
    monkeypatch.setenv("GITHUB_TOKEN", "fallback")
    monkeypatch.setattr(subprocess, "run", fake_run)

    response = github_api_module.gh_json("repos/lgtm-hq/py-lintro")

    assert_that(captured["GH_TOKEN"]).is_equal_to("preferred")
    assert_that(response).is_equal_to({"ok": True})
