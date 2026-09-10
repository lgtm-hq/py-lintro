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


def test_gh_json_falls_back_to_github_token(
    *,
    github_api_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GITHUB_TOKEN is used when no explicit GH_TOKEN is present."""
    captured: dict[str, str] = {}

    def fake_run(*args: object, **kwargs: object) -> SimpleNamespace:
        environment = kwargs.get("env")
        if not isinstance(environment, dict):
            raise TypeError("gh_json must pass an environment mapping")
        captured["GH_TOKEN"] = str(environment["GH_TOKEN"])
        return SimpleNamespace(returncode=0, stderr="", stdout='{"ok": true}')

    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "fallback")
    monkeypatch.setattr(subprocess, "run", fake_run)

    response = github_api_module.gh_json("repos/lgtm-hq/py-lintro")

    assert_that(captured["GH_TOKEN"]).is_equal_to("fallback")
    assert_that(response).is_equal_to({"ok": True})


def test_gh_json_passes_empty_token_when_unset(
    *,
    github_api_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With neither token set the call still runs and gh reports the failure."""

    def fake_run(*args: object, **kwargs: object) -> SimpleNamespace:
        environment = kwargs.get("env")
        if not isinstance(environment, dict):
            raise TypeError("gh_json must pass an environment mapping")
        assert_that(environment["GH_TOKEN"]).is_equal_to("")
        return SimpleNamespace(returncode=1, stderr="gh: HTTP 401\n", stdout="")

    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="HTTP 401"):
        github_api_module.gh_json("repos/lgtm-hq/py-lintro")


def test_gh_json_returns_none_for_an_empty_body(
    *,
    github_api_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 204 DELETE writes no body; that must not raise a decode error."""

    def fake_run(*args: object, **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setenv("GH_TOKEN", "token")
    monkeypatch.setattr(subprocess, "run", fake_run)

    assert_that(github_api_module.gh_json("--method", "DELETE", "orgs/x")).is_none()
