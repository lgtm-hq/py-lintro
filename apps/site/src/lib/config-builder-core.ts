export const CONFIG_GROUPS = [
  {
    id: 'python',
    label: 'Python',
    tools: ['ruff', 'black', 'mypy', 'bandit', 'pydoclint'],
    selected: true,
  },
  {
    id: 'web',
    label: 'JavaScript / TypeScript',
    tools: ['oxlint', 'oxfmt', 'prettier', 'tsc'],
    selected: true,
  },
  { id: 'shell', label: 'Shell', tools: ['shellcheck', 'shfmt'], selected: false },
  {
    id: 'rust',
    label: 'Rust',
    tools: ['clippy', 'rustfmt', 'cargo-audit', 'cargo-deny'],
    selected: false,
  },
  { id: 'go', label: 'Go', tools: ['golangci-lint'], selected: false },
  {
    id: 'config',
    label: 'YAML / TOML / .env',
    tools: ['yamllint', 'taplo', 'dotenv-linter'],
    selected: true,
  },
  { id: 'prose', label: 'Markdown / prose', tools: ['markdownlint', 'vale'], selected: false },
  {
    id: 'security',
    label: 'Secrets / dependencies',
    tools: ['gitleaks', 'pip-audit', 'osv-scanner'],
    selected: true,
  },
  {
    id: 'infra',
    label: 'Docker / Actions / SQL',
    tools: ['hadolint', 'actionlint', 'sqlfluff'],
    selected: false,
  },
] as const;

export interface BuilderOptions {
  groups: readonly string[];
  lineLength: number;
  aiEnabled: boolean;
  summary: boolean;
  review: boolean;
  provider: 'anthropic' | 'openai' | 'cursor';
  transport: 'api' | 'cli';
  maxCost: number;
}

export const DEFAULT_BUILDER: BuilderOptions = {
  groups: CONFIG_GROUPS.filter((group) => group.selected).map((group) => group.id),
  lineLength: 88,
  aiEnabled: false,
  summary: true,
  review: true,
  provider: 'anthropic',
  transport: 'api',
  maxCost: 2,
};

/** Emit only supported orchestration settings; native tool rules remain native. */
export function buildConfigurationWithTools(
  options: BuilderOptions,
  availableToolNames: readonly string[]
): string {
  if (!Number.isInteger(options.lineLength) || options.lineLength < 1 || options.lineLength > 500) {
    throw new Error('Enter a whole line length from 1 to 500.');
  }
  if (!Number.isFinite(options.maxCost) || options.maxCost <= 0) {
    throw new Error('Enter a positive API cost cap.');
  }
  const available = new Set(availableToolNames);
  const selected = new Set(
    CONFIG_GROUPS.filter((group) => options.groups.includes(group.id))
      .flatMap((group) => [...group.tools])
      .filter((tool) => available.has(tool))
  );
  if (selected.size === 0) throw new Error('Select at least one tool group.');
  const output = [
    '# Generated starter. Keep credentials out of this file.',
    'execution:',
    '  enabled_tools: [' + [...selected].join(', ') + ']',
  ];
  if (selected.has('ruff') || selected.has('black')) {
    output.push(
      '',
      '# Shared width is passed to Ruff and Black only.',
      'enforce:',
      '  line_length: ' + options.lineLength
    );
  }
  output.push('', 'ai:', '  enabled: ' + options.aiEnabled);
  if (options.aiEnabled) {
    output.push(
      '  provider: ' + options.provider,
      '  transport: ' + options.transport,
      '  lint: ' + options.summary,
      '  review: ' + options.review
    );
    if (options.transport === 'api') {
      output.push('  max_cost_usd: ' + options.maxCost.toFixed(2));
    }
  }
  return output.join('\n') + '\n';
}
