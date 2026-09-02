/** Example payloads and helpers shared by the homepage MCP console (server and client). */

/** Placeholder replaced with the built version by `withVersion`. */
export const VERSION_TOKEN = '__LINTRO_VERSION__';

export const MCP_TOOLS = [
  'lintro_ping',
  'lintro_check',
  'lintro_format',
  'lintro_review',
  'lintro_list_tools',
  'lintro_versions',
  'lintro_doctor',
] as const;

export type McpToolName = (typeof MCP_TOOLS)[number];

export const MCP_DEFAULT_TOOL: McpToolName = 'lintro_check';

export const MCP_RESPONSES: Record<McpToolName, string> = {
  lintro_ping: `{\n  "status": "ok",\n  "lintro_version": "${VERSION_TOKEN}",\n  "workspace": "/path/to/repo"\n}`,
  lintro_check:
    '{\n  "findings": [\n    {\n      "tool": "ruff",\n      "file": "/path/to/repo/bad.py",\n      "line": 1,\n      "column": 8,\n      "rule": "F401",\n      "severity": "WARNING",\n      "message": "`os` imported but unused",\n      "fixable": true,\n      "doc_url": "https://docs.astral.sh/ruff/rules/unused-import/"\n    }\n  ],\n  "tools": [{ "tool": "ruff", "status": "issues", "issue_count": 1, "duration": 0.47 }],\n  "summary": { "total_findings": 1, "tools_run": 1, "success": false }\n}',
  lintro_format:
    '{\n  "findings": [],\n  "tools": [\n    { "tool": "ruff", "status": "passed", "issue_count": 0, "duration": 0.66, "fixed_count": 2 }\n  ],\n  "summary": { "total_findings": 0, "tools_run": 1, "success": true },\n  "dry_run": true,\n  "changed_files": ["bad.py"],\n  "diffs": [\n    {\n      "file": "bad.py",\n      "diff": "--- a/bad.py\\n+++ b/bad.py\\n@@ -1,3 +1,2 @@\\n-import os\\n import sys\\n"\n    }\n  ]\n}',
  lintro_review:
    '{\n  "summary": "One change requested.",\n  "findings": [\n    {\n      "file": "src/db/orders.py",\n      "line": 41,\n      "severity": "P2",\n      "category": "architecture",\n      "title": "Raw SQL outside the repository layer",\n      "body": "cursor.execute is called with a string literal.\\n\\nFix: use OrdersRepository.find_open().",\n      "confidence": "high",\n      "suggested_code": "",\n      "suggestion_dropped": "",\n      "checklist_ids": [],\n      "source": "no-raw-sql"\n    }\n  ],\n  "run": {\n    "model": "claude-sonnet-4-6",\n    "provider": "anthropic",\n    "depth": 1,\n    "strictness": "balanced",\n    "cost_usd": 0.14,\n    "duration_seconds": 18.2,\n    "chunks": { "total": 2, "reviewed": 2 },\n    "files": { "reviewed": 7, "total": 7 },\n    "token_usage": { "prompt": 41200, "completion": 1800, "total": 43000 },\n    "partial": false,\n    "stopped_reason": ""\n  },\n  "budget": {\n    "requested_usd": null,\n    "configured_usd": 2.0,\n    "effective_usd": 2.0,\n    "clamped": false,\n    "exceeded": false\n  }\n}',
  lintro_list_tools:
    '{\n  "tools": [\n    {\n      "name": "ruff",\n      "description": "Fast Python linter and formatter replacing multiple tools",\n      "types": ["linter", "formatter"],\n      "languages": ["python"],\n      "installed": true,\n      "version": "0.14.2",\n      "expected_version": "0.14.2",\n      "minimum_version": "0.14.0",\n      "status": "ok",\n      "can_fix": true,\n      "capabilities": ["check", "fix"],\n      "execution_class": "deterministic",\n      "origin": "builtin",\n      "profile_membership": ["minimal", "python", "full", "complete"],\n      "install_hint": "uv pip install \'ruff>=0.14.0\'"\n    },\n    {\n      "name": "hadolint",\n      "description": "Dockerfile linter",\n      "types": ["linter"],\n      "languages": ["dockerfile"],\n      "installed": false,\n      "version": null,\n      "expected_version": "2.12.0",\n      "minimum_version": "2.12.0",\n      "status": "missing",\n      "can_fix": false,\n      "capabilities": ["check"],\n      "execution_class": "deterministic",\n      "origin": "builtin",\n      "profile_membership": ["complete", "full"],\n      "install_hint": "brew install hadolint"\n    }\n  ],\n  "profiles": [\n    { "name": "recommended", "description": "Tools for the languages in this tree", "strategy": "detect", "resolution": "workspace" }\n  ],\n  "summary": { "total": 40, "installed": 39, "missing": 1 }\n}',
  lintro_versions:
    '{\n  "tools": [\n    {\n      "name": "prettier",\n      "installed_version": "3.6.2",\n      "minimum_version": "3.0.0",\n      "recommended_version": "3.7.0",\n      "satisfies_minimum": true,\n      "below_recommended": true,\n      "status": "outdated",\n      "error": null,\n      "install_hint": "bun add -D prettier@3.7.0",\n      "binary_path": "/path/to/repo/node_modules/.bin/prettier",\n      "advisory": {\n        "tool": "prettier",\n        "installed": "3.6.2",\n        "latest_known": "3.7.0",\n        "channel": "npm",\n        "update_command": "bun add -D prettier@3.7.0"\n      }\n    }\n  ],\n  "summary": { "ok": 38, "outdated": 1, "missing": 1, "total": 40 }\n}',
  lintro_doctor:
    '{\n  "health": "degraded",\n  "checks": [\n    {\n      "check": "config.load",\n      "status": "ok",\n      "detail": "Configuration loaded from /path/to/repo/.lintro-config.yaml",\n      "remediation": "",\n      "category": "config"\n    },\n    {\n      "check": "tools.missing",\n      "status": "error",\n      "detail": "1 enabled tool(s) not installed: hadolint",\n      "remediation": "lintro install hadolint",\n      "category": "tools"\n    },\n    {\n      "check": "extras.mcp",\n      "status": "ok",\n      "detail": "mcp 2.x available",\n      "remediation": "",\n      "category": "extras"\n    }\n  ],\n  "summary": { "ok": 5, "warning": 0, "error": 1, "skipped": 1, "total": 7 }\n}',
};

