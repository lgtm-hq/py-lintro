import { describe, expect, it } from 'vitest';
import { docHref, docs, external, home } from './site-links';
import { sourceToDoc } from '../generated/docs-route-map';

describe('site-links', () => {
  it('exposes internal doc paths under docs/', () => {
    for (const path of Object.values(docs)) {
      expect(path).toMatch(/^(docs\/|coverage\/)/);
      expect(path.endsWith('/')).toBe(true);
    }
  });

  it('points every doc link at a migrated page', () => {
    const routes = new Set(
      Object.values(sourceToDoc).map((id) => `docs/${id.replace(/\/index$/, '')}/`)
    );
    routes.add('docs/');
    routes.add('docs/ai/');
    routes.add('coverage/');
    for (const [key, path] of Object.entries(docs)) {
      expect(routes.has(path), `${key} → ${path}`).toBe(true);
    }
  });

  it('exposes home and external link metadata', () => {
    expect(home.href).toBe('/');
    expect(external.github.href).toMatch(/^https:\/\//);
    expect(external.pypi.href).toMatch(/^https:\/\//);
    expect(external.npm.href).toMatch(/^https:\/\//);
  });

  it('joins doc paths with a normalized base slash', () => {
    expect(docHref('/py-lintro', 'docs/guides/configuration/')).toBe(
      '/py-lintro/docs/guides/configuration/'
    );
    expect(docHref('/py-lintro/', '/docs/guides/configuration/')).toBe(
      '/py-lintro/docs/guides/configuration/'
    );
    expect(docHref('  /py-lintro/  ', '/docs/guides/configuration/')).toBe(
      '/py-lintro/docs/guides/configuration/'
    );
    expect(docHref('/py-lintro///', '///docs/guides/configuration/')).toBe(
      '/py-lintro/docs/guides/configuration/'
    );
    expect(docHref('/py-lintro', '   ')).toBe('/py-lintro/');
    expect(docHref('/py-lintro', '')).toBe('/py-lintro/');
    expect(docHref('py-lintro', 'docs/start/getting-started')).toBe(
      '/py-lintro/docs/start/getting-started'
    );
    expect(docHref('/', 'docs/guides/configuration/')).toBe('/docs/guides/configuration/');
  });
});
