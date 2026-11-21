#!/usr/bin/env node
/**
 * Copy Turbo Themes CSS assets into public/assets/css/.
 * Copies only .css files (no .d.ts/.map/.js leaks) and strips external vendor font URLs
 * since fonts are self-hosted via @fontsource-variable.
 */
import { existsSync, mkdirSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const siteDir = resolve(__dirname, '..');
const publicCss = join(siteDir, 'public', 'assets', 'css');
const cssSrc = join(siteDir, 'node_modules', '@lgtm-hq', 'turbo-themes', 'packages', 'css', 'dist');

function sanitizeCss(css) {
  return css
    .replace(/@import\s+url\(\s*["']?https?:\/\/[^)]+\)\s*;/gi, '')
    .replace(/@font-face\s*\{[^}]*url\(\s*["']?https?:\/\/[^}]*\}/gi, '');
}

function copyCssDir(src, dest) {
  mkdirSync(dest, { recursive: true });
  for (const entry of readdirSync(src, { withFileTypes: true })) {
    const srcPath = join(src, entry.name);
    const destPath = join(dest, entry.name);
    if (entry.isDirectory()) {
      copyCssDir(srcPath, destPath);
    } else if (entry.name.endsWith('.css')) {
      const cleaned = sanitizeCss(readFileSync(srcPath, 'utf8'));
      writeFileSync(destPath, cleaned);
    }
  }
}

if (existsSync(cssSrc)) {
  rmSync(publicCss, { recursive: true, force: true });
  copyCssDir(cssSrc, publicCss);
  console.log('Copied sanitized Turbo Themes CSS assets (.css only)');
} else {
  console.warn('Warning: @lgtm-hq/turbo-themes CSS dist not found at', cssSrc);
}
