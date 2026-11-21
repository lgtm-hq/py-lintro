/**
 * Scroll contracts the stylesheets must keep. Each was found in a browser: stacked
 * anchor offsets left every in-page target 125-172px too low, scrolling panels clipped
 * the focus ring of an item scrolled flush to their edge, and fixed pixel offsets hid
 * the top 28px of the section tabs under the header at 200% text.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');
const sheets = ['highend.css', 'docs.css', 'global.css', 'code-view.css'].map((name) => ({
  name,
  css: read(`../src/styles/${name}`).replace(/\/\*[\s\S]*?\*\//g, ''),
}));
const blocks = sheets.flatMap(({ name, css }) =>
  [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)].map(([, selector, body]) => ({
    sheet: name,
    selector: selector.trim(),
    body,
  }))
);
const declarations = (property) =>
  blocks.flatMap(({ sheet, selector, body }) =>
    [...body.matchAll(new RegExp(`(?:^|[;\\s])${property}\\s*:\\s*([^;]+)`, 'g'))].map(
      ([, value]) => ({ sheet, selector, value: value.trim() })
    )
  );

describe('in-page anchor offset', () => {
  it('comes from the root alone, so no element stacks a second offset on top', () => {
    expect(declarations('scroll-padding-top')).toEqual([
      { sheet: 'highend.css', selector: 'html', value: 'var(--anchor-offset)' },
    ]);
    const positive = declarations('scroll-margin-top').filter(
      ({ value }) => !value.startsWith('calc(-1 *')
    );
    expect(positive).toEqual([]);
  });

  it('cancels the offset for pinned header controls but not the header’s scrolling panels', () => {
    const [rule, ...rest] = declarations('scroll-margin-top');
    expect(rest).toEqual([]);
    expect(rule.value).toBe('calc(-1 * var(--anchor-offset))');
    expect(rule.selector).toContain('.site-header');
    expect(rule.selector).toContain(':not(.mobile-menu nav a, .theme-picker-panel *)');
  });
});

describe('pinned chrome', () => {
  it('derives every offset from the measured sizes, never a fixed pixel position', () => {
    const offsets = declarations('--anchor-offset');
    expect(offsets.length).toBeGreaterThanOrEqual(3);
    for (const { selector, value } of offsets)
      expect(value, selector).toMatch(/^calc\(var\(--(header-bottom|docs-rail-top)\) \+ /);
    expect(declarations('--header-bottom').map(({ value }) => value)).toEqual([
      'calc(var(--header-stick) + var(--site-header-height))',
      '0px',
    ]);
    expect(declarations('--docs-rail-top').map(({ value }) => value)).toEqual([
      'calc(var(--header-height) + var(--docs-tabs-height, 0px))',
      'var(--header-height)',
    ]);
  });

  it('positions each pinned bar and rail with those variables', () => {
    const pinned = /(^|\s)\.(site-header|docs-section-tabs|docs-sidebar|docs-toc)$/;
    const positions = blocks
      .filter(({ selector }) => pinned.test(selector))
      .flatMap(({ selector, body }) =>
        [...body.matchAll(/(?:^|[;\s])(top|max-height)\s*:\s*([^;]+)/g)].map(
          ([, property, value]) => `${selector} ${property}: ${value.trim()}`
        )
      );
    expect(positions.sort()).toEqual(
      [
        '.docs-section-tabs top: var(--header-height)',
        '.docs-sidebar max-height: calc(100dvh - var(--docs-rail-top))',
        '.docs-sidebar top: var(--docs-rail-top)',
        '.docs-toc max-height: calc(100dvh - var(--docs-rail-top))',
        '.docs-toc top: var(--docs-rail-top)',
        'html .site-header top: auto',
        'html .site-header top: var(--header-stick)',
      ].sort()
    );
  });

  it('measures the two sizes the offsets are derived from', () => {
    const script = read('../src/components/StickyChrome.astro');
    expect(script).toContain("track('.site-header', '--site-header-height')");
    expect(script).toContain("track('.docs-section-tabs', '--docs-tabs-height')");
    expect(read('../src/layouts/BaseLayout.astro')).toMatch(/<Header \/>\s*<StickyChrome \/>/);
  });

  it('sizes its short-window breakpoints in rem so browser text size moves them', () => {
    const heights = [...sheets[0].css.matchAll(/\(max-height:\s*([\d.]+)(rem|px)\)/g)];
    expect(heights.length).toBeGreaterThanOrEqual(3);
    for (const [, , unit] of heights) expect(unit).toBe('rem');
  });
});

describe('focus ring reach', () => {
  it('is reserved in every panel that scrolls focusable items, and covers the ring', () => {
    const highend = sheets[0].css;
    const reach = Number(highend.match(/--focus-ring-reach:\s*(\d+)px/)[1]);
    const ring = highend.match(
      /html :focus-visible\s*\{[^}]*outline:\s*(\d+)px[^}]*outline-offset:\s*(\d+)px/
    );
    expect(reach).toBeGreaterThanOrEqual(Number(ring[1]) + Number(ring[2]));

    const [rule] = declarations('scroll-padding').filter(({ sheet }) => sheet === 'highend.css');
    expect(rule.value).toBe('var(--focus-ring-reach)');
    for (const panel of [
      '.mobile-menu nav',
      '.docs-sidebar',
      '.docs-toc',
      '.theme-picker-panel',
      'dialog#site-search',
    ])
      expect(rule.selector, panel).toContain(panel);
  });
});

describe('theme picker script', () => {
  it('keeps theme token data out of the picker client bundle', () => {
    const picker = read('../src/components/ThemeDropdown.astro');
    const client = picker.slice(picker.indexOf('<script>'));
    expect(client).not.toMatch(/from '\.\.\/data\/themes'/);
    expect(client).not.toContain('@lgtm-hq/turbo-themes');
    expect(read('../src/data/theme-defaults.ts')).not.toMatch(/^import /m);
  });
});
