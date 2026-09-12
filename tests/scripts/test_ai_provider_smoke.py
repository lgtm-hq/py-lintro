"""Tests for the weekly provider API smoke scripts (#2600).

Three properties carry the issue's intent and none of them is visible from the
workflow alone: the committed table is validated rather than trusted, a row
whose secret is unset reports a *skip* and never a pass, and a provider failure
records the error text the tracker issue quotes.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import shutil
import subprocess  # nosec B404 - runs the repo's own script with a fixed argv
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from assertpy import assert_that

from lintro.ai import providers as providers_module
from lintro.ai.enums import AITransport
from lintro.ai.provider_enum import AIProvider

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SMOKE_DIR = _REPO_ROOT / "scripts" / "ci" / "ai_provider_smoke"
_TABLE = _SMOKE_DIR / "providers.json"
_STATUS_SCRIPT = _SMOKE_DIR / "report-commit-status.sh"

#: Placeholder credential-variable *name* for the table-validation rows. It
#: names a variable; it is not a credential, and no test here ever holds one.
_EXAMPLE_ENV = "EXAMPLE_CREDENTIAL"

#: Stand-in for a live credential value. Assembled at runtime so no
#: credential-shaped literal is ever committed (GitGuardian scans every commit).
_FAKE_CREDENTIAL = "sk-" + "smoke" + "-" + ("0" * 24)


def _load(name: str, path: Path) -> ModuleType:
    """Load a script as an importable module.

    Args:
        name: Module name to register it under.
        path: Script path.

    Returns:
        The loaded module.

    Raises:
        RuntimeError: When the module spec cannot be created.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        msg = f"Unable to load module from {path}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def smoke() -> ModuleType:
    """Return the smoke runner module.

    Returns:
        The loaded ``run_smoke`` module.
    """
    return _load("ai_provider_run_smoke", _SMOKE_DIR / "run_smoke.py")


@pytest.fixture
def details() -> ModuleType:
    """Return the error-detail comment module.

    Returns:
        The loaded ``post_error_details`` module.
    """
    return _load("ai_provider_post_error_details", _SMOKE_DIR / "post_error_details.py")


def _write_table(path: Path, rows: list[dict[str, Any]]) -> Path:
    """Write a provider table for a validation test.

    Args:
        path: File to write.
        rows: Rows to place under ``providers``.

    Returns:
        The written path.
    """
    path.write_text(json.dumps({"providers": rows}), encoding="utf-8")
    return path


def _row(**overrides: str) -> dict[str, str]:
    """Return a valid table row with optional overrides.

    Args:
        **overrides: Fields to replace.

    Returns:
        A row mapping.
    """
    row = {
        "name": "example-api",
        "protocol": "anthropic",
        "base_url": "https://api.example.com",
        "key_env": _EXAMPLE_ENV,
        "model": "example-model",
    }
    row.update(overrides)
    return row


def test_committed_table_loads_and_covers_the_funded_providers(
    smoke: ModuleType,
) -> None:
    """The shipped table must validate and carry the four rows #2600 names.

    Args:
        smoke: The loaded smoke runner module.
    """
    rows = smoke.load_table(path=_TABLE)
    assert_that([row.name for row in rows]).is_equal_to(
        ["anthropic-api", "openai-api", "kimi-api", "zai-api"],
    )
    for row in rows:
        assert_that(smoke.SUPPORTED_PROTOCOLS).contains(row.protocol)
        assert_that(row.base_url).starts_with("https://")


@pytest.mark.parametrize(
    ("rows", "reason"),
    [
        ([_row(protocol="gemini")], "protocol"),
        ([_row(base_url="http://api.example.com")], "base_url"),
        ([_row(key_env=_EXAMPLE_ENV.lower())], "key_env"),
        ([_row(name="Example API")], "name"),
        ([_row(model="")], "model"),
        ([_row(), _row()], "duplicate"),
        ([], "providers"),
    ],
)
def test_table_validation_rejects_a_malformed_row(
    smoke: ModuleType,
    tmp_path: Path,
    rows: list[dict[str, Any]],
    reason: str,
) -> None:
    """A malformed table must fail the cheap job, not four provider jobs.

    Args:
        smoke: The loaded smoke runner module.
        tmp_path: Temporary directory for the table.
        rows: The rows under test.
        reason: Substring the error message must name.
    """
    table = _write_table(tmp_path / "providers.json", rows)
    with pytest.raises(ValueError, match=reason):
        smoke.load_table(path=table)


