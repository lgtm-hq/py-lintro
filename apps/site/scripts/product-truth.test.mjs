/**
 * Generated reader content must match what the frozen product actually does.
 * Each expectation names the frozen source that decides it; the behaviour itself is
 * reproduced by artifacts/website-study/fable-round1-product-proof.py.
 */
import { describe, expect, it } from 'vitest';
import { readdirSync, readFileSync, statSync } from 'node:fs';

const docs = new URL('../src/content/docs/', import.meta.url).pathname;
const read = (path) => readFileSync(`${docs}${path}`, 'utf8');
const snapshot = JSON.parse(
  readFileSync(new URL('../src/generated/product-snapshot.json', import.meta.url), 'utf8')
);
const walk = (dir) =>
  readdirSync(dir).flatMap((entry) => {
    const path = `${dir}${entry}`;
    if (statSync(path).isDirectory()) return walk(`${path}/`);
    return path.endsWith('.md') ? [path] : [];
  });
const pages = walk(docs).map((path) => ({
  path: path.slice(docs.length),
  text: readFileSync(path, 'utf8'),
}));

describe('tool option surfaces (lintro/config/tool_config.py, lintro/utils/config.py)', () => {
  it('sends every tool page to the pyproject table and the CLI flag, never a YAML options key', () => {
    expect(snapshot.tools.length).toBeGreaterThan(40);
    for (const tool of snapshot.tools) {
      const page = read(`tools/${tool.name.replaceAll('_', '-')}.md`);
      expect(page, tool.name).toContain(`[tool.lintro.${tool.name}]`);
      expect(page, tool.name).toContain('--tool-options');
      expect(page, tool.name).toContain('reported as an unknown option and is not applied');
      expect(page, tool.name).not.toMatch(/options belong under `tools\./);
    }
  });

  it('shows no .lintro-config.yaml example that nests options under a tool', () => {
    const offenders = pages
      .filter(({ text }) => /^tools:\n(?: {2}\S.*\n)*? {2}[\w-]+:\n {4}options:/m.test(text))
      .map(({ path }) => path);
    expect(offenders).toEqual([]);
  });
});

describe('line length enforcement (lintro/config/tool_config_generator.py)', () => {
  it('never says enforce.line_length reaches Prettier as --print-width', () => {
    const offenders = pages
      .filter(
        ({ text }) =>
          /enforce\.line_length` as `--print-width`/.test(text) ||
          /\| Prettier\s*\| `--print-width/.test(text) ||
          /Injected as --line-length,? .*--print-width/.test(text)
      )
      .map(({ path }) => path);
    expect(offenders).toEqual([]);
  });

  it('describes the Prettier line_length option as the fallback it is', () => {
    const row = read('tools/prettier.md')
      .split('\n')
      .find((line) => line.startsWith('| `line_length`'));
    expect(row).toContain('Fallback only');
    expect(row).toContain('prefer native printWidth');
    expect(row).not.toContain('(maps to --print-width)');
  });
});

describe('project pages state the release they were checked against', () => {
  const projectPages = ['project/index.md', 'project/vision.md', 'project/architecture.md'];
  const versions = (text) => [...text.matchAll(/\b0\.\d+\.\d+\b/g)].map(([version]) => version);

  it('retains a declared release for historical product verification', () => {
    for (const path of projectPages) {
      const found = new Set(versions(read(path)));
      expect(found.size, path).toBeGreaterThan(0);
      expect(read(path), path).toMatch(/checked|Checked/);
    }
  });

  it('counts built-in tools from the snapshot (lintro/tools/*/definition.py)', () => {
    for (const path of ['project/vision.md', 'project/architecture.md']) {
      const counts = [...read(path).matchAll(/\b(\d+) built-in (?:tool )?integrations\b/g)];
      expect(counts.length, path).toBeGreaterThan(0);
      for (const [, count] of counts) expect(Number(count), path).toBe(snapshot.tools.length);
    }
  });

  it('lists every public top-level command in the architecture overview (lintro/cli.py)', () => {
    const overview = read('project/architecture.md').split('## Core Components')[0];
    const topLevel = snapshot.commands
      .map(({ name }) => name)
      .filter((name) => name && !name.includes(' '));
    expect(topLevel.length).toBeGreaterThan(10);
    for (const name of topLevel) expect(overview, name).toMatch(new RegExp(`\\b${name}\\b`));
  });

  it('does not carry the retired claims the rewrite removed', () => {
    for (const path of projectPages) {
      const text = read(path);
      expect(text, path).not.toMatch(/Today \(12 tools\)/);
      expect(text, path).not.toMatch(/Current:\*\* Python package via PyPI, requires Python/);
      expect(text, path).not.toMatch(/does \*\*not\*\* read environment variables/);
      expect(text, path).not.toMatch(/Explicit `--config` path/);
    }
  });
});

describe('decision record descriptions (scripts/ci/site/migrate-docs-content.py)', () => {
  it('describes each record with prose, not its status word', () => {
    const records = pages.filter(({ path }) => /^project\/adr\/\d{4}-/.test(path));
    expect(records.length).toBeGreaterThan(5);
    for (const { path, text } of records) {
      const description = text.match(/^description: "(.*)"$/m)?.[1] ?? '';
      expect(description.length, path).toBeGreaterThan(50);
      expect(description, path).not.toMatch(/^(Accepted|Proposed|Superseded|Rejected)\b/);
    }
  });
});
