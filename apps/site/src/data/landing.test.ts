import { describe, expect, it } from 'vitest';
import { formatDocDescription, joinBase, sanitizeDocDescription } from './landing';

const BASE = '/py-lintro/';

describe('formatDocDescription', () => {
  it('leaves external descriptions as safe inline links', () => {
    const html =
      'Import from <a href="https://jsonresume.org/">JSON Resume</a> via <code>rustume parse</code>.';
    const output = formatDocDescription(html, BASE);

    expect(output).not.toContain('smart-link');
    expect(output).toContain('target="_blank"');
    expect(output).toContain('rel="noopener noreferrer"');
    expect(output).toContain('JSON Resume');
    expect(output).toContain('<code>rustume parse</code>');
  });

  it('prefixes internal doc links with the site base', () => {
    const html = 'Run with <a href="/docs/usage/docker/">Docker</a>.';
    const output = formatDocDescription(html, BASE);

    expect(output).not.toContain('doc-link');
    expect(output).toContain('href="/py-lintro/docs/usage/docker/"');
  });

  it('prefixes site-root links with the deployment base', () => {
    const output = formatDocDescription('Open <a href="/">Lintro</a>.', BASE);

    expect(output).toContain('href="/py-lintro/"');
  });

  it('leaves localhost links unchanged', () => {
    const html = 'Open <a href="http://localhost:5173">editor</a>.';
    const output = formatDocDescription(html, BASE);

    expect(output).not.toContain('smart-link');
    expect(output).toContain('href="http://localhost:5173"');
  });

  it('prefixes internal links when href is not the first attribute', () => {
    const html = 'See <a class="doc-link" href="/docs/usage/docker/">Docker</a>.';
    const output = formatDocDescription(html, BASE);

    expect(output).toContain('href="/py-lintro/docs/usage/docker/"');
  });

  it('adds rel when target=_blank is already present', () => {
    const html = '<a href="https://example.com" target="_blank">Example</a>';
    const output = formatDocDescription(html, BASE);

    expect(output).toContain('rel="noopener noreferrer"');
    expect(output).toContain('target="_blank"');
  });

  it('merges noopener into an existing rel attribute', () => {
    const html = '<a href="https://example.com" target="_blank" rel="external">Example</a>';
    const output = formatDocDescription(html, BASE);

    expect(output).toContain('noopener');
    expect(output).toContain('noreferrer');
    expect(output).toContain('external');
  });

  it('merges noopener into rel when target=_blank is already set', () => {
    const html = "<a href='https://example.com' target='_blank' rel='external'>Example</a>";
    const output = formatDocDescription(html, BASE);

    expect(output).toContain('rel="external noopener noreferrer"');
    expect(output).toContain('noopener');
    expect(output).toContain('target=');
  });
});

describe('joinBase', () => {
  it('joins base and suffix with a single slash', () => {
    expect(joinBase('https://example.com', 'docs/page/')).toBe('https://example.com/docs/page/');
  });

  it('normalizes base without trailing slash', () => {
    expect(joinBase('/py-lintro', '/docs/')).toBe('/py-lintro/docs/');
  });
});

describe('sanitizeDocDescription', () => {
  it('removes script tags and event handlers', () => {
    const html = '<script>alert(1)</script><a href="/x" onclick=alert(1)>link</a>';
    const output = sanitizeDocDescription(html);

    expect(output).not.toContain('<script');
    expect(output).not.toContain('onclick');
    expect(output).toContain('href="/x"');
  });
  it('strips quoted event handlers', () => {
    const html = '<a href="/x" onclick="alert(1)">link</a>';
    expect(sanitizeDocDescription(html)).not.toContain('onclick');
  });

  it('strips unquoted event handlers', () => {
    const html = '<a href="x" onclick=alert(1)>link</a>';
    expect(sanitizeDocDescription(html)).not.toContain('onclick');
  });

  it('strips handlers when href is not the first attribute', () => {
    const html = '<a onclick=alert(1) href="/x">link</a>';
    expect(sanitizeDocDescription(html)).not.toContain('onclick');
  });

  it('strips unquoted style attributes', () => {
    const html = '<a href="/x" style=color:red>link</a>';
    expect(sanitizeDocDescription(html)).not.toContain('style');
  });

  it('strips handlers when href is single-quoted', () => {
    const html = "<a href='/x' onclick=alert(1)>link</a>";
    const output = sanitizeDocDescription(html);

    expect(output).not.toContain('onclick');
    expect(output).toContain('href=');
    expect(output).toContain('/x');
  });

  it('strips handlers when href is unquoted', () => {
    const html = '<a href=/x onclick=alert(1)>link</a>';
    const output = sanitizeDocDescription(html);

    expect(output).not.toContain('onclick');
    expect(output).toContain('href=');
    expect(output).toContain('/x');
  });
});

describe('formatDocDescription href quoting', () => {
  it('prefixes internal links with single-quoted href', () => {
    const html = "See <a href='/docs/usage/docker/'>Docker</a>.";
    const output = formatDocDescription(html, BASE);

    expect(output).toContain('href="/py-lintro/docs/usage/docker/"');
  });

  it('prefixes internal links with unquoted href', () => {
    const html = 'See <a href=/docs/usage/docker/>Docker</a>.';
    const output = formatDocDescription(html, BASE);

    expect(output).toContain('href="/py-lintro/docs/usage/docker/"');
  });
});
