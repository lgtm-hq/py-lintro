/** The 40 tools lintro orchestrates, as shown on the homepage catalog. */

export const TOOL_CATEGORIES = ['lint', 'fmt', 'both', 'type', 'sec', 'test', 'ai'] as const;

export type ToolCategory = (typeof TOOL_CATEGORIES)[number];

export const TOOL_CATEGORY_LABELS: Record<ToolCategory, string> = {
  lint: 'Linters',
  fmt: 'Formatters',
  both: 'Lint + format',
  type: 'Type checkers',
  sec: 'Security',
  test: 'Tests',
  ai: 'AI advisory',
};

export interface CatalogTool {
  name: string;
  target: string;
  category: ToolCategory;
  fixes: boolean;
}

function tool(name: string, target: string, category: ToolCategory, fixes = false): CatalogTool {
  return { name, target, category, fixes };
}

export const TOOLS: readonly CatalogTool[] = [
  tool('actionlint', 'GitHub Actions', 'lint'),
  tool('clippy', 'Rust', 'lint', true),
  tool('commitlint', 'Git commits', 'lint'),
  tool('golangci-lint', 'Go', 'lint', true),
  tool('hadolint', 'Dockerfile', 'lint'),
  tool('html-validate', 'HTML', 'lint'),
  tool('markdownlint-cli2', 'Markdown', 'lint'),
  tool('vale', 'Prose and docs', 'lint'),
  tool('oxlint', 'JS / TS', 'lint', true),
  tool('pydoclint', 'Python docstrings', 'lint'),
  tool('shellcheck', 'Shell scripts', 'lint'),
  tool('spectral', 'OpenAPI / AsyncAPI / JSON Schema', 'lint'),
  tool('yamllint', 'YAML', 'lint'),
  tool('black', 'Python', 'fmt', true),
  tool('oxfmt', 'JS / TS', 'fmt', true),
  tool('prettier', 'JS / TS / JSON', 'fmt', true),
  tool('shfmt', 'Shell scripts', 'fmt', true),
  tool('rustfmt', 'Rust', 'fmt', true),
  tool('buf', 'Protobuf', 'both', true),
  tool('ruff', 'Python', 'both', true),
  tool('sqlfluff', 'SQL', 'both', true),
  tool('taplo', 'TOML', 'both', true),
  tool('dotenv-linter', '.env files', 'both', true),
  tool('stylelint', 'CSS / SCSS / Less', 'both', true),
  tool('typos', 'Spelling', 'both', true),
  tool('astro-check', 'Astro', 'type'),
  tool('mypy', 'Python', 'type'),
  tool('svelte-check', 'Svelte', 'type'),
  tool('tsc', 'TypeScript', 'type'),
  tool('vue-tsc', 'Vue', 'type'),
  tool('bandit', 'Python security', 'sec'),
  tool('gitleaks', 'Secret detection', 'sec'),
  tool('trufflehog', 'Secret detection', 'sec'),
  tool('semgrep', 'SAST, many languages', 'sec'),
  tool('cargo-audit', 'Rust dependencies', 'sec'),
  tool('cargo-deny', 'Rust licenses and advisories', 'sec'),
  tool('osv-scanner', 'Dependency vulnerabilities', 'sec'),
  tool('pip-audit', 'Python dependencies', 'sec'),
  tool('pytest', 'Python tests', 'test'),
  tool('idiom-review', 'AI finder · runs under review', 'ai'),
];

export const TOOL_COUNT = TOOLS.length;

export const FIXER_COUNT = TOOLS.filter((t) => t.fixes).length;

export function toolsInCategory(category: ToolCategory): CatalogTool[] {
  return TOOLS.filter((t) => t.category === category);
}
