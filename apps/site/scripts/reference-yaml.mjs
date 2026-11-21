/** Derive a setting index and focused examples from the canonical annotated YAML. */
import { readFileSync } from 'node:fs';
import { parse } from 'yaml';

const { aiConfig } = JSON.parse(
  readFileSync(new URL('../src/generated/product-snapshot.json', import.meta.url), 'utf8')
);

/** Walk real Pydantic JSON Schema properties, resolving nested model references. */
export function modelPaths(reference = aiConfig) {
  const paths = new Set();
  function visit(schema, root, prefix = '') {
    if (schema.$ref) schema = root.$defs[schema.$ref.split('/').at(-1)];
    for (const [key, value] of Object.entries(schema.properties ?? {})) {
      const path = prefix ? `${prefix}.${key}` : key;
      paths.add(path);
      visit(value, root, path);
    }
  }
  visit(reference.schema, reference.schema);
  for (const [provider, schema] of Object.entries(reference.providers)) {
    const path = `providers.${provider}`;
    paths.add(path); // An intentionally empty provider block is also a recognized path.
    visit(schema, schema, path);
  }
  return paths;
}

/** Fail generation on missing model paths, including nested and deprecated fields. */
export function assertCompleteConfiguration(groups, reference = aiConfig) {
  const indexed = new Set(groups.flatMap((group) => group.keys));
  const missing = [...modelPaths(reference)].filter((path) => !indexed.has(path));
  if (missing.length)
    throw new Error(`AI configuration reference is incomplete: ${missing.join(', ')}`);
  // Provider migration shims are not AIConfig fields. They remain accepted aliases,
  // documented below from the registry, rather than required canonical YAML examples.
}

