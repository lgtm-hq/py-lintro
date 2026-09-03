import { describe, expect, it } from 'vitest';
import {
  MCP_DEFAULT_TOOL,
  MCP_RESPONSES,
  MCP_TOOLS,
  colorJson,
  escapeHtml,
  isMcpToolName,
  withVersion,
} from './home-content';

describe('homepage MCP console content', () => {
  it('ships a valid JSON example for each of the seven tools', () => {
    expect(MCP_TOOLS).toHaveLength(7);
    for (const name of MCP_TOOLS) {
      expect(() => JSON.parse(MCP_RESPONSES[name])).not.toThrow();
    }
  });

  it('substitutes the version placeholder', () => {
    expect(MCP_RESPONSES.lintro_ping).toContain('__LINTRO_VERSION__');
    expect(withVersion(MCP_RESPONSES.lintro_ping, '1.2.3')).toContain('"lintro_version": "1.2.3"');
  });

  it('defaults to a known tool', () => {
    expect(isMcpToolName(MCP_DEFAULT_TOOL)).toBe(true);
    expect(isMcpToolName('lintro_nope')).toBe(false);
  });

  it('documents the dry-run default on lintro_format', () => {
    expect(JSON.parse(MCP_RESPONSES.lintro_format)).toMatchObject({ dry_run: true });
  });

  it('mirrors the documented MCP payload shapes', () => {
    const review = JSON.parse(MCP_RESPONSES.lintro_review);
    expect(review).toHaveProperty('summary');
    expect(review.findings[0]).toMatchObject({ severity: 'P2', source: 'no-raw-sql' });
    expect(review.run).toHaveProperty('cost_usd');
    expect(review.budget).toHaveProperty('effective_usd');
    const list = JSON.parse(MCP_RESPONSES.lintro_list_tools);
    expect(list.tools[0]).toMatchObject({ types: ['linter', 'formatter'], status: 'ok' });
    expect(list.summary).toMatchObject({ total: 40 });
    const versions = JSON.parse(MCP_RESPONSES.lintro_versions);
    expect(versions.tools[0]).toHaveProperty('installed_version');
    expect(versions.summary).toHaveProperty('outdated');
    const doctor = JSON.parse(MCP_RESPONSES.lintro_doctor);
    expect(doctor.checks[0]).toMatchObject({ check: 'config.load', category: 'config' });
    expect(doctor.summary).toHaveProperty('skipped');
  });

  it('does not colour literals that appear inside strings', () => {
    const out = colorJson('{ "msg": "true", "ok": true }');
    expect(out).toContain('<span class="hi">"true"</span>');
    expect(out).toContain('<span class="ok">true</span>');
    expect(out.match(/class="ok"/g)).toHaveLength(1);
  });

  it('escapes markup before colouring', () => {
    expect(escapeHtml('<b>&</b> "q" \'a\'')).toBe(
      '&lt;b&gt;&amp;&lt;/b&gt; &quot;q&quot; &#39;a&#39;'
    );
    const out = colorJson('{ "ok": true, "msg": "<x>" }');
    expect(out).toContain('<span class="key">"ok"</span>');
    expect(out).toContain('<span class="ok">true</span>');
    expect(out).toContain('&lt;x&gt;');
    expect(out).not.toContain('<x>');
  });
});
