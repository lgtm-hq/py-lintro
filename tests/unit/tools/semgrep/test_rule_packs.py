"""Pack configuration reaches Semgrep without changing its default rules."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from assertpy import assert_that
from click.testing import CliRunner
from pydantic import ValidationError

from lintro.cli_utils.commands.list_tools import list_tools_command
from lintro.config.config_loader import load_config
from lintro.config.config_validator import validate_config_file
from lintro.config.lintro_config import LintroConfig
from lintro.config.tool_config import LintroToolConfig
from lintro.enums.action import Action
from lintro.exceptions.errors import ConfigurationError
from lintro.plugins.subprocess_executor import SubprocessResult
from lintro.rules import available_rule_packs, resolve_rule_pack
from lintro.tools.semgrep.definition import SemgrepPlugin
from lintro.utils.execution.tool_configuration import configure_tool_for_execution
from lintro.utils.unified_config import UnifiedConfigManager

PACK = "python/none-coercion"


@pytest.mark.parametrize("pack", available_rule_packs())
@pytest.mark.parametrize("enabled", [False, True], ids=["disabled", "enabled"])
def test_only_shipped_rule_ids_are_restored_before_parsing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semgrep_plugin: SemgrepPlugin,
    pack: str,
    enabled: bool,
) -> None:
    """User metadata cannot turn arbitrary native IDs into packaged IDs.

    Args:
        tmp_path: Directory containing a discoverable scan input.
        monkeypatch: Replace only the engine report boundary.
        semgrep_plugin: Plugin with version probing bypassed.
        pack: Shipped pack whose actual YAML defines the positive controls.
        enabled: Whether the pack is selected for this scan.
    """
    source = tmp_path / "scan.py"
    source.write_text("value = 1\n", encoding="utf-8")
    rules = yaml.safe_load(resolve_rule_pack(pack).read_text(encoding="utf-8"))["rules"]
    shipped_ids = [rule["id"] for rule in rules]
    namespace = "lintro." + pack.replace("/", ".") + "."
    marked = {"lintro_rule_pack": pack}
    cases = [
        (f"installed.rules.{rule_id}", marked, rule_id) for rule_id in shipped_ids
    ] + [(rule_id, marked, rule_id) for rule_id in shipped_ids]
    native_ids = [
        (f"custom.{namespace}user-rule", marked),
        (f"custom.{shipped_ids[0]}.user-extension", marked),
        (f"custom.{shipped_ids[0]}", {}),
        (f"custom.{shipped_ids[0]}", {"lintro_rule_pack": "python/unknown"}),
        (
            f"custom.{shipped_ids[0]}",
            (
                {"lintro_rule_pack": "scripts/no-direct-formatter"}
                if pack == PACK
                else {"lintro_rule_pack": PACK}
            ),
        ),
        (f"custom{shipped_ids[0]}", marked),
        ("custom.native-rule", marked),
    ]
    cases.extend((rule_id, metadata, rule_id) for rule_id, metadata in native_ids)
    report = json.dumps(
        {
            "results": [
                {
                    "check_id": reported,
                    "path": str(source),
                    "start": {"line": line, "col": 1},
                    "extra": {"message": "ID control", "metadata": metadata},
                }
                for line, (reported, metadata, _expected) in enumerate(cases, 1)
            ],
            "errors": [],
        },
    )
    monkeypatch.setattr(
        semgrep_plugin,
        "_run_subprocess_result",
        lambda **kwargs: SubprocessResult(0, report, "", report),
    )
    result = semgrep_plugin.check(
        [str(source)],
        {"rule_packs": [pack] if enabled else []},
    )

    assert_that(result.success).is_true()
    assert_that(result.parse_failures_count or 0).is_zero()
    assert_that(result.issues_count).is_equal_to(len(cases))
    assert_that(result.issues).extracting("check_id").is_equal_to(
        [expected if enabled else reported for reported, _metadata, expected in cases],
    )


@pytest.mark.parametrize("native", [".semgrep.yaml", ".semgrep.yml", ".semgrep/"])
def test_pack_augments_native_config(tmp_path: Path, native: str) -> None:
    """Pack-only configuration keeps local rules and avoids registry access.

    Args:
        tmp_path: Project root containing a native configuration.
        native: Supported native Semgrep configuration location.
    """
    config = tmp_path / native
    if native.endswith("/"):
        config.mkdir()
    else:
        config.write_text("rules: []\n", encoding="utf-8")
    plugin = SemgrepPlugin()
    plugin.set_options(rule_packs=[PACK, PACK])
    cmd = plugin._build_check_command(["example.py"], cwd=str(tmp_path))
    configs = [cmd[i + 1] for i, arg in enumerate(cmd) if arg == "--config"]

    assert_that(configs).is_length(2).does_not_contain("auto")
    assert_that(Path(configs[0])).is_equal_to(config)
    assert_that(Path(configs[1]).is_file()).is_true()
    assert_that(Path(configs[1]).as_posix()).ends_with(f"rules/{PACK}.yaml")


def test_pack_can_run_alone_or_with_explicit_config(tmp_path: Path) -> None:
    """Enabling a pack is independent of native or remote rule configurations.

    Args:
        tmp_path: Empty project root.
    """
    plugin = SemgrepPlugin()
    assert_that(plugin.options["rule_packs"]).is_empty()
    plugin.set_options(rule_packs=[PACK])
    cmd = plugin._build_check_command(["sample.py"], cwd=str(tmp_path))
    assert_that(cmd.count("--config")).is_equal_to(1)
    assert_that(cmd).does_not_contain("auto")

    plugin.set_options(config="custom.yaml")
    cmd = plugin._build_check_command(["sample.py"], cwd=str(tmp_path))
    assert_that(cmd.count("--config")).is_equal_to(2)
    assert_that(cmd[cmd.index("--config") + 1]).is_equal_to("custom.yaml")


def test_explicit_auto_survives_pack_selection_and_copy(tmp_path: Path) -> None:
    """An explicit registry choice survives later options and execution copies.

    Args:
        tmp_path: Empty scan project root.
    """
    plugin = SemgrepPlugin()
    plugin.set_options(config="auto")
    plugin.set_options(rule_packs=[PACK])
    copied = plugin.copy_for_execution()
    copied.set_options(jobs=1)
    cmd = copied._build_check_command(["sample.py"], cwd=str(tmp_path))
    configs = [cmd[i + 1] for i, arg in enumerate(cmd) if arg == "--config"]
    assert_that(configs).is_length(2).contains("auto")

    copied.reset_options()
    copied.set_options(rule_packs=[PACK])
    reset_cmd = copied._build_check_command(["sample.py"], cwd=str(tmp_path))
    assert_that(reset_cmd.count("--config")).is_equal_to(1)
    assert_that(reset_cmd).does_not_contain("auto")


@pytest.mark.parametrize("source", ["pyproject", "cli"])
def test_explicit_auto_reaches_execution_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: str,
) -> None:
    """Execution configuration retains auto from both public option sources.

    Args:
        tmp_path: Project containing an explicit Semgrep registry setting.
        monkeypatch: Isolate project configuration discovery.
        source: Supply config through pyproject or CLI overrides.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        '[tool.lintro.semgrep]\nconfig = "auto"\n' if source == "pyproject" else "",
        encoding="utf-8",
    )
    configured = configure_tool_for_execution(
        tool=SemgrepPlugin(),
        tool_name="semgrep",
        config_manager=UnifiedConfigManager(),
        tool_option_dict={"semgrep": {"config": "auto"}} if source == "cli" else {},
        exclude=None,
        include_venv=False,
        incremental=False,
        action=Action.CHECK,
        selected_tools={"semgrep"},
        lintro_config=LintroConfig.model_validate(
            {"tools": {"semgrep": {"rule_packs": [PACK]}}},
        ),
    )
    if not isinstance(configured, SemgrepPlugin):
        pytest.fail("Semgrep execution setup must retain the plugin type")
    cmd = configured._build_check_command(["sample.py"], cwd=str(tmp_path))
    assert_that(cmd.count("--config")).is_equal_to(2)
    assert_that(cmd[cmd.index("--config") + 1]).is_equal_to("auto")


