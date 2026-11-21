/** Generate focused AI task pages from the corrected release article, without duplicating it. */
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { referenceHeadings } from './reference-headings.mjs';
import { refineConfiguration } from './reference-yaml.mjs';

const site = fileURLToPath(new URL('../', import.meta.url));
const root = '/docs/ai/ai-features/';

/**
 * Pages whose opening source prose only introduces a list, table or example ("... is:").
 * As metadata that is a dangling fragment in search results and hub listings, so these
 * get a complete sentence that restates what the page's own first section says.
 */
const LEAD_IN_DESCRIPTIONS = {
  'configuration-reference':
    'Find settings by name or group, read annotated examples, or copy the complete configuration.',
  setup:
    'Install the AI extra, switch on ai.enabled with ai.lint or ai.review, and choose a provider and transport in .lintro-config.yaml.',
  transports:
    'Lintro reaches a provider through its Python SDK (api) or a locally installed agent CLI (cli), selected by ai.transport.',
  'summaries-and-fixes':
    'With AI lint enabled, lintro check can request a summary with an overview, key patterns, priority actions and an effort estimate.',
  'invocation-overrides':
    'CLI flags override environment variables, which override project and user configuration and built-in defaults, for provider, model, transport and cost settings.',
  'pre-execution-summary':
    'When AI is enabled, the pre-execution summary table lists the provider, model and operational AI settings before tools run.',
  'environment-support':
    'How AI output renders in a terminal, GitHub Actions, Markdown and JSON, including the ai_summary and metadata JSON fields.',
  'retry-and-error-handling':
    'AI calls retry rate limits and transient provider errors with exponential backoff; by default an AI failure never breaks the linting run.',
  'addressed-lifecycle-on-inline-threads':
    'When lintro review --post runs again, findings the new round no longer reproduces are marked addressed, partially addressed or regressed on their own inline comments.',
};

/** Assign known sections to useful tasks; new sections retain their own canonical page. */
function taskFor(heading, top) {
  const slug = heading.slug;
  if (
    [
      'quick-start',
      'configuration',
      'basic-setup',
      'feature-toggles',
      'config-defaults-for-cli-flags',
    ].includes(slug)
  )
    return { slug: 'setup', title: 'Set up AI', group: 'Start using AI' };
  if (
    [
      'ai-summary-opt-in-with-check--chk',
      'interactive-fix-suggestions---fix',
      'fix-response-validation',
      'git-checkpoints--rollback',
      'ai-fixes-in-format',
    ].includes(slug)
  )
    return { slug: 'summaries-and-fixes', title: 'Summaries and fixes', group: 'Use AI' };
  if (slug === 'full-configuration-reference')
    return {
      slug: 'configuration-reference',
      title: 'AI configuration reference',
      group: 'Look up a setting',
    };
  if (['providers', 'provider-specific-settings', 'cursor-workspace-trust'].includes(slug))
    return { slug, title: heading.text, group: 'Start using AI' };
  if (top === 'features-overview') return { slug, title: heading.text, group: 'Review reference' };
  if (top === 'transports') return { slug, title: heading.text, group: 'Choose a transport' };
  return { slug, title: heading.text, group: 'Operate and troubleshoot' };
}

