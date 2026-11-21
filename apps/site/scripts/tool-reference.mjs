/** Reader reference derived from resolved frozen ToolDefinitions and authored caveats. */
import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
const site = fileURLToPath(new URL('../', import.meta.url));
const generated = `${site}src/generated/`;
const docs = `${site}src/content/docs/`;
const snapshot = JSON.parse(readFileSync(`${generated}product-snapshot.json`, 'utf8'));
const config = JSON.parse(readFileSync(`${generated}configuration-pages.json`, 'utf8'));
const notes = {
  actionlint:
    'Only YAML files under .github/workflows are checked. The wrapper uses native text diagnostics and does not expose custom output templates.',
  'astro-check':
    'Runs Astro check over the project, rather than checking only individual supplied component files. Uses the project Astro and TypeScript configuration; this is diagnostics, not an Astro build.',
  bandit:
    'Reports potential security problems; it does not repair them. Native JSON is parsed internally. Keep Bandit-specific excludes in native configuration or use Lintro file exclusion; the proposed exclude/exit_zero/recursive options below are not implemented.',
  black:
    'Black owns Python formatting when selected with Ruff; Ruff then checks and fixes lint rules without formatting. The E501 supplement needs Ruff. Incomplete native reports cannot certify clean findings or measured fixes; remove Black quiet=true if it suppresses per-file reports.',
  buf: 'Checks both lint rules and formatting. Fix mode writes formatting only; lint-rule violations remain for manual repair. Requires a usable Buf module/workspace.',
  'cargo-audit':
    'Audits Cargo.lock dependencies with cargo audit --json. It reports vulnerabilities without updating dependencies; it requires the cargo-audit subcommand and may need advisory-database access.',
  'cargo-deny':
    'Runs cargo deny check for dependency policy. Configure bans, licenses, sources and advisories in deny.toml. This does not update or remediate dependencies.',
  checkov:
    'Restricted to Terraform and Terraform JSON syntax. The wrapper passes --skip-download and does not opt into the Checkov platform, but an inherited BC_API_KEY can still enable platform mode. Severity is null when platform enrichment is unavailable; no automatic fixes.',
  clippy:
    'Runs through Cargo in a Rust project. Fix mode invokes cargo clippy --fix and rechecks; not every lint has an applicable fix. Native Cargo configuration and the working tree can affect whether fixes are accepted.',
  commitlint:
    'Checks the latest Git commit message, rather than linting the contents of arbitrary file paths. Requires a Git repository and a commitlint configuration; without config the wrapper skips.',
  cppcheck:
    'Analyzes C/C++ sources with native XML diagnostics. Header-only globs are not declared here. Enable/suppress/std options affect the analysis; fixes are manual.',
  'dotenv-linter':
    'Checks and fixes .env files. Schema validation is optional; formatting fixes do not establish that environment values are correct or safe.',
  gitleaks:
    'Secret detection is check-only. The no_git option controls file scanning versus Git scanning; review its value before assuming history was scanned. Redaction affects reported secrets, not the source file.',
  'golangci-lint':
    'Requires golangci-lint v2 and a Go toolchain/module. Native v2 configuration chooses linters; fix mode applies only the enabled linters’ available fixes.',
  hadolint:
    'Targets Dockerfile and Dockerfile.*. It reports Dockerfile and embedded shell findings, without fixing files or validating Docker Compose.',
  'html-validate':
    'Validates HTML and declared Vue/Svelte patterns using native rules. Literal paths containing glob characters use a stdin workaround; unsupported path cases are held back rather than silently reinterpreted.',
  'idiom-review':
    'Optional, disabled-by-default advisory AI. Runs under lintro review, never deterministic check/format. Configure AI provider, transport and credentials first. Uncached work can incur provider costs; findings are nondeterministic and no fixes are applied.',
  'import-linter':
    'Checks import contracts once per project using lint-imports. With no real import-linter configuration the wrapper returns a clean no-config result, which does not prove any contracts were checked.',
  markdownlint:
    'The Lintro wrapper is check-only even though native markdownlint-cli2 can apply fixes via its own configuration. Central line length can be injected for MD013; this does not expose every native rule as a CLI option.',
  mypy: 'Strict and ignore_missing_imports are enabled by wrapper default. Native config controls plugins and advanced compiler policy. Decimal python_version CLI values are coerced to floats and rejected; use a string in native configuration instead.',
  'osv-scanner':
    'The native scanner discovers lockfiles recursively; the wrapper bypasses normal file-pattern discovery. It explicitly passes discovered .osv-scanner.toml and can probe with config disabled to identify stale suppressions. Network/database availability matters; no fixes or SBOM option are exposed.',
  oxfmt:
    'Formats JS/TS and Vue patterns assigned by this wrapper. Formatting style belongs in .oxfmtrc.json or .oxfmtrc.jsonc. Prettier handles the separately declared CSS/HTML/JSON/YAML/Markdown/GraphQL/Astro patterns.',
  oxlint:
    'Checks JS/TS and declared framework patterns. Type-aware mode needs its native backend; consult the detailed Oxc analysis for framework and path-alias constraints. Standard native fixes are supported; dangerous fix flags are not exposed.',
  'pip-audit':
    'Audits requirements files and project manifests with strict dependency collection. No Lintro automatic remediation or ignore_vulns option is exposed. Dependency resolution and advisory access can require a network connection.',
  prettier:
    'This wrapper assigns JS/TS/Vue to Oxfmt; selecting only Prettier does not add those file patterns. A failed pre-write check aborts the batch. Astro formatting may require the project’s native Prettier plugin. Set formatting width with native printWidth: enforce.line_length is not passed to Prettier, and without a native config Lintro injects a generated config (built-in proseWrap: always plus defaults.prettier).',
  pydoclint:
    'Checks Python docstring semantics without fixing documentation. Configure style and rule switches in [tool.pydoclint] or .pydoclint.toml; style is not a supported Lintro tool option.',
  pylint:
    'One project-scoped invocation permits cross-module checks such as R0801. Clone sets are reported as native messages. This is check-only and may overlap with Ruff; choose native checker policy deliberately.',
  pytest:
    'Executes tests; it is not a formatter. Use lintro test for test-specific coverage, reporting, plugin and parallel options. pytest-cov, pytest-xdist and pytest-json-report are optional installed plugins, not capabilities installed automatically by Lintro.',
  ruff: 'Combines lint and formatting, with Black owning formatting when both are selected. Unsafe fixes are opt-in. Native JSON is parser input, separate from Lintro output serializers; native --watch is not passed through.',
  rustfmt:
    'Runs Cargo formatting in a Rust project: cargo fmt -- --check for checks and cargo fmt --all for fixes. Cargo workspace scope can exceed individual supplied paths; this is formatting, not Clippy linting.',
  semgrep:
    'Check-only in Lintro even when a native rule offers autofix. Rule packs/configuration determine coverage. Packaged local rules can run without a provider; remote registry configurations may require network access.',
  shellcheck:
    'Reports shell bugs and suggestions but does not apply patches. Select the shell dialect when needed. Following external sourced files is opt-in through external_sources and source_paths.',
  shfmt:
    'Formats shell syntax and optionally simplifies it. Select language_dialect for dialect-specific parsing. This does not replace ShellCheck semantic analysis.',
  spectral:
    'Requires a ruleset; skips without one. Select it explicitly or enable it in project configuration for API documents. JSON/YAML file extensions alone do not mean every document is OpenAPI.',
  sqlfluff:
    'Native dialect and templater configuration control SQL parsing. Fixes cover supported rules; templating failures and unsafe/unfixable findings can remain.',
  stylelint:
    'Requires native Stylelint configuration and skips without it. Only native fixable rules are repaired. Native JSON does not provide per-warning fixability metadata, so a finding’s fixability is not guaranteed.',
  'svelte-check':
    'Checks the whole Svelte project using Svelte and TypeScript configuration. Native watch is not passed through. Project dependencies must be available; it emits diagnostics without fixing files.',
  taplo:
    'Runs TOML syntax lint and formatting check; fix mode formats TOML. Optional schema selection is separate from basic syntax validity and may require access to the schema.',
  trufflehog:
    'Secret detection uses filesystem scanning and disables verification by default. Enabling verification can contact credential providers; no automatic secret rotation or source repair is performed.',
  tsc: 'Uses --noEmit for type checking. Explicit projects and native include/files/exclude scoping retain project selection; temporary narrowed configs apply only in other eligible branches. Vue/Svelte/Astro projects need their framework checker. Native build/emit modes are not exposed.',
  typos:
    'Automatic corrections change source text; inspect the diff. On no-config default runs, selection requires a scan-root typos config; explicit --tools typos selects it directly.',
  vale: 'Requires native Vale styles/configuration for useful prose checks. Native JSON is internal parser input. Suggestions remain manual edits; custom native formatters are not selected through this wrapper.',
  'vue-tsc':
    'Uses project TypeScript configuration for Vue diagnostics without emission. Explicit projects and native include/files/exclude scoping take precedence over narrowing to supplied paths. Native compiler build/watch flags are not forwarded.',
  yamllint:
    'Checks YAML syntax/style, not application-specific schema correctness. Does not fix YAML. Native config, relaxed and strict options influence findings and exit behavior.',
};
/**
 * Reader corrections for option descriptions that the frozen docstrings overstate.
 * Each entry was traced to its runtime consumer in the frozen source, cited inline.
 */
