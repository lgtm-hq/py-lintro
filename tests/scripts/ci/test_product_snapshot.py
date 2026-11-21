"""Exercise source parsing and the frozen product-data generation contract."""

from __future__ import annotations

import ast
import importlib.util
import json
import re
from pathlib import Path
from typing import Any

import click
import pytest
from assertpy import assert_that

SCRIPT = Path(__file__).resolve().parents[3] / "scripts/ci/site/product-snapshot.py"


@pytest.fixture
def snapshot() -> Any:
    """Load the generator without running product code or its main function."""
    spec = importlib.util.spec_from_file_location("product_snapshot", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load product snapshot generator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_frozen_source_follows_the_product_source_variable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """LINTRO_PRODUCT_SOURCE selects the frozen source; unset, the repository is the source."""
    spec = importlib.util.spec_from_file_location("product_snapshot_env", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load product snapshot generator")
    (tmp_path / "marker.txt").write_text("frozen", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nversion = "1.2.3"\n',
        encoding="utf-8",
    )

    monkeypatch.setenv("LINTRO_PRODUCT_SOURCE", str(tmp_path))
    selected = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(selected)

    monkeypatch.delenv("LINTRO_PRODUCT_SOURCE")
    default = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(default)

    assert_that(selected.BASELINE).is_equal_to(tmp_path)
    assert_that(selected.git_read("marker.txt")).is_equal_to("frozen")
    assert_that(default.BASELINE).is_equal_to(SCRIPT.parents[3])


@pytest.fixture
def inspector() -> Any:
    """Load the public reference inspector without invoking its CLI entry point."""
    spec = importlib.util.spec_from_file_location(
        "public_cli_reference",
        SCRIPT.with_name("public_cli_reference.py"),
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load Click inspector")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_tool_metadata_resolves_constants_and_advisory_class(
    snapshot: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A constant-named advisory integration must not disappear from the count."""
    source = """
TOOL_NAME = "example-review"
def definition():
    return ToolDefinition(
        name=TOOL_NAME,
        description="An " "advisory example",
        tool_type=ToolType.LINTER,
        execution_class=ExecutionClass.ADVISORY,
        can_fix=False,
    )
"""
    monkeypatch.setattr(snapshot, "git_read", lambda path: source)
    tools = snapshot.parse_tools(["ignored.py", "lintro/tools/example/definition.py"])
    assert_that(tools).is_length(1)
    assert_that(tools[0]).contains_entry(
        name="example-review",
        advisory=True,
        fixes=False,
    )
    assert_that(tools[0]["description"]).is_equal_to("An advisory example")


def test_published_descriptions_drop_unmeasured_claims_and_keep_the_source(
    snapshot: Any,
) -> None:
    """Catalog wording carries no speed or count claim; the frozen text survives."""
    paths = [
        str(path.relative_to(snapshot.BASELINE))
        for path in sorted((snapshot.BASELINE / "lintro/tools").glob("*/definition.py"))
    ]
    tools = {tool["name"]: tool for tool in snapshot.parse_tools(paths)}

    assert_that(len(tools)).is_greater_than(40)
    offenders = {
        name: tool["description"]
        for name, tool in tools.items()
        if re.search(snapshot.UNMEASURED_CLAIM, tool["description"])
    }
    assert_that(offenders).is_empty()
    assert_that(tools["oxlint"]["sourceDescription"]).contains("661+")
    assert_that(tools["oxfmt"]["sourceDescription"]).contains("faster than Prettier")
    # A curated entry must still name a real tool, or it silently stops applying.
    assert_that(set(snapshot.READER_DESCRIPTIONS) - set(tools)).is_empty()


def test_nonliteral_values_are_never_executed(snapshot: Any) -> None:
    """Source inspection must not evaluate calls while reading help metadata."""
    call = ast.parse("dangerous_call()", mode="eval").body
    assert_that(snapshot.literal(call, "fallback")).is_equal_to("fallback")
    assert_that(snapshot.literal(None)).is_none()


def test_public_metadata_controls_visibility_per_command(inspector: Any) -> None:
    """Identical option spellings follow each command's actual visibility."""

    @click.group()
    def root() -> None:
        """Public root."""

    @root.command()
    @click.option("--no-log", hidden=True)
    @click.option("--mode", type=click.Choice(["safe", "full"]), default="safe")
    def check(**kwargs: object) -> None:
        """Check a fixture."""
        raise RuntimeError("Callbacks must never run")

    @root.command()
    @click.option("--no-log", is_flag=True)
    @click.option("--stream/--no-stream", default=False)
    def format(**kwargs: object) -> None:
        """Format a fixture."""
        raise RuntimeError("Callbacks must never run")

    tree = inspector.command_tree(root, Path(__file__).parent)
    check_ref = next(c for c in tree if c["name"] == "check")
    format_ref = next(c for c in tree if c["name"] == "format")
    assert_that([p["flags"] for p in check_ref["options"]]).does_not_contain(
        ["--no-log"],
    )
    mode = next(p for p in check_ref["options"] if "--mode" in p["flags"])
    assert_that(mode["choices"]).is_equal_to(["safe", "full"])
    assert_that(mode["default"]).is_equal_to('"safe"')
    assert_that(format_ref["help"]).contains("--no-log", "--stream / --no-stream")
    assert_that([p["flags"] for p in format_ref["options"]]).contains(
        ["--stream", "--no-stream"],
    )
    unresolved = click.Option(
        ["--dynamic"],
        default=lambda: (_ for _ in ()).throw(RuntimeError("Never resolve")),
    )
    assert_that(inspector.declared_default(unresolved, click.Context(root))).contains(
        "Computed at invocation",
    )


def test_tool_reference_preserves_nondefault_and_inherited_options(
    inspector: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reference metadata must retain supported setters absent from defaults."""

    def forbid_process(*args: object, **kwargs: object) -> None:
        """Fail if metadata inspection attempts to invoke a native command."""
        raise RuntimeError("Metadata inspection must not execute a command")

    monkeypatch.setenv("LINTRO_ENABLE_EXTERNAL_PLUGINS", "0")
    monkeypatch.setattr("subprocess.Popen", forbid_process)
    tools = inspector.tool_details(SCRIPT.parents[3])
    oxfmt = tools["oxfmt"]
    options = {option["name"]: option for option in oxfmt["options"]}
    assert_that(options).contains_key("config", "ignore_path")
    assert_that(oxfmt["defaults"]).does_not_contain_key("config", "ignore_path")
    assert_that(options["config"]["default"]).is_none()
    assert_that(options["config"]["description"]).contains("--config")
    assert_that(options).does_not_contain_key("self", "kwargs")
    for name in ("tsc", "vue-tsc"):
        inherited = tools[name]
        assert_that(inherited["optionSource"]).is_equal_to(
            "lintro/tools/ts_checker/base.py",
        )
        assert_that([option["name"] for option in inherited["options"]]).contains(
            "project",
            "strict",
            "use_project_files",
        )


@pytest.mark.parametrize(
    ("package", "message"),
    [
        ("Stray-Dir", "Unexpected tool definition module"),
        ("ghost_tool", "Tool definition modules did not load"),
    ],
)
def test_tool_reference_fails_closed_on_unexpected_or_unloaded_definitions(
    inspector: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    package: str,
    message: str,
) -> None:
    """A definition the product discovery did not load must stop generation.

    Built-in discovery logs and skips a module it cannot import, so a silent
    skip would publish a reference with a tool missing.
    """
    definition = tmp_path / "lintro/tools" / package / "definition.py"
    definition.parent.mkdir(parents=True)
    definition.write_text("", encoding="utf-8")
    monkeypatch.setenv("LINTRO_ENABLE_EXTERNAL_PLUGINS", "0")

    with pytest.raises(RuntimeError) as raised:
        inspector.tool_details(tmp_path)

    assert_that(str(raised.value)).contains(message, package)


def test_frozen_generation_produces_linkable_sources_without_command_execution(
    snapshot: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The extracted frozen source includes CLI and advisory metadata."""
    dest = tmp_path / "product.json"
    monkeypatch.setattr(snapshot, "DEST", dest)
    snapshot.main()
    payload = json.loads(dest.read_text(encoding="utf-8"))
    assert_that(payload["version"]).matches(r"^\d+\.\d+\.\d+$")
    names = [tool["name"] for tool in payload["tools"]]
    assert_that(names).contains(
        "checkov",
        "cppcheck",
        "pylint",
        "import-linter",
        "idiom-review",
    )
    assert_that(len(set(names))).is_equal_to(len(names))
    assert_that(
        all(tool["source"] and tool["line"] > 0 for tool in payload["tools"]),
    ).is_true()
    for tool in payload["tools"]:
        minimum = tool["minimumVersion"]
        if minimum is not None:
            assert_that(minimum).matches(r"^\d+(\.\d+)+$")
        assert_that(tool).contains_key(
            "patterns",
            "nativeConfigs",
            "options",
            "defaults",
        )
        assert_that(tool["optionSource"]).ends_with(".py")
    ai = payload["aiConfig"]
    assert_that(set(ai["defaults"])).is_equal_to(set(ai["schema"]["properties"]))
    assert_that(ai["schema"]["additionalProperties"]).is_false()
    assert_that(ai["providers"]).contains_key("anthropic", "cursor", "openai")
    assert_that(ai["legacyAliases"]).contains_entry(
        cli_bare="providers.anthropic.cli_bare",
        cursor_trust_workspace="providers.cursor.trust_workspace",
    )
    transport = ai["schema"]["$defs"]["CliTransportProfile"]
    assert_that(transport["additionalProperties"]).is_false()
    assert_that(transport["properties"]["max_turns"]["anyOf"][0]).contains_entry(
        type="integer",
        minimum=1,
    )


def test_frozen_public_reference_covers_factories_and_real_visibility(
    snapshot: Any,
) -> None:
    """The frozen release expands nested factories and per-command public flags."""
    commands = {command["name"]: command for command in snapshot.parse_commands([])}
    assert_that(commands[""]["help"]).contains("check", "--version")
    assert_that(commands).contains_key(
        "baseline create",
        "baseline update",
        "baseline prune",
        "config init",
        "config show",
        "config validate",
    )
    assert_that(commands["check"]["aliases"]).contains("chk", "lint")
    assert_that(commands["format"]["help"]).contains(
        "--no-log",
        "--stream / --no-stream",
    )
    assert_that(commands["check"]["help"]).does_not_contain("--no-log", "--no-stream")
    output = next(
        p for p in commands["check"]["options"] if "--output-format" in p["flags"]
    )
    assert_that(output["choices"]).contains("json", "sarif")
    assert_that(output["default"]).is_equal_to('"grid"')
    assert_that(commands["baseline create"]["help"]).contains("--tools", "--baseline")