/** Partition every prose line once; explicit IDs preserve full-article slug disambiguation. */
export async function splitAiArticle(article, source) {
  const end = article.indexOf('\n---', 3) + 4;
  const body = article.slice(end).trim();
  const headings = await referenceHeadings(body);
  const legacy = await referenceHeadings(source.replace(/^# .+\n/, ''));
  if (legacy.length !== headings.length)
    throw new Error('AI heading count changed; review legacy mapping');
  const sections = [];
  const intro = [];
  let headingIndex = 0;
  let top = '';
  let fence = null;
  for (const line of body.split('\n')) {
    const marker = line.match(/^\s{0,3}(`{3,}|~{3,})/);
    if (marker) {
      if (!fence) fence = marker[1];
      else if (marker[1][0] === fence[0] && marker[1].length >= fence.length) fence = null;
      (sections.at(-1)?.lines ?? intro).push(line);
      continue;
    }
    const match = !fence && line.match(/^(#{2,6})\s+(.+)$/);
    if (!match) {
      (sections.at(-1)?.lines ?? intro).push(line);
      continue;
    }
    const heading = headings[headingIndex];
    const old = legacy[headingIndex++];
    if (!heading || heading.depth !== match[1].length)
      throw new Error(`Unresolved AI heading: ${line}`);
    if (heading.depth === 2) top = heading.slug;
    const boundary =
      heading.depth === 2 ||
      (heading.depth === 3 && ['features-overview', 'configuration', 'transports'].includes(top));
    if (boundary)
      sections.push({
        ...taskFor(heading, top),
        top,
        sourceAnchor: old.slug,
        lines: [],
        headings: [],
        aliases: [],
      });
    const section = sections.at(-1);
    if (!section) throw new Error('AI section has no owner');
    section.headings.push(heading);
    // Preserve the public bookmark from before the opt-in wording correction.
    if (heading.slug === 'ai-summary-opt-in-with-check--chk') {
      const alias = 'ai-summary-default-with-check--chk';
      section.aliases.push({ ...heading, slug: alias });
      section.lines.push(`<span id="${alias}"></span>`);
    }
    if (old.slug !== heading.slug) {
      section.aliases.push(old);
      section.lines.push(`<span id="${old.slug}"></span>`);
    }
    section.lines.push(`${line.replace(/\s*\{#[\w-]+\}\s*$/, '')} {#${heading.slug}}`);
  }
  if (headingIndex !== headings.length) throw new Error('Not all AI headings were preserved');
  const pages = [];
  for (const section of sections) {
    let page = pages.find((p) => p.slug === section.slug);
    if (!page) {
      page = { ...section, sections: [] };
      pages.push(page);
    }
    page.sections.push(section);
  }
  const targets = Object.fromEntries(
    pages.flatMap((p) =>
      p.sections.flatMap((s) =>
        [...s.headings, ...s.aliases].map((h) => [h.slug, `${root}${p.slug}/#${h.slug}`])
      )
    )
  );
  const rewrite = (prose) =>
    prose.replace(/\]\(#([^)]*)\)/g, (m, hash) => (targets[hash] ? `](${targets[hash]})` : m));
  for (const page of pages) {
    const childSections = page.sections.filter(
      (s) => s.headings[0].depth === 2 && ['features-overview', 'transports'].includes(s.top)
    );
    const children = pages.filter((p) => p !== page && childSections.some((s) => p.top === s.top));
    const childLinks = children.length
      ? `\nChoose a topic:\n\n${children.map((p) => `- [${p.title}](${root}${p.slug}/)`).join('\n')}\n`
      : '';
    page.titleAnchor = page.sections[0].headings[0].slug;
    const sourceAnchor = page.sections[0].sourceAnchor;
    // Opening source prose supplies context without a second maintained facts table.
    page.description =
      page.sections
        .flatMap((s) => s.lines)
        .join('\n')
        .split(/\n\s*\n/)
        .find((p) => p.trim() && !/^[#<`|>\-*]/.test(p.trim()))
        ?.replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
        .replace(/[`*]/g, '')
        .replace(/\s*\([^)]*(?:#\d|milestone)[^)]*\)/g, '')
        .replace(/\s+/g, ' ')
        .trim() ?? `${page.title}: optional AI behavior, configuration and limits in Lintro.`;
    const sentenceEnds = [...page.description.matchAll(/[.!?](?=\s|$)/g)]
      .map((match) => match.index + 1)
      .filter((end) => end >= 40 && end <= 190);
    if (page.description.length > 190)
      page.description = sentenceEnds.length
        ? page.description.slice(0, sentenceEnds.at(-1))
        : page.description.slice(0, 187).replace(/\s+\S*$/, '') + '…';
    page.description = LEAD_IN_DESCRIPTIONS[page.slug] ?? page.description;
    const metadata = `---\ntitle: ${JSON.stringify(page.title)}\ntitleAnchor: ${JSON.stringify(page.titleAnchor)}\nsourceAnchor: ${JSON.stringify(sourceAnchor)}\ndescription: ${JSON.stringify(page.description)}\ncategory: ai\norder: ${100 + pages.indexOf(page)}\nsidebar: ${['setup', 'providers', 'transports', 'cost-control', 'data--privacy', 'configuration-reference', 'summaries-and-fixes', 'troubleshooting', 'features-overview'].includes(page.slug)}\nnavGroup: ${page.group === 'Review reference' ? 'review' : 'features'}\n---`;
    const minDepth = Math.min(...page.sections.flatMap((s) => s.headings.map((h) => h.depth)));
    let prose = page.sections
      .map((s) => s.lines.join('\n'))
      .join('\n')
      .replace(
        /^(#{2,6})(\s.+ \{#[^}]+\})$/gm,
        (_, marks, rest) => '#'.repeat(Math.max(2, marks.length - minDepth + 1)) + rest
      );
    prose = prose.replace(new RegExp(`^## .+ \\{#${page.titleAnchor}\\}\\n?`, 'm'), '');
    if (page.slug === 'configuration-reference') prose = refineConfiguration(prose);
    if (page.slug === 'providers')
      prose = prose.replace(
        /(Every provider fact on this page[\s\S]*?asserts the retired-model table against the metadata\.)/,
        '<details data-pagefind-ignore>\n<summary>Provider metadata provenance and snapshot maintenance</summary>\n\n$1\n\n</details>'
      );
    const context =
      {
        setup: `Install \`lintro[ai]\` for Anthropic/OpenAI API dependencies, or \`lintro[cursor]\` for Cursor SDK/API. CLI transport uses an installed agent binary. [Choose a provider](${root}providers/) and [check transport authentication](${root}transport-authentication/) before enabling a feature.`,
        providers: `New to AI? [Set up dependencies and feature toggles](${root}setup/), then choose a provider below. For nested provider configuration, see [provider-specific settings](${root}provider-specific-settings/).`,
        'cost-control': `For diff-review caps, [read the source/cost-basis enforcement policy](${root}transports-in-ci/#transports-in-ci) and the [API/CLI decision guide](/docs/ai/review-transports/#decision-table). Local caps can be overshot by a call in flight; they are not a provider-side billing ceiling. [Provider pricing and defaults](${root}providers/) and the [configuration reference](${root}configuration-reference/) supply the related settings.`,
        'docker-with-ai': `Before running a container, [check transport authentication](${root}transport-authentication/) for the effective key or session.`,
      }[page.slug] ?? '';
    page.markdown = `${metadata}\n\n[← AI task hub](${root})\n\n${context}\n\n${rewrite(prose)}\n${childLinks}\n\n[Back to AI tasks](${root})\n`;
  }
  const groups = [
    'Start using AI',
    'Use AI',
    'Choose a transport',
    'Look up a setting',
    'Review reference',
    'Operate and troubleshoot',
  ];
  const steps = [
    ['setup', 'Set up AI', 'Install dependencies and enable only the features you need.'],
    ['providers', 'Providers', 'Choose a provider, model and credentials.'],
    ['transports', 'Transports', 'Choose API or CLI and check authentication.'],
    ['cost-control', 'Cost Control', 'Understand estimates, limits and billing before a run.'],
  ];
  const hub = [
    '---',
    'title: "AI task hub"',
    'description: "Set up optional AI, choose a provider and transport, control cost, and look up review behavior."',
    'category: ai',
    'order: 10',
    'navTitle: "AI task hub"',
    'navGroup: features',
    '---',
    '',
    '## Start here',
    '',
    'AI is optional and off by default. Choose a provider explicitly; none is selected for you. Enable `ai.enabled` and the feature you need (`ai.lint` or `ai.review`), and set a transport explicitly. Deterministic linting and MCP tools work independently of AI.',
    '',
    '<ol class="ai-task-path">',
    ...steps.map(
      ([slug, title, description], index) =>
        `<li><a href="${root}${slug}/"><span class="ai-task-number" aria-hidden="true">0${index + 1}</span><span><strong>${title}</strong><small>${description}</small></span><span class="ai-task-arrow" aria-hidden="true">↗</span></a></li>`
    ),
    '</ol>',
    '',
    `Before sharing source, read [Data & Privacy](${root}data--privacy/).`,
    '',
    'These pages describe shipped capabilities in this release, behind the documented opt-ins and prerequisites. Historical design records describe proposals; [roadmap priorities](/docs/project/roadmap/) distinguish current and planned work.',
    '',
    '<details><summary>Installation prerequisites and credentials</summary>',
    '',
    ...intro,
    '',
    '</details>',
    '',
    '## Find a task or reference',
    '',
    ...groups.flatMap((group) => [
      `### ${group}`,
      '',
      ...pages
        .filter((p) => p.group === group)
        .map((p) => `- [${p.title}](${root}${p.slug}/) — ${p.description}`),
      '',
    ]),
    '## Related workflows',
    '',
    '- [Review transport decision guide](/docs/ai/review-transports/)',
    '- [MCP integration](/docs/ai/mcp/)',
    '- [Deterministic tool configuration](/docs/guides/configuration/)',
    '',
  ].join('\n');
  return { hub, pages, targets, headings };
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const path = `${site}src/content/docs/ai/ai-features.md`;
  // Build from this repository unless an explicit release fixture is selected.
  const productSource =
    process.env.LINTRO_PRODUCT_SOURCE ?? fileURLToPath(new URL('../../../', import.meta.url));
  const source = readFileSync(`${productSource}/docs/ai-features.md`, 'utf8');
  const result = await splitAiArticle(readFileSync(path, 'utf8'), source);
  mkdirSync(`${site}src/content/docs/ai/ai-features`, { recursive: true });
  for (const page of result.pages)
    writeFileSync(`${site}src/content/docs/ai/ai-features/${page.slug}.md`, page.markdown);
  writeFileSync(path, result.hub);
  writeFileSync(
    `${site}src/generated/ai-tasks.json`,
    JSON.stringify(
      result.pages.map(({ slug, title, group }) => ({ slug, title, group })),
      null,
      2
    )
  );
  writeFileSync(`${site}src/generated/ai-anchors.json`, JSON.stringify(result.targets, null, 2));
  writeFileSync(`${site}src/generated/ai-headings.json`, JSON.stringify(result.headings, null, 2));
  console.log(
    `AI split: ${result.pages.length} focused pages; ${Object.keys(result.targets).length} exact anchors retained.`
  );
  await import('./tool-reference.mjs');
  await import('./split-start.mjs');
}
