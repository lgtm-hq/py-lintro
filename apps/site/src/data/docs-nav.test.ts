import { describe, expect, it } from 'vitest';
import {
  CATEGORY_BLURBS,
  CATEGORY_LABELS,
  DOC_CATEGORIES,
  SECTION_OVERVIEW_ID,
  buildMainNav,
  categoryLandingHref,
  docRoute,
  docsNavGroups,
  findSectionOverviewDoc,
  isDocCategory,
  isSectionOverview,
  sectionOverviewIds,
} from './docs-nav';

const mockDocs = [
  {
    id: 'guides/configuration',
    data: {
      title: 'Configuration Guide',
      navTitle: 'Configuration',
      category: 'guides' as const,
      order: 10,
    },
  },
  {
    id: 'tools/ruff',
    data: { title: 'Ruff Tool Analysis', navTitle: 'ruff', category: 'tools' as const, order: 10 },
  },
  {
    id: 'start/getting-started',
    data: { title: 'Getting Started', category: 'start' as const, order: 10 },
  },
  {
    id: 'ai/mcp',
    data: { title: 'MCP Server', navTitle: 'MCP server', category: 'ai' as const, order: 40 },
  },
  { id: 'ai', data: { title: 'AI', category: 'ai' as const, order: 5 } },
  { id: 'tools', data: { title: 'Tools', category: 'tools' as const, order: 5 } },
];

describe('docs-nav', () => {
  it('lists the six task-based sections in reading order', () => {
    expect([...DOC_CATEGORIES]).toEqual([
      'start',
      'guides',
      'ai',
      'tools',
      'contribute',
      'project',
    ]);
  });

  it('labels, blurbs and landing ids cover every section', () => {
    for (const key of DOC_CATEGORIES) {
      expect(CATEGORY_LABELS[key].length).toBeGreaterThan(0);
      expect(CATEGORY_BLURBS[key].length).toBeGreaterThan(0);
      expect(SECTION_OVERVIEW_ID[key].startsWith(key)).toBe(true);
    }
    expect(isDocCategory('ai')).toBe(true);
    expect(isDocCategory('usage')).toBe(false);
  });

  it('puts Docs, Tools and AI in the navbar and drops Coverage', () => {
    const labels = buildMainNav('/py-lintro/', mockDocs).map((link) => link.label);
    expect(labels).toEqual(['Docs', 'Tools', 'AI']);
  });

  it('builds one dropdown group per section with short labels', () => {
    const groups = docsNavGroups(mockDocs, '/py-lintro/');
    expect(groups.map((g) => g.label)).toEqual(['Start', 'Guides', 'AI', 'Tools']);
    expect(groups.find((g) => g.label === 'Guides')?.items[0]).toEqual({
      label: 'Configuration',
      href: '/py-lintro/docs/guides/configuration/',
    });
    expect(groups.find((g) => g.label === 'AI')?.href).toBe('/py-lintro/docs/ai/');
  });

  it('keeps section landing pages out of dropdown items', () => {
    const groups = docsNavGroups(mockDocs, '/py-lintro/');
    const ai = groups.find((g) => g.label === 'AI');
    expect(ai?.items.map((i) => i.label)).toEqual(['MCP server']);
  });

  it('resolves section overview from the flattened Astro index id', () => {
    expect(findSectionOverviewDoc(mockDocs, 'ai')?.id).toBe('ai');
    expect(findSectionOverviewDoc(mockDocs, 'guides')).toBeUndefined();
    expect(isSectionOverview('ai', 'ai')).toBe(true);
    expect(isSectionOverview('ai/index', 'ai')).toBe(true);
    expect(isSectionOverview('start/overview', 'start')).toBe(true);
    expect(isSectionOverview('start', 'start')).toBe(true);
    expect(isSectionOverview('ai/mcp', 'ai')).toBe(false);
  });

  it('shares one overview id set between detection and lookup', () => {
    expect(sectionOverviewIds('start')).toEqual(['start/overview', 'start/index', 'start']);
    expect(sectionOverviewIds('ai')).toEqual(['ai', 'ai/index']);
    for (const id of sectionOverviewIds('start')) {
      expect(isSectionOverview(id, 'start')).toBe(true);
      expect(
        findSectionOverviewDoc([{ id, data: { title: id, category: 'start', order: 1 } }], 'start')
          ?.id
      ).toBe(id);
    }
  });

  it('routes index ids to their parent path', () => {
    expect(docRoute('/py-lintro/', 'project/adr/index')).toBe('/py-lintro/docs/project/adr/');
    expect(docRoute('/', 'ai/mcp')).toBe('/docs/ai/mcp/');
  });

  it('lands section tabs on the overview or the first page', () => {
    expect(categoryLandingHref(mockDocs, 'tools', '/py-lintro/')).toBe('/py-lintro/docs/tools/');
    expect(categoryLandingHref(mockDocs, 'guides', '/py-lintro/')).toBe(
      '/py-lintro/docs/guides/configuration/'
    );
    expect(categoryLandingHref(mockDocs, 'project', '/py-lintro/')).toBe('/py-lintro/docs/');
  });
});
