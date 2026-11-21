import { describe, expect, it } from 'vitest';
import { findResults, prioritizeTitles, type SearchHit, type SearchIndex } from './search-results';

const hit = (id: string): SearchHit => ({
  id,
  data: async () => ({ url: `/docs/${id}/`, meta: { title: id }, excerpt: id }),
});

describe('Pagefind whole-word results', () => {
  it('retains relevance ranking while filtering incidental partial matches', async () => {
    const guide = hit('configuration');
    const incidental = hit('incidental');
    const advanced = hit('advanced');
    const index: SearchIndex = {
      search: async (term) => ({
        results: term.startsWith('"') ? [advanced, guide] : [guide, incidental, advanced],
      }),
    };
    expect(await findResults(index, 'configuration', false)).toEqual([guide, advanced]);
  });
  it('returns an empty result when only tiny partial matches exist', async () => {
    const index: SearchIndex = {
      search: async (term) => ({ results: term.startsWith('"') ? [] : [hit('incidental')] }),
    };
    expect(await findResults(index, 'unknownword', false)).toEqual([]);
  });
  it.each([
    { term: 'configuration', partial: true },
    { term: '"exact phrase"', partial: false },
  ])('preserves explicitly requested search semantics for $term', async ({ term, partial }) => {
    const result = hit('matching-page');
    const index: SearchIndex = { search: async () => ({ results: [result] }) };
    expect(await findResults(index, term, partial)).toEqual([result]);
  });
});

describe('direct page-title lookup', () => {
  it('keeps untitled results available and leaves an empty lookup in relevance order', () => {
    const items = [{ url: '/untitled/', meta: {}, excerpt: 'A related page' }];
    expect(prioritizeTitles(items, 'Providers')).toEqual(items);
    expect(prioritizeTitles(items, '  ')).toEqual(items);
  });
  it('promotes an exact title ahead of incidental matches while preserving their order', async () => {
    const items = await Promise.all(
      ['provider-specific settings', 'architecture', 'Providers'].map((id) => hit(id).data())
    );
    expect(prioritizeTitles(items, 'providers')).toEqual([items[2], items[0], items[1]]);
  });
  it('recognizes a guide title without moving unrelated results', async () => {
    const items = await Promise.all(
      ['tool configuration', 'Configuration Guide', 'advanced'].map((id) => hit(id).data())
    );
    expect(prioritizeTitles(items, 'configuration')).toEqual([items[1], items[0], items[2]]);
    expect(prioritizeTitles(items, 'unknown concept')).toEqual(items);
  });
});

describe('Pagefind whole-word results', () => {
  it('retains relevance ranking while filtering incidental partial matches', async () => {
    const guide = hit('configuration');
    const incidental = hit('incidental');
    const advanced = hit('advanced');
    const index: SearchIndex = {
      search: async (term) => ({
        results: term.startsWith('"') ? [advanced, guide] : [guide, incidental, advanced],
      }),
    };
    expect(await findResults(index, 'configuration', false)).toEqual([guide, advanced]);
  });
  it('returns an empty result when only tiny partial matches exist', async () => {
    const index: SearchIndex = {
      search: async (term) => ({ results: term.startsWith('"') ? [] : [hit('incidental')] }),
    };
    expect(await findResults(index, 'unknownword', false)).toEqual([]);
  });
  it.each([
    { term: 'configuration', partial: true },
    { term: '"exact phrase"', partial: false },
  ])('preserves explicitly requested search semantics for $term', async ({ term, partial }) => {
    const result = hit('matching-page');
    const index: SearchIndex = { search: async () => ({ results: [result] }) };
    expect(await findResults(index, term, partial)).toEqual([result]);
  });
});
