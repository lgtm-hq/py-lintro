import snapshot from '../generated/product-snapshot.json';
import {
  TOOL_CATEGORIES,
  TOOL_CATEGORY_LABELS,
  TOOL_PURPOSE_LABELS,
  type CatalogTool,
  type ToolCategory,
} from './tool-categories';

export {
  TOOL_CATEGORIES,
  TOOL_CATEGORY_LABELS,
  TOOL_PURPOSE_LABELS,
  type CatalogTool,
  type ToolCategory,
};

function category(types: string, advisory: boolean): ToolCategory {
  if (advisory) return 'ai';
  if (types.includes('TYPE_CHECKER')) return 'type';
  if (types.includes('SECURITY')) return 'sec';
  if (types.includes('TEST_RUNNER')) return 'test';
  if (types.includes('FORMATTER')) return types.includes('LINTER') ? 'both' : 'fmt';
  return 'lint';
}

export const TOOLS: readonly CatalogTool[] = snapshot.tools.map((item) => ({
  name: item.name.replaceAll('_', '-'),
  registeredName: item.name,
  target: item.description,
  category: category(item.types, item.advisory),
  fixes: item.fixes,
  source: item.source,
  line: item.line,
  patterns: item.patterns,
  nativeConfigs: item.nativeConfigs,
}));
export const TOOL_COUNT = TOOLS.length;
export const FIXER_COUNT = TOOLS.filter((item) => item.fixes).length;
export function toolsInCategory(value: ToolCategory): CatalogTool[] {
  return TOOLS.filter((item) => item.category === value);
}
