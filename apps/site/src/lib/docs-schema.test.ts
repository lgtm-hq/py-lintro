import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { parse as parseYaml } from 'yaml';
import { describe, expect, it } from 'vitest';
import { docsFrontmatterSchema } from './docs-schema';

const DOCS_ROOT = join(import.meta.dirname, '..', 'content', 'docs');

function listMarkdownFiles(dir: string): string[] {
  const entries = readdirSync(dir, { withFileTypes: true });
  const files: string[] = [];

  for (const entry of entries) {
    const fullPath = join(dir, entry.name);
    if (entry.isDirectory()) {
      files.push(...listMarkdownFiles(fullPath));
      continue;
    }
    if (entry.name.endsWith('.md') || entry.name.endsWith('.mdx')) {
      files.push(fullPath);
    }
  }

  return files;
}

function parseFrontmatter(source: string): Record<string, unknown> {
  const match = source.match(/^---\r?\n([\s\S]*?)\r?\n---/);
  if (!match?.[1]) {
    throw new Error('Missing frontmatter block');
  }

  const parsed = parseYaml(match[1]);
  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
    throw new Error('Frontmatter must be a YAML mapping');
  }

  return parsed as Record<string, unknown>;
}

describe('docs frontmatter', () => {
  const files = listMarkdownFiles(DOCS_ROOT);

  it('finds the expected documentation set', () => {
    expect(files.length).toBeGreaterThanOrEqual(30);
  });

  it.each(files)('validates %s', (filePath) => {
    const source = readFileSync(filePath, 'utf8');
    const frontmatter = parseFrontmatter(source);
    const result = docsFrontmatterSchema.safeParse(frontmatter);

    expect(result.success, result.success ? undefined : JSON.stringify(result.error.issues)).toBe(
      true
    );
  });
});
