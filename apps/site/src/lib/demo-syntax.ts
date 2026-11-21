import { escapeText } from '../data/home-content';

/** Highlight small generated examples using the selected theme's syntax roles. */
function highlight(source: string, pattern: RegExp, language: 'shell' | 'yaml' | 'diff'): string {
  let cursor = 0;
  const output: string[] = [];
  for (const match of source.matchAll(pattern)) {
    const position = match.index;
    const value = match[0];
    output.push(escapeText(source.slice(cursor, position)));
    let role = 'function';
    if (language === 'diff' && value.startsWith('+')) role = 'inserted';
    else if (language === 'diff' && value.startsWith('-')) role = 'deleted';
    else if (value.startsWith('#')) role = 'comment';
    else if (/^['"]/.test(value)) role = 'string';
    else if (/^\d/.test(value)) role = 'number';
    else if (/^(true|false|null)$/.test(value)) role = 'keyword';
    else if (value === '&&' || value === '|') role = 'operator';
    else if (language === 'yaml' || value.startsWith('-')) role = 'keyword';
    output.push('<span class="syntax-' + role + '">' + escapeText(value) + '</span>');
    cursor = position + value.length;
  }
  output.push(escapeText(source.slice(cursor)));
  return output.join('');
}

/** Highlight an install or CLI command without making its contents executable. */
export function colorShell(source: string): string {
  return highlight(
    source,
    /#[^\n]*|"(?:[^"\\]|\\.)*"|'[^']*'|--?[\w-]+|\b(?:uv|pip|bun|brew|docker|lintro|install|run)\b|&&|\b\d+(?:\.\d+)*\b/g,
    'shell'
  );
}

/** Highlight the deliberately small YAML vocabulary emitted by the builder. */
export function colorYaml(source: string): string {
  return highlight(
    source,
    /#[^\n]*|"(?:[^"\\]|\\.)*"|'[^']*'|\b(?:true|false|null)\b|\b\d+(?:\.\d+)?\b|[A-Za-z_][\w-]*(?=\s*:)/g,
    'yaml'
  );
}

/** Keep additions and deletions tied to the selected palette's diff roles. */
export function colorDiff(source: string): string {
  return highlight(source, /^[+-].*$|^@@.*$/gm, 'diff');
}
