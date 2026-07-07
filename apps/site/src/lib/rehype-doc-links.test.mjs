import { describe, expect, it } from "vitest";
import { rehype } from "rehype";
import rehypeStringify from "rehype-stringify";
import { rehypeDocLinks, rehypeUnwrapHeadingLinks } from "./rehype-doc-links.mjs";

const BASE = "/Rustume/";

/**
 * @param {string} html
 * @returns {Promise<string>}
 */
async function transform(html) {
  const file = await rehype()
    .data("settings", { fragment: true })
    .use(rehypeDocLinks, BASE)
    .use(rehypeStringify)
    .process(html);

  return String(file);
}

describe("rehypeDocLinks", () => {
  it("keeps external links readable and opens them safely", async () => {
    const output = await transform('<p><a href="https://www.rust-lang.org/">Rust</a></p>');

    expect(output).not.toContain("smart-link");
    expect(output).toContain("Rust");
    expect(output).toContain('target="_blank"');
    expect(output).toContain('rel="noopener noreferrer"');
  });

  it("prefixes internal prose links with the site base without turning them into pills", async () => {
    const output = await transform(
      '<p><a href="/docs/getting-started/quickstart/">Quickstart</a></p>',
    );

    expect(output).not.toContain("doc-link");
    expect(output).toContain('href="/Rustume/docs/getting-started/quickstart/"');
  });

  it("leaves localhost links unchanged", async () => {
    const output = await transform('<p><a href="http://localhost:5173">editor</a></p>');

    expect(output).not.toContain("smart-link");
    expect(output).toContain('href="http://localhost:5173"');
  });

  it("skips links inside code blocks", async () => {
    const output = await transform(
      '<pre><code><a href="https://example.com">Example</a></code></pre>',
    );

    expect(output).not.toContain("smart-link");
  });

  it("leaves hash-only anchors unchanged", async () => {
    const output = await transform('<p><a href="#section">Section</a></p>');

    expect(output).toContain('href="#section"');
  });

  it("leaves mailto links unchanged", async () => {
    const output = await transform('<p><a href="mailto:hi@example.com">Email</a></p>');

    expect(output).toContain('href="mailto:hi@example.com"');
  });

  it("normalizes parent segments in internal paths", async () => {
    const output = await transform(
      '<p><a href="/docs/foo/../getting-started/quickstart/">Quickstart</a></p>',
    );

    expect(output).toContain('href="/Rustume/docs/getting-started/quickstart/"');
  });

  it("unwraps links inside headings to plain text", async () => {
    const file = await rehype()
      .data("settings", { fragment: true })
      .use(rehypeUnwrapHeadingLinks)
      .use(rehypeDocLinks, BASE)
      .use(rehypeStringify)
      .process('<h2><a href="https://www.linkedin.com/">LinkedIn</a> data export</h2>');

    const output = String(file);

    expect(output).not.toContain("smart-link");
    expect(output).toContain("LinkedIn data export");
    expect(output).not.toContain("<a");
  });
});
