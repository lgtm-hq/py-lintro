import { describe, expect, it } from 'vitest';
import { colorDiff, colorShell, colorYaml } from './demo-syntax';

describe('example syntax', () => {
  it('escapes executable markup before adding highlighting', () => {
    const html = colorShell('lintro check "<img src=x onerror=alert(1)>" # <script>');
    expect(html).not.toContain('<img');
    expect(html).not.toContain('<script>');
    expect(html).toContain('&lt;img');
    expect(html).toContain('syntax-string');
    expect(html).toContain('syntax-comment');
  });
  it('preserves raw YAML when syntax spans are removed', () => {
    const yaml = 'ai:\n  enabled: false\n  max_cost_usd: 2.00\n';
    const html = colorYaml(yaml);
    expect(html.replace(/<[^>]+>/g, '')).toBe(yaml);
    expect(html).toContain('syntax-keyword');
    expect(html).toContain('syntax-number');
  });
  it('distinguishes additions, deletions and unchanged context', () => {
    const html = colorDiff('-import os\n+import sys\n context\n@@ -1 +1 @@');
    expect(html).toContain('<span class="syntax-deleted">-import os</span>');
    expect(html).toContain('<span class="syntax-inserted">+import sys</span>');
    expect(html).toContain('\n context\n');
  });
});
