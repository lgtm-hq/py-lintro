import { describe, expect, it } from "vitest";
import { docHref, docs, external, home } from "./site-links";

describe("site-links", () => {
  it("exposes internal doc paths under docs/", () => {
    expect(docs.gettingStarted).toMatch(/^docs\//);
    expect(docs.configuration).toMatch(/^docs\//);
  });

  it("exposes home and external link metadata", () => {
    expect(home.href).toBe("/");
    expect(external.github.href).toMatch(/^https:\/\//);
    expect(external.pypi.href).toMatch(/^https:\/\//);
  });

  it("joins doc paths with a normalized base slash", () => {
    expect(docHref("/py-lintro", "docs/usage/configuration/")).toBe(
      "/py-lintro/docs/usage/configuration/",
    );
    expect(docHref("/py-lintro/", "/docs/usage/configuration/")).toBe(
      "/py-lintro/docs/usage/configuration/",
    );
    expect(docHref("  /py-lintro/  ", "/docs/usage/configuration/")).toBe(
      "/py-lintro/docs/usage/configuration/",
    );
    expect(docHref("/py-lintro///", "///docs/usage/configuration/")).toBe(
      "/py-lintro/docs/usage/configuration/",
    );
    expect(docHref("/py-lintro", "   ")).toBe("/py-lintro/");
    expect(docHref("/py-lintro", "")).toBe("/py-lintro/");
    expect(docHref("py-lintro", "docs/getting-started/getting-started")).toBe(
      "/py-lintro/docs/getting-started/getting-started",
    );
    expect(docHref("/", "docs/usage/configuration/")).toBe("/docs/usage/configuration/");
  });
});
