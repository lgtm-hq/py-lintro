import { describe, expect, it } from "vitest";
import { faviconDomain, remoteFaviconUrl } from "./favicon";

describe("faviconDomain", () => {
  it("returns hostname for public https URLs", () => {
    expect(faviconDomain("https://www.docker.com/get-started")).toBe("docker.com");
    expect(faviconDomain("https://vite.dev/guide/")).toBe("vite.dev");
  });

  it("returns undefined for internal paths and non-http schemes", () => {
    expect(faviconDomain("/docs/deployment/docker/")).toBeUndefined();
    expect(faviconDomain("mailto:team@example.com")).toBeUndefined();
    expect(faviconDomain("#section")).toBeUndefined();
  });

  it("returns undefined for local development hosts", () => {
    expect(faviconDomain("http://localhost:5173")).toBeUndefined();
    expect(faviconDomain("http://127.0.0.1:3000/swagger-ui/")).toBeUndefined();
  });
});

describe("remoteFaviconUrl", () => {
  it("builds Google favicon URL for resolvable domains", () => {
    expect(remoteFaviconUrl("https://github.com/lgtm-hq/Rustume")).toBe(
      "https://www.google.com/s2/favicons?domain=github.com&sz=32",
    );
  });

  it("returns undefined when domain cannot be resolved", () => {
    expect(remoteFaviconUrl("http://localhost:3000")).toBeUndefined();
    expect(remoteFaviconUrl("/docs/cloud/overview/")).toBeUndefined();
  });
});
