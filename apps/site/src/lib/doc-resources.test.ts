import { describe, expect, it } from "vitest";
import { collectDocResources, dedupeResources } from "./doc-resources";

describe("collectDocResources", () => {
  it("collects visible description and Markdown links in page order", () => {
    const resources = collectDocResources({
      description: 'Read <a href="https://typst.app/">Typst</a> templates.',
      markdown: [
        "Use [Quickstart](/docs/getting-started/quickstart/) and [Typst](https://typst.app/).",
      ],
    });

    expect(resources).toEqual([
      { label: "Typst", href: "https://typst.app/" },
      { label: "Quickstart", href: "/docs/getting-started/quickstart/" },
    ]);
  });

  it("does not treat same-page anchors as resources", () => {
    const resources = collectDocResources({
      markdown: ["Read [this section](#configuration) and [Cloud](/docs/cloud/overview/)."],
      currentDocId: "usage/configuration",
    });

    expect(resources).toEqual([{ label: "Cloud", href: "/docs/cloud/overview/" }]);
  });

  it("only keeps cross-page body links when a doc id is provided", () => {
    const resources = collectDocResources({
      markdown: [
        [
          "See [Configuration](configuration.md) and [this section](#quick-start).",
          "Read [gosu](https://github.com/tianon/gosu).",
        ].join("\n"),
      ],
      currentDocId: "usage/docker",
    });

    expect(resources).toEqual([
      { label: "Configuration", href: "docs/usage/configuration/" },
      { label: "gosu", href: "https://github.com/tianon/gosu" },
    ]);
  });

  it("includes explicitly inserted content links", () => {
    const resources = collectDocResources({
      inserted: [{ label: "Hosting options", href: "/docs/pricing/plans/" }],
      markdown: ["See [Storage](/docs/cloud/storage/)."],
    });

    expect(resources).toEqual([
      { label: "Hosting options", href: "/docs/pricing/plans/" },
      { label: "Storage", href: "/docs/cloud/storage/" },
    ]);
  });

  it("filters invalid inserted resources", () => {
    const resources = collectDocResources({
      inserted: [
        { label: "Skip anchor", href: "#plans" },
        { label: "Valid", href: "/docs/pricing/plans/" },
        { label: "", href: "" },
      ],
    });

    expect(resources).toEqual([{ label: "Valid", href: "/docs/pricing/plans/" }]);
  });

  it("skips repo README links and normalizes arrow labels", () => {
    const resources = collectDocResources({
      markdown: [
        [
          "[main README](../README.md)",
          "[Main README → Installation](../README.md#installation)",
          "[Docker Usage → Quick Start](docker.md#quick-start)",
          "[Getting Started → Troubleshooting](getting-started.md#troubleshooting)",
        ].join("\n"),
      ],
      currentDocId: "getting-started/hub",
    });

    expect(resources).toEqual([
      { label: "Quick Start", href: "docs/usage/docker/#quick-start" },
      { label: "Troubleshooting", href: "docs/getting-started/getting-started/#troubleshooting" },
    ]);
  });

  it("drops markdown links whose targets were not migrated", () => {
    const resources = collectDocResources({
      markdown: ["See [Comparison](comparison.md) and [Docker](docker.md)."],
      currentDocId: "getting-started/hub",
    });

    expect(resources).toEqual([{ label: "Docker", href: "docs/usage/docker/" }]);
  });

  it("enriches generic table labels from href targets", () => {
    const resources = collectDocResources({
      markdown: [
        [
          "| Ruff | [Config Guide](configuration.md#ruff-configuration) |",
          "| pydoclint | [Analysis](tool-analysis/pydoclint-analysis.md) |",
        ].join("\n"),
      ],
      currentDocId: "getting-started/hub",
    });

    expect(resources).toEqual([
      { label: "Ruff Config", href: "docs/usage/configuration/#ruff-configuration" },
      { label: "Pydoclint Analysis", href: "docs/tools/pydoclint/" },
    ]);
  });
});

describe("dedupeResources", () => {
  it("keeps the first label for repeated destinations", () => {
    expect(
      dedupeResources([
        { label: "Cloud overview", href: "/docs/cloud/overview/" },
        { label: "Cloud", href: "/docs/cloud/overview/" },
      ]),
    ).toEqual([{ label: "Cloud overview", href: "/docs/cloud/overview/" }]);
  });
});
