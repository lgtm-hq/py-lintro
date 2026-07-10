"""Signature extraction for the ``idiom-review`` duplication mode.

Extracts function/class signatures (plus a few body lines) from Python
source via :mod:`ast`, and renders a compact signature map across many
files for the cross-file duplication prompt. Malformed source is skipped
gracefully rather than raising, so one unparseable file never aborts a run.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

from loguru import logger

# Number of leading body lines captured per signature to give the model a
# sense of the implementation without sending whole function bodies.
_BODY_PREVIEW_LINES = 3


@dataclass(frozen=True)
class Signature:
    """A single extracted function or class signature.

    Attributes:
        file: Path of the source file.
        name: Fully qualified name (e.g. ``ClassName.method``).
        kind: ``function`` or ``class``.
        line: 1-based line where the definition starts.
        signature: The rendered ``def``/``class`` header line.
        body_preview: The first few source lines of the body.
    """

    file: str
    name: str
    kind: str
    line: int
    signature: str
    body_preview: str


def _render_args(args: ast.arguments) -> str:
    """Render a function argument list to a compact string.

    Args:
        args: The AST arguments node.

    Returns:
        A comma-separated argument name list (annotations omitted).
    """
    names: list[str] = [a.arg for a in args.posonlyargs]
    names.extend(a.arg for a in args.args)
    if args.vararg:
        names.append(f"*{args.vararg.arg}")
    names.extend(a.arg for a in args.kwonlyargs)
    if args.kwarg:
        names.append(f"**{args.kwarg.arg}")
    return ", ".join(names)


def extract_python_signatures(file_path: str, source: str) -> list[Signature]:
    """Extract function/class signatures from Python source.

    Nested functions and methods are captured with a dotted qualified name.
    Returns an empty list when the source cannot be parsed.

    Args:
        file_path: Path used to tag each signature.
        source: Raw Python source.

    Returns:
        A list of :class:`Signature` objects (possibly empty).
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError) as exc:
        logger.warning(
            "[idiom-review] Skipping unparseable file {}: {}",
            file_path,
            exc,
        )
        return []

    source_lines = source.splitlines()
    signatures: list[Signature] = []

    def _body_preview(node: ast.AST) -> str:
        start = getattr(node, "lineno", 0)
        end = getattr(node, "end_lineno", start)
        if not start:
            return ""
        snippet = source_lines[start - 1 : min(end, start - 1 + _BODY_PREVIEW_LINES)]
        return "\n".join(line.strip() for line in snippet)

    def _walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualified = f"{prefix}{child.name}"
                header = f"def {child.name}({_render_args(child.args)})"
                signatures.append(
                    Signature(
                        file=file_path,
                        name=qualified,
                        kind="function",
                        line=child.lineno,
                        signature=header,
                        body_preview=_body_preview(child),
                    ),
                )
                _walk(child, f"{qualified}.")
            elif isinstance(child, ast.ClassDef):
                qualified = f"{prefix}{child.name}"
                signatures.append(
                    Signature(
                        file=file_path,
                        name=qualified,
                        kind="class",
                        line=child.lineno,
                        signature=f"class {child.name}",
                        body_preview=_body_preview(child),
                    ),
                )
                _walk(child, f"{qualified}.")

    _walk(tree, "")
    return signatures


def render_signature_map(signatures: list[Signature]) -> str:
    """Render signatures into a compact text map for the duplication prompt.

    Args:
        signatures: Signatures collected across the scoped files.

    Returns:
        A newline-delimited signature map string.
    """
    blocks: list[str] = []
    for sig in signatures:
        block = (
            f"# {sig.file}:{sig.line} ({sig.kind})\n"
            f"{sig.signature}\n"
            f"{sig.body_preview}"
        )
        blocks.append(block.rstrip())
    return "\n\n".join(blocks)
