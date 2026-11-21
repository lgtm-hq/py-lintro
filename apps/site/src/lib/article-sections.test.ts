import { describe, expect, it } from 'vitest';
import { articleSections } from './article-sections';

describe('article navigation', () => {
  it('nests provider links under their parent and returns to the correct level', () => {
    const sections = articleSections([
      { depth: 2, slug: 'configuration', text: 'Configuration' },
      { depth: 3, slug: 'providers', text: 'Providers' },
      { depth: 4, slug: 'cursor', text: 'Cursor' },
      { depth: 3, slug: 'transport', text: 'Transport' },
      { depth: 2, slug: 'cost', text: 'Cost' },
    ]);
    expect(sections.map((section) => section.slug)).toEqual(['configuration', 'cost']);
    expect(sections[0]?.children.map((section) => section.slug)).toEqual([
      'providers',
      'transport',
    ]);
    expect(sections[0]?.children[0]?.children[0]?.slug).toBe('cursor');
  });
  it('keeps standalone subsections reachable while excluding non-navigation headings', () => {
    expect(
      articleSections([
        { depth: 1, slug: 'title', text: 'Title' },
        { depth: 3, slug: 'start', text: 'Start' },
        { depth: 5, slug: 'detail', text: 'Detail' },
        { depth: 4, slug: 'example', text: 'Example' },
      ])
    ).toEqual([
      {
        depth: 3,
        slug: 'start',
        text: 'Start',
        children: [{ depth: 4, slug: 'example', text: 'Example', children: [] }],
      },
    ]);
  });
});