export const MCP_STATUS_NOTES: Partial<Record<McpToolName, string>> = {
  lintro_format: 'dry_run: true · tree left byte-identical',
  lintro_review: 'cost_usd 0.14 of effective_usd 2.00',
  lintro_doctor: 'health: degraded · 1 error',
};

/** Substitute the version placeholder in an example payload. */
export function withVersion(payload: string, version: string): string {
  return payload.split(VERSION_TOKEN).join(version);
}

export function isMcpToolName(value: string): value is McpToolName {
  return (MCP_TOOLS as readonly string[]).includes(value);
}

export function escapeHtml(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;');
}

/** Wrap JSON keys, strings and literals in the terminal colour classes. */
export function colorJson(s: string): string {
  return escapeHtml(s)
    .replace(/"([^"\\]|\\.)*"(?=\s*:)/g, (m) => `<span class="key">${m}</span>`)
    .replace(/:\s*"([^"\\]|\\.)*"/g, (m) => {
      const i = m.indexOf('"');
      return `${m.slice(0, i)}<span class="hi">${m.slice(i)}</span>`;
    })
    .replace(/(:\s*)(true)\b/g, '$1<span class="ok">$2</span>')
    .replace(/(:\s*)(false|null)\b/g, '$1<span class="er">$2</span>');
}
