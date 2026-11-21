import type { CatalogTool } from '../data/tool-categories';

/** Derive a compact roster family from declared patterns, rather than a second tool list. */
export function toolFamily(tool: CatalogTool): string {
  const patterns = tool.patterns.join(' ').toLowerCase();
  if (patterns.includes('*.py') || patterns.includes('requirements')) return 'Python';
  if (patterns.includes('*.rs') || patterns.includes('cargo.')) return 'Rust';
  if (patterns.includes('*.go')) return 'Go';
  if (/\*\.(?:js|ts|css|vue|svelte|astro|html)/.test(patterns)) return 'Web';
  if (/\*\.(?:sh|bash|zsh)/.test(patterns)) return 'Shell';
  if (/\*\.(?:md|rst|txt)/.test(patterns) && tool.category !== 'sec') return 'Docs';
  if (tool.category === 'sec') return 'Security';
  if (tool.category === 'ai') return 'AI';
  return 'Project';
}

/** Use the actual registered identifier when composing a command. */
export function toolCommand(tool: CatalogTool, format = false): string {
  if (tool.category === 'ai')
    return 'lintro review --advisory-only --tool-options idiom-review:enabled=true';
  return (
    'lintro ' + (format && tool.fixes ? 'format' : 'check') + ' . --tools ' + tool.registeredName
  );
}
