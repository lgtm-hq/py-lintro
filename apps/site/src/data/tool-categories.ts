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

export const TOOL_PURPOSE_LABELS: Record<ToolCategory, string> = {
  lint: 'Lint',
  fmt: 'Format',
  both: 'Lint + format',
  type: 'Type check',
  sec: 'Security',
  test: 'Test runner',
  ai: 'AI advisory',
};

export interface CatalogTool {
  name: string;
  registeredName: string;
  target: string;
  category: ToolCategory;
  fixes: boolean;
  source: string;
  line: number;
  patterns: readonly string[];
  nativeConfigs: readonly string[];
}
