import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { createMarkdownProcessor } from '@astrojs/markdown-remark';
import { splitAiArticle } from './split-ai.mjs';
import { remarkHeadingIds } from '../src/lib/remark-heading-ids.mjs';
import { assertCompleteConfiguration, configurationGroups, modelPaths } from './reference-yaml.mjs';
import { parse } from 'yaml';

const body = readFileSync('../../docs/ai-features.md', 'utf8');
const { aiConfig } = JSON.parse(readFileSync('src/generated/product-snapshot.json', 'utf8'));
const productSource =
  process.env.LINTRO_PRODUCT_SOURCE ?? fileURLToPath(new URL('../../../', import.meta.url));
const source = readFileSync(`${productSource}/docs/ai-features.md`, 'utf8');
const article = `---\ntitle: AI\n---\n\n${body.replace(/^# .+\n/, '')}`;
const processor = await createMarkdownProcessor({
  remarkPlugins: [remarkHeadingIds],
  syntaxHighlight: false,
});
const withoutFrontmatter = (markdown) => markdown.replace(/^---\n[\s\S]*?\n---\n/, '');

describe('AI article migration', () => {
  it('preserves every legacy and corrected section in one canonical page', async () => {
    const result = await splitAiArticle(article, source);
    expect(result.pages).toHaveLength(43);
    expect(Object.keys(result.targets)).toHaveLength(72);
    const rendered = new Map();
    for (const page of result.pages) {
      const { code, metadata } = await processor.render(withoutFrontmatter(page.markdown));
      rendered.set(page.slug, `${code}<h1 id="${page.titleAnchor}"></h1>`);
      let previous = 1;
      for (const heading of metadata.headings) {
        expect(heading.depth, `${page.slug}: ${heading.text}`).toBeLessThanOrEqual(previous + 1);
        previous = heading.depth;
      }
    }
    for (const [id, target] of Object.entries(result.targets)) {
      const slug = target.split('/').at(-2);
      expect(rendered.get(slug), target).toContain(`id="${id}"`);
      expect(target.split('#')[1]).toBe(id);
    }
    expect(result.targets.providers).toBe('/docs/ai/ai-features/providers/#providers');
    expect(result.targets['ai-summary-default-with-check--chk']).toBe(
      '/docs/ai/ai-features/summaries-and-fixes/#ai-summary-default-with-check--chk'
    );
  });

  it('describes every page with a complete sentence, never a dangling list lead-in', async () => {
    const result = await splitAiArticle(article, source);
    const descriptions = result.pages.map((page) => ({
      slug: page.slug,
      description: page.markdown.match(/^description: (".*")$/m)?.[1],
    }));
    const dangling = descriptions.filter(({ description }) => {
      const text = description ? JSON.parse(description) : '';
      return text.length < 50 || text.length > 190 || /[:,;]$/.test(text);
    });
    expect(dangling).toEqual([]);
  });

  it('retains all substantive lines and exact fenced examples once, deterministically', async () => {
    const result = await splitAiArticle(article, source);
    expect(await splitAiArticle(article, source)).toEqual(result);
    const complete = body.match(
      /### Full Configuration Reference[\s\S]*?```yaml\n([\s\S]*?)\n```/
    )[1];
    const combined = [result.hub, ...result.pages.map((page) => page.markdown)]
      .map(withoutFrontmatter)
      .join('\n')
      .replace(/```yaml\nai:\n  # ─[\s\S]*?```/g, '');
    const counts = (text) =>
      text.split('\n').reduce((map, line) => map.set(line, (map.get(line) ?? 0) + 1), new Map());
    const expected = counts(body.replace(`\`\`\`yaml\n${complete}\n\`\`\``, ''));
    const actual = counts(combined);
    for (const [line, count] of expected) {
      if (!line.trim() || /^#{1,6} /.test(line) || /\]\(#/.test(line)) continue;
      expect(actual.get(line), line).toBe(count);
    }
    for (const block of body.matchAll(/```[^\n]*\n[\s\S]*?```/g))
      if (!block[0].includes(complete)) expect(combined).toContain(block[0]);
    const headings = result.pages.flatMap((page) =>
      page.sections.flatMap((section) => section.headings)
    );
    expect(headings).toHaveLength(result.headings.length);
    expect(new Set(headings.map((heading) => heading.slug)).size).toBe(headings.length);
    expect(
      result.pages
        .filter((page) => page.slug !== 'configuration-reference')
        .every((page) => page.markdown.length < 20000)
    ).toBe(true);
    expect(result.hub.length).toBeLessThan(body.length / 6);
  });

  it('reconstructs every configuration line and offers valid complete YAML without indexing a duplicate', async () => {
    const complete = body.match(
      /### Full Configuration Reference[\s\S]*?```yaml\n([\s\S]*?)\n```/
    )[1];
    const groups = configurationGroups(complete);
    expect(`ai:\n${groups.map((group) => group.yaml.slice(4)).join('\n')}`).toBe(complete);
    const result = await splitAiArticle(article, source);
    const page = result.pages.find((page) => page.slug === 'configuration-reference');
    const copy = page.markdown.match(
      /<details class="complete-configuration" data-pagefind-ignore>[\s\S]*?```yaml\n([\s\S]*?)\n```/
    )[1];
    expect(copy).toBe(complete);
    expect(parse(copy).ai).toHaveProperty('providers.anthropic.cli_bare', 'auto');
    const indexed = groups.flatMap((group) => group.keys);
    expect(new Set(indexed)).toEqual(modelPaths());
    expect(indexed).toHaveLength(new Set(indexed).size);
    expect(groups.flatMap((group) => group.keys)).toContain('providers.cursor.trust_workspace');
    const { code } = await processor.render(withoutFrontmatter(page.markdown));
    expect(code).toContain('id="find-a-setting"');
    expect(code).not.toContain('## Find a setting');
    for (const group of groups) {
      expect(parse(group.yaml)).toHaveProperty('ai');
      expect(page.markdown).toContain(`\`\`\`yaml\n${group.yaml}\n\`\`\``);
      expect(code).toContain(`id="${group.id}"`);
      for (const key of group.keys)
        expect(code).toContain(`href="#${group.id}"><code>${key}</code>`);
    }
  });

  it('ignores wrapped comment prose without corrupting subsequent nested paths', () => {
    const groups = configurationGroups(`ai:
  # ── Example ──
  # budget: a sentence continuation, not a field
  # model: claude-sonnet-4-6
  # diagnostics: another sentence continuation
  providers:
    anthropic:
      # budget: nested prose is still not a setting
      cli_bare: auto
    cursor:
      trust_workspace: true
  # cli_max_diff_tokens: 7000
  # made_up_optional: false`);
    expect(groups[0].keys).toEqual([
      'model',
      'providers',
      'providers.anthropic',
      'providers.anthropic.cli_bare',
      'providers.cursor',
      'providers.cursor.trust_workspace',
      'cli_max_diff_tokens',
    ]);
    expect(groups[0].yaml).toContain('# diagnostics: another sentence continuation');
  });

  it.each([
    'imaginary: true',
    'imaginary-field: true',
    '"imaginaryField": true',
    'transports: { cli: { imaginary: true } }',
    'transports:\n    cli:\n      imaginary: true',
    'providers:\n    anthropic:\n      trust_workspace: true',
  ])('rejects nonexistent active paths: %s', (setting) => {
    expect(() => configurationGroups(`ai:\n  # ── Example ──\n  ${setting}`)).toThrow(
      /Unknown AI configuration setting/
    );
  });

  it('fails completeness for every omitted field and for a newly added schema property', () => {
    const complete = body.match(
      /### Full Configuration Reference[\s\S]*?```yaml\n([\s\S]*?)\n```/
    )[1];
    const groups = configurationGroups(complete);
    for (const path of modelPaths()) {
      const partial = groups.map((group) => ({
        ...group,
        keys: group.keys.filter((key) => key !== path),
      }));
      expect(() => assertCompleteConfiguration(partial), path).toThrow(path);
    }
    const next = structuredClone(aiConfig);
    next.schema.properties.new_supported_field = { type: 'boolean', default: false };
    expect(() => assertCompleteConfiguration(groups, next)).toThrow('new_supported_field');
    next.schema.$defs.CliTransportProfile.properties.new_nested_field = { type: 'string' };
    expect(() => assertCompleteConfiguration(groups, next)).toThrow(
      'transports.cli.new_nested_field'
    );
  });

  it('writes indexed detail, return paths and frozen-source anchors without duplicate reference prose', async () => {
    const result = await splitAiArticle(article, source);
    expect(result.hub).toContain('AI is optional and off by default');
    expect(result.hub).not.toContain('Every key below maps 1:1');
    for (const page of result.pages) {
      expect(page.markdown).toContain('[Back to AI tasks](/docs/ai/ai-features/)');
      expect(page.markdown).toContain('sourceAnchor:');
      expect(page.markdown).not.toContain('noindex');
    }
    const fixes = result.pages.find((page) => page.slug === 'summaries-and-fixes');
    expect(fixes.markdown).toContain('sourceAnchor: "ai-summary-opt-in-with-check--chk"');
    expect(result.pages.find((page) => page.slug === 'cost-control').markdown).toContain(
      '/docs/ai/ai-features/transports-in-ci/#transports-in-ci'
    );
  });
});
