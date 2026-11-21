"""Derive website reference facts from the repository product source.

Read tool syntax and introspect public Click help in an isolated source copy.
No command callbacks or provider workflows run.
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess  # nosec B404 - read-only Git subprocesses, fixed argv, no shell
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[3]
# An explicit source override supports release fixtures; normal builds use this repo.
BASELINE = Path(os.environ.get("LINTRO_PRODUCT_SOURCE", str(ROOT))).resolve()
PRODUCT_REF = (
    "v"
    + tomllib.loads(
        (BASELINE / "pyproject.toml").read_text(encoding="utf-8"),
    )[
        "project"
    ]["version"]
)
DEST = ROOT / "apps/site/src/generated/product-snapshot.json"
# Reader wording for definitions whose frozen description makes a claim about the
# wrapped tool that Lintro neither measures nor consumes: a speed adjective or
# comparison, a rule/linter/credential count, a false-positive rate. Those drift with
# the installed upstream release. The frozen text stays in ``sourceDescription``.
READER_DESCRIPTIONS = {
    "dotenv_linter": (
        "Linter for .env files that detects duplicate keys, lowercase keys, "
        "and formatting issues (with auto-fix)"
    ),
    "golangci_lint": (
        "Go meta-linter that runs errcheck, staticcheck, ineffassign, govet "
        "and other linters"
    ),
    "oxfmt": "JavaScript/TypeScript formatter",
    "oxlint": "JavaScript/TypeScript linter",
    "prettier": (
        "Formatter for CSS, HTML, JSON, YAML, Markdown, GraphQL and Astro; "
        "Lintro assigns JavaScript/TypeScript/Vue formatting to Oxfmt"
    ),
    "ruff": "Python linter and formatter",
    "semgrep": (
        "Open-source static analysis tool for finding bugs and enforcing "
        "code standards"
    ),
    "stylelint": "CSS/SCSS/Sass/Less linter and fixer",
    "trufflehog": (
        "Secrets scanner that detects API keys, tokens and other credentials "
        "via regex and entropy analysis"
    ),
    "typos": "Source-code spell checker that finds and corrects typos",
}
# What a published description may not assert about a wrapped tool.
UNMEASURED_CLAIM = (
    r"(?i)\bfast(er|est)?\b|\d+\s*\+|\d+\s*x\b|false-positive|better performance"
)


def git_read(path: str) -> str:
    """Read one UTF-8 source file from the extracted frozen source.

    Args:
        path: Repository-relative source path.

    Returns:
        Frozen source text; the extracted directory has no Git metadata.
    """
    return (BASELINE / path).read_text(encoding="utf-8")


def literal(node: ast.AST | None, default: object = None) -> object:
    """Extract literal syntax without evaluating expressions.

    Args:
        node: Optional Python AST node.
        default: Fallback for expressions that are not literals.

    Returns:
        Literal value or the fallback.
    """
    try:
        return ast.literal_eval(node) if node else default
    except (ValueError, TypeError):
        return default


def parse_tools(paths: list[str]) -> list[dict[str, object]]:
    """Extract registered tool definitions from source syntax.

    Args:
        paths: Frozen repository paths.

    Returns:
        Alphabetically sorted tool metadata with source locations.
    """
    tools: list[dict[str, object]] = []
    for path in paths:
        if not path.startswith("lintro/tools/") or not path.endswith("/definition.py"):
            continue
        tree = ast.parse(git_read(path))
        constants = {
            node.targets[0].id: literal(node.value)
            for node in tree.body
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
        }
        for node in ast.walk(tree):
            if (
                not isinstance(node, ast.Call)
                or ast.unparse(node.func) != "ToolDefinition"
            ):
                continue
            fields = {kw.arg: kw.value for kw in node.keywords}
            name_node = fields.get("name")
            name = (
                constants.get(name_node.id)
                if isinstance(name_node, ast.Name)
                else literal(name_node)
            )
            if not isinstance(name, str):
                continue
            source_description = literal(fields.get("description"), name)
            tools.append(
                {
                    "name": name,
                    "description": READER_DESCRIPTIONS.get(name, source_description),
                    "sourceDescription": source_description,
                    "fixes": literal(fields.get("can_fix"), False),
                    "types": (
                        ast.unparse(fields["tool_type"])
                        if "tool_type" in fields
                        else ""
                    ),
                    "advisory": "ADVISORY"
                    in ast.unparse(fields.get("execution_class", ast.Constant(""))),
                    "source": path,
                    "line": node.lineno,
                },
            )
    return sorted(tools, key=lambda item: str(item["name"]))


def parse_reference(paths: list[str]) -> dict[str, Any]:
    """Inspect public help and AI models in an isolated frozen archive.

    Args:
        paths: Frozen source paths (shared generator interface).

    Returns:
        Public commands and model-derived AI schema, defaults and aliases.
    """
    with tempfile.TemporaryDirectory(prefix="lintro-public-help-") as folder:
        for package in ("lintro", "lintro_build"):
            shutil.copytree(
                BASELINE / package,
                Path(folder) / package,
                ignore=shutil.ignore_patterns("__pycache__"),
            )
        generator = Path(folder) / "scripts/ci/generate-builtin-tool-index.py"
        generator.parent.mkdir(parents=True)
        shutil.copyfile(
            BASELINE / "scripts/ci/generate-builtin-tool-index.py",
            generator,
        )
        subprocess.run(  # nosec B603 - frozen build generator, fixed argv
            [
                sys.executable,
                str(Path(folder) / "scripts/ci/generate-builtin-tool-index.py"),
            ],
            cwd=folder,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        output = subprocess.check_output(  # nosec B603 - fixed inspector; no callbacks
            [
                sys.executable,
                str(Path(__file__).with_name("public_cli_reference.py")),
                folder,
            ],
            cwd=folder,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            text=True,
        )
    return cast(dict[str, Any], json.loads(output))


def parse_commands(paths: list[str]) -> list[dict[str, object]]:
    """Return public commands from the isolated frozen inspector.

    Args:
        paths: Frozen source paths (shared generator interface).

    Returns:
        Public command records without invoking command callbacks.
    """
    return cast(list[dict[str, object]], parse_reference(paths)["commands"])


def main() -> None:
    """Write reproducible product data for the static website."""
    paths = [str(path.relative_to(BASELINE)) for path in BASELINE.rglob("*.py")]
    reference = parse_reference(paths)
    tools = parse_tools(paths)
    for tool in tools:
        tool.update(reference["toolDetails"].pop(str(tool["name"])))
    reference.pop("toolDetails")
    data = {
        "ref": PRODUCT_REF,
        "version": tomllib.loads(git_read("pyproject.toml"))["project"]["version"],
        "tools": tools,
        **reference,
    }
    DEST.parent.mkdir(parents=True, exist_ok=True)
    DEST.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"Product snapshot: {data['version']}; {len(data['tools'])} tools")


if __name__ == "__main__":
    main()
