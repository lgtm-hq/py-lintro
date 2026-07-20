# Template-Aware Preprocessing

Opt-in pre-processing so lintro can lint **source-language Jinja templates** such as
`main.py.jinja`, `pyproject.toml.jinja`, and `docker-compose.yml.jinja`.

> **Best-effort pre-pass — read this before enabling.**
>
> Stub rendering can **hide real template bugs** (for example a `{% for %}` over an
> empty stub collection skips the loop body). Line-number mapping is **imperfect around
> Jinja control structures** that emit or suppress blocks. Treat findings as a useful
> signal, not as a guarantee that the rendered project is clean.

This feature is **disabled by default**. It generalizes the idea behind SQLFluff’s
`templater` option: pre-render with stubs, lint the host language, then map issues back
to the original template.

## Quick Start

```yaml
# .lintro-config.yaml
template_aware:
  enabled: true
  # patterns / engine / stub_strategy / route use sensible defaults
```

```bash
lintro check --tools ruff
```

With the defaults, `*.py.jinja` files under the scanned paths are stub-rendered to
temporary `.py` files, checked with ruff, and issues are reported against the original
`*.py.jinja` paths.

## Configuration

```yaml
template_aware:
  enabled: true
  patterns:
    - '*.py.jinja'
    - '*.toml.jinja'
    - '*.yml.jinja'
    - '*.yaml.jinja'
    - '*.json.jinja'
  engine: jinja2 # jinja2 | copier | cookiecutter
  stub_strategy: sentinel # sentinel | defaults | context_file
  context_file: .copier-answers.yml # used when stub_strategy: context_file
  route:
    '*.py.jinja': ruff
    '*.toml.jinja': taplo
    '*.yml.jinja': yamllint
    '*.yaml.jinja': yamllint
    '*.json.jinja': prettier
```

| Field           | Default    | Description                                                                 |
| --------------- | ---------- | --------------------------------------------------------------------------- |
| `enabled`       | `false`    | Master switch. Must be `true` for any preprocessing.                        |
| `patterns`      | see above  | Filename globs for templates to consider.                                   |
| `engine`        | `jinja2`   | Engine hint for defaults discovery (`copier` / `cookiecutter` file layout). |
| `stub_strategy` | `sentinel` | How `{{ var }}` values are supplied (see below).                            |
| `context_file`  | `null`     | Path to YAML/JSON answers when using `context_file` strategy.               |
| `route`         | see above  | Template pattern → host tool name.                                          |

## Stub Strategies

### `sentinel` (default)

Replaces undefined `{{ var }}` expressions with type-stable placeholders (`__STR__`,
`__INT__` for integer-looking names). `{% if %}` blocks stay on the true branch;
`{% for %}` iterates once so loop bodies are not skipped.

### `defaults`

Reads defaults from nearby `copier.yml` / `copier.yaml` or `cookiecutter.json` (searched
upward from the template). Prefer when scaffolding files declare defaults.

### `context_file`

Uses a user-supplied YAML/JSON file (`context_file`) with real answer values. Highest
fidelity when answers are available (for example `.copier-answers.yml`).

## Pipeline

1. Discover templates matching `patterns` that `route` to the running host tool.
2. Stub-render each template into a temporary directory as the host language.
3. Run the host linter (ruff, taplo, yamllint, prettier, …) on rendered files.
4. Source-map reported line numbers back to the original `*.jinja` path.
5. Report issues against the original template coordinates.

## Fidelity Warning

- **Stub gaps:** Empty collections, falsey branches, and missing macros can omit code
  the real render would include — or include code a real render would skip.
- **Line maps:** Control structures that expand or collapse blocks make
  rendered→original line mapping approximate. Prefer `context_file` with real answers
  when line accuracy matters.
- **Not a Jinja linter:** Template syntax itself is still best checked with dedicated
  Jinja tools (see djlint / j2lint plugins). Template-aware mode only helps
  **host-language** rules on rendered output.

## Relation to SQLFluff templater

SQLFluff’s `templater: jinja` option remains the right way to lint templated SQL.
Template-aware mode does **not** replace it; it applies the same _idea_ to non-SQL
source templates that have no native templater.
