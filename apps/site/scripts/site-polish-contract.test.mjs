import { describe, expect, it } from 'vitest';
import { existsSync, readFileSync } from 'node:fs';

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');

describe('SEO and metadata contracts', () => {
  it('avoids duplicating the brand name when title starts with Lintro', () => {
    const seo = read('../src/components/SEOHead.astro');
    expect(seo).toContain("title.startsWith('Lintro')");
    expect(seo).toContain('application/ld+json');
    expect(seo).toContain('og-card.png');
    expect(seo).toContain('og:image:width');
  });

  it('ships a robots.txt referencing the sitemap', () => {
    const robotsUrl = new URL('../public/robots.txt', import.meta.url);
    expect(existsSync(robotsUrl)).toBe(true);
    const robots = readFileSync(robotsUrl, 'utf8');
    expect(robots).toContain('User-agent: *');
    expect(robots).toContain('Sitemap:');
  });
});

describe('accessibility and layout polish contracts', () => {
  it('preserves native summary disclosure semantics in Header', () => {
    const header = read('../src/components/Header.astro');
    expect(header).not.toContain('<summary role="button"');
  });

  it('suppresses duplicate section headers on dedicated subpages', () => {
    expect(read('../src/pages/ai.astro')).toContain('<AiFeatures showHeader={false}');
    expect(read('../src/pages/mcp.astro')).toContain('<McpConsole showHeader={false}');
    expect(read('../src/pages/compare.astro')).toContain('<Comparison showHeader={false}');
  });

  it('places tool stack selection before AI settings and disables AI controls when AI is off', () => {
    const cb = read('../src/components/home/ConfigBuilder.astro');
    expect(cb.indexOf('Tool groups to enable')).toBeLessThan(cb.indexOf('Optional AI features'));
    expect(cb).toContain('data-ai-dependent');
    expect(cb).toContain('control.disabled = !aiEnabled');
  });
});

describe('client bundle and asset hygiene contracts', () => {
  it('keeps product-snapshot.json out of the ToolCatalog client script', () => {
    const tc = read('../src/components/home/ToolCatalog.astro');
    const client = tc.slice(tc.lastIndexOf('<script>'));
    expect(client).not.toContain('tools-catalog');
    expect(client).not.toContain('catalog-filter');
    expect(client).toContain('tool-categories');
    expect(client).toContain('catalog-matcher');
  });

  it('copies only .css files from turbo-themes into public/assets/css', () => {
    const copy = read('./copy-assets.mjs');
    expect(copy).toContain("entry.name.endsWith('.css')");
    expect(copy).toContain('@font-face');
  });
});

describe('ConfigBuilder bundle and AI heading contracts', () => {
  it('keeps product-snapshot.json out of the ConfigBuilder client script', () => {
    const cb = read('../src/components/home/ConfigBuilder.astro');
    const client = cb.slice(cb.lastIndexOf('<script>'));
    expect(client).not.toContain('data/config-builder');
    expect(client).toContain('config-builder-core');
  });

  it('uses h2 inside AiFeatures panels when section header is hidden', () => {
    const ai = read('../src/components/home/AiFeatures.astro');
    expect(ai).toContain('{showHeader ? <h3>{mode.title}</h3> : <h2>{mode.title}</h2>}');
  });
});
