/** Partition the long guide once and preserve its full heading/fragment contract. */
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { referenceHeadings } from './reference-headings.mjs';
const site = fileURLToPath(new URL('../', import.meta.url));
const root = '/docs/start/getting-started/';
const path = `${site}src/content/docs/start/getting-started.md`;
const article = readFileSync(path, 'utf8');
const body = article.slice(article.indexOf('\n---', 3) + 4).trim();
const headings = await referenceHeadings(body);
const sections = [];
let index = 0;
let fence = null;
for (const line of body.split('\n')) {
  const marker = line.match(/^\s{0,3}(`{3,}|~{3,})/);
  if (marker) {
    if (!fence) fence = marker[1];
    else if (marker[1][0] === fence[0] && marker[1].length >= fence.length) fence = null;
  }
  const match = !fence && line.match(/^(#{2,6})\s+(.+)$/);
  if (match) {
    const heading = headings[index++];
    if (!heading || heading.depth !== match[1].length)
      throw new Error('Unmapped onboarding heading');
    if (heading.depth === 2) sections.push({ ...heading, lines: [], headings: [] });
    const section = sections.at(-1);
    section.headings.push(heading);
    section.lines.push(`${line.replace(/\s*\{#[\w-]+\}\s*$/, '')} {#${heading.slug}}`);
  } else if (sections.length) sections.at(-1).lines.push(line);
}
if (index !== headings.length) throw new Error('Lost onboarding headings');
const targets = Object.fromEntries(
  sections.flatMap((s) => s.headings.map((h) => [h.slug, `${root}${s.slug}/#${h.slug}`]))
);
mkdirSync(`${site}src/content/docs/start/getting-started`, { recursive: true });
for (const section of sections) {
  const prose = section.lines
    .join('\n')
    .replace(/^## .+\n/, '')
    .replace(/^###/gm, '##')
    .replace(/\]\(#([^)]*)\)/g, (m, hash) => (targets[hash] ? `](${targets[hash]})` : m));
  writeFileSync(
    `${site}src/content/docs/start/getting-started/${section.slug}.md`,
    `---\ntitle: ${JSON.stringify(section.text)}\ntitleAnchor: ${JSON.stringify(section.slug)}\nsourceAnchor: ${JSON.stringify(section.slug)}\ndescription: ${JSON.stringify(`${section.text}: focused setup and operating guidance for Lintro.`)}\ncategory: start\nsidebar: false\norder: 100\n---\n\n[← First-run journey](${root})\n\n${prose}\n\n[Choose another setup or workflow topic](${root}#continue-with-your-task).\n`
  );
}
const steps = [
  [
    'Install the CLI',
    'pip install lintro',
    `Python installs need Python 3.11+. [Choose Homebrew, bundled Python tools or Docker](${root}installation/).`,
  ],
  [
    'Initialize your project',
    'lintro init',
    'Stack detection prepares .lintro-config.yaml. Inspect the selected tools before running them.',
  ],
  [
    'Diagnose tool availability',
    'lintro doctor',
    'A missing or skipped tool has not checked your code.',
  ],
  [
    'Install the recommended profile',
    'lintro install --profile recommended',
    `Node tools become project dev dependencies by default. [Read requirements and installation policy](${root}requirements/), then rerun doctor.`,
  ],
  [
    'Run your first check',
    'lintro check .',
    `Review findings before using \`lintro format .\` to apply supported fixes, then check again. [Understand the example output](${root}first-steps/#3-understanding-the-output).`,
  ],
];
const tasks = [
  ['supported-languages-and-tools', 'Choose tools for your language or stack'],
  ['configuration', 'Configure native rules and Lintro policy'],
  ['common-workflows', 'Use daily, CI and changed-file workflows'],
  ['output-system-auto-generated-reports', 'Save and interpret reports'],
  ['integration-examples', 'Connect pre-commit, Make or VS Code'],
  ['troubleshooting', 'Troubleshoot a first run'],
  ['ai-powered-features', 'Understand optional AI prerequisites'],
];
writeFileSync(
  path,
  `---\ntitle: "Get your first check running"\ndescription: "Install Lintro, initialize a project, diagnose tools, install a profile and check your code."\ncategory: start\norder: 10\nnavTitle: "Getting started"\n---\n\nStart in your project directory. Lintro is a CLI; wrapped tools are separate dependencies. The default install does not install every linter or enable AI.\n\n## Install, initialize, diagnose, check\n\n${steps.map(([title, command, text], i) => `${i + 1}. **${title}**\n\n   \`\`\`bash\n   ${command}\n   \`\`\`\n\n   ${text}`).join('\n\n')}\n\n## Continue with your task\n\n${tasks.map(([slug, label]) => `- [${label}](${root}${slug}/)`).join('\n')}\n- [All 44 tool integrations](/docs/tools/) · [Full configuration directory](/docs/guides/configuration/)\n\n<details><summary>More recipes and background</summary>\n\n${sections
    .filter(
      (s) =>
        !tasks.some(([slug]) => slug === s.slug) &&
        !['requirements', 'installation', 'first-steps'].includes(s.slug)
    )
    .map((s) => `- [${s.text}](${root}${s.slug}/)`)
    .join('\n')}\n\n</details>\n`
);
writeFileSync(`${site}src/generated/start-anchors.json`, JSON.stringify(targets, null, 2));
writeFileSync(`${site}src/generated/start-headings.json`, JSON.stringify(headings, null, 2));
console.log(`Onboarding split: ${sections.length} pages; ${headings.length} bookmarks retained.`);
