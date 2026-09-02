import { describe, expect, it } from 'vitest';
import {
  FIXER_COUNT,
  TOOL_CATEGORIES,
  TOOL_CATEGORY_LABELS,
  TOOL_COUNT,
  TOOLS,
  toolsInCategory,
} from './tools-catalog';

describe('tools catalog', () => {
  it('lists all 40 tools', () => {
    expect(TOOLS).toHaveLength(40);
    expect(TOOL_COUNT).toBe(40);
  });

  it('marks exactly 15 tools as able to write fixes', () => {
    expect(FIXER_COUNT).toBe(TOOLS.filter((t) => t.fixes).length);
    expect(FIXER_COUNT).toBe(15);
  });

  it('uses unique tool names', () => {
    const names = TOOLS.map((t) => t.name);
    expect(new Set(names).size).toBe(names.length);
  });

  it('has at least one tool and a label in every category', () => {
    for (const category of TOOL_CATEGORIES) {
      expect(toolsInCategory(category).length).toBeGreaterThan(0);
      expect(TOOL_CATEGORY_LABELS[category].trim().length).toBeGreaterThan(0);
    }
  });

  it('only uses known categories', () => {
    for (const t of TOOLS) {
      expect(TOOL_CATEGORIES).toContain(t.category);
    }
  });

  it('gives every tool a non-empty target', () => {
    for (const t of TOOLS) {
      expect(t.target.trim().length).toBeGreaterThan(0);
    }
  });
});
