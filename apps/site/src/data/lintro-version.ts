import { existsSync, readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

/** Walk up from `start` until a directory containing `name` is found. */
export function findUp(name: string, start: string): string | undefined {
  let dir = resolve(start);
  for (;;) {
    const candidate = join(dir, name);
    if (existsSync(candidate)) return candidate;
    const parent = dirname(dir);
    if (parent === dir) return undefined;
    dir = parent;
  }
}

/** Read the published lintro version from the repo-root pyproject.toml. */
export function readLintroVersion(pyprojectPath?: string): string {
  const path =
    pyprojectPath ??
    findUp('pyproject.toml', dirname(fileURLToPath(import.meta.url))) ??
    findUp('pyproject.toml', process.cwd());
  if (!path) {
    throw new Error('No pyproject.toml found above the site directory');
  }
  if (!existsSync(path)) {
    throw new Error(`No pyproject.toml at ${path}`);
  }
  const source = readFileSync(path, 'utf8');
  const match = source.match(/^version\s*=\s*"([^"]+)"/m);
  if (!match?.[1]) {
    throw new Error(`No version field found in ${path}`);
  }
  return match[1];
}

/** The lintro version the site was built against. */
export const LINTRO_VERSION = readLintroVersion();
