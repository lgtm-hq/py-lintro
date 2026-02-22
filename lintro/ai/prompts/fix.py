"""Prompt templates for AI fix generation."""

from __future__ import annotations

FIX_SYSTEM = (
    "You are a senior software engineer fixing code quality issues. "
    "Provide minimal, targeted fixes that resolve the reported issue "
    "without changing unrelated code. "
    "Respond ONLY with the requested JSON format, no markdown fences."
)

FIX_PROMPT_TEMPLATE = """\
Tool: {tool_name}
Error code: {code}
File: {file}
Line: {line}
Issue: {message}

Here is the relevant section of the file \
(lines {context_start}-{context_end}):
```
{code_context}
```

Provide a fix for this issue. Only change what is necessary.

Respond in this exact JSON format:
{{
  "original_code": "the exact lines that need to change \
(copy from above)",
  "suggested_code": "the corrected version of those lines",
  "explanation": "Imperative fix description \
(e.g. 'Add docstring for X')",
  "confidence": "high|medium|low",
  "risk_level": "safe-style|behavioral-risk"
}}

Risk level guidelines:
- "safe-style": whitespace, formatting, trailing commas, quote style, \
line length — changes that ONLY affect style and cannot alter runtime behavior
- "behavioral-risk": anything that adds, removes, or changes logic, imports, \
type annotations, docstrings, variable names, or control flow
"""
