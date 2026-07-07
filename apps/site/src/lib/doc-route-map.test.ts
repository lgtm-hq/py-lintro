import { describe, expect, it } from "vitest";
import { sourceToDoc } from "../generated/docs-route-map";
import { routeForDocHref, sourceForDocId } from "./doc-route-map";

const fixtureMap: Record<string, string> = {
  "README.md": "getting-started/hub",
  "configuration.md": "usage/configuration",
  "docker.md": "usage/docker",
  "architecture/README.md": "architecture/overview",
  "architecture/ARCHITECTURE.md": "architecture/architecture",
  "architecture/VISION.md": "architecture/vision",
  "tool-analysis/README.md": "tools/index",
  "tool-analysis/pytest-analysis.md": "tools/pytest",
};

describe("sourceForDocId", () => {
  it("inverts the source→doc map", () => {
    expect(sourceForDocId("architecture/overview", fixtureMap)).toBe("architecture/README.md");
    expect(sourceForDocId("usage/docker", fixtureMap)).toBe("docker.md");
    expect(sourceForDocId("missing/doc", fixtureMap)).toBeUndefined();
  });
});

describe("routeForDocHref", () => {
  it("resolves sibling links relative to the source directory", () => {
    expect(routeForDocHref("./ARCHITECTURE.md", "architecture/overview", fixtureMap)).toBe(
      "docs/architecture/architecture/",
    );
    expect(routeForDocHref("VISION.md", "architecture/architecture", fixtureMap)).toBe(
      "docs/architecture/vision/",
    );
  });

  it("resolves parent-relative links from nested sources", () => {
    expect(routeForDocHref("../configuration.md", "architecture/overview", fixtureMap)).toBe(
      "docs/usage/configuration/",
    );
  });

  it("resolves root-level links from root-sourced docs", () => {
    expect(routeForDocHref("docker.md", "usage/configuration", fixtureMap)).toBe(
      "docs/usage/docker/",
    );
    expect(routeForDocHref("tool-analysis/pytest-analysis.md", "getting-started/hub", fixtureMap)).toBe(
      "docs/tools/pytest/",
    );
  });

  it("resolves directory links to the section README and flattens index ids", () => {
    expect(routeForDocHref("tool-analysis/", "getting-started/hub", fixtureMap)).toBe(
      "docs/tools/",
    );
    expect(routeForDocHref("architecture/", "getting-started/hub", fixtureMap)).toBe(
      "docs/architecture/overview/",
    );
    expect(routeForDocHref("tool-analysis/README.md", "getting-started/hub", fixtureMap)).toBe(
      "docs/tools/",
    );
  });

  it("preserves section hashes on resolved routes", () => {
    expect(routeForDocHref("docker.md#quick-start", "usage/configuration", fixtureMap)).toBe(
      "docs/usage/docker/#quick-start",
    );
  });

  it("returns null for external, non-markdown, and unmigrated targets", () => {
    expect(
      routeForDocHref(
        "https://github.com/lgtm-hq/py-lintro/issues/new?template=question.md",
        "getting-started/hub",
        fixtureMap,
      ),
    ).toBeNull();
    expect(routeForDocHref("/docs/usage/docker/", "getting-started/hub", fixtureMap)).toBeNull();
    expect(routeForDocHref("comparison.md", "getting-started/hub", fixtureMap)).toBeNull();
    expect(routeForDocHref("docker.md", "unknown/doc", fixtureMap)).toBeNull();
  });

  it("resolves real cross-links against the generated map", () => {
    expect(routeForDocHref("./ARCHITECTURE.md", "architecture/overview", sourceToDoc)).toBe(
      "docs/architecture/architecture/",
    );
    expect(routeForDocHref("../contributing.md", "architecture/roadmap", sourceToDoc)).toBe(
      "docs/contributing/contributing/",
    );
    expect(routeForDocHref("./yamllint-analysis.md", "tools/index", sourceToDoc)).toBe(
      "docs/tools/yamllint/",
    );
    expect(routeForDocHref("architecture/VISION.md", "getting-started/hub", sourceToDoc)).toBe(
      "docs/architecture/vision/",
    );
  });
});