const optionNotes = {
  // lintro/tools/prettier/definition.py: --print-width is added only when no config
  // is injected and no native config is found; TOOL_BUILTIN_DEFAULTS normally injects one.
  'prettier.line_length':
    'Fallback only: adds --print-width when Lintro injects no config and finds no native Prettier config. A generated default config normally pre-empts it; prefer native printWidth.',
};
/**
 * Where a tool option can be persisted. `.lintro-config.yaml` `tools.<name>` accepts
 * only the LintroToolConfig fields; an `options` key there is an unknown option that
 * no execution path reads (lintro/config/tool_config.py, lintro/config/config_loader.py).
 * @param {{ name: string, defaults: { timeout: unknown } }} tool
 * @returns {string}
 */
const optionSurfaces = (tool) =>
  `Persist tool options in \`pyproject.toml\` under \`[tool.lintro.${tool.name}]\`, or pass them for one run with \`--tool-options\`. In ${snapshot.version}, \`tools.${tool.name}\` in \`.lintro-config.yaml\` accepts \`enabled\`, \`auto_install\`, \`rule_packs\` (read for Semgrep) and the display-only \`config_source\`; an \`options\` key there is reported as an unknown option and is not applied.`;
const code = (value) => `\`${String(value).replaceAll('`', '')}\``;
const source = (tool, option = false) =>
  `https://github.com/lgtm-hq/py-lintro/blob/${snapshot.ref}/${option ? tool.optionSource : tool.source}#L${option ? tool.optionLine : tool.line}`;