export function configurationGroups(yaml) {
  const lines = yaml.split('\n');
  if (lines.shift() !== 'ai:') throw new Error('Expected the complete ai: configuration');
  const groups = [];
  const ancestors = [];
  const recognized = new Set([...modelPaths(), ...Object.keys(aiConfig.legacyAliases)]);
  const document = parse(yaml);
  if (
    !document?.ai ||
    typeof document.ai !== 'object' ||
    Array.isArray(document.ai) ||
    Object.keys(document).length !== 1
  )
    throw new Error('Expected only an ai: configuration mapping');
  function validateActivePaths(value, prefix = '') {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return;
    for (const [key, child] of Object.entries(value)) {
      const path = prefix ? `${prefix}.${key}` : key;
      if (!recognized.has(path)) throw new Error(`Unknown AI configuration setting: ${path}`);
      validateActivePaths(child, path);
    }
  }
  validateActivePaths(document.ai);
  for (const line of lines) {
    const marker = line.match(/^  # ─+ (.+?) ─+\s*$/);
    if (marker) {
      const title = marker[1].replace(/\s*\([^)]*\)/g, '').trim();
      const id = `settings-${title
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, '-')
        .replace(/-$/, '')}`;
      groups.push({ title, id, lines: [], keys: [] });
      ancestors.length = 0;
    }
    const group = groups.at(-1);
    if (!group) throw new Error('Configuration line has no group');
    group.lines.push(line);
    // Include commented optional/deprecated examples as well as active fields.
    const key = line.match(/^( +)(# )?([a-z_]+):/);
    if (key) {
      const depth = key[1].length / 2 - 1;
      const path = [...ancestors.slice(0, depth), key[3]].join('.');
      if (!recognized.has(path)) {
        if (!key[2]) throw new Error(`Unknown AI configuration setting: ${path}`);
        continue; // Wrapped comment prose is not a configuration example.
      }
      if (depth > ancestors.length) throw new Error(`Invalid AI configuration nesting: ${path}`);
      ancestors.length = depth;
      ancestors.push(key[3]);
      group.keys.push(path);
    }
  }
  return groups.map(({ lines, ...group }) => ({ ...group, yaml: `ai:\n${lines.join('\n')}` }));
}

/** Replace the large block with native disclosure groups, keeping one indexed copy. */
export function refineConfiguration(prose) {
  const match = prose.match(/```yaml\n(ai:\n[\s\S]*?)\n```/);
  if (!match) throw new Error('Full AI reference YAML missing');
  const complete = match[1];
  const groups = configurationGroups(complete);
  assertCompleteConfiguration(groups);
  const index = [
    '## Find a setting {#find-a-setting}',
    '',
    'Choose a group or setting to read its annotated example. Each focused example keeps the `ai:` root. Use the [complete configuration](#complete-configuration) to read or copy every group together. These are example values; comments state the defaults and limits.',
    '',
    '<div class="reference-filter" hidden data-pagefind-ignore>',
    '<label for="setting-query">Find a configuration setting</label>',
    '<input id="setting-query" type="search" placeholder="Try max_cost_usd or cache" aria-controls="setting-index" />',
    '<p id="setting-status" role="status" aria-live="polite"></p>',
    '</div>',
    '',
    '<nav id="setting-index" class="setting-index" aria-label="Configuration settings" data-pagefind-ignore>',
    ...groups.map(
      (group) =>
        `<div class="setting-index-shell" data-setting-index><div><a class="setting-group-link" href="#${group.id}">${group.title}</a><details><summary>${group.keys.length} setting ${group.keys.length === 1 ? 'name' : 'names'}</summary><ul>${group.keys.map((key) => `<li><a href="#${group.id}"><code>${key}</code></a></li>`).join('')}</ul></details></div></div>`
    ),
    '</nav>',
    '',
  ].join('\n');
  const focused = groups
    .map((group) =>
      [
        `## ${group.title} {#${group.id}}`,
        '',
        `<details class="setting-example" data-setting-example data-pagefind-weight="5">`,
        `<summary>Read ${group.keys.length} ${group.keys.length === 1 ? 'setting' : 'settings'} · ${group.title}</summary>`,
        '',
        ...(group.keys.includes('review_inline_min_confidence')
          ? [
              'For the fix-suggestion threshold mentioned in these comments, see [`min_confidence` in Safety & filtering](#settings-safety-filtering).',
              '',
            ]
          : []),
        ...(group.keys.includes('review_note_categories')
          ? [
              `Supported review categories: ${aiConfig.reviewCategories.map((name) => `\`${name}\``).join(', ')}.`,
              '',
            ]
          : []),
        ...(group.keys.includes('providers')
          ? [
              'Compatibility-only top-level aliases (accepted with a deprecation warning; the nested value wins when both are present):',
              '',
              ...Object.entries(aiConfig.legacyAliases).map(
                ([alias, path]) => `- \`${alias}\` → \`${path}\``
              ),
              '',
            ]
          : []),
        `\`\`\`yaml\n${group.yaml}\n\`\`\``,
        '',
        '</details>',
        '',
        '[Back to setting index](#find-a-setting)',
        '',
      ].join('\n')
    )
    .join('\n');
  const full = [
    '## Complete configuration {#complete-configuration}',
    '',
    '<details class="complete-configuration" data-pagefind-ignore>',
    '<summary>Read or copy the complete annotated YAML</summary>',
    '',
    'Copy this complete example into `.lintro-config.yaml`, then review the feature switches, provider, transport and limits for your project. Select the text directly when script or clipboard access is unavailable.',
    '',
    `\`\`\`yaml\n${complete}\n\`\`\``,
    '',
    '</details>',
  ].join('\n');
  const explanation = prose.slice(0, match.index).trim();
  const shape = [
    '<details>',
    '<summary>Configuration shape, defaults and validation</summary>',
    '',
    explanation,
    '',
    '</details>',
    '',
  ].join('\n');
  return `${shape}\n${prose.slice(match.index).replace(match[0], `${index}\n${focused}\n${full}`)}`;
}
