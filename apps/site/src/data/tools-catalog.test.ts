import snapshot from '../generated/product-snapshot.json';
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
  it('retains every registered integration and normalizes names for routes', () => {
    expect(TOOLS.map((tool) => tool.name)).toEqual(
      snapshot.tools.map((tool) => tool.name.replaceAll('_', '-'))
    );
    expect(TOOL_COUNT).toBe(snapshot.tools.length);
    expect(TOOLS.find((tool) => tool.name === 'idiom-review')?.category).toBe('ai');
  });

  it('preserves declared fix capability when categorizing tools', () => {
    expect(FIXER_COUNT).toBe(TOOLS.filter((t) => t.fixes).length);
    expect(FIXER_COUNT).toBe(snapshot.tools.filter((tool) => tool.fixes).length);
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
