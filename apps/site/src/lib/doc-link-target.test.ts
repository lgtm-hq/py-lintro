import { describe, expect, it } from "vitest";
import { docIdFromVFilePath, isCrossPageLink, resolveTargetDocId } from "./doc-link-target.mjs";

describe("docIdFromVFilePath", () => {
  it("derives collection ids from markdown source paths", () => {
    expect(docIdFromVFilePath("/repo/apps/site/src/content/docs/usage/docker.md")).toBe(
      "usage/docker",
    );
    expect(docIdFromVFilePath("/repo/apps/site/src/content/docs/getting-started/hub.md")).toBe(
      "getting-started/hub",
    );
  });
});

describe("resolveTargetDocId", () => {
  it("resolves sibling and parent-relative markdown paths", () => {
    expect(resolveTargetDocId("configuration.md", "usage/docker")).toBe("usage/configuration");
    expect(resolveTargetDocId("../configuration.md", "usage")).toBe("configuration");
    expect(resolveTargetDocId("#quick-start", "usage/docker")).toBe("usage/docker");
  });
});

describe("isCrossPageLink", () => {
  it("treats anchors and same-page paths as in-page navigation", () => {
    expect(isCrossPageLink("#quick-start", "usage/docker")).toBe(false);
    expect(isCrossPageLink("docker.md#troubleshooting", "usage/docker")).toBe(false);
  });

  it("treats other docs and external URLs as resources", () => {
    expect(isCrossPageLink("configuration.md", "usage/docker")).toBe(true);
    expect(isCrossPageLink("https://github.com/lgtm-hq/py-lintro", "usage/docker")).toBe(true);
    expect(isCrossPageLink("../README.md", "getting-started/hub")).toBe(false);
  });
});
