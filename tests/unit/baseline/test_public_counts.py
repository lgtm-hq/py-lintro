"""Public counts stay raw while API callers can explicitly select a baseline."""

import json
from pathlib import Path
from typing import Literal

import pytest
from click.testing import CliRunner

from lintro import api
from lintro.cli import cli
from lintro.config.config_loader import clear_config_cache
from lintro.exceptions.errors import ConfigurationError


@pytest.fixture
def configured_baseline(workspace: Path) -> Path:
    """Acknowledge a real unfixable E501 finding in the project configuration."""
    (workspace / "pyproject.toml").write_text(
        '[tool.ruff.lint]\nselect = ["E501"]\n'
        "[tool.lintro.ruff]\nformat_check = false\n",
        encoding="utf-8",
    )
    file = workspace / "example.py"
    file.write_text('value = "' + "x" * 100 + '"\n', encoding="utf-8")
    created = CliRunner().invoke(
        cli,
        ["baseline", "create", file.name, "--tools", "ruff"],
    )
    assert created.exit_code == 0, created.output
    config = workspace / ".lintro-config.yaml"
    config.write_text(
        config.read_text().replace(
            "execution:\n",
            "execution:\n  enabled_tools: [ruff]\n",
        )
        + "baseline: .lintro-baseline.json\n",
        encoding="utf-8",
    )
    clear_config_cache()
    return workspace / ".lintro-baseline.json"


@pytest.mark.parametrize("entrypoint", ["check", "check_run", "format", "format_run"])
@pytest.mark.parametrize("selection", ["omitted", None, False, ".lintro-baseline.json"])
def test_api_baseline_default_override_and_explicit_opt_out(
    configured_baseline: Path,
    entrypoint: str,
    selection: str | Literal[False] | None,
) -> None:
    """None uses config; False returns the raw failing check or verified residual."""
    kwargs = {} if selection == "omitted" else {"baseline": selection}
    result = getattr(api, entrypoint)(
        paths=[str(configured_baseline.parent / "example.py")],
        tools="ruff",
        output_format="json",
        yes=True,
        **kwargs,
    )

    # Uncertified format residuals remain failing even when acknowledged by
    # a check baseline; the opt-out must not weaken that existing contract.
    is_format = entrypoint.startswith("format")
    expected_count = int(is_format or selection is False)
    assert result.exit_code == expected_count
    if entrypoint.endswith("_run"):
        assert result.severity_counts.total == expected_count
        assert result.tool_results[0].baselined_count == (
            None if selection is False else int(not is_format)
        )


@pytest.mark.parametrize("selection", [None, False])
@pytest.mark.parametrize("entrypoint", ["check", "check_run", "format", "format_run"])
def test_api_missing_configured_baseline_fails_unless_explicitly_disabled(
    configured_baseline: Path,
    selection: Literal[False] | None,
    entrypoint: str,
) -> None:
    """An explicit opt-out bypasses loading without turning the default fail-open."""
    configured_baseline.unlink()
    kwargs = {
        "paths": ["example.py"],
        "tools": "ruff",
        "output_format": "json",
        "yes": True,
    }
    if selection is None:
        with pytest.raises(
            ConfigurationError,
            match="Cannot load fingerprint baseline",
        ):
            getattr(api, entrypoint)(**kwargs)
    else:
        result = getattr(api, entrypoint)(**kwargs, baseline=False)
        assert result.exit_code == 1
        if entrypoint.endswith("_run"):
            assert result.severity_counts.total == 1


def test_api_does_not_implicitly_load_an_unconfigured_default_file(
    configured_baseline: Path,
) -> None:
    """A baseline file existing on disk alone never enables filtering."""
    config = configured_baseline.parent / ".lintro-config.yaml"
    config.write_text(
        config.read_text().replace("baseline: .lintro-baseline.json\n", ""),
        encoding="utf-8",
    )
    clear_config_cache()

    result = api.check_run(
        paths=["example.py"],
        tools="ruff",
        output_format="json",
        yes=True,
    )

    assert result.exit_code == 1
    assert result.severity_counts.total == 1
    assert result.tool_results[0].baselined_count is None


@pytest.mark.parametrize("baseline_state", ["valid", "missing", "malformed"])
def test_badge_ignores_configured_acknowledgements_and_baseline_loading_errors(
    configured_baseline: Path,
    baseline_state: str,
) -> None:
    """A public badge always reports measured debt, even with an unusable baseline."""
    if baseline_state == "missing":
        configured_baseline.unlink()
    elif baseline_state == "malformed":
        configured_baseline.write_text("not JSON", encoding="utf-8")

    result = CliRunner().invoke(cli, ["badge", "example.py", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["counts"]["total"] == 1
    assert payload["color"] != "brightgreen"
