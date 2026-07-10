import { describe, expect, it } from 'vitest';
import {
  CORE_DOC_CATEGORIES,
  DOC_CATEGORIES,
  STANDALONE_DOC_CATEGORIES,
  buildMainNav,
  categoryLandingHref,
  findSectionOverviewDoc,
  isStandaloneCategory,
} from './docs-nav';

const mockDocs = [
  {
    id: 'usage/configuration',
    data: { title: 'Configuration', category: 'usage' as const, order: 10 },
  },
  {
    id: 'tools/ruff',
    data: { title: 'Ruff', category: 'tools' as const, order: 10 },
  },
  {
    id: 'getting-started/getting-started',
    data: { title: 'Getting Started', category: 'getting-started' as const, order: 10 },
  },
];

describe('docs-nav', () => {
  it('covers every category exactly once between core and standalone', () => {
    const combined = [...CORE_DOC_CATEGORIES, ...STANDALONE_DOC_CATEGORIES].sort();
    expect(combined).toEqual([...DOC_CATEGORIES].sort());
  });

  it('has no standalone sections for lintro', () => {
    expect(STANDALONE_DOC_CATEGORIES).toHaveLength(0);
    expect(isStandaloneCategory('usage')).toBe(false);
  });

  it('includes Docs and Coverage in navbar', () => {
    const labels = buildMainNav('/py-lintro/', []).map((link) => link.label);
    expect(labels).toContain('Docs');
    expect(labels).toContain('Coverage');
  });

  it('builds dropdown groups from docs collection', () => {
    const nav = buildMainNav('/py-lintro/', mockDocs);
    const docsItem = nav.find((n) => n.label === 'Docs');
    expect(docsItem?.groups.some((g) => g.label === 'Getting Started')).toBe(true);
    expect(docsItem?.groups.find((g) => g.label === 'Usage')?.items[0]?.label).toBe(
      'Configuration'
    );
    expect(docsItem?.groups.find((g) => g.label === 'Tools')?.items[0]?.label).toBe('Ruff');
  });

  it('resolves section overview from Astro index slug', () => {
    const docs = [
      {
        id: 'usage',
        data: { title: 'usage', category: 'usage' as const, order: 5 },
      },
    ];

    expect(findSectionOverviewDoc(docs, 'usage')?.id).toBe('usage');
  });

  it('resolves category landing href to each section overview', () => {
    const docs = [
      ...mockDocs,
      {
        id: 'getting-started/hub',
        data: { title: 'Hub', category: 'getting-started' as const, order: 1 },
      },
      {
        id: 'usage',
        data: { title: 'overview', category: 'usage' as const, order: 1 },
      },
      {
        id: 'tools',
        data: { title: 'Tools', category: 'tools' as const, order: 1 },
      },
      {
        id: 'architecture/overview',
        data: { title: 'overview', category: 'architecture' as const, order: 1 },
      },
      {
        id: 'security',
        data: { title: 'overview', category: 'security' as const, order: 1 },
      },
      {
        id: 'contributing/contributing',
        data: { title: 'contributing', category: 'contributing' as const, order: 1 },
      },
    ];

    expect(categoryLandingHref(docs, 'getting-started', '/py-lintro/')).toBe(
      '/py-lintro/docs/getting-started/hub/'
    );
    expect(categoryLandingHref(docs, 'usage', '/py-lintro/')).toBe('/py-lintro/docs/usage/');
    expect(categoryLandingHref(docs, 'tools', '/py-lintro/')).toBe('/py-lintro/docs/tools/');
    expect(categoryLandingHref(docs, 'security', '/py-lintro/')).toBe('/py-lintro/docs/security/');
  });
});