def test_matrix_carries_the_egress_host_for_every_row(smoke: ModuleType) -> None:
    """Each matrix entry must carry its own harden-runner endpoint.

    The workflow's allowlist is built from this, so a provider added to the
    table cannot arrive without the host it needs.

    Args:
        smoke: The loaded smoke runner module.
    """
    matrix = smoke.build_matrix(rows=smoke.load_table(path=_TABLE))
    egress = {entry["name"]: entry["egress"] for entry in matrix["include"]}
    assert_that(egress["anthropic-api"]).is_equal_to("api.anthropic.com:443")
    assert_that(egress["zai-api"]).is_equal_to("api.z.ai:443")
    for entry in matrix["include"]:
        assert_that(entry["egress"]).ends_with(":443")


def test_egress_follows_the_port_the_row_actually_connects_to(
    smoke: ModuleType,
    tmp_path: Path,
) -> None:
    """A non-443 row must allowlist the port it dials, not the default.

    An entry naming 443 for a row pointing at 8443 has harden-runner block the
    call, so the weekly run reports an opaque network error instead of the
    provider's own verdict.

    Args:
        smoke: The loaded smoke runner module.
        tmp_path: Temporary directory for the table.
    """
    table = _write_table(
        tmp_path / "providers.json",
        [_row(base_url="https://gateway.example.com:8443/v1")],
    )
    (row,) = smoke.load_table(path=table)

    assert_that(row.egress).is_equal_to("gateway.example.com:8443")
    entry = smoke.build_matrix(rows=[row])["include"][0]
    assert_that(entry["egress"]).is_equal_to("gateway.example.com:8443")


@pytest.mark.parametrize(
    "base_url",
    ["https://api.example.com:0", "https://api.example.com:99999"],
)
def test_table_validation_rejects_an_unusable_port(
    smoke: ModuleType,
    tmp_path: Path,
    base_url: str,
) -> None:
    """A port urlparse cannot use must fail the table, not the matrix step.

    ``urlparse`` defers the port check to attribute access, so without this the
    row validates and only explodes where the allowlist entry is built.

    Args:
        smoke: The loaded smoke runner module.
        tmp_path: Temporary directory for the table.
        base_url: The malformed base URL under test.
    """
    table = _write_table(tmp_path / "providers.json", [_row(base_url=base_url)])
    with pytest.raises(ValueError, match="port"):
        smoke.load_table(path=table)


