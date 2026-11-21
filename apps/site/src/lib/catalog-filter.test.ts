import { describe, expect, it } from 'vitest';
import { TOOLS } from '../data/tools-catalog';
import { matchesCatalog } from './catalog-filter';

describe('catalog filtering', () => {
  it('finds a tool by purpose regardless of casing or surrounding whitespace', () => {
    const tools = TOOLS.filter((tool) =>
      matchesCatalog(tool, { category: 'all', query: '  Dockerfile ', fixOnly: false })
    );
    expect(tools.map((tool) => tool.name)).toContain('hadolint');
  });
  it.each(['lint', 'fmt'] as const)('includes dual-purpose tools in %s results', (category) => {
    const tools = TOOLS.filter((tool) =>
      matchesCatalog(tool, { category, query: 'ruff', fixOnly: true })
    );
    expect(tools.map((tool) => tool.name)).toContain('ruff');
  });
  it('combines category, query and declared fixing support', () => {
    expect(
      TOOLS.filter((tool) =>
        matchesCatalog(tool, { category: 'sec', query: 'bandit', fixOnly: true })
      )
    ).toEqual([]);
    expect(
      TOOLS.filter((tool) =>
        matchesCatalog(tool, { category: 'fmt', query: 'bandit', fixOnly: false })
      )
    ).toEqual([]);
  });
});