@pytest.mark.parametrize("toml", [False, True], ids=["yaml", "pyproject"])
def test_non_semgrep_packs_are_rejected_by_loader(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    toml: bool,
) -> None:
    """Unsupported tool packs fail both loading and configuration diagnostics.

    Args:
        tmp_path: Project configuration directory.
        monkeypatch: Isolate pyproject fallback discovery.
        toml: Whether to configure the unsupported tool through TOML.
    """
    monkeypatch.chdir(tmp_path)
    path = tmp_path / ("pyproject.toml" if toml else ".lintro-config.yaml")
    path.write_text(
        (
            f'[tool.lintro.ruff]\nrule_packs = ["{PACK}"]\n'
            if toml
            else f"tools:\n  ruff:\n    rule_packs: [{PACK}]\n"
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("lintro.config.config_loader._find_config_file", lambda: None)
    with pytest.raises(ConfigurationError, match=r"ruff.*rule_packs.*[Ss]emgrep"):
        load_config(None if toml else path)
    assert_that(validate_config_file(path).is_valid).is_false()


@pytest.mark.parametrize("tool", ["ruff", "RUFF", "unknown-tool"])
@pytest.mark.parametrize("model_instance", [False, True], ids=["mapping", "model"])
def test_direct_model_rejects_unsupported_packs(
    tool: str,
    model_instance: bool,
) -> None:
    """Direct root validation rejects packs even with prevalidated tool models.

    Args:
        tool: Unsupported tool name, including case variants.
        model_instance: Whether the nested tool config was already validated.
    """
    selection = (
        LintroToolConfig(rule_packs=[PACK])
        if model_instance
        else {"rule_packs": [PACK]}
    )
    with pytest.raises(ValidationError, match=r"rule_packs.*[Ss]emgrep"):
        LintroConfig.model_validate(
            {"tools": {tool: selection}},
        )


@pytest.mark.parametrize(
    "tool,packs",
    [("ruff", []), ("semgrep", [PACK]), ("SEMGREP", [PACK])],
)
def test_direct_model_accepts_supported_pack_selections(
    tool: str,
    packs: list[str],
) -> None:
    """Empty selections remain valid everywhere and Semgrep accepts named packs.

    Args:
        tool: Tool name to validate.
        packs: Valid selection for this tool.
    """
    config = LintroConfig.model_validate({"tools": {tool: {"rule_packs": packs}}})
    assert_that(config.get_tool_config(tool).rule_packs).is_equal_to(packs)


@pytest.mark.parametrize(
    "packs",
    [["python/missing"], ["../../etc/passwd"], "python/none-coercion", [42], [""]],
)
def test_bad_pack_options_raise_configuration_error(packs: object) -> None:
    """Invalid options fail before a matcher subprocess is started.

    Args:
        packs: Unknown pack name or invalid option type.
    """
    plugin = SemgrepPlugin()
    with pytest.raises(ConfigurationError, match="rule pack|rule_packs"):
        plugin.set_options(rule_packs=packs)
    assert_that(plugin.options["rule_packs"]).is_empty()


@pytest.mark.parametrize("toml", [False, True], ids=["yaml", "pyproject"])
def test_pack_config_round_trip_and_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    toml: bool,
) -> None:
    """Both configuration formats retain packs through execution setup.

    Args:
        tmp_path: Isolated project root.
        monkeypatch: Change the configuration discovery directory.
        toml: Whether to use a pyproject tool table instead of YAML.
    """
    monkeypatch.chdir(tmp_path)
    path = tmp_path / ("pyproject.toml" if toml else ".lintro-config.yaml")
    contents = (
        f'[tool.lintro.semgrep]\nrule_packs = ["{PACK}"]\n'
        if toml
        else f"tools:\n  semgrep:\n    rule_packs: [{PACK}]\n"
    )
    path.write_text(contents, encoding="utf-8")
    if toml:
        # Keep the enclosing checkout's YAML config out of fallback discovery.
        monkeypatch.setattr(
            "lintro.config.config_loader._find_config_file",
            lambda: None,
        )
    config = load_config(None if toml else path)
    restored = LintroConfig.model_validate(config.model_dump())
    assert_that(restored.get_tool_config("semgrep").rule_packs).is_equal_to([PACK])
    validation = validate_config_file(path)
    assert_that(validation.errors).is_empty()
    assert_that(validation.warnings).is_empty()

    template = SemgrepPlugin()
    configured = configure_tool_for_execution(
        tool=template,
        tool_name="semgrep",
        config_manager=UnifiedConfigManager(),
        tool_option_dict={},
        exclude=None,
        include_venv=False,
        incremental=False,
        action=Action.CHECK,
        selected_tools={"semgrep"},
        lintro_config=config,
    )
    assert_that(configured.options["rule_packs"]).is_equal_to([PACK])
    assert_that(template.options["rule_packs"]).is_empty()


@pytest.mark.parametrize("packs", [["python/typo"], None, "python/none-coercion"])
def test_invalid_pack_in_yaml_is_rejected(tmp_path: Path, packs: object) -> None:
    """The loader and validator reject bad packs instead of dropping the key.

    Args:
        tmp_path: Project root for the configuration.
        packs: Invalid YAML pack value, encoded with JSON's YAML-compatible syntax.
    """
    path = tmp_path / ".lintro-config.yaml"
    path.write_text(
        f"tools:\n  semgrep:\n    rule_packs: {json.dumps(packs)}\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match="rule pack|rule_packs"):
        load_config(path)
    assert_that(validate_config_file(path).is_valid).is_false()


@pytest.mark.parametrize("json_output", [False, True], ids=["text", "json"])
@pytest.mark.parametrize("verbose", [False, True], ids=["normal", "verbose"])
def test_rule_pack_listing(json_output: bool, verbose: bool, tmp_path: Path) -> None:
    """Packs can be discovered without running the Semgrep binary.

    Args:
        json_output: Select machine-readable output.
        verbose: Tool details must not affect the pack listing.
        tmp_path: Destination for the listing's file output.
    """
    output = tmp_path / "packs.txt"
    result = CliRunner().invoke(
        list_tools_command,
        ["--rules", "--output", str(output)]
        + (["--json"] if json_output else [])
        + (["--verbose"] if verbose else []),
    )
    assert_that(result.exit_code).is_zero()
    assert_that(result.output).contains(PACK)
    assert_that(output.read_text(encoding="utf-8")).is_equal_to(result.output)
    if json_output:
        assert_that(json.loads(result.output)[PACK]).is_equal_to(
            {"engine": "semgrep", "advisory": True},
        )
    else:
        assert_that(result.output).contains("semgrep", "advisory")


def test_runtime_pack_option_errors_before_empty_scan() -> None:
    """A runtime override cannot hide a misspelled pack behind a no-files result."""
    with pytest.raises(ConfigurationError, match="python/typo"):
        SemgrepPlugin().check([], {"rule_packs": ["python/typo"]})


def test_pack_findings_have_no_registry_doc_url() -> None:
    """Shipped rule identifiers do not link to nonexistent registry entries."""
    assert_that(
        SemgrepPlugin().doc_url("lintro.python.none-coercion.or-fallback"),
    ).is_none()


@pytest.mark.parametrize("cli_packs", [None, [], [PACK]])
def test_pack_selection_respects_yaml_and_cli_priority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cli_packs: list[str] | None,
) -> None:
    """YAML can disable pyproject packs, while an explicit CLI list wins.

    Args:
        tmp_path: Project with conflicting YAML and pyproject settings.
        monkeypatch: Set the configuration discovery directory.
        cli_packs: Optional explicit CLI pack override.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        f'[tool.lintro.semgrep]\nrule_packs = ["{PACK}"]\n',
        encoding="utf-8",
    )
    yaml = tmp_path / ".lintro-config.yaml"
    yaml.write_text("tools:\n  semgrep:\n    rule_packs: []\n", encoding="utf-8")
    plugin = configure_tool_for_execution(
        tool=SemgrepPlugin(),
        tool_name="semgrep",
        config_manager=UnifiedConfigManager(),
        tool_option_dict=(
            {} if cli_packs is None else {"semgrep": {"rule_packs": cli_packs}}
        ),
        exclude=None,
        include_venv=False,
        incremental=False,
        action=Action.CHECK,
        selected_tools={"semgrep"},
        lintro_config=load_config(yaml),
    )
    assert_that(plugin.options["rule_packs"]).is_equal_to(
        [] if cli_packs is None else cli_packs,
    )