def test_the_openai_protocol_row_drives_lintros_openai_provider(
    smoke: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The openai row must reach lintro's openai provider, base URL and all.

    Every other behavioural test here runs the anthropic row; the protocol
    field is what selects the code path, so the second protocol needs its own
    proof that the row's base URL, model and credential *variable name* arrive
    on the config lintro builds the call from.

    Args:
        smoke: The loaded smoke runner module.
        monkeypatch: Provider-factory patcher.
    """
    captured: list[Any] = []

    class _StubProvider:
        async def complete(self, _prompt: str, **_kwargs: Any) -> Any:
            return type("R", (), {"content": "pong"})()

        async def aclose(self) -> None:
            return None

    def _get_provider(config: Any) -> Any:
        captured.append(config)
        return _StubProvider()

    monkeypatch.setattr(providers_module, "get_provider", _get_provider)

    row = next(
        candidate
        for candidate in smoke.load_table(path=_TABLE)
        if candidate.protocol == "openai"
    )
    content = asyncio.run(
        smoke._complete(row=row, credential_env="LINTRO_SMOKE_CREDENTIAL"),
    )

    assert_that(content).is_equal_to("pong")
    assert_that(captured).is_length(1)
    config = captured[0]
    assert_that(config.provider).is_equal_to(AIProvider.OPENAI)
    assert_that(config.transport).is_equal_to(AITransport.API)
    assert_that(config.api_base_url).is_equal_to(row.base_url)
    assert_that(config.model).is_equal_to(row.model)
    assert_that(config.api_key_env).is_equal_to("LINTRO_SMOKE_CREDENTIAL")


def test_a_missing_secret_reports_a_skip_and_never_calls_the_provider(
    smoke: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An absent credential is announced, not passed off as a green check.

    Args:
        smoke: The loaded smoke runner module.
        tmp_path: Temporary directory for the Actions output files.
        monkeypatch: Environment patcher.
    """
    called = False

    def _fail(**_kwargs: Any) -> str:
        nonlocal called
        called = True
        msg = "the provider must not be called without a credential"
        raise AssertionError(msg)

    monkeypatch.setattr(smoke, "_complete", _fail)
    output = tmp_path / "output"
    summary = tmp_path / "summary"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.delenv("LINTRO_SMOKE_CREDENTIAL", raising=False)

    row = smoke.load_table(path=_TABLE)[0]
    code = smoke.run_smoke(
        row=row,
        credential_env="LINTRO_SMOKE_CREDENTIAL",
        error_file=None,
    )

    assert_that(code).is_equal_to(0)
    assert_that(called).is_false()
    assert_that(output.read_text(encoding="utf-8")).contains("outcome=skipped")
    assert_that(summary.read_text(encoding="utf-8")).contains("skipped")


@pytest.mark.parametrize("answer", ["pong", "  Pong!  ", '"pong"'])
def test_a_successful_call_reports_success(
    smoke: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    answer: str,
) -> None:
    """Answering the prompt is what counts as a pass, however it is dressed.

    Case, surrounding whitespace and the punctuation a chat model likes to add
    are not drift worth reddening main for; anything else is (see the failure
    cases below).

    Args:
        smoke: The loaded smoke runner module.
        tmp_path: Temporary directory for the Actions output files.
        monkeypatch: Environment patcher.
        answer: The response content to simulate.
    """
    monkeypatch.setattr(smoke.asyncio, "run", lambda _coro: answer)
    monkeypatch.setattr(smoke, "_complete", lambda **_kwargs: None)
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("LINTRO_SMOKE_CREDENTIAL", _FAKE_CREDENTIAL)

    code = smoke.run_smoke(
        row=smoke.load_table(path=_TABLE)[0],
        credential_env="LINTRO_SMOKE_CREDENTIAL",
        error_file=None,
    )

    assert_that(code).is_equal_to(0)
    assert_that(output.read_text(encoding="utf-8")).contains("outcome=success")


@pytest.mark.parametrize(
    ("content", "expected_detail"),
    [
        ("", "empty response"),
        (None, "Credit balance is too low"),
        ("Sorry, I cannot help with that.", "expected 'pong'"),
        ('{"error": "model_not_found"}', "expected 'pong'"),
    ],
)
def test_a_failing_call_records_the_error_text(
    smoke: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    content: str | None,
    expected_detail: str,
) -> None:
    """Failure text must reach the file the tracker comment quotes.

    An empty envelope, a raised provider error and an answer that is not the
    word asked for are all failures — the credit-exhaustion case is the one
    that went unseen for a month (#2600), and a gateway that renders its error
    as prose would otherwise score a green tick on a call that failed.

    Args:
        smoke: The loaded smoke runner module.
        tmp_path: Temporary directory for the Actions output files.
        monkeypatch: Environment patcher.
        content: Response content to simulate, or None to raise instead.
        expected_detail: Substring the recorded error must contain.
    """

    def _run(_coro: Any) -> str:
        if content is None:
            msg = "Credit balance is too low"
            raise RuntimeError(msg)
        return content

    monkeypatch.setattr(smoke.asyncio, "run", _run)
    monkeypatch.setattr(smoke, "_complete", lambda **_kwargs: None)
    output = tmp_path / "output"
    error_file = tmp_path / "smoke-error.md"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("LINTRO_SMOKE_CREDENTIAL", _FAKE_CREDENTIAL)

    code = smoke.run_smoke(
        row=smoke.load_table(path=_TABLE)[0],
        credential_env="LINTRO_SMOKE_CREDENTIAL",
        error_file=error_file,
    )

    assert_that(code).is_equal_to(1)
    assert_that(output.read_text(encoding="utf-8")).contains("outcome=failure")
    recorded = error_file.read_text(encoding="utf-8")
    assert_that(recorded).contains(expected_detail)
    assert_that(recorded).contains("anthropic-api")


def test_a_wrong_answer_carrying_the_credential_is_redacted(
    smoke: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A gateway that echoes the key back as content must not publish it.

    The unexpected-answer text is quoted into the log, the summary, the error
    file and the tracker issue, so it has to clear the same bar as provider
    error text: nothing derived from the credential survives.

    Args:
        smoke: The loaded smoke runner module.
        tmp_path: Temporary directory for the Actions output files.
        monkeypatch: Environment patcher.
    """
    echoed = f"Unauthorized: key {_FAKE_CREDENTIAL} is not valid"
    monkeypatch.setattr(smoke.asyncio, "run", lambda _coro: echoed)
    monkeypatch.setattr(smoke, "_complete", lambda **_kwargs: None)
    output = tmp_path / "output"
    summary = tmp_path / "summary"
    error_file = tmp_path / "smoke-error.md"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setenv("LINTRO_SMOKE_CREDENTIAL", _FAKE_CREDENTIAL)

    code = smoke.run_smoke(
        row=smoke.load_table(path=_TABLE)[0],
        credential_env="LINTRO_SMOKE_CREDENTIAL",
        error_file=error_file,
    )

    assert_that(code).is_equal_to(1)
    assert_that(output.read_text(encoding="utf-8")).contains("outcome=failure")
    for written in (error_file, summary):
        text = written.read_text(encoding="utf-8")
        assert_that(text).described_as(str(written)).does_not_contain(
            _FAKE_CREDENTIAL,
        )
        assert_that(text).contains("RedactedProviderError")


def _run_status_script(*, tmp_path: Path, env: dict[str, str]) -> dict[str, str]:
    """Run the commit-status script against a stub ``gh`` and return its call.

    The script's only output is a GitHub API call, so the stub records the
    flags it was handed; asserting on those is asserting on behaviour rather
    than on how the shell spells its variables.

    Args:
        tmp_path: Temporary directory for the stub and its recording.
        env: Environment the script runs with, on top of the stub's PATH.

    Returns:
        The ``-f key=value`` pairs the script passed to ``gh``.
    """
    recording = tmp_path / "gh-args.txt"
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    stub = stub_dir / "gh"
    stub.write_text(
        '#!/usr/bin/env bash\nprintf "%s\\n" "$@" > "$GH_RECORDING"\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)

    bash = shutil.which("bash") or "/bin/bash"
    result = subprocess.run(  # nosec B603 - fixed argv, shell=False, no user input
        [bash, str(_STATUS_SCRIPT)],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{stub_dir}:{os.environ['PATH']}",
            "GH_RECORDING": str(recording),
            **env,
        },
    )
    assert_that(result.returncode).described_as(result.stderr).is_equal_to(0)

    fields: dict[str, str] = {}
    args = recording.read_text(encoding="utf-8").splitlines()
    for flag, value in zip(args, args[1:], strict=False):
        if flag == "-f" and "=" in value:
            key, _, raw = value.partition("=")
            fields[key] = raw
    return fields


@pytest.mark.parametrize(
    ("env", "state", "detail"),
    [
        ({"SMOKE_OUTCOME": "success"}, "success", "answered"),
        ({"SMOKE_OUTCOME": "skipped"}, "pending", _EXAMPLE_ENV),
        ({"SMOKE_OUTCOME": "failure"}, "failure", "smoke failed"),
        ({"SMOKE_STEP": "skipped"}, "pending", "infra failure before the smoke"),
        ({"SMOKE_STEP": "failure"}, "failure", "smoke failed"),
        ({}, "failure", "smoke failed"),
    ],
)
def test_the_commit_status_never_paints_an_uncalled_row_green(
    tmp_path: Path,
    env: dict[str, str],
    state: str,
    detail: str,
) -> None:
    """Only an answered provider gets a green tick on main.

    A call that was never made must not look like a pass, so both ways of not
    calling report *pending*: an unset credential, and a smoke step that never
    ran because an earlier one failed. They carry different descriptions —
    claiming a missing credential when one is present sends the reader to the
    wrong place. A step that ran and reported nothing is a failure.

    Args:
        tmp_path: Temporary directory for the stub GitHub CLI.
        env: Outcome variables under test.
        state: Commit-status state the script must post.
        detail: Substring the status description must carry.
    """
    fields = _run_status_script(
        tmp_path=tmp_path,
        env={
            "SMOKE_NAME": "example-api",
            "SMOKE_KEY_ENV": _EXAMPLE_ENV,
            "GITHUB_REPOSITORY": "lgtm-hq/py-lintro",
            "GITHUB_SHA": "0" * 40,
            "SMOKE_OUTCOME": "",
            "SMOKE_STEP": "",
            **env,
        },
    )

    assert_that(fields["state"]).is_equal_to(state)
    assert_that(fields["context"]).is_equal_to("ai-provider-smoke/example-api")
    assert_that(fields["description"]).contains(detail)


def test_an_unknown_row_name_fails_loudly(smoke: ModuleType) -> None:
    """Naming a row the table does not have must not silently do nothing.

    Args:
        smoke: The loaded smoke runner module.
    """
    with pytest.raises(SystemExit) as excinfo:
        smoke.row_for(name="nope-api", rows=smoke.load_table(path=_TABLE))
    assert_that(excinfo.value.code).is_equal_to(2)


def test_error_details_target_the_shared_notifier_issue(details: ModuleType) -> None:
    """The comment must find the notifier's issue, not open a second one.

    Args:
        details: The loaded error-detail module.
    """
    title = details.failure_issue_title(
        workflow_key="ai-provider-api-smoke",
        branch="main",
    )
    assert_that(title).is_equal_to(
        "fix(ci): main workflow failed: main (ai-provider-api-smoke)",
    )


def test_error_details_collects_every_uploaded_error(
    details: ModuleType,
    tmp_path: Path,
) -> None:
    """Every failing provider's text reaches the comment body.

    Args:
        details: The loaded error-detail module.
        tmp_path: Temporary artifact directory.
    """
    (tmp_path / "smoke-error-anthropic-api").mkdir(parents=True)
    (tmp_path / "smoke-error-anthropic-api" / "smoke-error.md").write_text(
        "### `anthropic-api`\n\nCredit balance is too low\n",
        encoding="utf-8",
    )
    (tmp_path / "smoke-error-zai-api").mkdir(parents=True)
    (tmp_path / "smoke-error-zai-api" / "smoke-error.md").write_text(
        "### `zai-api`\n\nHTTP 401\n",
        encoding="utf-8",
    )

    body = details.collect_error_details(errors_dir=tmp_path)
    assert_that(body).contains("anthropic-api")
    assert_that(body).contains("zai-api")
    assert_that(body).contains("Credit balance is too low")


def test_error_details_are_silent_when_nothing_was_recorded(
    details: ModuleType,
    tmp_path: Path,
) -> None:
    """No recorded error means nothing to say, not an empty comment.

    Args:
        details: The loaded error-detail module.
        tmp_path: Empty artifact directory.
    """
    assert_that(details.collect_error_details(errors_dir=tmp_path)).is_empty()


def test_the_smoke_drives_lintros_own_provider_code_path(
    smoke: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The call must go through lintro's provider, pointed at the table's row.

    A hand-rolled HTTP call would prove nothing about the code a review runs
    on, so the smoke builds the real provider from the row and only the SDK
    client underneath it is stubbed. The row's base URL and model must reach
    that provider, or the weekly signal is about the wrong endpoint.

    Args:
        smoke: The loaded smoke runner module.
        monkeypatch: Attribute and environment patcher.
    """
    import asyncio

    from lintro.ai.providers import anthropic as anthropic_pkg
    from lintro.ai.providers.anthropic import provider as anthropic_mod

    captured: dict[str, Any] = {}

    class _FakeMessages:
        async def create(self, **kwargs: Any) -> Any:
            captured["request"] = kwargs
            block = type("Block", (), {"text": "pong", "type": "text"})()
            usage = type("Usage", (), {"input_tokens": 9, "output_tokens": 2})()
            return type("Response", (), {"content": [block], "usage": usage})()

    class _FakeClient:
        messages = _FakeMessages()

        async def close(self) -> None:
            captured["closed"] = True

    monkeypatch.setattr(anthropic_mod, "_has_anthropic", True)
    original_build = anthropic_pkg.AnthropicPlugin.build

    def _build(self: Any, config: Any) -> Any:
        provider = original_build(self, config)
        captured["base_url"] = provider._base_url
        captured["model"] = provider._model
        provider._client = _FakeClient()
        return provider

    monkeypatch.setattr(anthropic_pkg.AnthropicPlugin, "build", _build)
    monkeypatch.setenv("LINTRO_SMOKE_CREDENTIAL", _FAKE_CREDENTIAL)

    row = smoke.load_table(path=_TABLE)[0]
    content = asyncio.run(
        smoke._complete(row=row, credential_env="LINTRO_SMOKE_CREDENTIAL"),
    )

    assert_that(content).is_equal_to("pong")
    assert_that(captured["base_url"]).is_equal_to(row.base_url)
    assert_that(captured["model"]).is_equal_to(row.model)
    assert_that(captured["request"]["model"]).is_equal_to(row.model)


def test_a_provider_error_that_echoes_the_credential_never_reaches_disk(
    smoke: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 401 that quotes what it was sent must not leak it (CodeQL #6862/#6863).

    A gateway rejecting a call commonly echoes the ``Authorization`` header
    back, so the provider's own error text is the one string in this script
    that can carry the live credential — into the log, the step summary, and
    the error file the tracker issue quotes verbatim. All three must come out
    clean, and nothing derived from the credential may survive, not even a
    masked remnant.

    Args:
        smoke: The loaded smoke runner module.
        tmp_path: Temporary directory for the Actions output files.
        monkeypatch: Environment patcher.
    """

    def _run(_coro: Any) -> str:
        msg = f"401 Unauthorized: invalid api key 'Bearer {_FAKE_CREDENTIAL}'"
        raise RuntimeError(msg)

    monkeypatch.setattr(smoke.asyncio, "run", _run)
    monkeypatch.setattr(smoke, "_complete", lambda **_kwargs: None)
    summary = tmp_path / "summary"
    output = tmp_path / "output"
    error_file = tmp_path / "smoke-error.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("LINTRO_SMOKE_CREDENTIAL", _FAKE_CREDENTIAL)

    code = smoke.run_smoke(
        row=smoke.load_table(path=_TABLE)[0],
        credential_env="LINTRO_SMOKE_CREDENTIAL",
        error_file=error_file,
    )

    assert_that(code).is_equal_to(1)
    written = error_file.read_text(encoding="utf-8")
    summarised = summary.read_text(encoding="utf-8")
    for where, text in (("error file", written), ("summary", summarised)):
        assert_that(text).described_as(where).does_not_contain(_FAKE_CREDENTIAL)
        # Not even a fragment: a partial credential is still a credential.
        assert_that(text).described_as(where).does_not_contain(
            _FAKE_CREDENTIAL[: len(_FAKE_CREDENTIAL) // 2],
        )
    # The failure is still reported — redaction must not silence the alarm.
    assert_that(written).contains("anthropic-api")
    assert_that(output.read_text(encoding="utf-8")).contains("outcome=failure")


def test_a_key_shaped_literal_in_provider_text_is_redacted(
    smoke: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Someone else's key quoted by the provider is redacted too.

    The credential-equality check cannot catch a key that is not ours, so the
    surviving text still goes through lintro's own ``redact_secrets``.

    Args:
        smoke: The loaded smoke runner module.
        tmp_path: Temporary directory for the error file.
        monkeypatch: Environment patcher.
    """
    foreign = "sk-" + "ant" + "-" + ("a" * 32)

    def _run(_coro: Any) -> str:
        msg = f"400 Bad Request: key {foreign} is disabled"
        raise RuntimeError(msg)

    monkeypatch.setattr(smoke.asyncio, "run", _run)
    monkeypatch.setattr(smoke, "_complete", lambda **_kwargs: None)
    error_file = tmp_path / "smoke-error.md"
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "output"))
    monkeypatch.setenv("LINTRO_SMOKE_CREDENTIAL", _FAKE_CREDENTIAL)

    smoke.run_smoke(
        row=smoke.load_table(path=_TABLE)[0],
        credential_env="LINTRO_SMOKE_CREDENTIAL",
        error_file=error_file,
    )

    written = error_file.read_text(encoding="utf-8")
    assert_that(written).does_not_contain(foreign)
    assert_that(written).contains("[REDACTED]")


def test_the_skip_notice_names_the_variable_and_holds_no_credential(
    smoke: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The skip path reports a variable name, which is not sensitive data.

    Args:
        smoke: The loaded smoke runner module.
        tmp_path: Temporary directory for the Actions output files.
        monkeypatch: Environment patcher.
    """
    summary = tmp_path / "summary"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "output"))
    monkeypatch.delenv("LINTRO_SMOKE_CREDENTIAL", raising=False)

    row = smoke.load_table(path=_TABLE)[0]
    smoke.run_smoke(
        row=row,
        credential_env="LINTRO_SMOKE_CREDENTIAL",
        error_file=None,
    )

    written = summary.read_text(encoding="utf-8")
    assert_that(written).contains(row.key_env)
    assert_that(written).does_not_contain(_FAKE_CREDENTIAL)
