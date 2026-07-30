"""Tests for Claude CLI auth-mode detection and the ``--bare`` decision."""

from __future__ import annotations

import json
import subprocess  # nosec B404 - only CompletedProcess objects are constructed here
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.ai.enums import AITransport, CliBareMode
from lintro.ai.providers.anthropic import AnthropicProvider
from lintro.ai.providers.claude_auth import (
    BARE_MODE_ENV,
    CLAUDE_API_KEY_ENV,
    claude_api_key_available,
    resolve_bare_mode,
    should_send_bare,
)
from tests.unit.ai.conftest import patch_cli_exec


@pytest.fixture()
def isolated_auth_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Iterator[Path]:
    """Strip every ambient Claude credential so detection is deterministic.

    Args:
        monkeypatch: Pytest environment patcher.
        tmp_path: Per-test temporary directory.

    Yields:
        Path: An empty directory usable as both the Claude config dir's parent
        and the CLI working directory.
    """
    monkeypatch.delenv(CLAUDE_API_KEY_ENV, raising=False)
    monkeypatch.delenv(BARE_MODE_ENV, raising=False)
    config_dir = tmp_path / "claude-config"
    config_dir.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
    workdir = tmp_path / "repo"
    workdir.mkdir()
    yield workdir


def _write_settings(workdir: Path, payload: object) -> None:
    """Write a project-level Claude settings file under *workdir*.

    Args:
        workdir: Directory that will hold ``.claude/settings.json``.
        payload: Object serialized as the settings body.
    """
    settings_dir = workdir / ".claude"
    settings_dir.mkdir(exist_ok=True)
    (settings_dir / "settings.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )


class TestClaudeApiKeyAvailable:
    """Tests for API-key reachability detection."""

    def test_false_without_key_or_helper(self, isolated_auth_env: Path) -> None:
        """Report no API key when neither env var nor helper is present."""
        assert_that(
            claude_api_key_available(cwd=str(isolated_auth_env)),
        ).is_false()

    def test_true_when_env_key_set(
        self,
        isolated_auth_env: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Report an API key when ANTHROPIC_API_KEY is set."""
        monkeypatch.setenv(CLAUDE_API_KEY_ENV, "sk-ant-test")
        assert_that(
            claude_api_key_available(cwd=str(isolated_auth_env)),
        ).is_true()

    def test_blank_env_key_is_not_a_credential(
        self,
        isolated_auth_env: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Treat a whitespace-only ANTHROPIC_API_KEY as unset."""
        monkeypatch.setenv(CLAUDE_API_KEY_ENV, "   ")
        assert_that(
            claude_api_key_available(cwd=str(isolated_auth_env)),
        ).is_false()

    def test_true_when_project_settings_declare_helper(
        self,
        isolated_auth_env: Path,
    ) -> None:
        """Report an API key when project settings declare an apiKeyHelper."""
        _write_settings(isolated_auth_env, {"apiKeyHelper": "/bin/echo key"})
        assert_that(
            claude_api_key_available(cwd=str(isolated_auth_env)),
        ).is_true()

    def test_true_when_user_settings_declare_helper(
        self,
        isolated_auth_env: Path,
    ) -> None:
        """Report an API key from the CLAUDE_CONFIG_DIR settings file."""
        config_dir = isolated_auth_env.parent / "claude-config"
        (config_dir / "settings.json").write_text(
            json.dumps({"apiKeyHelper": "/bin/echo key"}),
            encoding="utf-8",
        )
        assert_that(
            claude_api_key_available(cwd=str(isolated_auth_env)),
        ).is_true()

    def test_malformed_settings_are_ignored(
        self,
        isolated_auth_env: Path,
    ) -> None:
        """Treat unparseable settings as declaring no helper."""
        settings_dir = isolated_auth_env / ".claude"
        settings_dir.mkdir()
        (settings_dir / "settings.json").write_text("{not json", encoding="utf-8")
        assert_that(
            claude_api_key_available(cwd=str(isolated_auth_env)),
        ).is_false()

    def test_empty_helper_is_not_a_credential(
        self,
        isolated_auth_env: Path,
    ) -> None:
        """Treat a blank apiKeyHelper as absent."""
        _write_settings(isolated_auth_env, {"apiKeyHelper": "  "})
        assert_that(
            claude_api_key_available(cwd=str(isolated_auth_env)),
        ).is_false()

    def test_non_object_settings_are_ignored(
        self,
        isolated_auth_env: Path,
    ) -> None:
        """Treat a JSON array settings file as declaring no helper."""
        _write_settings(isolated_auth_env, ["apiKeyHelper"])
        assert_that(
            claude_api_key_available(cwd=str(isolated_auth_env)),
        ).is_false()


class TestResolveBareMode:
    """Tests for the environment override."""

    def test_returns_configured_without_env(self, isolated_auth_env: Path) -> None:
        """Keep the configured mode when the env var is unset."""
        del isolated_auth_env
        assert_that(
            resolve_bare_mode(CliBareMode.NEVER),
        ).is_equal_to(CliBareMode.NEVER)

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("always", CliBareMode.ALWAYS),
            ("NEVER", CliBareMode.NEVER),
            (" auto ", CliBareMode.AUTO),
            ("1", CliBareMode.ALWAYS),
            ("false", CliBareMode.NEVER),
        ],
    )
    def test_env_override_wins(
        self,
        isolated_auth_env: Path,
        monkeypatch: pytest.MonkeyPatch,
        raw: str,
        expected: CliBareMode,
    ) -> None:
        """Honour LINTRO_CLI_BARE over the configured mode.

        Args:
            isolated_auth_env: Credential-free environment fixture.
            monkeypatch: Pytest environment patcher.
            raw: Raw environment value under test.
            expected: Mode the value must resolve to.
        """
        del isolated_auth_env
        monkeypatch.setenv(BARE_MODE_ENV, raw)
        assert_that(resolve_bare_mode(CliBareMode.AUTO)).is_equal_to(expected)

    def test_unrecognised_env_falls_back(
        self,
        isolated_auth_env: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Ignore an unparseable override rather than guessing a mode."""
        del isolated_auth_env
        monkeypatch.setenv(BARE_MODE_ENV, "maybe")
        assert_that(
            resolve_bare_mode(CliBareMode.ALWAYS),
        ).is_equal_to(CliBareMode.ALWAYS)


class TestShouldSendBare:
    """Tests for the combined decision."""

    def test_auto_sends_bare_with_api_key(
        self,
        isolated_auth_env: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Send --bare under auto when an API key is reachable."""
        monkeypatch.setenv(CLAUDE_API_KEY_ENV, "sk-ant-test")
        assert_that(
            should_send_bare(
                configured=CliBareMode.AUTO,
                cwd=str(isolated_auth_env),
            ),
        ).is_true()

    def test_auto_omits_bare_without_api_key(
        self,
        isolated_auth_env: Path,
    ) -> None:
        """Omit --bare under auto for a subscription-only login."""
        assert_that(
            should_send_bare(
                configured=CliBareMode.AUTO,
                cwd=str(isolated_auth_env),
            ),
        ).is_false()

    def test_always_sends_bare_without_api_key(
        self,
        isolated_auth_env: Path,
    ) -> None:
        """Send --bare under always even with no detectable API key."""
        assert_that(
            should_send_bare(
                configured=CliBareMode.ALWAYS,
                cwd=str(isolated_auth_env),
            ),
        ).is_true()

    def test_never_omits_bare_with_api_key(
        self,
        isolated_auth_env: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Omit --bare under never even when an API key is present."""
        monkeypatch.setenv(CLAUDE_API_KEY_ENV, "sk-ant-test")
        assert_that(
            should_send_bare(
                configured=CliBareMode.NEVER,
                cwd=str(isolated_auth_env),
            ),
        ).is_false()


@pytest.fixture()
def _mock_claude_on_path() -> Iterator[None]:
    """Patch claude binary discovery for CLI transport tests.

    Yields:
        None: Control, with ``_find_claude`` returning a fake path.
    """
    with patch(
        "lintro.ai.providers.anthropic._find_claude",
        return_value="/usr/local/bin/claude",
    ):
        yield


async def _argv_for(
    *,
    workdir: Path,
    cli_bare: CliBareMode,
) -> list[str]:
    """Run one CLI completion and return the argv it built.

    Args:
        workdir: Working directory passed as the review repo root.
        cli_bare: Bare-mode policy handed to the provider.

    Returns:
        The argv the transport was invoked with.
    """
    provider = AnthropicProvider(
        transport=AITransport.CLI,
        cli_bare=cli_bare,
    )
    stdout = json.dumps(
        {
            "result": '{"summary": "ok"}',
            "usage": {"input_tokens": 1, "output_tokens": 1},
            "total_cost_usd": 0.0,
        },
    )
    with patch_cli_exec() as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=stdout,
            stderr="",
        )
        await provider.complete("Review this diff", repo_root=str(workdir))
    argv: list[str] = mock_run.call_args.args[0]
    return argv


class TestProviderBareFlag:
    """Tests for the flag the provider actually sends."""

    async def test_bare_sent_when_api_key_present(
        self,
        _mock_claude_on_path: None,
        isolated_auth_env: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Pass --bare when the CLI can authenticate from an API key."""
        monkeypatch.setenv(CLAUDE_API_KEY_ENV, "sk-ant-test")
        argv = await _argv_for(
            workdir=isolated_auth_env,
            cli_bare=CliBareMode.AUTO,
        )
        assert_that(argv).contains("--bare")

    async def test_bare_omitted_for_subscription_login(
        self,
        _mock_claude_on_path: None,
        isolated_auth_env: Path,
    ) -> None:
        """Omit --bare when no API key is reachable, so OAuth login works."""
        argv = await _argv_for(
            workdir=isolated_auth_env,
            cli_bare=CliBareMode.AUTO,
        )
        assert_that(argv).does_not_contain("--bare")
        assert_that(argv).contains("-p", "--output-format", "json")

    async def test_never_override_omits_bare_with_api_key(
        self,
        _mock_claude_on_path: None,
        isolated_auth_env: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Honour an explicit 'never' even when an API key is present."""
        monkeypatch.setenv(CLAUDE_API_KEY_ENV, "sk-ant-test")
        argv = await _argv_for(
            workdir=isolated_auth_env,
            cli_bare=CliBareMode.NEVER,
        )
        assert_that(argv).does_not_contain("--bare")

    async def test_always_override_sends_bare_without_api_key(
        self,
        _mock_claude_on_path: None,
        isolated_auth_env: Path,
    ) -> None:
        """Honour an explicit 'always' even with no detectable API key."""
        argv = await _argv_for(
            workdir=isolated_auth_env,
            cli_bare=CliBareMode.ALWAYS,
        )
        assert_that(argv).contains("--bare")

    async def test_env_override_beats_config(
        self,
        _mock_claude_on_path: None,
        isolated_auth_env: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Let LINTRO_CLI_BARE override a config that says otherwise."""
        monkeypatch.setenv(BARE_MODE_ENV, "never")
        argv = await _argv_for(
            workdir=isolated_auth_env,
            cli_bare=CliBareMode.ALWAYS,
        )
        assert_that(argv).does_not_contain("--bare")