const aliases = { tsc: 'typescript-compiler-tsc', markdownlint: 'markdownlint-cli2' };
const inventory = [];
for (const tool of snapshot.tools) {
  const name = tool.name.replaceAll('_', '-');
  if (!notes[name]) throw new Error(`Missing reader caveat: ${name}`);
  const path = `${docs}tools/${name}.md`;
  const old = existsSync(path) ? readFileSync(path, 'utf8') : '';
  const end = old.indexOf('\n---', 3) + 4;
  const hasAnalysis = old && !old.includes('A full wrapper analysis has not been authored');
  const configSlug = aliases[name] ?? name;
  let configPage = config.pages.find((p) => p.slug === `tools/${configSlug}`);
  if (!configPage) {
    configPage = {
      slug: `tools/${configSlug}`,
      title: `${name} configuration`,
      href: `/docs/guides/configuration/tools/${configSlug}/`,
      titleAnchor: `${name}-configuration`,
    };
    config.pages.push(configPage);
    writeFileSync(
      `${docs}guides/configuration/tools/${configSlug}.md`,
      `---\ntitle: ${JSON.stringify(configPage.title)}\ndescription: ${JSON.stringify(`Configure ${name} native policy and Lintro orchestration.`)}\ncategory: guides\nsidebar: false\norder: 900\n---\n\nUse [the ${name} reference](/docs/tools/${name}/#configure-${name}) for declared native files, supported tool options and defaults.\n\n${notes[name]}\n\nKeep orchestration in [Lintro tool policy](/docs/guides/configuration/tool-policy/) and review [configuration precedence](/docs/guides/configuration/precedence/) before overriding native settings. [Inspect effective settings](/docs/guides/configuration/reports/) with \`lintro config -v\`.\n`
    );
  }
  const command = tool.advisory
    ? 'lintro review --advisory-only --tool-options idiom-review:enabled=true'
    : name === 'pytest'
      ? 'lintro test'
      : `lintro check . --tools ${tool.name}`;
  const patterns = tool.patterns.length
    ? tool.patterns.map(code).join(', ')
    : 'Native recursive lockfile discovery (no wrapper glob list).';
  const options = new Map(tool.options.map((o) => [o.name, o]));
  for (const [key, value] of Object.entries(tool.defaults)) {
    if (!options.has(key))
      options.set(key, {
        name: key,
        default: value,
        description:
          key === 'timeout'
            ? 'Execution timeout in seconds.'
            : 'Declared wrapper default; see the integration source and notes for behavior.',
      });
  }
  const rows = [...options.values()]
    .map(
      (o) =>
        `| ${code(o.name)} | ${code(JSON.stringify(o.default))} | ${(optionNotes[`${tool.name}.${o.name}`] ?? o.description).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('|', '\\|').replaceAll('`', '')} |`
    )
    .join('\n');
  const opening = `## Use ${name}\n\n${tool.description}.\n\n**Targets:** ${patterns}\n\n**Check/fix boundary:** ${tool.advisory ? 'Advisory review only; no automatic fixes.' : tool.fixes ? 'Checking reports findings. Formatting applies only fixes the native integration supports; review the diff and check again.' : 'Check-only in Lintro. Repair findings manually or use a separately documented native workflow.'}\n\n${tool.advisory ? '[Set up the optional AI provider and transport](/docs/ai/ai-features/setup/) before this opt-in command. It can send source to the configured provider and incur costs.' : 'From the project root, after installing the native tool and its project dependencies:'}\n\n\`\`\`bash\n${command}\n\`\`\`\n\n${tool.minimumVersion ? `Declared native minimum: **${tool.minimumVersion}**. ` : ''}${tool.advisory ? 'No external tool binary is used.' : 'CLI installation does not install every integration. Run `lintro doctor` to inspect availability; a skipped tool has not checked the project.'}\n\n## Configure ${name}\n\n**Native files declared by the wrapper:** ${tool.nativeConfigs.length ? tool.nativeConfigs.map(code).join(', ') + '.' : 'No filenames declared. This is not a claim that the native tool has no configuration; see the integration notes.'}\n\n[${name} configuration guide](${configPage.href}) · [Configuration precedence](/docs/guides/configuration/precedence/) · [Effective configuration report](/docs/guides/configuration/reports/)\n\n<details><summary>Supported Lintro options and declared defaults</summary>\n\nThese are wrapper options, not arbitrary native CLI flags. Defaults below come from the integration definition/signature; native configuration and resolved central settings can change effective behavior. A null value means no value is declared here. Keep complex lists and values whose CLI coercion changes their type in native configuration or the \`pyproject.toml\` table named below.\n\nUse \`--tool-options ${tool.name}:timeout=${tool.defaults.timeout}\` for a timeout override${tool.advisory ? ' on review' : name === 'pytest' ? ' on test' : tool.fixes ? ' on check/format' : ' on check'}. ${optionSurfaces(tool)} Test command options are additionally documented in the [test command reference](/docs/reference/cli/test/).\n\n| Option | Declared default | Purpose / constraints |\n| --- | --- | --- |\n${rows}\n\n[Read option validation and construction](${source(tool, true)}).\n\n</details>\n\n## Boundaries and next steps\n\n${notes[name]}\n\n${!tool.advisory && name !== 'pytest' ? `For repeated checks, [Lintro watch](/docs/guides/watch-mode/) reruns the selected tools. This is separate orchestration; a native tool’s --watch/build/emit flag is not implied. ` : ''}Native diagnostic formats (JSON, JSONL, XML or text) are internal parser inputs. Choose public serializers with [Lintro output options](/docs/reference/cli/${tool.advisory ? 'review' : name === 'pytest' ? 'test' : 'check'}/); available serializers do not imply a native formatter flag is exposed.\n\n[Integration source](${source(tool)}) · [All tools](/docs/tools/) · [Install and diagnose tools](/docs/guides/configuration/setup-and-install/)${['oxlint', 'oxfmt'].includes(name) ? ' · [Detailed Oxc comparison](/docs/tools/oxc/)' : ''}\n\n`;
  const metadata = hasAnalysis
    ? old.slice(0, end)
    : `---\ntitle: "${name} reference"\ndescription: ${JSON.stringify(`${tool.description}. Targets, first command, configuration and wrapper limitations.`)}\ncategory: tools\norder: 900\nnavTitle: "${name}"\nnavGroup: reference\n---`;
  writeFileSync(
    path,
    `${metadata}\n\n${opening}${hasAnalysis ? old.slice(end).trim() + '\n' : ''}`
  );
  const cp = `${docs}guides/configuration/${configPage.slug}.md`;
  if (existsSync(cp) && !readFileSync(cp, 'utf8').includes(`/docs/tools/${name}/`))
    writeFileSync(
      cp,
      readFileSync(cp, 'utf8') +
        `\n[${name}: targets, commands and wrapper limits](/docs/tools/${name}/).\n`
    );
  inventory.push({
    name,
    command,
    config: configPage.href,
    source: source(tool),
    options: source(tool, true),
    minimumVersion: tool.minimumVersion,
    notes: notes[name],
  });
}
writeFileSync(`${generated}configuration-pages.json`, JSON.stringify(config, null, 2));
writeFileSync(`${generated}tool-claims.json`, JSON.stringify(inventory, null, 2));
const index = `${docs}tools/index.md`;
writeFileSync(
  index,
  readFileSync(index, 'utf8') +
    `\n## Find an integration\n\n${inventory.map((t) => `- [${t.name}](/docs/tools/${t.name}/) — [configuration](${t.config})`).join('\n')}\n`
);
console.log(
  `Reader tool references: ${inventory.length}; all have configuration cross-links and source evidence.`
);
