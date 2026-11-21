import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { createMarkdownProcessor } from '@astrojs/markdown-remark';
import { remarkHeadingIds } from '../src/lib/remark-heading-ids.mjs';
const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');
const snapshot = JSON.parse(read('../src/generated/product-snapshot.json'));
const claims = JSON.parse(read('../src/generated/tool-claims.json'));
const anchors = JSON.parse(read('../src/generated/start-anchors.json'));
const processor = await createMarkdownProcessor({
  remarkPlugins: [remarkHeadingIds],
  syntaxHighlight: false,
});
describe('reader reference generation contract', () => {
  it('gives every integration a usable command, native policy and a working two-way configuration link', () => {
    expect(claims).toHaveLength(snapshot.tools.length);
    for (const claim of claims) {
      const markdown = read(`../src/content/docs/tools/${claim.name}.md`);
      expect(markdown).toContain(claim.command);
      expect(markdown).toContain(`](${claim.config})`);
      expect(markdown).toContain(claim.notes);
      expect(markdown.match(new RegExp(`^## Use ${claim.name}$`, 'gm'))).toHaveLength(1);
      const config = read(
        `../src/content/${claim.config.replace(/^\/docs\//, 'docs/').replace(/\/$/, '')}.md`
      );
      expect(config).toContain(`/docs/tools/${claim.name}/`);
      expect(claim.minimumVersion === null || /^\d+(\.\d+)+/.test(claim.minimumVersion)).toBe(true);
    }
  });
  it('preserves every authored onboarding heading exactly once at a reachable focused destination', async () => {
    const source = read('../../../docs/getting-started.md').replace(/^# .+\n/, '');
    const {
      metadata: { headings },
    } = await processor.render(source);
    expect(Object.keys(anchors)).toEqual(headings.map((heading) => heading.slug));
    for (const heading of headings) {
      const destination = anchors[heading.slug];
      const [route, fragment] = destination.split('#');
      const markdown = read(
        `../src/content/${route.replace(/^\/docs\//, 'docs/').replace(/\/$/, '')}.md`
      );
      const { metadata } = await processor.render(markdown.replace(/^---\n[\s\S]*?\n---\n/, ''));
      const matches = metadata.headings.filter((h) => h.slug === fragment);
      expect(
        matches.length + Number(markdown.includes(`titleAnchor: "${fragment}"`)),
        fragment
      ).toBe(1);
    }
  });
});
